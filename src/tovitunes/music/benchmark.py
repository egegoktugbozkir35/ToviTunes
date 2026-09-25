"""Offline planning and durable, fail-closed music execution."""

import json
import os
import wave
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field

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


def inspect_wav(data: bytes) -> float:
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
            return duration
    except (wave.Error, EOFError) as exc:
        raise ValueError("invalid WAV") from exc


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

    def _audio_path(self, request_id: str) -> Path:
        return self.audio_root / f"{request_id}.wav"

    def _write_audio(self, request_id: str, data: bytes) -> Path:
        path = self._audio_path(request_id)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def _finalize(self, request_id: str) -> dict[str, Any]:
        path = self._audio_path(request_id)
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute(
                "SELECT * FROM music_receipts WHERE request_id = ?", (request_id,)
            ).fetchone()
            if receipt is None:
                raise ValueError("no durable receipt")
            if not path.is_file() or path.is_symlink():
                raise ValueError("received audio bytes missing")
            data = path.read_bytes()
            if sha256(data).hexdigest() != receipt["sha256"] or len(data) != receipt["byte_count"]:
                raise ValueError("received audio bytes changed")
            existing = db.execute(
                "SELECT * FROM music_outputs WHERE request_id = ?", (request_id,)
            ).fetchone()
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
        assert output is not None
        return dict(output)

    def reconcile(self, request_id: str) -> dict[str, Any]:
        with closing(self.database.connect()) as db:
            receipt = db.execute(
                "SELECT 1 FROM music_receipts WHERE request_id = ?", (request_id,)
            ).fetchone()
        if receipt is None:
            return {"request_id": request_id, "action": "provider_side_reconciliation_required"}
        output = self._finalize(request_id)
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
        if row["remote_started_at"] is not None:
            return {
                "request_id": request_id,
                "status": row["status"],
                "action": "manual_reconciliation_required",
            }
        if row["status"] == "terminal_failure":
            return {
                "request_id": request_id,
                "status": row["status"],
                "action": "new_attempt_required",
            }
        started = False

        def remote_start() -> None:
            nonlocal started
            if started:
                raise ValueError("remote-start callback invoked twice")
            self._transition(request_id, "remote_started")
            started = True

        try:
            result = provider.generate(item.canonical_spec, item.translated_request, remote_start)
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
            self._transition(request_id, status, category="unexpected", reason=str(exc))
            return {"request_id": request_id, "status": status, "action": "recorded"}
        if not started:
            self._transition(
                request_id,
                "terminal_failure",
                category="provider_contract",
                reason="provider returned without remote-start callback",
            )
            return {"request_id": request_id, "status": "terminal_failure", "action": "recorded"}
        try:
            if result.mime_type != "audio/wav":
                raise ValueError("only validated PCM WAV ingest is implemented")
            duration = inspect_wav(result.audio_bytes)
            self._write_audio(request_id, result.audio_bytes)
            self._record_receipt(request_id, result, duration)
            output = self._finalize(request_id)
            return {
                "request_id": request_id,
                "status": "succeeded",
                "blind_id": output["blind_id"],
                "action": "generated",
            }
        except ValueError as exc:
            self._transition(
                request_id,
                "terminal_failure",
                category="invalid_output",
                reason=str(exc),
                provider_request_id=result.provider_request_id,
            )
            return {"request_id": request_id, "status": "terminal_failure", "action": "recorded"}
        except Exception as exc:
            return {
                "request_id": request_id,
                "status": "remote_started",
                "error": str(exc),
                "action": "local_reconciliation_required",
            }

    def _record_receipt(self, request_id: str, result: MusicResult, duration: float) -> None:
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
                    result.container,
                    result.codec,
                    json.dumps(result.usage) if result.usage is not None else None,
                    result.actual_cost_amount,
                    result.cost_currency,
                    result.pricing_policy,
                    json.dumps(result.rights_evidence)
                    if result.rights_evidence is not None
                    else None,
                    json.dumps(result.response_metadata),
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
                row = db.execute(
                    "SELECT o.rights_status, r.canonical_spec_json FROM music_outputs o "
                    "JOIN music_requests r USING (request_id) WHERE o.blind_id = ?",
                    (blind_id,),
                ).fetchone()
                if row is None or row["rights_status"] != "commercial_use_confirmed":
                    raise ValueError("music approval requires cleared rights")
                lyrics = CanonicalMusicSpec.model_validate_json(row["canonical_spec_json"]).lyrics
                lyric_row = db.execute(
                    "SELECT status FROM music_lyric_decisions WHERE lyric_id = ? "
                    "AND lyric_sha256 = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (lyrics.id, lyric_hash(lyrics)),
                ).fetchone()
                if lyric_row is None or lyric_row["status"] != "approved":
                    raise ValueError("music approval requires approved lyrics")
                reviews = db.execute(
                    "SELECT reviewer, hard_failures_json FROM music_reviews WHERE blind_id = ?",
                    (blind_id,),
                ).fetchall()
                if len(reviews) < 2 or any(
                    json.loads(item["hard_failures_json"]) for item in reviews
                ):
                    raise ValueError("music approval requires two clean listening reviews")
            db.execute(
                "INSERT INTO music_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (decision_id, blind_id, decision_type, status, actor, evidence, now()),
            )
            column = "rights_status" if decision_type == "rights" else "approval_status"
            db.execute(
                f"UPDATE music_outputs SET {column} = ? WHERE blind_id = ?", (status, blind_id)
            )
            if decision_type == "rights" and status != "commercial_use_confirmed":
                db.execute(
                    "UPDATE music_outputs SET approval_status = 'pending' WHERE blind_id = ?",
                    (blind_id,),
                )
                db.execute(
                    "INSERT INTO music_decisions VALUES (?, ?, 'approval', 'pending', "
                    "'system', 'rights no longer confirmed', ?)",
                    (str(uuid4()), blind_id, now()),
                )
            db.commit()
        return decision_id

    def lyric_decision(self, lyrics: LyricCandidate, status: str, actor: str, evidence: str) -> str:
        if status not in {"approved", "rejected"} or not actor or not evidence:
            raise ValueError("invalid lyric decision")
        decision_id = str(uuid4())
        with closing(self.database.connect()) as db:
            db.execute(
                "INSERT INTO music_lyric_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (decision_id, lyrics.id, lyric_hash(lyrics), status, actor, evidence, now()),
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
                "INSERT INTO music_timing_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (decision_id, blind_id, version, status, actor, evidence, now()),
            )
            db.commit()
        return decision_id
