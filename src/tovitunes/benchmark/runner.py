"""Plan, execute, resume, review, and aggregate the visual benchmark."""

import json
import os
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from typing import Any
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict

from tovitunes.artifacts.media import validate_media
from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.benchmark.models import (
    BenchmarkDefinition,
    CanonicalImageSpec,
    Rubric,
    build_spec,
    resolve_reviews,
)
from tovitunes.benchmark.persistence import BenchmarkStore
from tovitunes.benchmark.providers import ImageProvider, ProviderFailure
from tovitunes.domain.artifact import Provenance


class PlannedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    case_id: str
    attempt: int
    input_fingerprint: str
    canonical_spec: dict[str, Any]
    canonical_prompt: str
    translated_request: dict[str, Any]
    capabilities: dict[str, Any]


def _request_fingerprint(
    spec: CanonicalImageSpec,
    provider: str,
    model: str,
    translated_request: dict[str, Any],
) -> str:
    identity = {
        "canonical_spec": spec.model_dump(mode="json"),
        "provider": provider,
        "model": model,
        "translated_request": translated_request,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def plan_requests(
    definition: BenchmarkDefinition,
    providers: Sequence[ImageProvider],
    *,
    pack_path: Path,
    lock_path: Path,
    case_ids: set[str] | None = None,
    attempts: int | None = None,
) -> tuple[PlannedRequest, ...]:
    count = attempts or definition.minimum_requests_per_provider_case
    if count < 1:
        raise ValueError("attempts must be positive")
    selected = [item for item in definition.cases if case_ids is None or item.id in case_ids]
    missing = (case_ids or set()) - {item.id for item in selected}
    if missing:
        raise ValueError(f"unknown benchmark cases: {', '.join(sorted(missing))}")
    plans = []
    for provider in providers:
        for case in selected:
            for attempt in range(1, count + 1):
                spec = build_spec(definition, case, attempt, pack_path, lock_path)
                translated = provider.translate(spec).model_dump(mode="json")
                plans.append(
                    PlannedRequest(
                        provider=provider.provider,
                        model=provider.model,
                        case_id=case.id,
                        attempt=attempt,
                        input_fingerprint=_request_fingerprint(
                            spec, provider.provider, provider.model, translated
                        ),
                        canonical_spec=spec.model_dump(mode="json"),
                        canonical_prompt=spec.prompt(),
                        translated_request=translated,
                        capabilities=provider.capabilities.model_dump(mode="json"),
                    )
                )
    return tuple(plans)


class BenchmarkRunner:
    def __init__(
        self,
        state: BenchmarkStore,
        assets: AssetStore,
        returned_root: Path,
    ) -> None:
        self.state = state
        self.assets = assets
        self.returned_root = returned_root.resolve()
        self.returned_root.mkdir(parents=True, exist_ok=True)
        if returned_root.is_symlink() or not self.returned_root.is_relative_to(assets.root):
            raise ValueError("returned-byte staging must be under the trusted data root")

    @staticmethod
    def _suffix(mime_type: str) -> str:
        suffixes = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
        if mime_type not in suffixes:
            raise ValueError(f"unsupported returned image MIME type: {mime_type}")
        return suffixes[mime_type]

    def _staged_path(self, request_id: str, mime_type: str) -> Path:
        return self.returned_root / f"{request_id}{self._suffix(mime_type)}"

    def _verify_staged(self, request_id: str, receipt: dict[str, Any]) -> Path:
        path = self._staged_path(request_id, receipt["mime_type"])
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(self.returned_root)
        ):
            raise ValueError("request-scoped returned bytes are missing or untrusted")
        data = path.read_bytes()
        if (
            len(data) != receipt["returned_byte_count"]
            or sha256(data).hexdigest() != receipt["returned_sha256"]
        ):
            raise ValueError("staged returned bytes differ from receipt")
        mime_type, _, _ = validate_media(path)
        if mime_type != receipt["mime_type"]:
            raise ValueError("staged MIME type differs from receipt")
        return path

    def _ingest_result(self, request_id: str, source: Path) -> str:
        request = self.state.get_request(request_id)
        receipt = self.state.receipt(request_id)
        if receipt is None:
            raise ValueError("cannot ingest without a durable receipt")
        spec = CanonicalImageSpec.model_validate_json(request["canonical_spec_json"])
        blind_id = "vb_" + uuid4().hex
        artifact = self.assets.ingest(
            source,
            owner_scope="brand",
            owner_id=spec.brand_revision_id,
            kind="benchmark_image",
            slot_key=blind_id,
            provenance=Provenance(
                source_kind="provider",
                acquired_at=datetime.now(UTC),
                provider=request["provider"],
                model=request["model"],
                request_id=receipt["provider_request_id"],
                local_request_id=request_id,
                prompt_version=spec.prompt_version,
                input_artifact_ids=tuple(item.artifact_id for item in spec.references),
            ),
            dependencies=tuple(
                InputDependency(item.artifact_id, f"benchmark reference {item.role}")
                for item in spec.references
            ),
            expected_media_type=receipt["mime_type"],
        )
        return artifact.identity.artifact_id

    def reconcile(self, request_id: str) -> dict[str, Any]:
        """Repair only from local evidence; this method has no provider argument or call."""
        request = self.state.get_request(request_id)
        if request["status"] in {"prepared", "retryable_failure", "terminal_failure"}:
            return {
                "request_id": request_id,
                "status": request["status"],
                "action": "not_reconcilable",
            }
        mapping = self.state.output(request_id)
        receipt = self.state.receipt(request_id)
        if receipt is None:
            if request["status"] == "succeeded":
                raise ValueError("succeeded request has no provider receipt")
            return {
                "request_id": request_id,
                "status": request["status"],
                "action": "provider_side_reconciliation_required",
            }
        staged = self._staged_path(request_id, receipt["mime_type"])
        if staged.exists():
            self._verify_staged(request_id, receipt)
        candidates = self.state.candidates(request_id)
        if len(candidates) > 1:
            raise ValueError("multiple request-owned benchmark artifacts require inspection")
        if mapping is not None:
            artifact_id = mapping["artifact_id"]
            if candidates != [artifact_id]:
                raise ValueError("mapped artifact conflicts with request-owned artifacts")
            blind_id = mapping["blind_id"]
            action = "finalized_mapping"
        elif candidates:
            artifact_id = candidates[0]
            blind_id = self.assets.get(artifact_id).identity.slot_key
            action = "restored_mapping"
        else:
            source = self._verify_staged(request_id, receipt)
            artifact_id = self._ingest_result(request_id, source)
            blind_id = self.assets.get(artifact_id).identity.slot_key
            action = "ingested_staged_result"
        self.state.finalize_success(request_id, artifact_id, blind_id, self.assets)
        staged.unlink(missing_ok=True)
        return {
            "request_id": request_id,
            "status": "succeeded",
            "action": action,
            "artifact_id": artifact_id,
            "blind_id": blind_id,
        }

    def run(self, plan: PlannedRequest, provider: ImageProvider) -> dict[str, Any]:
        if (provider.provider, provider.model) != (plan.provider, plan.model):
            raise ValueError("plan provider identity differs from adapter")
        spec = CanonicalImageSpec.model_validate(plan.canonical_spec)
        expected_fingerprint = _request_fingerprint(
            spec, provider.provider, provider.model, plan.translated_request
        )
        if expected_fingerprint != plan.input_fingerprint:
            raise ValueError("planned request fingerprint is invalid")
        row = self.state.prepare(
            benchmark_version=spec.benchmark_version,
            prompt_version=spec.prompt_version,
            pack_revision_id=spec.pack_revision_id,
            brand_revision_id=spec.brand_revision_id,
            case_id=spec.case_id,
            attempt=spec.attempt,
            provider=provider.provider,
            model=provider.model,
            fingerprint=plan.input_fingerprint,
            canonical_spec=plan.canonical_spec,
            translated_request=plan.translated_request,
            capabilities=plan.capabilities,
        )
        request_id = row["request_id"]
        if row["status"] == "succeeded":
            mapping = self.state.output(request_id)
            if mapping is None:
                raise ValueError("succeeded request has no output")
            self.state.validate_output(request_id, mapping["artifact_id"], self.assets)
            return {"request_id": request_id, "status": "succeeded", "action": "reused"}
        if row["status"] in {"remote_started", "ambiguous"}:
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
        if row["status"] == "retryable_failure" and self.state.receipt(request_id) is not None:
            return {
                "request_id": request_id,
                "status": row["status"],
                "action": "local_reconciliation_required",
            }
        reference_paths = tuple(self.assets.path_for(item.artifact_id) for item in spec.references)
        self.state.transition(request_id, "remote_started")
        started = time.monotonic()
        try:
            result = provider.generate(spec, reference_paths)
        except ProviderFailure as exc:
            latency = time.monotonic() - started
            self.state.transition(
                request_id,
                exc.outcome,
                provider_request_id=exc.provider_request_id,
                latency_seconds=latency,
                error_kind=exc.outcome,
                error_reason=str(exc),
            )
            return {"request_id": request_id, "status": exc.outcome, "action": "recorded"}
        latency = time.monotonic() - started
        try:
            returned = self._staged_path(request_id, result.mime_type)
        except ValueError:
            self.state.transition(
                request_id,
                "ambiguous",
                provider_request_id=result.provider_request_id,
                latency_seconds=latency,
                error_kind="unknown_result_mime",
            )
            return {"request_id": request_id, "status": "ambiguous", "action": "recorded"}
        temporary = returned.with_suffix(returned.suffix + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(result.image_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, returned)
            self.state.record_receipt(
                request_id,
                provider_request_id=result.provider_request_id,
                returned_sha256=sha256(result.image_bytes).hexdigest(),
                returned_byte_count=len(result.image_bytes),
                mime_type=result.mime_type,
                latency_seconds=latency,
                usage=result.usage,
                actual_cost_amount=result.actual_cost_amount,
                cost_currency=result.cost_currency,
                pricing_policy=result.pricing_policy,
                response_metadata=result.response_metadata,
            )
            try:
                with Image.open(returned) as image:
                    image.load()
            except (OSError, ValueError) as exc:
                self.state.transition(
                    request_id,
                    "terminal_failure",
                    error_kind="invalid_output",
                    error_reason=str(exc),
                )
                return {
                    "request_id": request_id,
                    "status": "terminal_failure",
                    "action": "recorded",
                }
            reconciled = self.reconcile(request_id)
            return {**reconciled, "action": "generated"}
        except Exception as exc:
            return {
                "request_id": request_id,
                "status": "remote_started",
                "action": "local_reconciliation_required",
                "error": str(exc),
            }
        finally:
            temporary.unlink(missing_ok=True)


def blind_review_queue(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "blind_id": row["blind_id"],
            "artifact_id": row["artifact_id"],
            "case_id": row["case_id"],
            "attempt": row["attempt"],
        }
        for row in rows
        if row["blind_id"] is not None
    ]


def aggregate(state: BenchmarkStore, rubric: Rubric) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in state.requests():
        grouped[(row["provider"], row["model"])].append(row)
    reports = []
    for (provider, model), rows in sorted(grouped.items()):
        resolved: list[tuple[dict[str, Any], Any]] = []
        incomplete_reviews = 0
        reason_counts: Counter[str] = Counter()
        for row in rows:
            if row["blind_id"] is None:
                continue
            reviews = state.reviews(row["blind_id"])
            for review in reviews:
                reason_counts.update(review.hard_failure_reasons)
                if review.evidence_note:
                    reason_counts.update((review.evidence_note,))
            try:
                score = resolve_reviews(reviews, rubric)
            except ValueError:
                if reviews:
                    incomplete_reviews += 1
                continue
            resolved.append((row, score))
        usable = [(row, score) for row, score in resolved if score.usable]
        costs = [row["actual_cost_amount"] for row in rows if row["actual_cost_amount"] is not None]
        known_spend = float(sum(costs))
        all_costs_known = len(costs) == len(rows) and bool(rows)
        latencies = [row["latency_seconds"] for row in rows if row["latency_seconds"] is not None]
        per_scene = Counter(row["case_id"] for row, _ in usable)
        scores = [score.weighted_score for _, score in resolved]
        reports.append(
            {
                "provider": provider,
                "model": model,
                "total_requests": len(rows),
                "succeeded_requests": sum(row["status"] == "succeeded" for row in rows),
                "decoded_outputs": sum(row["artifact_id"] is not None for row in rows),
                "hard_failures": sum(bool(score.hard_failure_reasons) for _, score in resolved),
                "usable_outputs": len(usable),
                "usable_outputs_per_request": len(usable) / len(rows) if rows else None,
                "actual_spend": known_spend if all_costs_known else None,
                "known_spend": known_spend,
                "cost_known_requests": len(costs),
                "cost_currency": (
                    next(iter({row["cost_currency"] for row in rows}))
                    if rows and len({row["cost_currency"] for row in rows}) == 1
                    else None
                ),
                "usable_outputs_per_dollar": (
                    len(usable) / known_spend if all_costs_known and known_spend > 0 else None
                ),
                "median_latency_seconds": median(latencies) if latencies else None,
                "per_scene_usable_counts": dict(sorted(per_scene.items())),
                "average_weighted_score": mean(scores) if scores else None,
                "median_weighted_score": median(scores) if scores else None,
                "common_repair_failure_reasons": reason_counts.most_common(),
                "incomplete_review_sets": incomplete_reviews,
            }
        )
    return {"schema_version": 1, "providers": reports}


def dump_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, indent=2)
