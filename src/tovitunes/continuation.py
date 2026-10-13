"""MPT-derived continuation planning; jobs, progress and stage events are never inputs."""

import json
import sqlite3
from contextlib import closing
from typing import Any

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig
from tovitunes.creative.service import episode_by_key
from tovitunes.creative.visual_recovery import recovery_state
from tovitunes.domain.creative_drafts import parse_episode_spec, parse_lyrics, parse_music_spec
from tovitunes.domain.production_v3 import AudioClock, TimedStoryboardV3
from tovitunes.domain.storyboard import parse_storyboard
from tovitunes.domain.visual_draft import (
    EpisodeVisualPlanV2,
    parse_visual_plan,
    validate_compiled_visual,
)
from tovitunes.domain.visual_plan import validate_visual_plan
from tovitunes.persistence.db import Database
from tovitunes.pipeline.duration_source import admit_duration_source, uses_duration_path
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.pipeline.production import accept_episode_source, production_analysis_version
from tovitunes.pipeline.targets import STAGES, ProductionTarget, stages_for
from tovitunes.publication.models import PublicationState, publication_state
from tovitunes.publication.preflight import evaluate_release
from tovitunes.render.admission_v3 import load_v3_evidence
from tovitunes.render.models import parse_manifest
from tovitunes.render.production import validate_manifest
from tovitunes.render.visual_evidence import validate_visual_receipt

_STAGE_ARTIFACTS = {
    "CREATIVE": ("episode_spec", "lyrics", "music_spec"),
    "MUSIC": ("audio_master",),
    "VISUAL_PLAN": ("episode_visual_plan",),
    "STORYBOARD": ("timed_storyboard",),
    "RENDER": ("final_render", "render_manifest"),
    "MEDIA_QA": ("media_qa",),
    "METADATA": ("publication_metadata",),
}


def plan_continuation(
    config: RuntimeConfig,
    episode_key: str | None = None,
    *,
    target: ProductionTarget = ProductionTarget.PUBLISH,
) -> dict[str, Any]:
    """Read-only projection: artifacts and ledgers prove completion; diagnostics are excluded."""
    report: dict[str, Any] = {
        "episode_key": episode_key,
        "status": "READY",
        "dry_run": True,
        "provider_calls": 0,
        "auto_publish": config.automation.auto_publish,
        "publish_visibility": config.automation.publish_visibility,
        "require_human_review": config.automation.require_human_review,
        "publication_would_be_attempted": False,
        "stages": [{"name": stage, "status": "NOT_STARTED", "evidence": {}} for stage in STAGES],
    }
    by_name = {stage["name"]: stage for stage in report["stages"]}
    if not config.database_path.is_file():
        if episode_key:
            raise KeyError("episode database unavailable")
    else:
        with closing(
            sqlite3.connect(config.database_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.row_factory = sqlite3.Row
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            episode = (
                db.execute("SELECT * FROM episodes WHERE external_key=?", (episode_key,)).fetchone()
                if episode_key
                else None
            )
            if episode_key and episode is None:
                raise KeyError(episode_key)
            if episode is not None:
                eid = report["episode_id"] = episode["episode_id"]
                report["historical"] = (
                    "historical_production_episodes" in tables
                    and db.execute(
                        "SELECT 1 FROM historical_production_episodes WHERE episode_id=?", (eid,)
                    ).fetchone()
                    is not None
                )
                artifacts = {}
                store = AssetStore(
                    config.data_root,
                    Database(config.database_path),
                    initialize=False,
                    local_preview=True,
                )
                selected_rows = db.execute(
                    "SELECT v.* FROM artifact_selections s JOIN artifact_versions v "
                    "ON v.artifact_id=s.artifact_id WHERE s.owner_scope='episode' AND "
                    "s.owner_id=?",
                    (eid,),
                ).fetchall()
                eligibility = store.eligibility_batch([row["artifact_id"] for row in selected_rows])
                for row in selected_rows:
                    if not eligibility[row["artifact_id"]][0]:
                        report["status"] = "BLOCKED"
                        report["blocker"] = "Selected artifact failed retained byte/SHA validation"
                        continue
                    artifacts[(row["kind"], row["slot_key"])] = dict(row)
                for stage, kinds in _STAGE_ARTIFACTS.items():
                    refs = [
                        artifacts.get((kind, "main_v4")) or artifacts.get((kind, "main"))
                        for kind in kinds
                    ]
                    if all(ref is not None for ref in refs):
                        by_name[stage].update(
                            status="COMPLETE",
                            evidence={"artifact_ids": [ref["artifact_id"] for ref in refs if ref]},
                        )
                    elif by_name[stage]["status"] == "COMPLETE":
                        by_name[stage]["status"] = "BLOCKED"
                expected_music = None
                if by_name["CREATIVE"]["status"] == "COMPLETE":
                    ids = by_name["CREATIVE"]["evidence"]["artifact_ids"]
                    try:
                        expected_music = creative_music_spec(
                            episode_by_key(store.database, str(episode["external_key"])),
                            parse_episode_spec(store.read_json(ids[0])),
                            parse_lyrics(store.read_json(ids[1])),
                            parse_music_spec(store.read_json(ids[2])),
                            tuple(ids),
                        )
                    except (KeyError, ValueError, OSError):
                        report["status"] = "BLOCKED"
                        report["blocker"] = "Selected creative facts differ from episode identity"
                        by_name["CREATIVE"]["status"] = "BLOCKED"
                binding = None
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
                            else "PENDING_PROVIDER"
                            if state == "ambiguous" and binding["provider_request_id"]
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
                        if binding["blind_id"] and expected_music is not None:
                            version = production_analysis_version(db, binding["blind_id"], config)
                            analysis = db.execute(
                                "SELECT * FROM music_audio_analysis WHERE blind_id=? AND version=?",
                                (binding["blind_id"], version),
                            ).fetchone()
                            if analysis:
                                evaluations = db.execute(
                                    "SELECT subject_type FROM music_policy_evaluations "
                                    "WHERE (subject_type='audio' AND subject_id=? "
                                    "AND policy_id='music_qa' AND policy_version=2) OR "
                                    "(subject_type='timing' AND subject_id=? "
                                    "AND policy_id='music_timing' AND policy_version=1)",
                                    (binding["blind_id"], f"{binding['blind_id']}:{version}"),
                                ).fetchall()
                                stage = by_name["AUDIO_ANALYSIS"]
                                stage["evidence"] = {
                                    "audio_sha256": analysis["audio_sha256"],
                                    "version": version,
                                }
                                if {row["subject_type"] for row in evaluations} != {
                                    "audio",
                                    "timing",
                                }:
                                    stage["status"] = "READY"
                                else:
                                    try:
                                        accept_episode_source(
                                            config,
                                            episode_by_key(
                                                store.database, str(episode["external_key"])
                                            ),
                                            binding["request_id"],
                                            expected_music,
                                            version,
                                            connection=db,
                                        )
                                        stage["status"] = "COMPLETE"
                                    except (ValueError, OSError, KeyError) as exc:
                                        stage["status"] = "BLOCKED"
                                        stage["evidence"]["reason"] = str(exc)
                visual = artifacts.get(("episode_visual_plan", "main"))
                duration_path = uses_duration_path(db, eid)
                if visual:
                    try:
                        current_visual = parse_visual_plan(store.read_json(visual["artifact_id"]))
                        duration_path = isinstance(current_visual, EpisodeVisualPlanV2)
                        if expected_music is None:
                            raise ValueError("visual plan lacks retained creative inputs")
                        current_episode = episode_by_key(
                            store.database, str(episode["external_key"])
                        )
                        selected_ids = by_name["CREATIVE"]["evidence"]["artifact_ids"]
                        creative_ids = (selected_ids[0], selected_ids[1], selected_ids[2])
                        if isinstance(current_visual, EpisodeVisualPlanV2):
                            validate_compiled_visual(current_visual, current_episode, creative_ids)
                            validate_visual_receipt(store, current_visual)
                        else:
                            validate_visual_plan(
                                current_visual,
                                current_episode,
                                parse_episode_spec(store.read_json(creative_ids[0])),
                                parse_lyrics(store.read_json(creative_ids[1])),
                                creative_ids,
                            )
                    except (ValueError, OSError, KeyError) as exc:
                        by_name["VISUAL_PLAN"].update(
                            status="BLOCKED", evidence={"reason": str(exc)}
                        )
                elif duration_path:
                    try:
                        recovery = recovery_state(db, eid)
                        if recovery:
                            by_name["VISUAL_PLAN"].update(
                                status="READY" if recovery["authorized"] else "BLOCKED",
                                evidence=recovery,
                            )
                    except (ValueError, OSError, KeyError) as exc:
                        by_name["VISUAL_PLAN"].update(
                            status="BLOCKED", evidence={"reason": str(exc)}
                        )
                    pending = db.execute(
                        "SELECT * FROM generation_requests WHERE episode_id=? "
                        "AND prompt_version='visual_creative_v2' ORDER BY rowid DESC LIMIT 1",
                        (eid,),
                    ).fetchone()
                    if pending and pending["status"] in {"remote_started", "ambiguous"}:
                        by_name["VISUAL_PLAN"].update(
                            status="AMBIGUOUS",
                            evidence={
                                "request_id": pending["request_id"],
                                "provider": pending["provider"],
                                "model": pending["model"],
                                "error_kind": "ambiguous",
                            },
                        )
                if (
                    duration_path
                    and expected_music is not None
                    and binding
                    and by_name["MUSIC"]["status"] == "COMPLETE"
                ):
                    clock_row = artifacts.get(("audio_clock", "main"))
                    stage = by_name["AUDIO_ANALYSIS"]
                    stage.update(
                        status="READY", evidence={"mode": "duration", "asr_required": False}
                    )
                    if clock_row:
                        try:
                            master = artifacts[("audio_master", "main")]
                            measured_clock = admit_duration_source(
                                config,
                                episode_by_key(store.database, str(episode["external_key"])),
                                binding["request_id"],
                                expected_music,
                                master["artifact_id"],
                                connection=db,
                            )
                            if (
                                AudioClock.model_validate(store.read_json(clock_row["artifact_id"]))
                                != measured_clock
                            ):
                                raise ValueError(
                                    "audio clock differs from original source evidence"
                                )
                            stage.update(
                                status="COMPLETE",
                                evidence={
                                    "audio_clock_artifact_id": clock_row["artifact_id"],
                                    "asr_required": False,
                                },
                            )
                        except (ValueError, OSError, KeyError) as exc:
                            stage.update(status="BLOCKED", evidence={"reason": str(exc)})
                if visual and by_name["VISUAL_PLAN"]["status"] == "COMPLETE":
                    data = json.loads(
                        (config.data_root / visual["relative_path"]).read_text(encoding="utf-8")
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
                            if image_request["status"] in {"failed", "remote_started", "ambiguous"}:
                                by_name["VISUAL_ASSETS"]["evidence"].update(
                                    request_id=image_request["request_id"],
                                    subsystem="illustration",
                                    error_kind=image_request["error_kind"]
                                    or "retained_image_failure",
                                    retry_state="ambiguous"
                                    if image_request["status"] != "failed"
                                    else "requires_configuration"
                                    if image_request["error_kind"]
                                    in {"workflow_rejected", "runtime_incompatible"}
                                    else "requires_recovery",
                                )
                    env_request = (
                        db.execute(
                            "SELECT status,request_id FROM environment_requests "
                            "WHERE episode_id=? "
                            "AND status NOT IN ('prepared','succeeded') "
                            + (
                                "AND request_id NOT IN (SELECT request_id FROM "
                                "environment_source_reconciliations) "
                                if "environment_source_reconciliations" in tables else ""
                            )
                            + "ORDER BY rowid LIMIT 1",
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
                                "subsystem": "environment",
                                "error_kind": "environment_provider_failure",
                                "retry_state": "ambiguous"
                                if env_request["status"] in {"remote_started", "ambiguous"}
                                else "requires_recovery",
                            },
                        )
                board_row = artifacts.get(("timed_storyboard", "main"))
                if board_row and duration_path:
                    try:
                        board_v3 = parse_storyboard(store.read_json(board_row["artifact_id"]))
                        if not isinstance(board_v3, TimedStoryboardV3):
                            raise ValueError("duration path requires a V3 storyboard")
                        load_v3_evidence(
                            config,
                            store,
                            episode_by_key(store.database, str(episode["external_key"])),
                            board_v3,
                            board_row["artifact_id"],
                        )
                    except (ValueError, OSError, KeyError) as exc:
                        by_name["STORYBOARD"].update(
                            status="BLOCKED", evidence={"reason": str(exc)}
                        )
                        by_name["RENDER"]["status"] = "BLOCKED"
                        by_name["MEDIA_QA"]["status"] = "BLOCKED"
                        report["status"] = "BLOCKED"
                        report["blocker"] = "Selected V3 storyboard evidence failed admission"
                final = artifacts.get(("final_render", "main_v4")) or artifacts.get(
                    ("final_render", "main")
                )
                if final:
                    manifest_row = artifacts.get(("render_manifest", final["slot_key"]))
                    qa_row = artifacts.get(("media_qa", final["slot_key"]))
                    storyboard_row = artifacts.get(("timed_storyboard", "main"))
                    try:
                        if manifest_row is None or storyboard_row is None:
                            raise ValueError("render dependencies missing")
                        manifest = parse_manifest(store.read_json(manifest_row["artifact_id"]))
                        board = parse_storyboard(store.read_json(storyboard_row["artifact_id"]))
                        if (
                            manifest.episode_id != eid
                            or board.episode_id != eid
                            or manifest.timed_storyboard_artifact_id
                            != storyboard_row["artifact_id"]
                        ):
                            raise ValueError("render identity differs")
                        if (
                            db.execute(
                                "SELECT 1 FROM artifact_dependencies WHERE consumer_artifact_id=? "
                                "AND input_artifact_id=? AND input_sha256=?",
                                (
                                    final["artifact_id"],
                                    manifest_row["artifact_id"],
                                    manifest_row["sha256"],
                                ),
                            ).fetchone()
                            is None
                        ):
                            raise ValueError(
                                "render manifest differs from pinned render dependency"
                            )
                        validate_manifest(store, manifest, board)
                        if qa_row:
                            if (
                                db.execute(
                                    "SELECT 1 FROM artifact_dependencies "
                                    "WHERE consumer_artifact_id=? "
                                    "AND input_artifact_id=? AND input_sha256=?",
                                    (qa_row["artifact_id"], final["artifact_id"], final["sha256"]),
                                ).fetchone()
                                is None
                            ):
                                raise ValueError("QA differs from pinned render dependency")
                            qa = store.read_json(qa_row["artifact_id"])
                            if (
                                not isinstance(qa, dict)
                                or qa.get("passed") is not True
                                or (
                                    qa.get("render_artifact_id") != final["artifact_id"]
                                    or qa.get("render_sha256") != final["sha256"]
                                )
                            ):
                                raise ValueError("media QA differs")
                    except (KeyError, ValueError, OSError):
                        report["status"] = "BLOCKED"
                        report["blocker"] = "Retained render manifest or QA is invalid"
                        by_name["RENDER"]["status"] = "BLOCKED"
                        by_name["MEDIA_QA"]["status"] = "BLOCKED"
                    report.update(
                        final_render_id=final["artifact_id"],
                        output_path=str(config.data_root / final["relative_path"]),
                        ready_local_preview=by_name["RENDER"]["status"] == "COMPLETE",
                    )
                historical_storyboard = artifacts.get(("timed_storyboard", "main"))
                if historical_storyboard:
                    payload = json.loads(
                        (config.data_root / historical_storyboard["relative_path"]).read_text(
                            encoding="utf-8"
                        )
                    )
                    report["historical"] = (
                        bool(report.get("historical")) or payload.get("schema_version") == 1
                    )
                handoff = artifacts.get(("production_handoff", "main"))
                if (
                    handoff
                    and json.loads(handoff["provenance_json"]).get("model")
                    == "production_storyboard_v1"
                ):
                    report["historical"] = True
                if "publication_attempts" in tables:
                    publication = db.execute(
                        "SELECT * FROM publication_attempts WHERE episode_id=? ORDER BY "
                        "(youtube_video_id IS NOT NULL AND trim(youtube_video_id)<>'') DESC, "
                        "rowid DESC LIMIT 1",
                        (eid,),
                    ).fetchone()
                    if publication:
                        promotion = db.execute(
                            "SELECT * FROM publication_visibility_events WHERE upload_attempt_id=? "
                            "ORDER BY rowid DESC LIMIT 1",
                            (publication["attempt_id"],),
                        ).fetchone()
                        remote_id = str(publication["youtube_video_id"] or "").strip()
                        channel_ok = not publication["expected_channel_id"] or (
                            publication["expected_channel_id"] == config.expected_youtube_channel_id
                        )
                        at_target = (
                            bool(remote_id)
                            and channel_ok
                            and (
                                config.automation.publish_visibility == "private"
                                or publication["privacy_status"] == "public"
                                or (promotion is not None and promotion["outcome"] == "succeeded")
                            )
                        )
                        by_name["YOUTUBE"].update(
                            status="COMPLETE"
                            if at_target
                            else "BLOCKED"
                            if not channel_ok
                            else "AMBIGUOUS"
                            if (
                                not remote_id
                                and publication_state(publication)
                                in {
                                    PublicationState.REMOTE_STARTED,
                                    PublicationState.AMBIGUOUS_FAILED,
                                }
                            )
                            or (
                                promotion is not None
                                and promotion["outcome"] in {"remote_started", "ambiguous"}
                            )
                            else "READY"
                            if remote_id
                            else "READY"
                            if publication_state(publication)
                            in {PublicationState.NOT_ATTEMPTED, PublicationState.RETRYABLE_FAILED}
                            else "FAILED",
                            evidence={
                                "attempt_id": publication["attempt_id"],
                                "video_id": publication["youtube_video_id"],
                            },
                        )
                if final and "episode_visual_plan" in {kind for kind, _ in artifacts}:
                    release = evaluate_release(config, str(episode["external_key"]))
                    allowed = (
                        release.public_release_allowed
                        if config.automation.publish_visibility == "public"
                        else release.private_test_upload_allowed
                    )
                    by_name["RELEASE"].update(
                        status="COMPLETE"
                        if allowed
                        else "NEEDS_REVIEW"
                        if config.automation.require_human_review
                        else "BLOCKED",
                        evidence=release.as_dict(),
                    )
                    report["publication_would_be_attempted"] = (
                        config.automation.auto_publish
                        and allowed
                        and config.publication.youtube.enabled
                        and bool(config.expected_youtube_channel_id)
                    )

    report["target"] = target.value
    if target != ProductionTarget.PUBLISH:
        report["publication_would_be_attempted"] = False
    report["stages"] = [s for s in report["stages"] if s["name"] in stages_for(target)]
    reusable = {s["name"] for s in report["stages"] if s["status"] == "COMPLETE"}
    if "YOUTUBE" in reusable:
        # A persisted successful remote ID is stronger than local/diagnostic lifecycle.
        reusable.update(STAGES)
        report["status"] = "READY"
    render_ready = {"RENDER", "MEDIA_QA"}.issubset(reusable)
    # A trusted render is an immutable continuation boundary; upstream stages stay reused.
    if render_ready:
        reusable.update(STAGES[:7])
    report["draft_ready"] = "CREATIVE" in reusable
    report["render_ready"] = render_ready
    report["reusable_stages"] = sorted(reusable)
    report["target_complete"] = set(stages_for(target)).issubset(reusable)
    report["authoritative_stage"] = next((s for s in reversed(STAGES) if s in reusable), None)
    first = next((s for s in report["stages"] if s["name"] not in reusable), None)
    report["next_stage"] = first["name"] if first else None
    report["current_stage"] = (
        first["name"] if first else report["authoritative_stage"] or "CREATIVE"
    )
    report["next_action"] = "resume " + first["name"].lower() if first else "complete"
    if first and first["status"] == "NOT_STARTED":
        first["status"] = "READY"
    if (
        first
        and report["status"] == "READY"
        and first["status"]
        in {"BLOCKED", "AMBIGUOUS", "FAILED", "NEEDS_REVIEW", "PENDING_PROVIDER"}
    ):
        report["status"] = first["status"]
        report["blocker"] = (
            {"release": first["evidence"]} if first["name"] == "RELEASE" else first["evidence"]
        )
    report["dependencies_needed"] = [s for s in stages_for(target) if s not in reusable]
    report["allowed"] = report["status"] not in {"BLOCKED", "AMBIGUOUS", "FAILED", "NEEDS_REVIEW"}
    if report["target_complete"]:
        report["status"] = "COMPLETE"
    return report
