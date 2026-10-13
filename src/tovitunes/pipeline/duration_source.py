"""Strict original-music admission for the duration-only local production path."""

import json
import sqlite3
from contextlib import closing, nullcontext
from hashlib import sha256

import miniaudio

from tovitunes.config import RuntimeConfig
from tovitunes.domain.episode import Episode
from tovitunes.domain.production_v3 import AudioClock
from tovitunes.domain.storyboard import BeatAnalysis
from tovitunes.music.analysis import technical_measurements
from tovitunes.music.analysis_models import AudioAnalysis
from tovitunes.music.audio import inspect_audio
from tovitunes.music.models import CanonicalMusicSpec
from tovitunes.persistence.db import Database


def uses_duration_path(db: sqlite3.Connection, episode_id: str) -> bool:
    episode = db.execute(
        "SELECT learning_source FROM episodes WHERE episode_id=?", (episode_id,)
    ).fetchone()
    if episode is not None and episode["learning_source"] == "legacy_curriculum":
        return False
    row = db.execute(
        "SELECT v.relative_path FROM artifact_selections s JOIN artifact_versions v "
        "ON v.artifact_id=s.artifact_id WHERE s.owner_scope='episode' AND s.owner_id=? "
        "AND s.kind='episode_visual_plan' AND s.slot_key='main'",
        (episode_id,),
    ).fetchone()
    # The caller parses the selected plan when present. Successful pending V1 receipts also
    # retain their original rendering contract; failed V1 attempts are historical evidence.
    if row is None:
        return (
            db.execute(
                "SELECT 1 FROM generation_requests WHERE episode_id=? "
                "AND prompt_version='episode_visual_plan_v1' AND status='succeeded'",
                (episode_id,),
            ).fetchone()
            is None
        )
    return True


def admit_duration_source(
    config: RuntimeConfig,
    episode: Episode,
    request_id: str,
    expected: CanonicalMusicSpec,
    audio_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> AudioClock:
    """Measure bytes and check receipts; retained ASR/QA records are never rewritten."""
    with (
        nullcontext(connection)
        if connection is not None
        else closing(Database(config.database_path).connect())
    ) as db:
        request = db.execute("SELECT * FROM music_requests WHERE request_id=?", (request_id,))
        r = request.fetchone()
        receipt = db.execute(
            "SELECT * FROM music_receipts WHERE request_id=?", (request_id,)
        ).fetchone()
        output = db.execute(
            "SELECT * FROM music_outputs WHERE request_id=?", (request_id,)
        ).fetchone()
        binding = db.execute(
            "SELECT * FROM episode_music_bindings WHERE episode_id=?", (episode.episode_id,)
        ).fetchone()
        master = db.execute(
            "SELECT v.* FROM artifact_selections s JOIN artifact_versions v "
            "ON v.artifact_id=s.artifact_id WHERE s.owner_id=? AND s.owner_scope='episode' "
            "AND s.kind='audio_master' AND s.slot_key='main'",
            (episode.episode_id,),
        ).fetchone()
        if (
            r is None
            or receipt is None
            or output is None
            or binding is None
            or master is None
            or r["status"] != "succeeded"
            or binding["request_id"] != request_id
            or master["artifact_id"] != audio_id
            or master["episode_id"] != episode.episode_id
            or not r["provider_request_id"]
            or receipt["provider_request_id"] != r["provider_request_id"]
            or CanonicalMusicSpec.model_validate_json(r["canonical_spec_json"]) != expected
            or expected.brief.episode_key != episode.external_key
        ):
            raise ValueError("duration source request, receipt, episode or master identity differs")
        root = (config.data_root / "music-benchmark").resolve(strict=True)
        path = (root / output["relative_path"]).resolve(strict=True)
        master_root = config.data_root.resolve(strict=True)
        master_path = (master_root / master["relative_path"]).resolve(strict=True)
        if not path.is_relative_to(root) or not master_path.is_relative_to(master_root):
            raise ValueError("duration source path escapes authoritative roots")
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        if (
            digest != receipt["sha256"]
            or digest != output["sha256"]
            or digest != master["sha256"]
            or sha256(master_path.read_bytes()).hexdigest() != digest
            or len(data) != receipt["byte_count"]
            or len(data) != master["byte_count"]
        ):
            raise ValueError("duration source SHA or byte count differs from receipt/master")
        provenance = json.loads(master["provenance_json"])
        if any(
            provenance.get(key) != value
            for key, value in {
                "source_kind": "provider",
                "local_request_id": request_id,
                "request_id": r["provider_request_id"],
                "provider": r["provider"],
                "model": r["model"],
            }.items()
        ):
            raise ValueError("audio master provenance differs from original music request")
        info = inspect_audio(data, receipt["mime_type"])
        if (
            info.container != receipt["container"]
            or info.codec != receipt["codec"]
            or path.suffix.lower() != info.extension
            or abs(info.duration_seconds - receipt["duration_seconds"]) > 0.001
            or info.duration_seconds > expected.brief.maximum_duration_seconds
        ):
            raise ValueError("duration source metadata differs or exceeds resource duration limit")
        decoded = (
            miniaudio.wav_read_f32(data)
            if info.container == "wav"
            else miniaudio.mp3_read_f32(data)
        )
        metrics = technical_measurements(decoded)
        beats = None
        # Optional retained measured rhythm may improve cuts. No detector is invoked here.
        row = db.execute(
            "SELECT * FROM music_audio_analysis WHERE blind_id=? ORDER BY version DESC LIMIT 1",
            (output["blind_id"],),
        ).fetchone()
        if row:
            report = AudioAnalysis.model_validate_json(row["analysis_json"])
            if (
                row["audio_sha256"] != digest
                or row["version"] != report.version
                or row["request_id"] != request_id
                or row["analyzer_config_sha256"] != report.analyzer_config_sha256
                or report.audio_sha256 != digest
                or report.request_id != request_id
                or report.blind_id != output["blind_id"]
                or abs(report.duration_seconds - info.duration_seconds) > 0.005
            ):
                raise ValueError("retained analysis identity differs from duration source")
            rhythm = report.rhythm
            if (
                rhythm.status == "complete"
                and rhythm.estimated_bpm
                and rhythm.beat_seconds
                and rhythm.downbeat_seconds
                and rhythm.interval_cv is not None
                and rhythm.interval_cv <= report.thresholds.maximum_beat_interval_cv
                and rhythm.provenance
                and rhythm.provenance.source_audio_sha256 == digest
            ):
                beats = BeatAnalysis(
                    audio_master_artifact_id=audio_id,
                    audio_sha256=digest,
                    duration_seconds=info.duration_seconds,
                    source_analysis_version=report.version,
                    estimated_bpm=rhythm.estimated_bpm,
                    beat_seconds=rhythm.beat_seconds,
                    downbeat_seconds=rhythm.downbeat_seconds,
                    detector=rhythm.provenance.name,
                    detector_version=rhythm.provenance.version,
                    model_identity=rhythm.provenance.model_name,
                    model_revision=rhythm.provenance.model_revision,
                )
        return AudioClock(
            audio_master_artifact_id=audio_id,
            audio_sha256=digest,
            source_request_id=request_id,
            source_provider_request_id=r["provider_request_id"],
            source_blind_id=output["blind_id"],
            duration_seconds=info.duration_seconds,
            technical_metrics=metrics,
            measured_beats=beats,
        )
