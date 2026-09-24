"""Durable request, output mapping, and human-review records."""

import json
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from tovitunes.benchmark.models import Scorecard
from tovitunes.persistence.db import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class BenchmarkStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def prepare(
        self,
        *,
        benchmark_version: str,
        prompt_version: str,
        pack_revision_id: str,
        brand_revision_id: str,
        case_id: str,
        attempt: int,
        provider: str,
        model: str,
        fingerprint: str,
        canonical_spec: dict[str, Any],
        translated_request: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> dict[str, Any]:
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM visual_benchmark_requests WHERE provider = ? AND model = ? "
                "AND case_id = ? AND attempt = ? AND input_fingerprint = ?",
                (provider, model, case_id, attempt, fingerprint),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return dict(existing)
            request_id = str(uuid4())
            timestamp = _now()
            connection.execute(
                "INSERT INTO visual_benchmark_requests (request_id, benchmark_version, "
                "prompt_version, pack_revision_id, brand_revision_id, case_id, attempt, "
                "provider, model, input_fingerprint, status, canonical_spec_json, "
                "translated_request_json, capabilities_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?, ?, ?)",
                (
                    request_id,
                    benchmark_version,
                    prompt_version,
                    pack_revision_id,
                    brand_revision_id,
                    case_id,
                    attempt,
                    provider,
                    model,
                    fingerprint,
                    json.dumps(canonical_spec, sort_keys=True),
                    json.dumps(translated_request, sort_keys=True),
                    json.dumps(capabilities, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM visual_benchmark_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

    def transition(
        self,
        request_id: str,
        status: Literal[
            "remote_started",
            "succeeded",
            "retryable_failure",
            "terminal_failure",
            "ambiguous",
        ],
        *,
        provider_request_id: str | None = None,
        latency_seconds: float | None = None,
        usage: dict[str, Any] | None = None,
        actual_cost_amount: float | None = None,
        cost_currency: str | None = None,
        pricing_policy: str | None = None,
        response_metadata: dict[str, Any] | None = None,
        error_kind: str | None = None,
        error_reason: str | None = None,
    ) -> None:
        allowed = {
            "prepared": {"remote_started", "retryable_failure", "terminal_failure"},
            "retryable_failure": {"remote_started"},
            "remote_started": {
                "succeeded",
                "retryable_failure",
                "terminal_failure",
                "ambiguous",
            },
        }
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM visual_benchmark_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            if status not in allowed.get(row["status"], set()):
                raise ValueError(
                    f"invalid benchmark request transition: {row['status']} -> {status}"
                )
            connection.execute(
                "UPDATE visual_benchmark_requests SET status = ?, provider_request_id = ?, "
                "latency_seconds = ?, usage_json = ?, actual_cost_amount = ?, "
                "cost_currency = ?, pricing_policy = ?, response_metadata_json = ?, "
                "error_kind = ?, error_reason = ?, updated_at = ? WHERE request_id = ?",
                (
                    status,
                    provider_request_id,
                    latency_seconds,
                    json.dumps(usage, sort_keys=True) if usage is not None else None,
                    actual_cost_amount,
                    cost_currency,
                    pricing_policy,
                    json.dumps(response_metadata, sort_keys=True)
                    if response_metadata is not None
                    else None,
                    error_kind,
                    error_reason,
                    _now(),
                    request_id,
                ),
            )
            connection.commit()

    def record_output(
        self,
        request_id: str,
        artifact_id: str,
        blind_id: str,
        width: int,
        height: int,
        mime_type: str,
    ) -> None:
        with closing(self.database.connect()) as connection:
            connection.execute(
                "INSERT INTO visual_benchmark_outputs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, artifact_id, blind_id, width, height, mime_type, _now()),
            )
            connection.commit()

    def record_review(self, scorecard: Scorecard) -> str:
        review_id = str(uuid4())
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM visual_benchmark_reviews WHERE blind_id = ? ORDER BY reviewed_at",
                (scorecard.blind_id,),
            ).fetchall()
            role_count = sum(row["review_role"] == scorecard.review_role for row in existing)
            role_limit = 2 if scorecard.review_role == "initial" else 1
            if role_count >= role_limit:
                raise ValueError(f"review role limit reached: {scorecard.review_role}")
            if scorecard.review_role == "adjudication":
                initial = [row for row in existing if row["review_role"] == "initial"]
                if len(initial) != 2:
                    raise ValueError("adjudication requires exactly two initial reviews")
                differences = []
                first, second = (json.loads(row["scores_json"]) for row in initial)
                for axis in first:
                    differences.append(abs(first[axis] - second[axis]))
                if not any(value >= 2 for value in differences):
                    raise ValueError("adjudication is allowed only for a 2-point axis difference")
            connection.execute(
                "INSERT INTO visual_benchmark_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    scorecard.blind_id,
                    scorecard.reviewer,
                    scorecard.review_role,
                    scorecard.scores.model_dump_json(),
                    scorecard.notes,
                    scorecard.evidence_note,
                    json.dumps(scorecard.hard_failure_reasons, sort_keys=True),
                    scorecard.reviewed_at.isoformat(),
                ),
            )
            connection.commit()
        return review_id

    def requests(self) -> list[dict[str, Any]]:
        with closing(self.database.connect()) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT r.*, o.artifact_id, o.blind_id, o.width, o.height, "
                    "o.mime_type AS output_mime_type, a.sha256 AS output_sha256, "
                    "a.byte_count AS output_byte_count FROM visual_benchmark_requests r "
                    "LEFT JOIN visual_benchmark_outputs o ON o.request_id = r.request_id "
                    "LEFT JOIN artifact_versions a ON a.artifact_id = o.artifact_id "
                    "ORDER BY r.provider, r.model, r.case_id, r.attempt"
                )
            ]

    def reviews(self, blind_id: str) -> tuple[Scorecard, ...]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM visual_benchmark_reviews WHERE blind_id = ? ORDER BY reviewed_at",
                (blind_id,),
            ).fetchall()
        return tuple(
            Scorecard(
                blind_id=row["blind_id"],
                reviewer=row["reviewer"],
                review_role=row["review_role"],
                scores=json.loads(row["scores_json"]),
                notes=row["notes"],
                evidence_note=row["evidence_note"],
                hard_failure_reasons=tuple(json.loads(row["hard_failure_reasons_json"])),
                reviewed_at=datetime.fromisoformat(row["reviewed_at"]),
            )
            for row in rows
        )
