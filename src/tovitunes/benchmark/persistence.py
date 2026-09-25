"""Durable request, output mapping, and human-review records."""

import json
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from PIL import Image

from tovitunes.artifacts.store import AssetStore
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
            if (
                status == "retryable_failure"
                and connection.execute(
                    "SELECT 1 FROM visual_benchmark_receipts WHERE request_id = ?", (request_id,)
                ).fetchone()
                is not None
            ):
                raise ValueError("a received provider result cannot become safely retryable")
            if status == "remote_started":
                connection.execute(
                    "UPDATE visual_benchmark_requests SET status = 'remote_started', "
                    "provider_request_id = NULL, latency_seconds = NULL, "
                    "usage_json = NULL, actual_cost_amount = NULL, cost_currency = NULL, "
                    "pricing_policy = NULL, response_metadata_json = NULL, "
                    "error_kind = NULL, error_reason = NULL, updated_at = ? "
                    "WHERE request_id = ?",
                    (_now(), request_id),
                )
                connection.commit()
                return
            connection.execute(
                "UPDATE visual_benchmark_requests SET status = ?, "
                "provider_request_id = COALESCE(?, provider_request_id), "
                "latency_seconds = COALESCE(?, latency_seconds), "
                "usage_json = COALESCE(?, usage_json), "
                "actual_cost_amount = COALESCE(?, actual_cost_amount), "
                "cost_currency = COALESCE(?, cost_currency), "
                "pricing_policy = COALESCE(?, pricing_policy), "
                "response_metadata_json = COALESCE(?, response_metadata_json), "
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

    def get_request(self, request_id: str) -> dict[str, Any]:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM visual_benchmark_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return dict(row)

    def receipt(self, request_id: str) -> dict[str, Any] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM visual_benchmark_receipts WHERE request_id = ?", (request_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def record_receipt(
        self,
        request_id: str,
        *,
        provider_request_id: str | None,
        returned_sha256: str,
        returned_byte_count: int,
        mime_type: str,
        latency_seconds: float,
        usage: dict[str, Any] | None,
        actual_cost_amount: float | None,
        cost_currency: str | None,
        pricing_policy: str | None,
        response_metadata: dict[str, Any],
    ) -> None:
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM visual_benchmark_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None or row["status"] != "remote_started":
                raise ValueError("receipt requires a started remote request")
            connection.execute(
                "INSERT INTO visual_benchmark_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    provider_request_id,
                    returned_sha256,
                    returned_byte_count,
                    mime_type,
                    latency_seconds,
                    json.dumps(usage, sort_keys=True) if usage is not None else None,
                    actual_cost_amount,
                    cost_currency,
                    pricing_policy,
                    json.dumps(response_metadata, sort_keys=True),
                    _now(),
                ),
            )
            connection.execute(
                "UPDATE visual_benchmark_requests SET provider_request_id = ?, "
                "latency_seconds = ?, usage_json = ?, actual_cost_amount = ?, "
                "cost_currency = ?, pricing_policy = ?, response_metadata_json = ?, "
                "updated_at = ? WHERE request_id = ?",
                (
                    provider_request_id,
                    latency_seconds,
                    json.dumps(usage, sort_keys=True) if usage is not None else None,
                    actual_cost_amount,
                    cost_currency,
                    pricing_policy,
                    json.dumps(response_metadata, sort_keys=True),
                    _now(),
                    request_id,
                ),
            )
            connection.commit()

    def output(self, request_id: str) -> dict[str, Any] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM visual_benchmark_outputs WHERE request_id = ?", (request_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def candidates(self, request_id: str) -> list[str]:
        with closing(self.database.connect()) as connection:
            return [
                row["artifact_id"]
                for row in connection.execute(
                    "SELECT artifact_id FROM artifact_versions WHERE kind = 'benchmark_image' "
                    "AND json_extract(provenance_json, '$.local_request_id') = ?",
                    (request_id,),
                )
            ]

    def validate_output(
        self, request_id: str, artifact_id: str, assets: AssetStore
    ) -> tuple[int, int, str]:
        request = self.get_request(request_id)
        receipt = self.receipt(request_id)
        if receipt is None:
            raise ValueError("provider-result receipt is missing")
        for field in (
            "provider_request_id",
            "latency_seconds",
            "usage_json",
            "actual_cost_amount",
            "cost_currency",
            "pricing_policy",
            "response_metadata_json",
        ):
            if request[field] != receipt[field]:
                raise ValueError(f"request metadata differs from provider receipt: {field}")
        artifact = assets.get(artifact_id)
        spec = json.loads(request["canonical_spec_json"])
        references = spec["references"]
        provenance = artifact.provenance
        if (
            artifact.identity.owner_scope != "brand"
            or artifact.identity.owner_id != request["brand_revision_id"]
            or artifact.identity.kind != "benchmark_image"
            or artifact.sha256 != receipt["returned_sha256"]
            or artifact.byte_count != receipt["returned_byte_count"]
            or artifact.mime_type != receipt["mime_type"]
            or provenance.source_kind != "provider"
            or provenance.local_request_id != request_id
            or provenance.request_id != receipt["provider_request_id"]
            or provenance.provider != request["provider"]
            or provenance.model != request["model"]
            or provenance.prompt_version != request["prompt_version"]
            or tuple(provenance.input_artifact_ids)
            != tuple(ref["artifact_id"] for ref in references)
        ):
            raise ValueError("benchmark artifact identity or receipt differs")
        with closing(self.database.connect()) as connection:
            dependencies = connection.execute(
                "SELECT input_artifact_id, input_sha256, purpose FROM artifact_dependencies "
                "WHERE consumer_artifact_id = ? ORDER BY purpose",
                (artifact_id,),
            ).fetchall()
        expected = sorted(
            (ref["artifact_id"], ref["sha256"], f"benchmark reference {ref['role']}")
            for ref in references
        )
        if sorted(tuple(row) for row in dependencies) != expected:
            raise ValueError("benchmark artifact dependencies differ")
        if not assets.inspect(artifact_id).valid:
            raise ValueError("benchmark artifact file is invalid")
        with Image.open(assets.path_for(artifact_id)) as image:
            image.load()
            width, height = image.size
        return width, height, artifact.mime_type

    def finalize_success(
        self, request_id: str, artifact_id: str, blind_id: str, assets: AssetStore
    ) -> None:
        width, height, mime_type = self.validate_output(request_id, artifact_id, assets)
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT status FROM visual_benchmark_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None or request["status"] not in {
                "remote_started",
                "ambiguous",
                "succeeded",
            }:
                raise ValueError("request is not eligible for success finalization")
            existing = connection.execute(
                "SELECT artifact_id, blind_id, width, height, mime_type "
                "FROM visual_benchmark_outputs WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            expected = (artifact_id, blind_id, width, height, mime_type)
            if existing is None:
                if request["status"] == "succeeded":
                    raise ValueError("succeeded request has no output")
                connection.execute(
                    "INSERT INTO visual_benchmark_outputs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (request_id, *expected, _now()),
                )
            elif tuple(existing) != expected:
                raise ValueError("conflicting benchmark output mapping")
            if request["status"] != "succeeded":
                connection.execute(
                    "UPDATE visual_benchmark_requests SET status = 'succeeded', "
                    "error_kind = NULL, error_reason = NULL, updated_at = ? "
                    "WHERE request_id = ?",
                    (_now(), request_id),
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
