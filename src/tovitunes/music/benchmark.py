"""Offline planning and durable, fail-closed music execution."""

import json
import os
import re
import sqlite3
import wave
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field

from tovitunes.music.audio import inspect_audio
from tovitunes.music.models import (
    CanonicalMusicSpec,
    LyricCandidate,
    MusicBrief,
    MusicReview,
    MusicRubric,
    StrictModel,
    TimingAnalysis,
    fingerprint,
)
from tovitunes.music.providers import MusicFailure, MusicProvider, MusicResult
from tovitunes.persistence.db import Database


def now() -> str:
    return datetime.now(UTC).isoformat()


def lyric_hash(lyrics: LyricCandidate) -> str:
    return sha256(
        json.dumps(lyrics.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def json_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


POLICIES = {
    "lyrics": ("colors_red_lyrics", 1, "automated_lyrics_policy_v1"),
    "rights": ("commercial_music_rights", 1, "automated_rights_policy_v1"),
    "qa": ("music_qa", 1, "automated_music_qa_v1"),
    "approval": ("music_approval", 1, "automated_release_policy_v1"),
    "timing": ("music_timing", 1, "automated_timing_policy_v1"),
}


class PlannedMusicRequest(StrictModel):
    provider: str
    model: str
    attempt: int = Field(gt=0)
    input_fingerprint: str
    canonical_spec: CanonicalMusicSpec
    translated_request: dict[str, Any]
    capabilities: dict[str, Any]


def plan(
    brief: MusicBrief, lyrics: Any, providers: Sequence[MusicProvider]
) -> tuple[PlannedMusicRequest, ...]:
    plans = []
    for provider in providers:
        for attempt in range(1, brief.candidate_count_per_provider + 1):
            spec = CanonicalMusicSpec(brief=brief, lyrics=lyrics, attempt=attempt)
            translated = provider.translate(spec)
            plans.append(
                PlannedMusicRequest(
                    provider=provider.provider,
                    model=provider.model,
                    attempt=attempt,
                    input_fingerprint=fingerprint(
                        spec, provider.provider, provider.model, translated
                    ),
                    canonical_spec=spec,
                    translated_request=translated,
                    capabilities=provider.capabilities.model_dump(mode="json"),
                )
            )
    return tuple(plans)


def inspect_wav_metadata(data: bytes) -> tuple[float, str]:
    import io

    try:
        with wave.open(io.BytesIO(data), "rb") as stream:
            if stream.getnframes() == 0 or stream.getframerate() == 0:
                raise ValueError("empty WAV")
            if stream.getcomptype() != "NONE":
                raise ValueError("unsupported compressed WAV")
            duration = stream.getnframes() / stream.getframerate()
            if len(stream.readframes(stream.getnframes())) != (
                stream.getnframes() * stream.getnchannels() * stream.getsampwidth()
            ):
                raise ValueError("truncated WAV")
            codec = {1: "pcm_u8", 2: "pcm_s16le", 3: "pcm_s24le", 4: "pcm_s32le"}.get(
                stream.getsampwidth()
            )
            if codec is None:
                raise ValueError("unsupported PCM sample width")
            return duration, codec
    except (wave.Error, EOFError) as exc:
        raise ValueError("invalid WAV") from exc


def inspect_wav(data: bytes) -> float:
    return inspect_wav_metadata(data)[0]


class MusicBenchmark:
    def __init__(self, database: Database, audio_root: Path) -> None:
        self.database = database
        audio_root.mkdir(parents=True, exist_ok=True)
        if audio_root.is_symlink():
            raise ValueError("audio root cannot be a symlink")
        self.audio_root = audio_root.resolve()

    def prepare(self, item: PlannedMusicRequest) -> dict[str, Any]:
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM music_requests WHERE input_fingerprint = ?",
                (item.input_fingerprint,),
            ).fetchone()
            if row is None:
                request_id = str(uuid4())
                db.execute(
                    "INSERT INTO music_requests (request_id, brief_id, lyric_id, provider, model, "
                    "attempt, input_fingerprint, status, canonical_spec_json, "
                    "translated_request_json, capabilities_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?, ?, ?)",
                    (
                        request_id,
                        item.canonical_spec.brief.id,
                        item.canonical_spec.lyrics.id,
                        item.provider,
                        item.model,
                        item.attempt,
                        item.input_fingerprint,
                        item.canonical_spec.model_dump_json(),
                        json.dumps(item.translated_request, sort_keys=True),
                        json.dumps(item.capabilities, sort_keys=True),
                        now(),
                        now(),
                    ),
                )
                row = db.execute(
                    "SELECT * FROM music_requests WHERE request_id = ?", (request_id,)
                ).fetchone()
            db.commit()
            assert row is not None
            return dict(row)

    def request(self, request_id: str) -> dict[str, Any]:
        with closing(self.database.connect()) as db:
            row = db.execute(
                "SELECT * FROM music_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return dict(row)

    def _transition(
        self,
        request_id: str,
        status: str,
        *,
        category: str | None = None,
        reason: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        allowed = {
            "prepared": {"remote_started", "retryable_failure", "terminal_failure"},
            "retryable_failure": {"remote_started", "retryable_failure"},
            "remote_started": {"retryable_failure", "ambiguous", "terminal_failure"},
            "ambiguous": {"ambiguous", "terminal_failure"},
        }
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, remote_started_at FROM music_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None or status not in allowed.get(row["status"], set()):
                raise ValueError("invalid music request state transition")
            if status == "remote_started" and row["remote_started_at"] is not None:
                raise ValueError("remote request cannot be repeated")
            db.execute(
                "UPDATE music_requests SET status = ?, "
                "remote_started_at = COALESCE(remote_started_at, ?), "
                "provider_request_id = COALESCE(?, provider_request_id), failure_category = ?, "
                "failure_reason = ?, updated_at = ? WHERE request_id = ?",
                (
                    status,
                    now() if status == "remote_started" else None,
                    provider_request_id,
                    category,
                    reason,
                    now(),
                    request_id,
                ),
            )
            db.commit()

    def _record_provider_identity(self, request_id: str, provider_request_id: str) -> None:
        """Commit a returned remote identity before any later parsing or retrieval."""
        if not provider_request_id:
            raise ValueError("provider request identity is empty")
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, remote_started_at, provider_request_id FROM music_requests "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if (
                row is None
                or row["remote_started_at"] is None
                or row["status"] not in {"remote_started", "ambiguous"}
                or row["provider_request_id"] not in (None, provider_request_id)
            ):
                raise ValueError("provider request identity cannot be changed")
            db.execute(
                "UPDATE music_requests SET provider_request_id = ?, updated_at = ? "
                "WHERE request_id = ?",
                (provider_request_id, now(), request_id),
            )
            db.commit()

    def _audio_path(self, request_id: str, container: str = "wav") -> Path:
        if container not in {"wav", "mp3"}:
            raise ValueError("unsupported music container")
        return self.audio_root / f"{request_id}.{container}"

    def _staged_path(self, request_id: str, container: str = "wav") -> Path:
        if container not in {"wav", "mp3"}:
            raise ValueError("unsupported music container")
        return self.audio_root / ".music-returned" / f"{request_id}.{container}"

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _write_audio(self, request_id: str, data: bytes, container: str = "wav") -> Path:
        path = self._staged_path(request_id, container)
        path.parent.mkdir(exist_ok=True)
        self._sync_directory(self.audio_root)
        if (
            path.parent.is_symlink()
            or path.exists()
            or path.is_symlink()
            or self._audio_path(request_id, container).exists()
            or self._audio_path(request_id, container).is_symlink()
        ):
            raise FileExistsError("returned audio is already staged")
        temporary = path.with_name(f"{request_id}.{uuid4().hex}.tmp")
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        self._sync_directory(path.parent)
        return path

    def _verify_audio(self, path: Path, receipt: Any) -> None:
        if not path.is_file() or path.is_symlink():
            raise ValueError("received audio bytes missing")
        data = path.read_bytes()
        if sha256(data).hexdigest() != receipt["sha256"] or len(data) != receipt["byte_count"]:
            raise ValueError("received audio bytes changed")
        info = inspect_audio(data, receipt["mime_type"])
        if (
            receipt["container"] != info.container
            or receipt["codec"] != info.codec
            or receipt["duration_seconds"] != info.duration_seconds
            or path.suffix != info.extension
        ):
            raise ValueError("received audio metadata changed")
        metadata = json.loads(receipt["response_metadata_json"])
        if (
            "audio_sample_rate_hz" in metadata
            and metadata["audio_sample_rate_hz"] != info.sample_rate_hz
        ):
            raise ValueError("received audio sample rate changed")
        if "audio_bitrate_bps" in metadata and metadata["audio_bitrate_bps"] != info.bitrate_bps:
            raise ValueError("received audio bitrate changed")

    def _finalize(self, request_id: str) -> dict[str, Any]:
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute(
                "SELECT * FROM music_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            receipt = db.execute(
                "SELECT * FROM music_receipts WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None or receipt is None or request["remote_started_at"] is None:
                raise ValueError("no durable receipt")
            path = self._audio_path(request_id, receipt["container"])
            staged = self._staged_path(request_id, receipt["container"])
            if request["provider_request_id"] != receipt["provider_request_id"]:
                raise ValueError("receipt provider request identity differs")
            if staged.exists() or staged.is_symlink():
                self._verify_audio(staged, receipt)
            if path.exists() or path.is_symlink():
                self._verify_audio(path, receipt)
            elif staged.is_file():
                os.link(staged, path)
                self._sync_directory(path.parent)
            else:
                raise ValueError("received audio bytes missing")
            existing = db.execute(
                "SELECT * FROM music_outputs WHERE request_id = ?", (request_id,)
            ).fetchone()
            if existing is not None and (
                existing["sha256"] != receipt["sha256"] or existing["relative_path"] != path.name
            ):
                raise ValueError("output mapping differs from receipt")
            if existing is None:
                db.execute(
                    "INSERT INTO music_outputs (request_id, blind_id, relative_path, sha256, "
                    "created_at) VALUES (?, ?, ?, ?, ?)",
                    (request_id, "mb_" + uuid4().hex, path.name, receipt["sha256"], now()),
                )
            db.execute(
                "UPDATE music_requests SET status = 'succeeded', updated_at = ? "
                "WHERE request_id = ?",
                (now(), request_id),
            )
            output = db.execute(
                "SELECT * FROM music_outputs WHERE request_id = ?", (request_id,)
            ).fetchone()
            db.commit()
        if staged.is_file() and not staged.is_symlink():
            try:
                staged.unlink()
                self._sync_directory(staged.parent)
            except OSError:
                pass  # A later provider-free reconcile can remove this verified copy.
        assert output is not None
        return dict(output)

    def reconcile(self, request_id: str) -> dict[str, Any]:
        with closing(self.database.connect()) as db:
            receipt = db.execute(
                "SELECT 1 FROM music_receipts WHERE request_id = ?", (request_id,)
            ).fetchone()
        if receipt is None:
            return {
                "request_id": request_id,
                "action": "provider_side_reconciliation_required",
                "reason": "no immutable receipt for returned audio",
            }
        try:
            output = self._finalize(request_id)
        except (OSError, ValueError) as exc:
            return {
                "request_id": request_id,
                "action": "provider_side_reconciliation_required",
                "reason": str(exc),
            }
        return {
            "request_id": request_id,
            "status": "succeeded",
            "blind_id": output["blind_id"],
            "action": "reconciled",
        }

    def run(self, item: PlannedMusicRequest, provider: MusicProvider) -> dict[str, Any]:
        if (item.provider, item.model) != (provider.provider, provider.model):
            raise ValueError("provider identity differs from plan")
        if item.input_fingerprint != fingerprint(
            item.canonical_spec, item.provider, item.model, item.translated_request
        ):
            raise ValueError("plan fingerprint changed")
        row = self.prepare(item)
        request_id = row["request_id"]
        if row["status"] == "succeeded":
            output = self._finalize(request_id)
            return {
                "request_id": request_id,
                "status": "succeeded",
                "blind_id": output["blind_id"],
                "action": "reused",
            }
        if row["status"] == "terminal_failure":
            return {
                "request_id": request_id,
                "status": row["status"],
                "action": "new_attempt_required",
            }
        if row["remote_started_at"] is not None:
            return {
                "request_id": request_id,
                "status": row["status"],
                "action": "manual_reconciliation_required",
            }
        started = False
        identity_recorded = False

        def remote_start() -> None:
            nonlocal started
            if started:
                raise ValueError("remote-start callback invoked twice")
            self._transition(request_id, "remote_started")
            started = True

        def record_identity(provider_request_id: str) -> None:
            nonlocal identity_recorded
            if identity_recorded:
                raise ValueError("provider identity callback invoked twice")
            self._record_provider_identity(request_id, provider_request_id)
            identity_recorded = True

        try:
            generate_with_identity = getattr(provider, "generate_with_identity", None)
            if generate_with_identity is None:
                result = provider.generate(
                    item.canonical_spec, item.translated_request, remote_start
                )
            else:
                result = generate_with_identity(
                    item.canonical_spec, item.translated_request, remote_start, record_identity
                )
        except MusicFailure as exc:
            status = exc.outcome if started else "retryable_failure"
            self._transition(
                request_id,
                status,
                category="provider" if started else "local_preflight",
                reason=str(exc),
                provider_request_id=exc.provider_request_id if started else None,
            )
            return {"request_id": request_id, "status": status, "action": "recorded"}
        except Exception as exc:
            status = "ambiguous" if started else "retryable_failure"
            reason = "unexpected Vertex music error" if provider.provider == "google" else str(exc)
            self._transition(request_id, status, category="unexpected", reason=reason)
            return {"request_id": request_id, "status": status, "action": "recorded"}
        if not started:
            self._transition(
                request_id,
                "terminal_failure",
                category="provider_contract",
                reason="provider returned without remote-start callback",
            )
            return {"request_id": request_id, "status": "terminal_failure", "action": "recorded"}
        return self._finish_result(request_id, result, provider.provider)

    def provider_resume(self, request_id: str, provider: MusicProvider) -> dict[str, Any]:
        """Retrieve only an existing remote interaction; never create another one."""
        row = self.request(request_id)
        if (row["provider"], row["model"]) != (provider.provider, provider.model):
            raise ValueError("provider identity differs from stored request")
        with closing(self.database.connect()) as db:
            receipt = db.execute(
                "SELECT 1 FROM music_receipts WHERE request_id = ?", (request_id,)
            ).fetchone()
        if receipt is not None:
            return self.reconcile(request_id)
        if row["status"] == "terminal_failure":
            return {
                "request_id": request_id,
                "status": row["status"],
                "action": "new_attempt_required",
            }
        if (
            row["remote_started_at"] is None
            or row["status"] not in {"remote_started", "ambiguous"}
            or not row["provider_request_id"]
        ):
            return {
                "request_id": request_id,
                "status": row["status"],
                "action": "known_provider_identity_required",
            }
        retrieve = getattr(provider, "retrieve", None)
        if retrieve is None:
            raise ValueError("provider does not support existing-interaction retrieval")
        try:
            result = retrieve(
                row["provider_request_id"], json.loads(row["translated_request_json"])
            )
        except MusicFailure as exc:
            if exc.outcome == "retryable_failure":
                return {
                    "request_id": request_id,
                    "status": row["status"],
                    "action": "local_preflight_failure",
                }
            if row["status"] == "remote_started":
                self._transition(
                    request_id,
                    exc.outcome,
                    category="provider_retrieval",
                    reason=str(exc),
                    provider_request_id=row["provider_request_id"],
                )
            elif exc.outcome == "terminal_failure":
                self._transition(
                    request_id,
                    "terminal_failure",
                    category="provider_retrieval",
                    reason=str(exc),
                    provider_request_id=row["provider_request_id"],
                )
            return {
                "request_id": request_id,
                "status": exc.outcome,
                "action": "existing_interaction_not_complete",
            }
        except Exception:
            if row["status"] == "remote_started":
                self._transition(
                    request_id,
                    "ambiguous",
                    category="provider_retrieval",
                    reason="unexpected provider retrieval error",
                )
            return {"request_id": request_id, "status": "ambiguous", "action": "retrieval_failed"}
        return self._finish_result(request_id, result, provider.provider)

    def _finish_result(
        self, request_id: str, result: MusicResult, provider_name: str
    ) -> dict[str, Any]:
        try:
            stored = self.request(request_id)
            if stored["provider_request_id"] not in (None, result.provider_request_id):
                raise ValueError("returned provider identity differs from stored interaction")
            info = inspect_audio(result.audio_bytes, result.mime_type)
            if result.container not in (None, info.container) or result.codec not in (
                None,
                info.codec,
            ):
                raise ValueError("declared audio format differs from decoded audio")
        except ValueError as exc:
            status = "ambiguous" if provider_name == "google" else "terminal_failure"
            self._transition(
                request_id,
                status,
                category="invalid_output",
                reason=str(exc),
                provider_request_id=(
                    result.provider_request_id if stored["provider_request_id"] is None else None
                ),
            )
            return {"request_id": request_id, "status": status, "action": "recorded"}
        try:
            staged = self._staged_path(request_id, info.container)
            if staged.exists() or staged.is_symlink():
                if staged.is_symlink() or staged.read_bytes() != result.audio_bytes:
                    raise ValueError("staged bytes differ from retrieved provider audio")
            else:
                self._write_audio(request_id, result.audio_bytes, info.container)
            self._record_receipt(request_id, result, info.duration_seconds, info.codec)
            output = self._finalize(request_id)
            return {
                "request_id": request_id,
                "status": "succeeded",
                "blind_id": output["blind_id"],
                "action": "generated",
            }
        except Exception as exc:
            return {
                "request_id": request_id,
                "status": "remote_started",
                "error": (
                    "local Vertex music finalization error"
                    if provider_name == "google"
                    else str(exc)
                ),
                "action": "local_reconciliation_required",
            }

    def _record_receipt(
        self, request_id: str, result: MusicResult, duration: float, codec: str
    ) -> None:
        info = inspect_audio(result.audio_bytes, result.mime_type)
        if (duration, codec) != (info.duration_seconds, info.codec):
            raise ValueError("receipt metadata differs from original audio")
        staged = self._staged_path(request_id, info.container)
        if not staged.is_file() or staged.is_symlink():
            raise ValueError("durable staged audio missing")
        staged_bytes = staged.read_bytes()
        if (
            len(staged_bytes) != len(result.audio_bytes)
            or sha256(staged_bytes).digest() != sha256(result.audio_bytes).digest()
            or inspect_audio(staged_bytes, result.mime_type) != info
        ):
            raise ValueError("staged audio differs from returned bytes")
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO music_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    result.provider_request_id,
                    sha256(result.audio_bytes).hexdigest(),
                    len(result.audio_bytes),
                    duration,
                    result.mime_type,
                    info.container,
                    codec,
                    json.dumps(result.usage) if result.usage is not None else None,
                    result.actual_cost_amount,
                    result.cost_currency,
                    result.pricing_policy,
                    json.dumps(result.rights_evidence)
                    if result.rights_evidence is not None
                    else None,
                    json.dumps(
                        {
                            **result.response_metadata,
                            "audio_sample_rate_hz": info.sample_rate_hz,
                            "audio_bitrate_bps": info.bitrate_bps,
                        },
                        sort_keys=True,
                    ),
                    now(),
                ),
            )
            db.execute(
                "UPDATE music_requests SET provider_request_id = ?, updated_at = ? "
                "WHERE request_id = ?",
                (result.provider_request_id, now(), request_id),
            )
            db.commit()

    def status(self) -> list[dict[str, Any]]:
        with closing(self.database.connect()) as db:
            rows = db.execute(
                "SELECT r.*, o.blind_id, o.rights_status, o.approval_status, "
                "p.duration_seconds, p.sha256, p.byte_count, p.mime_type, p.container, p.codec, "
                "p.usage_json, p.actual_cost_amount, p.cost_currency, p.pricing_policy, "
                "p.rights_evidence_json, p.response_metadata_json, p.received_at "
                "FROM music_requests r LEFT JOIN music_receipts p USING (request_id) "
                "LEFT JOIN music_outputs o USING (request_id) ORDER BY r.created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def review_export(self) -> list[dict[str, Any]]:
        return [
            {
                "blind_id": row["blind_id"],
                "brief_id": row["brief_id"],
                "attempt": row["attempt"],
                "duration_seconds": row["duration_seconds"],
                "audio_sha256": row["sha256"],
            }
            for row in self.status()
            if row["blind_id"]
        ]

    def review(self, review: MusicReview) -> str:
        review_id = str(uuid4())
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO music_reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    review.blind_id,
                    review.reviewer,
                    json.dumps(review.scores),
                    review.evidence,
                    json.dumps(review.hard_failures),
                    now(),
                ),
            )
            if review.hard_failures:
                self._invalidate(db, review.blind_id, "human review hard failure")
            db.commit()
        return review_id

    def review_report(self, rubric: MusicRubric) -> list[dict[str, Any]]:
        with closing(self.database.connect()) as db:
            rows = db.execute(
                "SELECT o.blind_id, v.reviewer, v.scores_json, "
                "v.hard_failures_json, v.evidence FROM music_outputs o "
                "LEFT JOIN music_reviews v USING (blind_id) "
                "ORDER BY o.blind_id, v.created_at"
            ).fetchall()
        report = []
        for row in rows:
            scores = json.loads(row["scores_json"]) if row["scores_json"] else None
            report.append(
                {
                    "blind_id": row["blind_id"],
                    "reviewer": row["reviewer"],
                    "weighted_score": rubric.score(scores) if scores else None,
                    "hard_failures": json.loads(row["hard_failures_json"])
                    if row["hard_failures_json"]
                    else [],
                    "evidence": row["evidence"],
                }
            )
        return report

    @staticmethod
    def _record_evaluation(
        db: sqlite3.Connection,
        kind: str,
        subject_type: str,
        subject_id: str,
        subject_sha256: str,
        status: str,
        evidence: dict[str, Any],
        thresholds: dict[str, Any],
    ) -> dict[str, Any]:
        policy_id, version, evaluator = POLICIES[kind]
        record = {
            "evaluation_id": str(uuid4()),
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_sha256": subject_sha256,
            "policy_id": policy_id,
            "policy_version": version,
            "evaluator": evaluator,
            "evaluator_type": "machine",
            "status": status,
            "evidence_json": json.dumps(evidence, sort_keys=True),
            "thresholds_json": json.dumps(thresholds, sort_keys=True),
            "created_at": now(),
        }
        db.execute(
            "INSERT INTO music_policy_evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(record.values()),
        )
        return record

    @staticmethod
    def _latest_evaluation(
        db: sqlite3.Connection, kind: str, subject_id: str, subject_sha256: str
    ) -> sqlite3.Row | None:
        policy_id, version, _ = POLICIES[kind]
        row: sqlite3.Row | None = db.execute(
            "SELECT * FROM music_policy_evaluations WHERE subject_id = ? "
            "AND subject_sha256 = ? AND policy_id = ? AND policy_version = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (subject_id, subject_sha256, policy_id, version),
        ).fetchone()
        return row

    @staticmethod
    def _invalidate(db: sqlite3.Connection, blind_id: str, reason: str) -> None:
        row = db.execute(
            "SELECT approval_status FROM music_outputs WHERE blind_id = ?", (blind_id,)
        ).fetchone()
        if row is not None and row["approval_status"] == "approved":
            db.execute(
                "UPDATE music_outputs SET approval_status = 'pending' WHERE blind_id = ?",
                (blind_id,),
            )
            db.execute(
                "INSERT INTO music_decisions "
                "(decision_id, blind_id, decision_type, status, actor, evidence, created_at, "
                "actor_type) VALUES (?, ?, 'approval', 'pending', ?, ?, ?, 'machine')",
                (str(uuid4()), blind_id, "automated_release_policy_v1", reason, now()),
            )

    def policy_status(self, blind_id: str | None = None) -> list[dict[str, Any]]:
        with closing(self.database.connect()) as db:
            if blind_id is None:
                rows = db.execute(
                    "SELECT * FROM music_policy_evaluations ORDER BY rowid"
                ).fetchall()
            else:
                output = self._output(db, blind_id)
                lyrics = CanonicalMusicSpec.model_validate_json(
                    output["canonical_spec_json"]
                ).lyrics
                rows = db.execute(
                    "SELECT * FROM music_policy_evaluations WHERE subject_id = ? "
                    "OR (subject_type = 'timing' AND "
                    "substr(subject_id, 1, length(?) + 1) = ? || ':') "
                    "OR (subject_type = 'lyrics' AND subject_id = ? AND subject_sha256 = ?) "
                    "ORDER BY rowid",
                    (blind_id, blind_id, blind_id, lyrics.id, lyric_hash(lyrics)),
                ).fetchall()
        return [dict(row) for row in rows]

    def evaluate_lyrics(self, brief: MusicBrief, lyrics: LyricCandidate) -> dict[str, Any]:
        exact_hash = lyric_hash(lyrics)
        text = lyrics.text().lower()
        all_lyric_fields = json.dumps(lyrics.model_dump(mode="json")).lower()
        thresholds: dict[str, Any] = {
            "brief_id": "colors_red_v1",
            "max_words": 80,
            "max_lines": 8,
            "required_examples": ["apple", "ball"],
            "forbidden_names": ["disney", "taylor swift", "cocomelon"],
            "forbidden_safety_words": ["kill", "gun", "knife", "hate"],
        }
        checks = {
            "configured_brief": brief.id == thresholds["brief_id"],
            "brief_association": lyrics.brief_id == brief.id,
            "objective": brief.objective.strip().lower() == "red is a color.",
            "teaching_phrase": bool(re.search(r"\bred is a colo[u]?r\b", text)),
            "red_apple": bool(re.search(r"\bred apple\b|\bapple[^\n]*\bred\b", text)),
            "red_ball": bool(re.search(r"\bred ball\b|\bball[^\n]*\bred\b", text)),
            "no_detected_contradiction": not bool(
                re.search(r"\bred is not a colo[u]?r\b|\bred is (?:blue|green|yellow)\b", text)
            ),
            "scope": len(lyrics.lines) <= 8
            and len(re.findall(r"\b[\w']+\b", text)) <= 80
            and all(line.section in brief.sections for line in lyrics.lines),
            "no_configured_imitation": not any(
                name in all_lyric_fields for name in thresholds["forbidden_names"]
            ),
            "no_forbidden_safety_words": not any(
                re.search(r"\b" + word + r"\b", text)
                for word in thresholds["forbidden_safety_words"]
            ),
        }
        evidence = {
            "checks": checks,
            "brief_sha256": json_hash(brief.model_dump(mode="json")),
            "lyric_id": lyrics.id,
            "lyric_sha256": exact_hash,
            "word_count": len(re.findall(r"\b[\w']+\b", text)),
            "line_count": len(lyrics.lines),
            "scope_note": "Deterministic text checks; no semantic or sung-word claim",
        }
        status = "pass" if all(checks.values()) else "fail"
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._record_evaluation(
                db, "lyrics", "lyrics", lyrics.id, exact_hash, status, evidence, thresholds
            )
            if status != "pass":
                outputs = db.execute(
                    "SELECT o.blind_id, r.canonical_spec_json FROM music_outputs o "
                    "JOIN music_requests r USING (request_id) WHERE r.lyric_id = ? "
                    "AND o.approval_status = 'approved'",
                    (lyrics.id,),
                ).fetchall()
                for output in outputs:
                    candidate = CanonicalMusicSpec.model_validate_json(
                        output["canonical_spec_json"]
                    ).lyrics
                    if lyric_hash(candidate) == exact_hash:
                        self._invalidate(
                            db, output["blind_id"], f"exact lyrics policy failed: {exact_hash}"
                        )
            db.commit()
        return record

    def _output(self, db: sqlite3.Connection, blind_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = db.execute(
            "SELECT o.*, r.canonical_spec_json, r.provider, r.model, "
            "p.duration_seconds, p.sha256 AS receipt_sha256, p.byte_count, "
            "p.mime_type, p.container, p.codec, p.response_metadata_json FROM music_outputs o "
            "JOIN music_requests r USING (request_id) "
            "JOIN music_receipts p USING (request_id) WHERE o.blind_id = ?",
            (blind_id,),
        ).fetchone()
        if row is None:
            raise KeyError(blind_id)
        return row

    def evaluate_rights(self, blind_id: str, configured_evidence: dict[str, Any]) -> dict[str, Any]:
        required = (
            "provider",
            "model",
            "tier",
            "account_id",
            "terms_version",
            "terms_date",
            "terms_source",
            "terms_snapshot",
            "terms_sha256",
            "usage_mode",
        )
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            output = self._output(db, blind_id)
            checks = {
                "complete": all(
                    isinstance(configured_evidence.get(key), str) and bool(configured_evidence[key])
                    for key in required
                ),
                "provider_matches": configured_evidence.get("provider") == output["provider"],
                "model_matches": configured_evidence.get("model") == output["model"],
                "commercial_mode": configured_evidence.get("usage_mode") == "commercial",
                "commercial_grant": configured_evidence.get("commercial_use_allowed") is True,
                "retained_source": isinstance(configured_evidence.get("terms_source"), str)
                and bool(configured_evidence.get("terms_source")),
                "snapshot_hash_matches": isinstance(configured_evidence.get("terms_snapshot"), str)
                and configured_evidence.get("terms_sha256")
                == sha256(str(configured_evidence.get("terms_snapshot", "")).encode()).hexdigest(),
            }
            try:
                checks["valid_terms_date"] = (
                    date.fromisoformat(str(configured_evidence.get("terms_date", "")))
                    <= date.today()
                )
            except ValueError:
                checks["valid_terms_date"] = False
            status = "pass" if all(checks.values()) else "blocked"
            record = self._record_evaluation(
                db,
                "rights",
                "rights",
                blind_id,
                output["sha256"],
                status,
                {
                    "checks": checks,
                    "configuration": configured_evidence,
                    "request_id": output["request_id"],
                },
                {"required_fields": [*required, "commercial_use_allowed"]},
            )
            rights_status = "commercial_use_confirmed" if status == "pass" else "unknown"
            db.execute(
                "UPDATE music_outputs SET rights_status = ? WHERE blind_id = ?",
                (rights_status, blind_id),
            )
            db.execute(
                "INSERT INTO music_decisions (decision_id, blind_id, decision_type, status, "
                "actor, evidence, created_at, actor_type) "
                "VALUES (?, ?, 'rights', ?, ?, ?, ?, 'machine')",
                (
                    str(uuid4()),
                    blind_id,
                    rights_status,
                    POLICIES["rights"][2],
                    f"policy evaluation {record['evaluation_id']}",
                    now(),
                ),
            )
            if status != "pass":
                self._invalidate(db, blind_id, "rights policy blocked: missing or adverse evidence")
            db.commit()
        return record

    def evaluate_qa(self, blind_id: str, checks: dict[str, Any]) -> dict[str, Any]:
        required = (
            "lyric_adherence",
            "educational_correctness",
            "teaching_intelligibility",
            "preschool_safety",
            "beat_usable",
            "production_fit",
            "artifact_free",
        )
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            output = self._output(db, blind_id)
            spec = CanonicalMusicSpec.model_validate_json(output["canonical_spec_json"])
            objective: dict[str, Any] = {}
            try:
                self._verify_audio(
                    self._audio_path(output["request_id"], output["container"]), output
                )
                objective["decodes_and_matches_receipt"] = True
            except (OSError, ValueError):
                objective["decodes_and_matches_receipt"] = False
            objective["duration_in_scope"] = (
                0 < output["duration_seconds"] <= spec.brief.maximum_duration_seconds
            )
            supplied_valid = all(
                isinstance(checks.get(key), dict)
                and type(checks[key].get("passed")) is bool
                and isinstance(checks[key].get("source"), str)
                and bool(checks[key]["source"])
                for key in required
            )
            no_hard_failure = not bool(checks.get("hard_failures"))
            status = (
                "pass"
                if (
                    all(objective.values())
                    and supplied_valid
                    and no_hard_failure
                    and all(checks[key]["passed"] for key in required)
                )
                else "fail"
            )
            record = self._record_evaluation(
                db,
                "qa",
                "audio",
                blind_id,
                output["sha256"],
                status,
                {
                    "objective_checks": objective,
                    "supplied_checks": checks,
                    "required_checks_valid": supplied_valid,
                    "request_id": output["request_id"],
                },
                {
                    "required_checks": required,
                    "maximum_duration_seconds": spec.brief.maximum_duration_seconds,
                    "hard_failures_allowed": 0,
                },
            )
            if status != "pass":
                self._invalidate(db, blind_id, "music QA policy failed")
            db.commit()
        return record

    def _approval_blockers(
        self, db: sqlite3.Connection, blind_id: str, *, automatic: bool = False
    ) -> list[str]:
        output = self._output(db, blind_id)
        spec = CanonicalMusicSpec.model_validate_json(output["canonical_spec_json"])
        blockers = []
        if output["rights_status"] != "commercial_use_confirmed":
            blockers.append("rights not confirmed")
        rights = self._latest_evaluation(db, "rights", blind_id, output["sha256"])
        if rights is not None and rights["status"] != "pass":
            blockers.append("rights policy not passing")
        if automatic and (rights is None or rights["status"] != "pass"):
            blockers.append("automated rights policy not passing")
        latest_rights_decision = db.execute(
            "SELECT actor_type, status FROM music_decisions WHERE blind_id = ? "
            "AND decision_type = 'rights' ORDER BY rowid DESC LIMIT 1",
            (blind_id,),
        ).fetchone()
        if automatic and (
            latest_rights_decision is None
            or latest_rights_decision["actor_type"] != "machine"
            or latest_rights_decision["status"] != "commercial_use_confirmed"
        ):
            blockers.append("latest rights decision is not policy confirmation")
        lyrics = self._latest_evaluation(db, "lyrics", spec.lyrics.id, lyric_hash(spec.lyrics))
        brief_hash = json_hash(spec.brief.model_dump(mode="json"))
        manual_lyrics = db.execute(
            "SELECT status FROM music_lyric_decisions WHERE lyric_id = ? AND lyric_sha256 = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (spec.lyrics.id, lyric_hash(spec.lyrics)),
        ).fetchone()
        if (lyrics is None or lyrics["status"] != "pass") and (
            manual_lyrics is None or manual_lyrics["status"] != "approved"
        ):
            blockers.append("exact lyrics not approved by policy or manual intervention")
        if lyrics is not None and lyrics["status"] != "pass":
            blockers.append("latest exact lyrics policy failed")
        if (
            lyrics is not None
            and json.loads(lyrics["evidence_json"]).get("brief_sha256") != brief_hash
        ):
            blockers.append("lyrics policy brief identity differs")
        if automatic and (lyrics is None or lyrics["status"] != "pass"):
            blockers.append("automated lyrics policy not passing")
        if manual_lyrics is not None and manual_lyrics["status"] == "rejected":
            blockers.append("manual exact lyrics rejection")
        qa = self._latest_evaluation(db, "qa", blind_id, output["sha256"])
        if qa is None or qa["status"] != "pass":
            blockers.append("music QA policy not passing")
        reviews = db.execute(
            "SELECT hard_failures_json FROM music_reviews WHERE blind_id = ?", (blind_id,)
        ).fetchall()
        if any(json.loads(row["hard_failures_json"]) for row in reviews):
            blockers.append("human review hard failure")
        if output["approval_status"] == "rejected":
            blockers.append("manual rejection veto")
        try:
            self._verify_audio(self._audio_path(output["request_id"], output["container"]), output)
        except (OSError, ValueError):
            blockers.append("audio integrity failure")
        return blockers

    def evaluate_approval(self, blind_id: str) -> dict[str, Any]:
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            output = self._output(db, blind_id)
            blockers = self._approval_blockers(db, blind_id, automatic=True)
            status = "pass" if not blockers else "blocked"
            exact_lyrics = CanonicalMusicSpec.model_validate_json(
                output["canonical_spec_json"]
            ).lyrics
            prerequisites = {
                "lyrics": self._latest_evaluation(
                    db, "lyrics", exact_lyrics.id, lyric_hash(exact_lyrics)
                ),
                "rights": self._latest_evaluation(db, "rights", blind_id, output["sha256"]),
                "qa": self._latest_evaluation(db, "qa", blind_id, output["sha256"]),
            }
            evidence = {
                "blockers": blockers,
                "request_id": output["request_id"],
                "audio_sha256": output["sha256"],
                "lyric_sha256": lyric_hash(exact_lyrics),
                "rights_status": output["rights_status"],
                "prerequisite_evaluations": {
                    kind: {"evaluation_id": row["evaluation_id"], "status": row["status"]}
                    if row is not None
                    else None
                    for kind, row in prerequisites.items()
                },
            }
            record = self._record_evaluation(
                db,
                "approval",
                "audio",
                blind_id,
                output["sha256"],
                status,
                evidence,
                {
                    "lyrics_pass": True,
                    "rights_confirmed": True,
                    "qa_pass": True,
                    "hard_failures_allowed": 0,
                    "human_rejection_veto": True,
                },
            )
            if status == "pass":
                db.execute(
                    "INSERT INTO music_decisions (decision_id, blind_id, decision_type, status, "
                    "actor, evidence, created_at, actor_type) "
                    "VALUES (?, ?, 'approval', 'approved', ?, ?, ?, 'machine')",
                    (
                        str(uuid4()),
                        blind_id,
                        POLICIES["approval"][2],
                        f"policy evaluation {record['evaluation_id']}",
                        now(),
                    ),
                )
                db.execute(
                    "UPDATE music_outputs SET approval_status = 'approved' WHERE blind_id = ?",
                    (blind_id,),
                )
            else:
                self._invalidate(db, blind_id, "approval policy blocked: " + "; ".join(blockers))
            db.commit()
        return record

    def evaluate_timing(self, blind_id: str, version: int) -> dict[str, Any]:
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            output = self._output(db, blind_id)
            row = db.execute(
                "SELECT analysis_json FROM music_timing WHERE blind_id = ? AND version = ?",
                (blind_id, version),
            ).fetchone()
            if row is None:
                raise KeyError((blind_id, version))
            analysis = TimingAnalysis.model_validate_json(row["analysis_json"])
            checks = {
                "audio_sha_matches": analysis.audio_sha256 == output["sha256"],
                "duration_matches": abs(analysis.duration_seconds - output["duration_seconds"])
                < 0.001,
                "beats_present": bool(analysis.beat_seconds),
                "downbeats_present": bool(analysis.downbeat_seconds),
                "lyric_lines_present": bool(analysis.lyric_lines),
                "sections_present": bool(analysis.sections),
                "words_present": bool(analysis.words),
                "ordered": all(
                    tuple(sorted(items)) == items
                    for items in (analysis.beat_seconds, analysis.downbeat_seconds)
                ),
            }
            status = "pass" if all(checks.values()) else "fail"
            record = self._record_evaluation(
                db,
                "timing",
                "timing",
                f"{blind_id}:{version}",
                output["sha256"],
                status,
                {"checks": checks, "analysis_sha256": json_hash(analysis.model_dump(mode="json"))},
                {
                    "required_fields": [
                        "beat_seconds",
                        "downbeat_seconds",
                        "sections",
                        "lyric_lines",
                        "words",
                    ],
                    "duration_tolerance_seconds": 0.001,
                },
            )
            if status == "pass":
                db.execute(
                    "INSERT INTO music_timing_decisions (decision_id, blind_id, version, "
                    "status, actor, evidence, created_at, actor_type) "
                    "VALUES (?, ?, ?, 'approved', ?, ?, ?, 'machine')",
                    (
                        str(uuid4()),
                        blind_id,
                        version,
                        POLICIES["timing"][2],
                        f"policy evaluation {record['evaluation_id']}",
                        now(),
                    ),
                )
            db.commit()
        return record

    def decision(
        self, blind_id: str, decision_type: str, status: str, actor: str, evidence: str
    ) -> str:
        permitted = {
            "rights": {"unknown", "commercial_use_confirmed", "restricted"},
            "approval": {"pending", "approved", "rejected"},
        }
        if status not in permitted.get(decision_type, set()) or not actor or not evidence:
            raise ValueError("invalid or unsupported decision")
        decision_id = str(uuid4())
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            if decision_type == "approval" and status == "approved":
                blockers = self._approval_blockers(db, blind_id)
                if blockers:
                    raise ValueError("music approval blocked: " + "; ".join(blockers))
            db.execute(
                "INSERT INTO music_decisions (decision_id, blind_id, decision_type, status, "
                "actor, evidence, created_at, actor_type) VALUES (?, ?, ?, ?, ?, ?, ?, 'human')",
                (decision_id, blind_id, decision_type, status, actor, evidence, now()),
            )
            column = "rights_status" if decision_type == "rights" else "approval_status"
            db.execute(
                f"UPDATE music_outputs SET {column} = ? WHERE blind_id = ?", (status, blind_id)
            )
            if decision_type == "rights" and status != "commercial_use_confirmed":
                self._invalidate(db, blind_id, "rights no longer confirmed")
            db.commit()
        return decision_id

    def lyric_decision(self, lyrics: LyricCandidate, status: str, actor: str, evidence: str) -> str:
        if status not in {"approved", "rejected"} or not actor or not evidence:
            raise ValueError("invalid lyric decision")
        decision_id = str(uuid4())
        exact_hash = lyric_hash(lyrics)
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO music_lyric_decisions (decision_id, lyric_id, lyric_sha256, "
                "status, actor, evidence, created_at, actor_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'human')",
                (decision_id, lyrics.id, exact_hash, status, actor, evidence, now()),
            )
            if status == "rejected":
                outputs = db.execute(
                    "SELECT o.blind_id, r.canonical_spec_json FROM music_outputs o "
                    "JOIN music_requests r USING (request_id) "
                    "WHERE r.lyric_id = ? AND o.approval_status = 'approved'",
                    (lyrics.id,),
                ).fetchall()
                for output in outputs:
                    candidate = CanonicalMusicSpec.model_validate_json(
                        output["canonical_spec_json"]
                    ).lyrics
                    if lyric_hash(candidate) != exact_hash:
                        continue
                    self._invalidate(
                        db,
                        output["blind_id"],
                        f"exact lyrics rejected: {lyrics.id} {exact_hash}",
                    )
            db.commit()
        return decision_id

    def save_timing(self, blind_id: str, analysis: TimingAnalysis) -> None:
        if analysis.approval != "pending":
            raise ValueError("timing import cannot approve itself")
        with closing(self.database.connect()) as db:
            output = db.execute(
                "SELECT sha256 FROM music_outputs WHERE blind_id = ?", (blind_id,)
            ).fetchone()
            if output is None or output["sha256"] != analysis.audio_sha256:
                raise ValueError("timing analysis audio hash differs from candidate")
            db.execute(
                "INSERT INTO music_timing VALUES (?, ?, ?, ?)",
                (blind_id, analysis.version, analysis.model_dump_json(), now()),
            )
            db.commit()

    def timing_decision(
        self, blind_id: str, version: int, status: str, actor: str, evidence: str
    ) -> str:
        if status not in {"approved", "rejected"} or not actor or not evidence:
            raise ValueError("invalid timing decision")
        decision_id = str(uuid4())
        with closing(self.database.connect()) as db:
            db.execute(
                "INSERT INTO music_timing_decisions (decision_id, blind_id, version, status, "
                "actor, evidence, created_at, actor_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'human')",
                (decision_id, blind_id, version, status, actor, evidence, now()),
            )
            db.commit()
        return decision_id
