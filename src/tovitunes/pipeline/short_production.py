"""One durable production application service shared by CLI, WebUI and workers."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from tovitunes.artifacts.store import ArtifactRecord, AssetStore
from tovitunes.benchmark.providers import ImageProvider, ProviderFailure
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.creative.director import pinned_facts, validate_pins
from tovitunes.creative.factory import creative_generator
from tovitunes.creative.metadata import MetadataWriter
from tovitunes.creative.nvidia import NvidiaNIMClient
from tovitunes.creative.provider import GenerationContext, StructuredGenerator
from tovitunes.creative.workflow import CreativeWorkflow, episode_by_key
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.domain.storyboard import (
    LyricIntent,
    SceneIntent,
    StoryboardTemplate,
    TimedStoryboardV2,
    build_storyboard,
)
from tovitunes.domain.visual_plan import EpisodeVisualPlan, validate_visual_plan
from tovitunes.music.ace_step import AceStepLocalProvider
from tovitunes.music.analysis import AnalysisConfig
from tovitunes.music.benchmark import MusicBenchmark
from tovitunes.music.benchmark import plan as plan_music
from tovitunes.music.providers import MusicProvider
from tovitunes.persistence.db import Database
from tovitunes.pipeline.execution import production_execution
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.pipeline.production import accept_episode_source, extract_alignment, extract_beats
from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.service import PublicationService
from tovitunes.render.environment_sets import EnvironmentSet, generate_set, selected_set
from tovitunes.render.episode_assets import ImageStageBlocked, generate_assets, persist_file
from tovitunes.render.models import RenderManifest
from tovitunes.render.production import ProductionRenderer, load_inputs, validate_manifest
from tovitunes.youtube.client import UploadAmbiguous

STAGES = (
    "CREATIVE",
    "MUSIC",
    "AUDIO_ANALYSIS",
    "VISUAL_PLAN",
    "VISUAL_ASSETS",
    "STORYBOARD",
    "RENDER",
    "MEDIA_QA",
    "METADATA",
    "RELEASE",
    "YOUTUBE",
)
_STAGE_ARTIFACTS = {
    "CREATIVE": ("episode_spec", "lyrics", "music_spec"),
    "MUSIC": ("audio_master",),
    "VISUAL_PLAN": ("episode_visual_plan",),
    "STORYBOARD": ("timed_storyboard",),
    "RENDER": ("final_render", "render_manifest"),
    "MEDIA_QA": ("media_qa",),
    "METADATA": ("publication_metadata",),
}


class ProductionStop(RuntimeError):
    def __init__(self, status: str, reason: str, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.status, self.evidence = status, evidence or {}


class ShortProductionWorkflow:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        creative_provider: StructuredGenerator | None = None,
        music_provider: MusicProvider | None = None,
        image_provider: ImageProvider | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.creative_provider, self.music_provider = creative_provider, music_provider
        self.image_provider, self.progress = image_provider, progress

    @contextmanager
    def _creative(self) -> Iterator[CreativeWorkflow]:
        if self.creative_provider is not None:
            yield CreativeWorkflow(self.config, self.creative_provider)
        else:
            primary = NvidiaNIMClient(self.config.creative_llm)
            try:
                with creative_generator(
                    self.database, self.config.creative_llm, primary
                ) as provider:
                    yield CreativeWorkflow(self.config, provider)
            finally:
                primary.close()

    def _selected(self, episode: Episode, kind: str, slot: str = "main") -> ArtifactRecord | None:
        return self.store.selected("episode", episode.episode_id, kind, slot)

    def _json(
        self,
        episode: Episode,
        kind: str,
        payload: object,
        deps: tuple[str, ...],
        provenance: Provenance | None = None,
    ) -> ArtifactRecord:
        path = self.working / f"{uuid4()}.json"
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        try:
            return persist_file(self.store, episode, kind, "main", path, deps, provenance)
        finally:
            path.unlink(missing_ok=True)

    def _event(self, episode: Episode, stage: str, status: str, evidence: dict[str, Any]) -> None:
        self.leases.assert_owner(self.lease)
        with closing(self.database.connect()) as db:
            db.execute(
                "INSERT INTO production_stage_events VALUES (?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    episode.episode_id,
                    stage,
                    status,
                    json.dumps(evidence, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
            db.commit()
        if self.progress:
            self.progress(stage, status)

    def plan(self, episode_key: str | None = None) -> dict[str, Any]:
        """Read-only projection: events explain blockers, artifacts/ledgers prove completion."""
        report: dict[str, Any] = {
            "episode_key": episode_key,
            "status": "READY",
            "dry_run": True,
            "provider_calls": 0,
            "auto_publish": self.config.automation.auto_publish,
            "publish_visibility": self.config.automation.publish_visibility,
            "require_human_review": self.config.automation.require_human_review,
            "publication_would_be_attempted": False,
            "stages": [
                {"name": stage, "status": "NOT_STARTED", "evidence": {}} for stage in STAGES
            ],
        }
        by_name = {stage["name"]: stage for stage in report["stages"]}
        if not self.config.database_path.is_file():
            if episode_key:
                raise KeyError("episode database unavailable")
        else:
            with closing(
                sqlite3.connect(self.config.database_path.resolve().as_uri() + "?mode=ro", uri=True)
            ) as db:
                db.row_factory = sqlite3.Row
                tables = {
                    r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if episode_key is None and "production_next_runs" in tables:
                    active = db.execute(
                        "SELECT e.external_key FROM production_next_runs p "
                        "JOIN creative_runs c ON c.run_id=p.run_id "
                        "JOIN episodes e ON e.episode_id=coalesce(p.episode_id,c.episode_id) "
                        "WHERE p.status='active' ORDER BY p.rowid LIMIT 1"
                    ).fetchone()
                    if active:
                        episode_key = report["episode_key"] = active[0]
                episode = (
                    db.execute(
                        "SELECT * FROM episodes WHERE external_key=?", (episode_key,)
                    ).fetchone()
                    if episode_key
                    else None
                )
                if episode_key and episode is None:
                    raise KeyError(episode_key)
                if episode is not None:
                    eid = report["episode_id"] = episode["episode_id"]
                    artifacts = {}
                    checked: set[str] = set()
                    visiting: set[str] = set()

                    def valid_selected(aid: str) -> bool:
                        if aid in visiting:
                            return False
                        if aid in checked:
                            return True
                        row = db.execute(
                            "SELECT * FROM artifact_versions WHERE artifact_id=?", (aid,)
                        ).fetchone()
                        if row is None:
                            return False
                        path = (self.config.data_root / row["relative_path"]).resolve()
                        if (
                            not path.is_relative_to(self.config.data_root.resolve())
                            or not path.is_file()
                            or sha256(path.read_bytes()).hexdigest() != row["sha256"]
                        ):
                            return False
                        current = db.execute(
                            "SELECT artifact_id FROM artifact_selections WHERE owner_scope=? "
                            "AND owner_id=? AND kind=? AND slot_key=?",
                            (
                                row["owner_scope"],
                                row["episode_id"] or row["brand_revision_id"],
                                row["kind"],
                                row["slot_key"],
                            ),
                        ).fetchone()
                        if not current or current[0] != aid:
                            return False
                        decision = db.execute(
                            "SELECT status FROM approval_decisions WHERE artifact_id=? ORDER "
                            "BY rowid DESC LIMIT 1",
                            (aid,),
                        ).fetchone()
                        rights = db.execute(
                            "SELECT status FROM rights_decisions WHERE artifact_id=? ORDER BY "
                            "rowid DESC LIMIT 1",
                            (aid,),
                        ).fetchone()
                        admission = (
                            db.execute(
                                "SELECT sha256 FROM preview_admissions WHERE artifact_id=?", (aid,)
                            ).fetchone()
                            if "preview_admissions" in tables
                            else None
                        )
                        if (decision and decision[0] in {"rejected", "needs_review"}) or (
                            rights and rights[0] == "blocked"
                        ):
                            return False
                        if not (decision and decision[0] == "approved") and not (
                            admission and admission[0] == row["sha256"]
                        ):
                            return False
                        visiting.add(aid)
                        for dep in db.execute(
                            "SELECT * FROM artifact_dependencies WHERE consumer_artifact_id=?",
                            (aid,),
                        ):
                            source = db.execute(
                                "SELECT sha256 FROM artifact_versions WHERE artifact_id=?",
                                (dep["input_artifact_id"],),
                            ).fetchone()
                            if (
                                not source
                                or source[0] != dep["input_sha256"]
                                or not valid_selected(dep["input_artifact_id"])
                            ):
                                visiting.remove(aid)
                                return False
                        visiting.remove(aid)
                        checked.add(aid)
                        return True

                    for row in db.execute(
                        "SELECT v.* FROM artifact_selections s JOIN artifact_versions v "
                        "ON v.artifact_id=s.artifact_id WHERE s.owner_scope='episode' AND "
                        "s.owner_id=?",
                        (eid,),
                    ):
                        if not valid_selected(row["artifact_id"]):
                            report["status"] = "BLOCKED"
                            report["blocker"] = (
                                "Selected artifact failed retained byte/SHA validation"
                            )
                            continue
                        artifacts[(row["kind"], row["slot_key"])] = dict(row)
                    if "production_stage_events" in tables:
                        for event in db.execute(
                            "SELECT * FROM production_stage_events WHERE episode_id=? ORDER BY "
                            "rowid",
                            (eid,),
                        ):
                            if event["stage"] in by_name:
                                by_name[event["stage"]].update(
                                    status=event["status"],
                                    evidence=json.loads(event["evidence_json"]),
                                )
                    for stage, kinds in _STAGE_ARTIFACTS.items():
                        refs = [
                            artifacts.get((kind, "main_v4")) or artifacts.get((kind, "main"))
                            for kind in kinds
                        ]
                        if all(ref is not None for ref in refs):
                            by_name[stage].update(
                                status="COMPLETE",
                                evidence={
                                    "artifact_ids": [ref["artifact_id"] for ref in refs if ref]
                                },
                            )
                        elif by_name[stage]["status"] == "COMPLETE":
                            by_name[stage]["status"] = "BLOCKED"
                    if "episode_music_bindings" in tables:
                        binding = db.execute(
                            "SELECT r.*,o.blind_id FROM episode_music_bindings b "
                            "JOIN music_requests r ON r.request_id=b.request_id "
                            "LEFT JOIN music_outputs o ON o.request_id=r.request_id WHERE "
                            "b.episode_id=?",
                            (eid,),
                        ).fetchone()
                        if binding:
                            state = binding["status"]
                            status = (
                                "COMPLETE"
                                if state == "succeeded" and ("audio_master", "main") in artifacts
                                else "READY"
                                if state == "succeeded"
                                else "AMBIGUOUS"
                                if state == "ambiguous"
                                else "FAILED"
                                if state in {"terminal_failure", "retryable_failure"}
                                else "PENDING_PROVIDER"
                                if binding["provider_request_id"]
                                else "READY"
                            )
                            by_name["MUSIC"].update(
                                status=status,
                                evidence={
                                    "request_id": binding["request_id"],
                                    "provider_request_id": binding["provider_request_id"],
                                    "request_status": state,
                                },
                            )
                            if binding["blind_id"]:
                                analysis = db.execute(
                                    "SELECT * FROM music_audio_analysis WHERE blind_id=? AND "
                                    "version=?",
                                    (binding["blind_id"], self.config.automation.analysis_version),
                                ).fetchone()
                                if analysis:
                                    qa = db.execute(
                                        "SELECT * FROM music_policy_evaluations WHERE subject_id=? "
                                        "AND policy_id='music_qa' ORDER BY rowid DESC LIMIT 1",
                                        (binding["blind_id"],),
                                    ).fetchone()
                                    by_name["AUDIO_ANALYSIS"].update(
                                        status=(
                                            "COMPLETE"
                                            if qa and qa["status"] == "pass"
                                            else "BLOCKED"
                                        ),
                                        evidence={
                                            "audio_sha256": analysis["audio_sha256"],
                                            "version": analysis["version"],
                                        },
                                    )
                    visual = artifacts.get(("episode_visual_plan", "main"))
                    if visual:
                        data = json.loads(
                            (self.config.data_root / visual["relative_path"]).read_text()
                        )
                        keys = [item["asset_key"] for item in data["required_assets"]]
                        missing = [key for key in keys if ("visual_asset", key) not in artifacts]
                        by_name["VISUAL_ASSETS"]["evidence"] = {"missing_assets": missing}
                        if not missing and ("episode_environment", "main") in artifacts:
                            by_name["VISUAL_ASSETS"]["status"] = "COMPLETE"
                        report["assets_would_be_generated"] = missing
                        for key in missing:
                            image_request = db.execute(
                                "SELECT r.*,p.source_artifact_id FROM generation_requests r "
                                "LEFT JOIN production_image_receipts p ON "
                                "p.request_id=r.request_id "
                                "WHERE r.episode_id=? AND r.kind='visual_asset' AND r.slot_key=? "
                                "ORDER BY r.rowid DESC LIMIT 1",
                                (eid, key),
                            ).fetchone()
                            if image_request and not image_request["source_artifact_id"]:
                                if image_request["status"] in {"remote_started", "ambiguous"}:
                                    by_name["VISUAL_ASSETS"]["status"] = "AMBIGUOUS"
                                elif image_request["status"] == "failed":
                                    by_name["VISUAL_ASSETS"]["status"] = "FAILED"
                        env_request = (
                            db.execute(
                                "SELECT status,request_id FROM environment_requests "
                                "WHERE episode_id=? "
                                "AND status NOT IN ('prepared','succeeded') ORDER BY rowid LIMIT "
                                "1",
                                (eid,),
                            ).fetchone()
                            if "production_image_receipts" in tables
                            else None
                        )
                        if env_request:
                            by_name["VISUAL_ASSETS"].update(
                                status="AMBIGUOUS"
                                if env_request["status"] in {"remote_started", "ambiguous"}
                                else "FAILED",
                                evidence={
                                    "request_id": env_request["request_id"],
                                    "missing_assets": missing,
                                },
                            )
                    final = artifacts.get(("final_render", "main_v4")) or artifacts.get(
                        ("final_render", "main")
                    )
                    if final:
                        report.update(
                            final_render_id=final["artifact_id"],
                            output_path=str(self.config.data_root / final["relative_path"]),
                            ready_local_preview=True,
                        )
                    historical_storyboard = artifacts.get(("timed_storyboard", "main"))
                    if historical_storyboard:
                        payload = json.loads(
                            (
                                self.config.data_root / historical_storyboard["relative_path"]
                            ).read_text(encoding="utf-8")
                        )
                        report["historical"] = payload.get("schema_version") == 1
                    if "publication_attempts" in tables:
                        publication = db.execute(
                            "SELECT * FROM publication_attempts WHERE episode_id=? ORDER BY "
                            "rowid DESC LIMIT 1",
                            (eid,),
                        ).fetchone()
                        if publication:
                            by_name["YOUTUBE"].update(
                                status="COMPLETE"
                                if publication["outcome"] == "succeeded"
                                else "AMBIGUOUS"
                                if publication["outcome"] in {"remote_started", "ambiguous"}
                                else "FAILED",
                                evidence={
                                    "attempt_id": publication["attempt_id"],
                                    "video_id": publication["youtube_video_id"],
                                },
                            )
                    if final and "episode_visual_plan" in {kind for kind, _ in artifacts}:
                        release = evaluate_release(self.config, str(episode["external_key"]))
                        allowed = (
                            release.public_release_allowed
                            if self.config.automation.publish_visibility == "public"
                            else release.private_test_upload_allowed
                        )
                        by_name["RELEASE"].update(
                            status="READY" if allowed else "BLOCKED", evidence=release.as_dict()
                        )
                        report["publication_would_be_attempted"] = (
                            self.config.automation.auto_publish
                            and allowed
                            and self.config.publication.youtube.enabled
                            and bool(self.config.expected_youtube_channel_id)
                        )
        first = next(
            (stage for stage in report["stages"] if stage["status"] != "COMPLETE"),
            report["stages"][-1],
        )
        report["current_stage"] = first["name"]
        if first["status"] == "NOT_STARTED":
            first["status"] = "READY"
        report["next_action"] = "resume " + first["name"].lower()
        report["provider_would_be_called"] = {
            "CREATIVE": self.config.creative_llm.provider,
            "MUSIC": self.config.music_generation.provider,
            "VISUAL_PLAN": self.config.creative_llm.provider,
            "VISUAL_ASSETS": "qwen_comfyui",
            "METADATA": self.config.creative_llm.provider,
            "YOUTUBE": "youtube" if report["publication_would_be_attempted"] else None,
        }.get(first["name"])
        if first["name"] == "MUSIC" and first["evidence"].get("request_status") == "succeeded":
            report["provider_would_be_called"] = None
            report["next_action"] = "reuse retained music and materialize audio master"
        if report["status"] == "READY" and first["status"] in {
            "PENDING_PROVIDER",
            "BLOCKED",
            "AMBIGUOUS",
            "FAILED",
            "NEEDS_REVIEW",
        }:
            report["status"] = first["status"]
        return report

    def produce_next(self, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            return self.plan()
        self.database = Database(self.config.database_path)
        self.database.migrate()
        execution = production_execution(self.database, "short-production:next")
        leases, lease = execution.__enter__()
        try:
            with closing(self.database.connect()) as db:
                active = db.execute(
                    "SELECT p.run_id,coalesce(p.episode_id,c.episode_id) AS episode_id "
                    "FROM production_next_runs p JOIN creative_runs c ON c.run_id=p.run_id "
                    "WHERE p.status='active' ORDER BY p.rowid LIMIT 1"
                ).fetchone()
            with self._creative() as creative:
                if active is None:
                    run_id = creative.reserve_next_run()
                    with closing(self.database.connect()) as db:
                        db.execute(
                            "INSERT INTO production_next_runs VALUES (?,NULL,'active')", (run_id,)
                        )
                        db.commit()
                    key = None
                else:
                    run_id = active["run_id"]
                    key = (
                        self.database.get_episode(active["episode_id"]).external_key
                        if active["episode_id"]
                        else None
                    )
                if key is None:
                    result = creative.generate_next(run_id=run_id)
                    key = str(result["episode_key"])
                episode = episode_by_key(self.database, key)
                with closing(self.database.connect()) as db:
                    db.execute(
                        "UPDATE production_next_runs SET episode_id=? WHERE run_id=?",
                        (episode.episode_id, run_id),
                    )
                    db.commit()
            result = self.produce(key, confirmed=True)
            if result["status"] == "COMPLETE":
                with closing(self.database.connect()) as db:
                    db.execute(
                        "UPDATE production_next_runs SET status='complete' WHERE run_id=?",
                        (run_id,),
                    )
                    db.commit()
            result["run_id"] = run_id
            return result
        except Exception as exc:
            result = self.plan()
            return {
                **result,
                "dry_run": False,
                "provider_calls": None,
                "status": "AMBIGUOUS"
                if getattr(exc, "category", None) == "ambiguous"
                else "FAILED",
                "current_stage": "CREATIVE",
                "blocker": (
                    f"CREATIVE failed ({type(exc).__name__}); inspect durable creative attempts"
                ),
            }
        finally:
            execution.__exit__(None, None, None)

    def _creative_inputs(
        self, episode: Episode
    ) -> tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]]:
        records = [
            self._selected(episode, kind) for kind in ("episode_spec", "lyrics", "music_spec")
        ]
        if any(record is None for record in records):
            with self._creative() as creative:
                creative.generate_next(episode_key=episode.external_key)
            records = [
                self._selected(episode, kind) for kind in ("episode_spec", "lyrics", "music_spec")
            ]
        if any(record is None for record in records):
            raise ValueError("creative stage did not retain selected artifacts")
        ids = tuple(record.identity.artifact_id for record in records if record)
        assert len(ids) == 3
        creative_ids = (ids[0], ids[1], ids[2])
        spec = EpisodeSpec.model_validate(self.store.read_json(ids[0]))
        lyrics = LyricsSpec.model_validate(self.store.read_json(ids[1]))
        music = MusicSpec.model_validate(self.store.read_json(ids[2]))
        creative_music_spec(episode, spec, lyrics, music, creative_ids)
        return spec, lyrics, music, creative_ids

    def _music(
        self,
        episode: Episode,
        inputs: tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]],
    ) -> tuple[str, str]:
        spec, lyrics, music, ids = inputs
        canonical = creative_music_spec(episode, spec, lyrics, music, ids)
        adapter = self._json(
            episode,
            "creative_music_input",
            {
                "adapter_version": "creative_music_v1",
                "episode_id": episode.episode_id,
                "creative_artifact_ids": ids,
                "canonical_spec": canonical.model_dump(mode="json"),
            },
            ids,
        )
        benchmark = MusicBenchmark(self.database, self.config.data_root / "music-benchmark")
        with closing(self.database.connect()) as db:
            binding = db.execute(
                "SELECT * FROM episode_music_bindings WHERE episode_id=?", (episode.episode_id,)
            ).fetchone()
        if binding:
            if binding["adapter_artifact_id"] != adapter.identity.artifact_id:
                raise ProductionStop(
                    "BLOCKED", "Selected creative inputs differ from the immutable music binding"
                )
            request = benchmark.request(binding["request_id"])
            if request["status"] != "succeeded" and (
                binding["provider_endpoint"] != self.config.music_generation.base_url
            ):
                raise ProductionStop(
                    "BLOCKED", "Music endpoint differs from the durable task binding"
                )
            if request["status"] == "succeeded":
                result = benchmark.reconcile(request["request_id"])
            else:
                with closing(self.database.connect()) as db:
                    receipt = db.execute(
                        "SELECT 1 FROM music_receipts WHERE request_id=?", (request["request_id"],)
                    ).fetchone()
                if receipt:
                    result = benchmark.reconcile(request["request_id"])
                elif request["status"] == "ambiguous":
                    raise ProductionStop(
                        "AMBIGUOUS",
                        "Music interaction is uncertain; use music-benchmark "
                        "provider-resume/reconciliation",
                        {"request_id": request["request_id"]},
                    )
                elif request["status"] in {"retryable_failure", "terminal_failure"}:
                    raise ProductionStop(
                        "FAILED",
                        "Music request failed; explicit operator recovery is required",
                        {"request_id": request["request_id"]},
                    )
                else:
                    provider = self.music_provider or AceStepLocalProvider(
                        self.config.music_generation
                    )
                    if request["remote_started_at"]:
                        if not request["provider_request_id"]:
                            raise ProductionStop(
                                "AMBIGUOUS",
                                "Music remote start lacks task identity",
                                {"request_id": request["request_id"]},
                            )
                        result = benchmark.provider_resume(request["request_id"], provider)
                    else:
                        item = plan_music(canonical.brief, canonical.lyrics, [provider], attempt=1)[
                            0
                        ]
                        if item.input_fingerprint != request["input_fingerprint"]:
                            raise ProductionStop(
                                "BLOCKED",
                                "Music provider configuration differs from prepared request",
                            )
                        result = benchmark.run(item, provider)
        else:
            if self.config.music_generation.provider != "ace_step_local":
                raise ProductionStop(
                    "BLOCKED", "New production music requires configured ace_step_local"
                )
            provider = self.music_provider or AceStepLocalProvider(self.config.music_generation)
            item = plan_music(canonical.brief, canonical.lyrics, [provider], attempt=1)[0]
            request = benchmark.prepare(item)
            with closing(self.database.connect()) as db:
                db.execute(
                    "INSERT INTO episode_music_bindings VALUES (?,?,?,?)",
                    (
                        episode.episode_id,
                        request["request_id"],
                        adapter.identity.artifact_id,
                        self.config.music_generation.base_url,
                    ),
                )
                db.commit()
            # Re-enter via the authoritative binding, also handling prepare-before-bind crashes.
            return self._music(episode, inputs)
        if result.get("status") != "succeeded":
            status = (
                "PENDING_PROVIDER"
                if result.get("status") == "pending_provider"
                or result.get("action") == "existing_interaction_pending"
                else "AMBIGUOUS"
                if result.get("status") in {"ambiguous", "remote_started"}
                else "FAILED"
            )
            raise ProductionStop(
                status, "Music has not completed; no new candidate was generated", result
            )
        request_id, blind_id = str(result["request_id"]), str(result["blind_id"])
        request = benchmark.request(request_id)
        with closing(self.database.connect()) as db:
            output = db.execute(
                "SELECT * FROM music_outputs WHERE request_id=?", (request_id,)
            ).fetchone()
        assert output is not None
        path = self.config.data_root / "music-benchmark" / output["relative_path"]
        persist_file(
            self.store,
            episode,
            "audio_master",
            "main",
            path,
            (adapter.identity.artifact_id,),
            Provenance(
                source_kind="provider",
                acquired_at=datetime.now(UTC),
                provider=request["provider"],
                model=request["model"],
                request_id=request["provider_request_id"],
                local_request_id=request_id,
                prompt_version="creative_music_v1",
                input_artifact_ids=(adapter.identity.artifact_id,),
            ),
        )
        return request_id, blind_id

    def _analysis(self, episode: Episode, blind_id: str) -> dict[str, Any]:
        config = self.config.automation
        benchmark = MusicBenchmark(self.database, self.config.data_root / "music-benchmark")
        report, reused = benchmark.analyze_audio(
            blind_id,
            config.analysis_version,
            AnalysisConfig(
                asr_model=config.asr_model,
                device=config.analysis_device,
                allow_model_download=config.allow_model_download,
            ),
        )
        with closing(self.database.connect()) as db:
            qa_row = db.execute(
                "SELECT * FROM music_policy_evaluations WHERE subject_type='audio' AND "
                "subject_id=? AND policy_id='music_qa' ORDER BY rowid DESC LIMIT 1",
                (blind_id,),
            ).fetchone()
            timing_row = db.execute(
                "SELECT * FROM music_policy_evaluations WHERE subject_type='timing' AND "
                "subject_id=? AND policy_id='music_timing' ORDER BY rowid DESC LIMIT 1",
                (f"{blind_id}:{config.analysis_version}",),
            ).fetchone()
        analysis_hash = sha256(report.model_dump_json().encode()).hexdigest()
        timing_hash = sha256(
            json.dumps(
                report.timing.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        qa_cached = bool(
            qa_row
            and qa_row["evaluator_type"] == "machine"
            and qa_row["subject_sha256"] == report.audio_sha256
            and json.loads(qa_row["evidence_json"]).get("analysis_sha256") == analysis_hash
        )
        timing_cached = bool(
            timing_row
            and timing_row["evaluator_type"] == "machine"
            and timing_row["subject_sha256"] == report.audio_sha256
            and json.loads(timing_row["evidence_json"]).get("analysis_sha256") == timing_hash
        )
        qa = (
            dict(qa_row)
            if qa_cached and qa_row
            else benchmark.evaluate_analysis_qa(blind_id, config.analysis_version)
        )
        timing = (
            dict(timing_row)
            if timing_cached and timing_row
            else benchmark.evaluate_timing(blind_id, config.analysis_version)
        )
        evidence = {
            "analysis_version": report.version,
            "audio_sha256": report.audio_sha256,
            "qa": qa,
            "timing": timing,
            "reused": reused,
            "analysis_warnings": report.warnings,
            "transcription_failure": report.transcription.failure_reason,
            "alignment_failure": report.alignment.failure_reason,
            "rhythm_failure": report.rhythm.failure_reason,
        }
        if qa["status"] != "pass" or timing["status"] != "pass":
            raise ProductionStop(
                "BLOCKED", "Objective audio/lyric/timing QA did not pass", evidence
            )
        return evidence

    def _visual(
        self,
        episode: Episode,
        inputs: tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]],
    ) -> tuple[EpisodeVisualPlan, str]:
        spec, lyrics, music, ids = inputs
        selected = self._selected(episode, "episode_visual_plan")

        def validate(plan: EpisodeVisualPlan) -> None:
            validate_visual_plan(plan, episode, spec, lyrics, ids)

        if selected:
            plan = EpisodeVisualPlan.model_validate(
                self.store.read_json(selected.identity.artifact_id)
            )
            validate(plan)
            return plan, selected.identity.artifact_id
        facts = {
            **pinned_facts(self.catalog),
            "episode": episode.model_dump(mode="json"),
            "episode_spec": spec.model_dump(mode="json"),
            "lyrics": lyrics.model_dump(mode="json"),
            "music_spec": music.model_dump(mode="json"),
            "creative_artifact_ids": ids,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Plan preschool visuals for the exact selected episode and every exact "
                    "lyric line. "
                    "Keep Tovi as the sole character. Invent no lyrics, timing, curriculum claims, "
                    "rights or approval facts. Stable asset keys are data. Use concrete "
                    "entities supported "
                    "by the lyrics/story. Include a deterministic color_swatch for Colors lessons. "
                    "Provide all four environment roles in order: meadow_wide, lesson_garden, "
                    "play_path, "
                    "celebration_meadow. Reuse the shared textual preschool-world-v1 meadow "
                    "when compatible. "
                    "Qwen supports no reference images. Every required asset must appear in a "
                    "scene. "
                    "No artist/franchise imitation, text, logos, unsafe content or new characters. "
                    "Return the EpisodeVisualPlan schema; copy all three supplied creative "
                    "artifact IDs."
                ),
            },
            {"role": "user", "content": json.dumps(facts, sort_keys=True)},
        ]
        with self._creative() as creative:
            draft = creative.provider.generate(
                EpisodeVisualPlan,
                messages,
                context=GenerationContext(
                    "episode_visual_plan",
                    "episode_visual_plan_v1",
                    episode_id=episode.episode_id,
                    assert_owner=lambda: self.leases.assert_owner(self.lease),
                ),
                validate=validate,
            )
        validate(draft.output)
        record = self._json(
            episode,
            "episode_visual_plan",
            draft.output.model_dump(mode="json"),
            ids,
            Provenance(
                source_kind="provider",
                acquired_at=draft.generated_at,
                provider=draft.provider,
                model=draft.model,
                request_id=draft.request_id,
                local_request_id=draft.local_request_id,
                prompt_version=draft.prompt_version,
                input_artifact_ids=ids,
            ),
        )
        return draft.output, record.identity.artifact_id

    def _environment(self, episode: Episode, visual: EpisodeVisualPlan, plan_id: str) -> str:
        selected = self._selected(episode, "episode_environment")
        if selected:
            payload = self.store.read_json(selected.identity.artifact_id)
            if not isinstance(payload, dict) or payload["visual_plan_artifact_id"] != plan_id:
                raise ValueError("episode environment binding differs from current visual plan")
            aid = str(payload["environment_set_artifact_id"])
        else:
            with closing(self.database.connect()) as db:
                unresolved = db.execute(
                    "SELECT status,request_id FROM environment_requests "
                    "WHERE episode_id=? "
                    "AND status NOT IN ('prepared','succeeded') ORDER BY rowid LIMIT 1",
                    (episode.episode_id,),
                ).fetchone()
            if unresolved:
                raise ProductionStop(
                    "AMBIGUOUS"
                    if unresolved["status"] in {"remote_started", "ambiguous"}
                    else "FAILED",
                    "Environment interaction requires explicit recovery; no automatic resend",
                    {"request_id": unresolved["request_id"]},
                )
            shared = None
            if visual.reuse_shared_environment:
                try:
                    shared = selected_set(self.config)
                except ValueError as exc:
                    if "no selected approved environment set" not in str(exc):
                        raise
            if shared is not None and shared[0].theme == visual.world_contract:
                aid = shared[1]
            else:
                if self.config.environment_generation.provider != "qwen_comfyui":
                    raise ProductionStop(
                        "BLOCKED", "Production environments require configured qwen_comfyui"
                    )
                try:
                    result = generate_set(
                        self.config,
                        confirmed=True,
                        episode_id=episode.episode_id,
                        role_briefs={env.role: env.description for env in visual.environments},
                    )
                except ProviderFailure as exc:
                    raise ProductionStop(
                        "AMBIGUOUS" if exc.outcome == "ambiguous" else "FAILED",
                        "Qwen environment generation stopped; inspect the durable request",
                    ) from exc
                aid = str(result["manifest_artifact_id"])
            environment = EnvironmentSet.model_validate(self.store.read_json(aid))
            for plate in environment.plates:
                if plate.source_artifact_id:
                    self.store.admit_preview(plate.source_artifact_id)
                self.store.admit_preview(plate.artifact_id)
            self.store.admit_preview(aid)
            self._json(
                episode,
                "episode_environment",
                {
                    "visual_plan_artifact_id": plan_id,
                    "environment_set_artifact_id": aid,
                    "ownership": "shared_selected" if shared else "episode",
                },
                (plan_id, aid),
            )
        environment = EnvironmentSet.model_validate(self.store.read_json(aid))
        if environment.theme != visual.world_contract:
            raise ValueError("environment world differs from admitted visual plan")
        for plate in environment.plates:
            record = self.store.get(plate.artifact_id)
            if record.sha256 != plate.sha256 or not self.store.inspect(plate.artifact_id).valid:
                raise ValueError("environment plate failed immutable validation")
        return aid

    def _storyboard(
        self,
        episode: Episode,
        inputs: tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]],
        request_id: str,
        visual: EpisodeVisualPlan,
        plan_id: str,
        assets: dict[str, str],
        environment_id: str,
    ) -> None:
        spec, lyrics, music, ids = inputs
        source = accept_episode_source(
            self.config,
            episode,
            request_id,
            creative_music_spec(episode, spec, lyrics, music, ids),
            self.config.automation.analysis_version,
        )
        master = self._selected(episode, "audio_master")
        assert master is not None
        adapter = self._selected(episode, "creative_music_input")
        assert adapter is not None
        with closing(self.database.connect()) as db:
            binding = db.execute(
                "SELECT provider_endpoint FROM episode_music_bindings WHERE episode_id=?",
                (episode.episode_id,),
            ).fetchone()
        self._json(
            episode,
            "production_handoff",
            {
                **source.manifest,
                "creative_artifact_ids": ids,
                "adapter_artifact_id": adapter.identity.artifact_id,
                "provider_endpoint": binding[0] if binding else None,
            },
            (adapter.identity.artifact_id, master.identity.artifact_id),
        )
        alignment = extract_alignment(source, master.identity.artifact_id, generic=True)
        beats = extract_beats(source, master.identity.artifact_id)
        alignment_record = self._json(
            episode,
            "audio_alignment",
            alignment.model_dump(mode="json"),
            (master.identity.artifact_id,),
        )
        beat_record = self._json(
            episode, "beat_analysis", beats.model_dump(mode="json"), (master.identity.artifact_id,)
        )
        template = StoryboardTemplate(
            schema_version=2,
            template_id="episode_visual_plan_v1",
            concept_id=episode.concept_id,
            objective_id=episode.objective_id,
            intro=SceneIntent(visual_focus="Tovi enters the episode world", tovi_action="enter"),
            outro=SceneIntent(visual_focus="Tovi celebrates and settles", tovi_action="celebrate"),
            lyrics=tuple(
                LyricIntent(
                    lyric_text=scene.lyric_text,
                    visual_focus=scene.visual_focus,
                    tovi_action=scene.tovi_action,
                    required_props=scene.required_assets,
                    lesson_target=episode.concept_id,
                )
                for scene in visual.scenes
            ),
        )
        base = build_storyboard(
            episode,
            alignment,
            beats,
            alignment_record.identity.artifact_id,
            beat_record.identity.artifact_id,
            template,
            self.store.get(plan_id).sha256,
        )
        storyboard = TimedStoryboardV2(
            **base.model_dump(),
            lyrics_artifact_id=ids[1],
            visual_plan_artifact_id=plan_id,
            asset_artifact_ids=assets,
            environment_set_artifact_id=environment_id,
        )
        env_binding = self._selected(episode, "episode_environment")
        assert env_binding is not None
        self._json(
            episode,
            "timed_storyboard",
            storyboard.model_dump(mode="json"),
            (
                master.identity.artifact_id,
                alignment_record.identity.artifact_id,
                beat_record.identity.artifact_id,
                ids[1],
                plan_id,
                *assets.values(),
                env_binding.identity.artifact_id,
                environment_id,
            ),
        )

    def _render(self, episode: Episode) -> dict[str, Any]:
        inputs = load_inputs(self.config, episode.external_key, local_preview=True)
        final = self._selected(episode, "final_render", "main_v4")
        manifest = self._selected(episode, "render_manifest", "main_v4")
        qa = self._selected(episode, "media_qa", "main_v4")
        if final and manifest and qa:
            parsed = RenderManifest.model_validate(
                self.store.read_json(manifest.identity.artifact_id)
            )
            validate_manifest(self.store, parsed, inputs.storyboard)
            with closing(self.database.connect()) as db:
                dep = db.execute(
                    "SELECT input_sha256 FROM artifact_dependencies WHERE "
                    "consumer_artifact_id=? AND input_artifact_id=?",
                    (final.identity.artifact_id, manifest.identity.artifact_id),
                ).fetchone()
            if dep is None or dep[0] != manifest.sha256:
                raise ValueError("existing final render does not bind its current manifest")
            return {
                "final_render_id": final.identity.artifact_id,
                "output_path": str(self.store.path_for(final.identity.artifact_id)),
                "reused": True,
            }
        return ProductionRenderer(self.config, local_preview=True).render(
            episode.external_key, visual_story=True
        )

    def _media_qa(self, episode: Episode) -> dict[str, Any]:
        final = self._selected(episode, "final_render", "main_v4")
        qa_record = self._selected(episode, "media_qa", "main_v4")
        if not final or not qa_record:
            raise ValueError("renderer did not persist final render/media QA")
        qa = self.store.read_json(qa_record.identity.artifact_id)
        if (
            not isinstance(qa, dict)
            or qa.get("passed") is not True
            or qa.get("render_artifact_id") != final.identity.artifact_id
            or qa.get("render_sha256") != final.sha256
        ):
            raise ProductionStop("BLOCKED", "Media QA failed or differs from retained final MP4")
        return {"media_qa_artifact_id": qa_record.identity.artifact_id, "passed": True}

    def _metadata(self, episode: Episode) -> dict[str, Any]:
        with self._creative() as creative:
            creative.store.local_preview = True
            return MetadataWriter(creative).generate(episode.external_key)

    def _publication(self, episode: Episode) -> dict[str, Any]:
        release = evaluate_release(self.config, episode.external_key)
        if not self.config.automation.auto_publish:
            status = (
                "NEEDS_REVIEW"
                if self.config.automation.require_human_review
                and not release.public_release_allowed
                else "COMPLETE"
            )
            return {"status": status, "release": release.as_dict(), "publication": "disabled"}
        allowed = (
            release.public_release_allowed
            if self.config.automation.publish_visibility == "public"
            else release.private_test_upload_allowed
        )
        if not allowed:
            raise ProductionStop(
                "NEEDS_REVIEW" if self.config.automation.require_human_review else "BLOCKED",
                "Configured release gates block automatic publication",
                {"release": release.as_dict()},
            )
        if not self.config.publication.youtube.enabled:
            raise ProductionStop("BLOCKED", "YouTube is disabled in publication configuration")
        service = PublicationService(self.config)
        history = service.history(episode.episode_id)
        if any(
            row["outcome"] in {"remote_started", "ambiguous"}
            or (
                row.get("public_promotion")
                and row["public_promotion"]["outcome"] in {"remote_started", "ambiguous"}
            )
            for row in history
        ):
            raise ProductionStop(
                "AMBIGUOUS", "Previous YouTube interaction requires reconciliation"
            )
        if history and history[0]["outcome"] == "terminal_failure":
            raise ProductionStop(
                "FAILED", "Previous YouTube upload failed; operator recovery required"
            )
        try:
            uploaded = service.upload_private(episode.external_key)
        except UploadAmbiguous as exc:
            raise ProductionStop("AMBIGUOUS", "YouTube upload needs manual reconciliation") from exc
        if self.config.automation.publish_visibility == "public":
            try:
                promoted = service.publish_public(episode.external_key)
            except UploadAmbiguous as exc:
                raise ProductionStop(
                    "AMBIGUOUS", "YouTube visibility needs manual reconciliation"
                ) from exc
            except ValueError as exc:
                raise ProductionStop(
                    "BLOCKED",
                    "Public visibility requires ready processing and current release evidence",
                ) from exc
            if promoted.get("outcome") != "succeeded":
                raise ProductionStop(
                    "BLOCKED", "YouTube processing/promotion not complete", promoted
                )
        return {"status": "COMPLETE", "publication": uploaded}

    def produce(self, episode_key: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            return self.plan(episode_key)
        self.database = Database(self.config.database_path)
        self.database.migrate()
        episode = episode_by_key(self.database, episode_key)
        # Published episodes are historical output. Never regenerate or change any selections.
        with closing(self.database.connect()) as db:
            published = db.execute(
                "SELECT * FROM publication_attempts WHERE episode_id=? AND outcome='succeeded' "
                "ORDER BY rowid DESC LIMIT 1",
                (episode.episode_id,),
            ).fetchone()
            handoff = db.execute(
                "SELECT v.provenance_json FROM artifact_selections s JOIN artifact_versions v "
                "ON v.artifact_id=s.artifact_id WHERE s.owner_id=? AND v.kind='production_handoff'",
                (episode.episode_id,),
            ).fetchone()
        # Schema identity lives in retained bytes, never inferred from the episode's color.
        with closing(self.database.connect()) as db:
            storyboard_row = db.execute(
                "SELECT v.relative_path FROM artifact_selections s JOIN artifact_versions v "
                "ON v.artifact_id=s.artifact_id WHERE s.owner_id=? AND v.kind='timed_storyboard'",
                (episode.episode_id,),
            ).fetchone()
        v2 = bool(
            storyboard_row
            and json.loads(
                (self.config.data_root / storyboard_row[0]).read_text(encoding="utf-8")
            ).get("schema_version")
            == 2
        )
        if (published and not v2) or (
            handoff and json.loads(handoff[0]).get("model") == "production_storyboard_v1"
        ):
            result = self.plan(episode_key)
            return {
                **result,
                "dry_run": False,
                "provider_calls": None,
                "status": "COMPLETE",
                "historical": True,
                "next_action": "reuse immutable historical production",
            }
        self.catalog = load_brand(self.config.brand_root)
        validate_pins(episode, self.catalog)
        self.working = self.config.data_root / ".short-production"
        self.working.mkdir(parents=True, exist_ok=True)
        self.store = AssetStore(
            self.config.data_root,
            self.database,
            local_preview=True,
            generated_source_roots=[self.working, self.config.data_root / "music-benchmark"]
            if (self.config.data_root / "music-benchmark").is_dir()
            else [self.working],
        )
        execution = production_execution(self.database, f"short-production:{episode_key}")
        self.leases, self.lease = execution.__enter__()
        stage = "CREATIVE"
        try:

            def run(name: str, call: Callable[[], Any]) -> Any:
                nonlocal stage
                stage = name
                self.lease = self.leases.renew(self.lease, duration_seconds=14400)
                self._event(episode, name, "RUNNING", {})
                result = call()
                self._event(
                    episode,
                    name,
                    str(result.get("status", "COMPLETE"))
                    if name == "YOUTUBE" and isinstance(result, dict)
                    else "COMPLETE",
                    result if isinstance(result, dict) else {},
                )
                return result

            inputs = run("CREATIVE", lambda: self._creative_inputs(episode))
            # MusicBenchmark creates this directory before returned bytes are ingested.
            music_root = self.config.data_root / "music-benchmark"
            music_root.mkdir(exist_ok=True)
            self.store.generated_source_roots += (music_root.resolve(),)
            request_id, blind_id = run("MUSIC", lambda: self._music(episode, inputs))
            run("AUDIO_ANALYSIS", lambda: self._analysis(episode, blind_id))
            visual, plan_id = run("VISUAL_PLAN", lambda: self._visual(episode, inputs))

            def visuals() -> tuple[dict[str, str], str]:
                if self.config.lesson_object_generation.provider != "qwen_comfyui":
                    raise ProductionStop(
                        "BLOCKED", "Production illustrations require configured qwen_comfyui"
                    )
                assets = generate_assets(
                    self.config,
                    self.store,
                    episode,
                    visual,
                    plan_id,
                    self.working,
                    self.catalog.creative_bible.visual_direction,
                    self.image_provider,
                )
                return assets, self._environment(episode, visual, plan_id)

            assets, environment_id = run("VISUAL_ASSETS", visuals)
            run(
                "STORYBOARD",
                lambda: self._storyboard(
                    episode, inputs, request_id, visual, plan_id, assets, environment_id
                ),
            )
            rendered = run("RENDER", lambda: self._render(episode))
            run("MEDIA_QA", lambda: self._media_qa(episode))
            run("METADATA", lambda: self._metadata(episode))
            run("RELEASE", lambda: evaluate_release(self.config, episode_key).as_dict())
            publication = run("YOUTUBE", lambda: self._publication(episode))
            result = self.plan(episode_key)
            return {
                **result,
                **rendered,
                **publication,
                "dry_run": False,
                "provider_calls": None,
                "ready_local_preview": True,
            }
        except (ProductionStop, ImageStageBlocked) as exc:
            evidence = {
                "reason": str(exc),
                **(exc.evidence if isinstance(exc, ProductionStop) else {}),
            }
            self._event(episode, stage, exc.status, evidence)
            return {
                **self.plan(episode_key),
                "dry_run": False,
                "provider_calls": None,
                "status": exc.status,
                "current_stage": stage,
                "blocker": evidence,
            }
        except Exception as exc:
            # Never persist arbitrary downstream exception strings (credentials/signed URLs).
            evidence = {
                "reason": f"{stage} failed ({type(exc).__name__}); inspect configured local runtime"
            }
            self._event(episode, stage, "FAILED", evidence)
            return {
                **self.plan(episode_key),
                "dry_run": False,
                "provider_calls": None,
                "status": "FAILED",
                "current_stage": stage,
                "blocker": evidence,
            }
        finally:
            execution.__exit__(None, None, None)
