"""Historical V1 evidence import and shared domain-evidence extraction."""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from tovitunes.artifacts.store import ArtifactRecord, AssetStore, InputDependency
from tovitunes.catalog import BrandCatalog, load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.episode import Episode, PinnedCharacterPack
from tovitunes.domain.review import ApprovalDecision
from tovitunes.domain.storyboard import (
    AudioAlignment,
    BeatAnalysis,
    Interval,
    StoryboardTemplate,
    TimedStoryboard,
    TimedText,
    build_storyboard,
)
from tovitunes.music.analysis_models import AudioAnalysis
from tovitunes.music.models import CanonicalMusicSpec, TimingAnalysis
from tovitunes.persistence.db import Database
from tovitunes.persistence.leases import Lease, LeaseStore

READY = "MUSIC_TECHNICAL_PIPELINE_READY_FOR_STORYBOARD"
TOOL = "tovitunes.production_handoff"
VERSION = "production_storyboard_v1"
PILOT_AUDIO_SHA256 = "06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class AcceptedSource:
    audio_path: Path
    analysis: AudioAnalysis
    spec: CanonicalMusicSpec
    manifest: dict[str, Any]
    qa_evaluation_id: str
    timing_evaluation_id: str


def _source(
    db: sqlite3.Connection, root: Path, template: StoryboardTemplate, blind_id: str, version: int
) -> AcceptedSource:
    row = db.execute("SELECT * FROM music_outputs WHERE blind_id=?", (blind_id,)).fetchone()
    if row is None:
        raise ValueError("music blind ID does not exist")
    request = db.execute(
        "SELECT * FROM music_requests WHERE request_id=?", (row["request_id"],)
    ).fetchone()
    receipt = db.execute(
        "SELECT * FROM music_receipts WHERE request_id=?", (row["request_id"],)
    ).fetchone()
    analysis_row = db.execute(
        "SELECT * FROM music_audio_analysis WHERE blind_id=? AND version=?", (blind_id, version)
    ).fetchone()
    timing_row = db.execute(
        "SELECT * FROM music_timing WHERE blind_id=? AND version=?", (blind_id, version)
    ).fetchone()
    if any(x is None for x in (request, receipt, analysis_row, timing_row)):
        raise ValueError("source request, receipt, analysis, or timing is missing")
    report = AudioAnalysis.model_validate_json(analysis_row["analysis_json"])
    timing = TimingAnalysis.model_validate_json(timing_row["analysis_json"])
    spec = CanonicalMusicSpec.model_validate_json(request["canonical_spec_json"])
    translated = json.loads(request["translated_request_json"])
    audio_root = (root / "music-benchmark").resolve(strict=True)
    path = (audio_root / row["relative_path"]).resolve(strict=True)
    if not path.is_relative_to(audio_root) or not path.is_file():
        raise ValueError("source audio is outside retained music root")
    digest = sha256(path.read_bytes()).hexdigest()
    if (
        digest != row["sha256"]
        or digest != receipt["sha256"]
        or digest != report.audio_sha256
        or digest != analysis_row["audio_sha256"]
        or path.stat().st_size != receipt["byte_count"]
        or report.blind_id != blind_id
        or report.version != version
        or report.request_id != row["request_id"]
        or analysis_row["request_id"] != row["request_id"]
        or report.analyzer_config_sha256 != analysis_row["analyzer_config_sha256"]
        or report.duration_seconds != receipt["duration_seconds"]
        or timing != report.timing
        or receipt["provider_request_id"] != request["provider_request_id"]
        or receipt["mime_type"] != "audio/mpeg"
        or receipt["container"] != "mp3"
    ):
        raise ValueError("source audio SHA, receipt, analysis, or timing identity differs")
    if (
        request["status"] != "succeeded"
        or request["provider"] != "google"
        or request["model"] != "lyria-3-pro-preview"
        or request["attempt"] != 2
        or spec.attempt != 2
        or spec.brief.id != template.template_id
        or translated.get("prompt_contract") != "lyria_exact_lyrics_v2"
        or tuple(x.text for x in spec.lyrics.lines) != tuple(x.lyric_text for x in template.lyrics)
        or tuple(x.text for x in timing.lyric_lines) != tuple(x.text for x in spec.lyrics.lines)
    ):
        raise ValueError("source is not the retained candidate-2 pilot identity/lyrics")
    # The pilot is deliberately fail-closed on the known persisted identity. These values
    # are verified against persistence and bytes; they never supply missing evidence.
    if (
        blind_id != "mb_3f657849e3d04060a0107940b098fb60"
        or row["request_id"] != "152f47fa-54f6-4bb3-ad67-8dc5467380d5"
        or digest != PILOT_AUDIO_SHA256
        or version != 3
    ):
        raise ValueError("source differs from authoritative pilot identity/SHA/version")
    if row["rights_status"] != "unknown" or row["approval_status"] != "pending":
        raise ValueError("music rights/approval differ from unknown/pending handoff state")
    analysis_hash = sha256(analysis_row["analysis_json"].encode()).hexdigest()
    timing_hash = sha256(canonical_bytes(timing.model_dump(mode="json"))).hexdigest()
    evaluations: list[str] = []
    for subject_type, subject, policy, policy_version, evidence_hash in (
        ("audio", blind_id, "music_qa", 2, analysis_hash),
        ("timing", f"{blind_id}:{version}", "music_timing", 1, timing_hash),
    ):
        ev = db.execute(
            "SELECT * FROM music_policy_evaluations WHERE subject_type=? AND subject_id=? "
            "AND subject_sha256=? AND policy_id=? AND policy_version=? "
            "ORDER BY rowid DESC LIMIT 1",
            (subject_type, subject, digest, policy, policy_version),
        ).fetchone()
        if ev is None or ev["status"] != "pass" or ev["evaluator_type"] != "machine":
            raise ValueError(f"missing current {policy} pass for exact audio SHA/version")
        evidence = json.loads(ev["evidence_json"])
        if evidence.get("analysis_sha256") != evidence_hash or (
            subject_type == "audio"
            and (
                evidence.get("analysis_version") != version
                or evidence.get("request_id") != row["request_id"]
                or evidence.get("timing_admission", {}).get("admitted") is not True
            )
        ):
            raise ValueError(f"{policy} evidence does not bind admitted exact analysis version")
        evaluations.append(ev["evaluation_id"])
    manifest = {
        "schema_version": 1,
        "source_kind": "provider",
        "provider": request["provider"],
        "model": request["model"],
        "local_request_id": row["request_id"],
        "provider_request_id": request["provider_request_id"],
        "prompt_contract": translated["prompt_contract"],
        "source_audio_sha256": digest,
        "source_blind_id": blind_id,
        "attempt": request["attempt"],
        "byte_count": receipt["byte_count"],
        "duration_seconds": report.duration_seconds,
        "rights": "unknown",
        "music_approval": "pending",
    }
    return AcceptedSource(path, report, spec, manifest, *evaluations)


def accept_episode_source(
    config: RuntimeConfig,
    episode: Episode,
    request_id: str,
    expected: CanonicalMusicSpec,
    analysis_version: int,
) -> AcceptedSource:
    """V2 handoff verifies a generic retained receipt, exact lyrics and machine evidence.

    The frozen Red/Lyria `_source` path above remains independently fail-closed.
    Rights and subjective approval do not become commercial clearance here.
    """
    with closing(Database(config.database_path).connect()) as db:
        request = db.execute(
            "SELECT * FROM music_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        receipt = db.execute(
            "SELECT * FROM music_receipts WHERE request_id=?", (request_id,)
        ).fetchone()
        output = db.execute(
            "SELECT * FROM music_outputs WHERE request_id=?", (request_id,)
        ).fetchone()
        if request is None or receipt is None or output is None or request["status"] != "succeeded":
            raise ValueError("generic handoff requires a successful durable request/receipt/output")
        spec = CanonicalMusicSpec.model_validate_json(request["canonical_spec_json"])
        if spec != expected or spec.brief.episode_key != episode.external_key:
            raise ValueError("music request differs from exact selected episode/creative evidence")
        root = (config.data_root / "music-benchmark").resolve(strict=True)
        path = (root / output["relative_path"]).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("retained music path escapes its authoritative root")
        digest = sha256(path.read_bytes()).hexdigest()
        from tovitunes.music.audio import inspect_audio

        info = inspect_audio(path.read_bytes(), receipt["mime_type"])
        row = db.execute(
            "SELECT * FROM music_audio_analysis WHERE blind_id=? AND version=?",
            (output["blind_id"], analysis_version),
        ).fetchone()
        timing_row = db.execute(
            "SELECT * FROM music_timing WHERE blind_id=? AND version=?",
            (output["blind_id"], analysis_version),
        ).fetchone()
        if row is None or timing_row is None:
            raise ValueError("generic handoff lacks measured analysis/timing")
        report = AudioAnalysis.model_validate_json(row["analysis_json"])
        timing = TimingAnalysis.model_validate_json(timing_row["analysis_json"])
        if (
            not request["provider_request_id"]
            or request["provider_request_id"] != receipt["provider_request_id"]
            or any(
                value != digest
                for value in (
                    receipt["sha256"],
                    output["sha256"],
                    row["audio_sha256"],
                    report.audio_sha256,
                    timing.audio_sha256,
                )
            )
            or report.request_id != request_id
            or row["request_id"] != request_id
            or report.blind_id != output["blind_id"]
            or report.version != analysis_version
            or report.analyzer_config_sha256 != row["analyzer_config_sha256"]
            or path.stat().st_size != receipt["byte_count"]
            or info.container != receipt["container"]
            or info.codec != receipt["codec"]
            or path.suffix.lower() != "." + receipt["container"]
            or abs(info.duration_seconds - receipt["duration_seconds"]) > 0.001
            or report.duration_seconds != receipt["duration_seconds"]
            or timing != report.timing
            or report.lyric_comparison.expected_transcript != spec.lyrics.text()
            or tuple(line.text for line in timing.lyric_lines)
            != tuple(line.text for line in spec.lyrics.lines)
        ):
            raise ValueError("generic audio/receipt/lyrics/analysis/timing identity differs")
        evaluations = []
        for subject_type, subject, policy, policy_version, evidence_hash in (
            (
                "audio",
                output["blind_id"],
                "music_qa",
                2,
                sha256(row["analysis_json"].encode()).hexdigest(),
            ),
            (
                "timing",
                f"{output['blind_id']}:{analysis_version}",
                "music_timing",
                1,
                sha256(canonical_bytes(timing.model_dump(mode="json"))).hexdigest(),
            ),
        ):
            ev = db.execute(
                "SELECT * FROM music_policy_evaluations WHERE subject_type=? AND subject_id=? "
                "AND subject_sha256=? AND policy_id=? AND policy_version=? ORDER BY rowid DESC "
                "LIMIT 1",
                (subject_type, subject, digest, policy, policy_version),
            ).fetchone()
            if ev is None or ev["status"] != "pass" or ev["evaluator_type"] != "machine":
                raise ValueError(f"generic handoff requires machine {policy} pass")
            evidence = json.loads(ev["evidence_json"])
            if evidence.get("analysis_sha256") != evidence_hash:
                raise ValueError("QA evaluation differs from exact measured analysis")
            if subject_type == "audio" and (
                evidence.get("analysis_version") != analysis_version
                or evidence.get("request_id") != request_id
                or evidence.get("timing_admission", {}).get("admitted") is not True
            ):
                raise ValueError("QA does not admit exact request/timing version")
            evaluations.append(ev["evaluation_id"])
        manifest = {
            "schema_version": 2,
            "episode_id": episode.episode_id,
            "provider": request["provider"],
            "model": request["model"],
            "local_request_id": request_id,
            "provider_request_id": request["provider_request_id"],
            "source_audio_sha256": digest,
            "source_blind_id": output["blind_id"],
            "mime_type": receipt["mime_type"],
            "container": receipt["container"],
            "analysis_version": analysis_version,
            "byte_count": receipt["byte_count"],
            "duration_seconds": report.duration_seconds,
            "rights": output["rights_status"],
            "music_approval": output["approval_status"],
            "qa_evaluation_ids": evaluations,
        }
        return AcceptedSource(path, report, spec, manifest, *evaluations)


def _episode(
    db: sqlite3.Connection, catalog: BrandCatalog, concept: str, external_key: str
) -> tuple[Episode, bool]:
    expected = Episode.create(catalog, concept, external_key)
    row = db.execute("SELECT * FROM episodes WHERE external_key=?", (external_key,)).fetchone()
    if row is None:
        return expected, False
    packs = db.execute(
        "SELECT character_id, revision_id FROM episode_character_packs "
        "WHERE episode_id=? ORDER BY character_id",
        (row["episode_id"],),
    ).fetchall()
    episode = Episode(
        **{
            k: row[k]
            for k in (
                "episode_id",
                "external_key",
                "brand_revision_id",
                "curriculum_revision_id",
                "concept_id",
                "objective_id",
                "objective",
                "language",
                "target_duration_seconds",
                "lifecycle",
                "created_at",
            )
        },
        target_vocabulary=tuple(json.loads(row["target_vocabulary_json"])),
        character_packs=tuple(PinnedCharacterPack(**dict(p)) for p in packs),
    )
    if any(
        getattr(episode, k) != getattr(expected, k)
        for k in (
            "brand_revision_id",
            "curriculum_revision_id",
            "concept_id",
            "objective_id",
            "objective",
            "target_vocabulary",
            "character_packs",
            "language",
        )
    ):
        raise ValueError("existing episode differs from pinned catalog/objective/character pack")
    decision = db.execute(
        "SELECT status FROM approval_decisions WHERE episode_id=? ORDER BY rowid DESC LIMIT 1",
        (episode.episode_id,),
    ).fetchone()
    if decision and decision["status"] in {"rejected", "needs_review"}:
        raise ValueError("existing objective decision blocks technical production planning")
    return episode, True


def extract_alignment(
    source: AcceptedSource, master_id: str, *, generic: bool = False
) -> AudioAlignment:
    timing = source.analysis.timing
    return AudioAlignment(
        schema_version=2 if generic else 1,
        audio_master_artifact_id=master_id,
        audio_sha256=source.analysis.audio_sha256,
        source_blind_id=source.analysis.blind_id,
        source_analysis_version=source.analysis.version,
        duration_seconds=source.analysis.duration_seconds,
        words=tuple(TimedText.model_validate(x.model_dump()) for x in timing.words),
        lyric_lines=tuple(TimedText.model_validate(x.model_dump()) for x in timing.lyric_lines),
        sections=tuple(TimedText.model_validate(x.model_dump()) for x in timing.sections),
        pre_lyric=Interval.model_validate(timing.intro.model_dump()) if timing.intro else None,
        post_lyric=Interval.model_validate(timing.outro.model_dump()) if timing.outro else None,
    )


def extract_beats(source: AcceptedSource, master_id: str) -> BeatAnalysis:
    rhythm = source.analysis.rhythm
    provenance = rhythm.provenance
    if rhythm.status != "complete" or provenance is None or rhythm.estimated_bpm is None:
        raise ValueError("admitted rhythm lacks detector identity or measured BPM")
    return BeatAnalysis(
        audio_master_artifact_id=master_id,
        audio_sha256=source.analysis.audio_sha256,
        duration_seconds=source.analysis.duration_seconds,
        source_analysis_version=source.analysis.version,
        estimated_bpm=rhythm.estimated_bpm,
        beat_seconds=rhythm.beat_seconds,
        downbeat_seconds=rhythm.downbeat_seconds,
        detector=provenance.name,
        detector_version=provenance.version,
        model_identity=provenance.model_name,
        model_revision=provenance.model_revision,
    )


@dataclass(frozen=True)
class HandoffPlan:
    catalog: BrandCatalog
    episode: Episode
    episode_exists: bool
    source: AcceptedSource
    template: StoryboardTemplate
    template_sha256: str
    preview: TimedStoryboard
    actions: dict[str, str]

    def report(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode.episode_id if self.episode_exists else None,
            "episode_key": self.episode.external_key,
            "episode_action": "reuse" if self.episode_exists else "create",
            "source_audio_sha256": self.source.analysis.audio_sha256,
            "technical_readiness": READY,
            "analysis_version": self.source.analysis.version,
            "qa_evaluation_id": self.source.qa_evaluation_id,
            "timing_evaluation_id": self.source.timing_evaluation_id,
            "artifact_actions": self.actions,
            "scene_ids": self.preview.scene_ids,
            "rights": "unknown",
            "release_blocked": True,
            "provider_calls": 0,
        }


def plan_handoff(
    config: RuntimeConfig, concept: str, episode_key: str, blind_id: str, analysis_version: int
) -> HandoffPlan:
    """Validate all source/semantic inputs using a SQLite read-only snapshot, without writes."""
    if not config.database_path.is_file():
        raise ValueError("authoritative existing database is required")
    catalog = load_brand(config.brand_root)
    path = config.brand_root / "storyboards/colors_red_v1.yaml"
    raw = path.read_bytes()
    template = StoryboardTemplate.model_validate(yaml.safe_load(raw))
    if concept != template.concept_id:
        raise ValueError("concept differs from pilot template")
    with closing(sqlite3.connect(config.database_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        source = _source(db, config.data_root, template, blind_id, analysis_version)
        episode, exists = _episode(db, catalog, concept, episode_key)
        # Respect later decisions and immutable-file failures before any append on rerun.
        for row in db.execute(
            "SELECT v.artifact_id, v.relative_path, v.sha256 FROM artifact_versions v "
            "JOIN artifact_selections s ON s.artifact_id=v.artifact_id "
            "WHERE v.episode_id=? AND v.kind IN "
            "('production_handoff', 'audio_master', 'audio_alignment', "
            "'beat_analysis', 'timed_storyboard')",
            (episode.episode_id,),
        ):
            asset_path = (config.data_root / row["relative_path"]).resolve(strict=True)
            if not asset_path.is_relative_to(config.data_root.resolve(strict=True)) or (
                sha256(asset_path.read_bytes()).hexdigest() != row["sha256"]
            ):
                raise ValueError("selected production artifact failed immutable SHA validation")
            approval = db.execute(
                "SELECT status FROM approval_decisions WHERE artifact_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (row["artifact_id"],),
            ).fetchone()
            rights = db.execute(
                "SELECT status FROM rights_decisions WHERE artifact_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (row["artifact_id"],),
            ).fetchone()
            if (
                approval is None
                or approval[0] != "approved"
                or rights is None
                or rights[0] != "unknown"
            ):
                raise ValueError("selected production artifact approval/rights block reuse")
        actions: dict[str, str] = {}

        def ref(kind: str, digest: str) -> str:
            row = db.execute(
                "SELECT artifact_id FROM artifact_versions WHERE episode_id=? AND kind=? "
                "AND slot_key='main' AND sha256=? ORDER BY created_at LIMIT 1",
                (episode.episode_id, kind, digest),
            ).fetchone()
            actions[kind] = "reuse" if row else "create"
            return row[0] if row else f"planned_{kind}"

        ref("production_handoff", sha256(canonical_bytes(source.manifest)).hexdigest())
        master = ref("audio_master", source.analysis.audio_sha256)
        alignment = extract_alignment(source, master)
        beats = extract_beats(source, master)
        alignment_id = ref(
            "audio_alignment",
            sha256(canonical_bytes(alignment.model_dump(mode="json"))).hexdigest(),
        )
        beat_id = ref(
            "beat_analysis", sha256(canonical_bytes(beats.model_dump(mode="json"))).hexdigest()
        )
        preview = build_storyboard(
            episode, alignment, beats, alignment_id, beat_id, template, sha256(raw).hexdigest()
        )
        ref(
            "timed_storyboard", sha256(canonical_bytes(preview.model_dump(mode="json"))).hexdigest()
        )
    return HandoffPlan(
        catalog, episode, exists, source, template, sha256(raw).hexdigest(), preview, actions
    )


class ProductionHandoff:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def prepare(
        self, concept: str, episode_key: str, blind_id: str, analysis_version: int
    ) -> dict[str, Any]:
        # No initialization, migrations or lease writes before complete preflight.
        plan_handoff(self.config, concept, episode_key, blind_id, analysis_version)
        database = Database(self.config.database_path)
        leases = LeaseStore(database)
        lease = leases.acquire(f"production-storyboard:{episode_key}", duration_seconds=300)
        try:
            prepared = plan_handoff(self.config, concept, episode_key, blind_id, analysis_version)
            return self._persist(prepared, database, leases, lease)
        finally:
            leases.release(lease)

    def _persist(
        self, prepared: HandoffPlan, database: Database, leases: LeaseStore, lease: Lease
    ) -> dict[str, Any]:
        source, episode = prepared.source, prepared.episode
        leases.assert_owner(lease)
        if not prepared.episode_exists:
            database.create_episode(prepared.catalog, episode)
        staging = self.config.data_root / ".production-handoff"
        staging.mkdir(exist_ok=True)
        store = AssetStore(
            self.config.data_root,
            database,
            generated_source_roots=[
                source.audio_path.parent,
                staging,
            ],
        )
        self._approve(store, episode.episode_id, True)
        artifacts: dict[str, str] = {}
        actions: dict[str, str] = {}

        def persist(
            kind: str, payload: dict[str, Any], inputs: tuple[str, ...] = ()
        ) -> ArtifactRecord:
            leases.assert_owner(lease)
            encoded = canonical_bytes(payload)
            record = store.find_version(
                "episode", episode.episode_id, kind, "main", sha256(encoded).hexdigest()
            )
            reused = record is not None
            provenance = Provenance(
                source_kind="deterministic",
                acquired_at=datetime.now(UTC),
                provider=TOOL,
                model=VERSION,
                prompt_version=prepared.template.template_id,
                input_artifact_ids=inputs,
            )
            if record:
                self._check_reuse(store, record, inputs, provenance)
            else:
                path = staging / f"{uuid4()}.json"
                path.write_bytes(encoded)
                try:
                    record = store.ingest(
                        path,
                        owner_scope="episode",
                        owner_id=episode.episode_id,
                        kind=kind,
                        slot_key="main",
                        provenance=provenance,
                        dependencies=[InputDependency(x, "production_input") for x in inputs],
                        expected_media_type="application/json",
                    )
                finally:
                    path.unlink(missing_ok=True)
            actions[kind] = "reuse" if reused else "create"
            self._approve(store, record.identity.artifact_id)
            self._select(store, record)
            artifacts[kind] = record.identity.artifact_id
            return record

        manifest = persist("production_handoff", source.manifest)
        manifest_id = manifest.identity.artifact_id
        leases.assert_owner(lease)
        master = store.find_version(
            "episode", episode.episode_id, "audio_master", "main", source.analysis.audio_sha256
        )
        provenance = Provenance(
            source_kind="provider",
            acquired_at=datetime.now(UTC),
            provider=source.manifest["provider"],
            model=source.manifest["model"],
            request_id=source.manifest["provider_request_id"],
            local_request_id=source.manifest["local_request_id"],
            prompt_version=source.manifest["prompt_contract"],
            source_uri=f"music-benchmark://{source.analysis.blind_id}/attempt/2",
            input_artifact_ids=(manifest_id,),
        )
        actions["audio_master"] = "reuse" if master else "create"
        if master:
            self._check_reuse(store, master, (manifest_id,), provenance)
        else:
            master = store.ingest(
                source.audio_path,
                owner_scope="episode",
                owner_id=episode.episode_id,
                kind="audio_master",
                slot_key="main",
                provenance=provenance,
                dependencies=[InputDependency(manifest_id, "production_input")],
                expected_media_type="audio/mpeg",
            )
        if master.sha256 != source.analysis.audio_sha256:
            raise ValueError("production copy SHA differs from accepted audio")
        self._approve(store, master.identity.artifact_id)
        self._select(store, master)
        artifacts["audio_master"] = master.identity.artifact_id
        alignment = extract_alignment(source, master.identity.artifact_id)
        beats = extract_beats(source, master.identity.artifact_id)
        alignment_record = persist(
            "audio_alignment", alignment.model_dump(mode="json"), (master.identity.artifact_id,)
        )
        beat_record = persist(
            "beat_analysis", beats.model_dump(mode="json"), (master.identity.artifact_id,)
        )
        storyboard = build_storyboard(
            episode,
            alignment,
            beats,
            alignment_record.identity.artifact_id,
            beat_record.identity.artifact_id,
            prepared.template,
            prepared.template_sha256,
        )
        persist(
            "timed_storyboard",
            storyboard.model_dump(mode="json"),
            (
                master.identity.artifact_id,
                alignment_record.identity.artifact_id,
                beat_record.identity.artifact_id,
            ),
        )
        return {
            **prepared.report(),
            "episode_id": episode.episode_id,
            "artifact_ids": artifacts,
            "artifact_actions": actions,
            "scene_count": len(storyboard.scenes),
            "storyboard": storyboard.model_dump(mode="json"),
            "coverage": {
                "start": 0.0,
                "end": storyboard.duration_seconds,
                "gaps": 0,
                "overlaps": 0,
            },
            "classification": "TIMED_STORYBOARD_READY_FOR_ANIMATION",
        }

    @staticmethod
    def _check_reuse(
        store: AssetStore, record: ArtifactRecord, inputs: tuple[str, ...], provenance: Provenance
    ) -> None:
        if not store.inspect(record.identity.artifact_id).valid:
            raise ValueError("existing production artifact failed immutable file validation")
        if record.provenance.model_dump(exclude={"acquired_at"}) != provenance.model_dump(
            exclude={"acquired_at"}
        ):
            raise ValueError("existing artifact has different source provenance")
        with closing(store.database.connect()) as db:
            dependencies = db.execute(
                "SELECT input_artifact_id, input_sha256 FROM artifact_dependencies "
                "WHERE consumer_artifact_id=? ORDER BY rowid",
                (record.identity.artifact_id,),
            ).fetchall()
        if tuple(r[0] for r in dependencies) != inputs or any(
            store.get(r[0]).sha256 != r[1] for r in dependencies
        ):
            raise ValueError("existing artifact dependencies differ from pinned inputs")

    @staticmethod
    def _approve(store: AssetStore, target_id: str, objective: bool = False) -> None:
        column = "episode_id" if objective else "artifact_id"
        with closing(store.database.connect()) as db:
            decision = db.execute(
                f"SELECT status FROM approval_decisions WHERE {column}=? "
                "ORDER BY rowid DESC LIMIT 1",
                (target_id,),
            ).fetchone()
            if not objective:
                rights = db.execute(
                    "SELECT status FROM rights_decisions WHERE artifact_id=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (target_id,),
                ).fetchone()
                if rights is None or rights[0] != "unknown":
                    raise ValueError("production rights must remain unknown")
        if decision and decision[0] == "approved":
            return
        if decision and decision[0] in {"rejected", "needs_review"}:
            raise ValueError("existing decision blocks technical production approval")
        store.record_approval(
            ApprovalDecision(
                target_id=target_id,
                target_kind="episode" if objective else "artifact",
                status="approved",
                actor=f"machine:{TOOL}",
                policy_version="canonical_curriculum_v1"
                if objective
                else "technical_production_master_v1",
                reason=(
                    "Approve the pinned version-controlled curriculum objective for technical "
                    "production "
                    "planning only. No music rights, final-video, or publication approval."
                    if objective
                    else "Approved as a technical production artifact for storyboard/render "
                    "development "
                    "from admitted QA/timing evidence. No music-output or publication approval; "
                    "rights remain unknown."
                ),
                decided_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _select(store: AssetStore, record: ArtifactRecord) -> None:
        identity = record.identity
        current = store.selected("episode", identity.owner_id, identity.kind, "main")
        if current and current.identity.artifact_id == identity.artifact_id:
            return  # Preserve the selection timestamp as well as the pointer.
        if current:
            raise ValueError("existing selection differs; explicit replacement is required")
        store.select(identity.artifact_id)
