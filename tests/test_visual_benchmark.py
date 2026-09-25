import base64
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from google.oauth2.credentials import Credentials
from PIL import Image

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.benchmark.models import (
    CanonicalImageSpec,
    ReferenceImage,
    Scorecard,
    Scores,
    build_spec,
    load_benchmark,
    load_references,
    load_rubric,
    resolve_reviews,
)
from tovitunes.benchmark.persistence import BenchmarkStore
from tovitunes.benchmark.providers import (
    GeminiImageProvider,
    HttpResponse,
    OpenAIImageProvider,
    ProviderCapabilities,
    ProviderFailure,
    ProviderResult,
)
from tovitunes.benchmark.runner import (
    BenchmarkRunner,
    PlannedRequest,
    aggregate,
    blind_review_queue,
    plan_requests,
)
from tovitunes.catalog import BrandCatalog
from tovitunes.domain.artifact import Provenance
from tovitunes.persistence.db import Database

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "benchmarks/visual/cases.v1.yaml"
RUBRIC = ROOT / "benchmarks/visual/rubric.v1.yaml"
PACK = ROOT / "brands/tovitunes/characters/tovi/packs/v1/pack.yaml"
LOCK = PACK.with_name("artifact-lock.yaml")


def png_bytes(color: str = "red", size: tuple[int, int] = (90, 160)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def simple_spec(
    catalog: BrandCatalog, references: tuple[ReferenceImage, ...]
) -> CanonicalImageSpec:
    return CanonicalImageSpec(
        benchmark_version="visual-benchmark-schema-1",
        prompt_version="visual-benchmark-v1",
        case_id="red_apple",
        attempt=1,
        scene_brief="Tovi finds one clearly red apple and points to it.",
        teaching_check="The apple is red.",
        common_brief="Create one original portrait illustration.",
        pack_revision_id=catalog.pack_revisions[0].revision_id,
        brand_revision_id=catalog.version.revision_id,
        references=references,
        palette={"body_sky_blue": "#5FBFFC"},
        identity_rules=("Keep the musical-note tuft.",),
        forbidden_changes=("Do not add human hands.",),
    )


class FakeProvider:
    provider = "fake"
    model = "fake-image-v1"

    def __init__(self, result: ProviderResult | Exception) -> None:
        self.result = result
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            reference_images=True,
            maximum_reference_images=3,
            portrait_9_16=True,
            requested_size="90x160",
            api_contract="offline fake",
        )

    def translate(self, spec: CanonicalImageSpec) -> Any:
        from tovitunes.benchmark.providers import TranslatedRequest

        return TranslatedRequest(
            endpoint="fake://images",
            body={"prompt": spec.prompt(), "size": "90x160"},
            supplied_reference_artifact_ids=tuple(x.artifact_id for x in spec.references),
        )

    def generate(
        self,
        spec: CanonicalImageSpec,
        reference_paths: tuple[Path, ...],
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> ProviderResult:
        if on_remote_start is not None:
            on_remote_start()
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def send(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> HttpResponse:
        if on_remote_start is not None:
            on_remote_start()
        self.calls.append((url, headers, body))
        return self.response


class BoundaryTransport:
    """Offline transport that observes the durable state at the call boundary."""

    def __init__(self, state: BenchmarkStore, response: HttpResponse | Exception) -> None:
        self.state = state
        self.response = response
        self.calls = 0
        self.hooks = 0
        self.before: list[str] = []
        self.at_send: list[str] = []

    def send(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> HttpResponse:
        assert on_remote_start is not None
        request_id = self.state.requests()[0]["request_id"]
        self.before.append(self.state.get_request(request_id)["status"])
        on_remote_start()
        self.hooks += 1
        self.at_send.append(self.state.get_request(request_id)["status"])
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _boundary_response(kind: str, status: int = 200) -> HttpResponse:
    encoded = base64.b64encode(png_bytes()).decode("ascii")
    if kind == "openai":
        body = {"data": [{"b64_json": encoded}]}
    else:
        body = {
            "id": "gemini-request",
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "image", "data": encoded, "mime_type": "image/png"}],
                }
            ],
        }
    return HttpResponse(status, {"x-request-id": "remote-boundary"}, json.dumps(body).encode())


def _boundary_provider(
    kind: str, transport: BoundaryTransport, *, api_key: str | None = "test"
) -> Any:
    return OpenAIImageProvider(transport=transport, api_key=api_key)


def test_cases_rubric_prompt_and_canonical_references_are_locked() -> None:
    definition = load_benchmark(CASES)
    rubric = load_rubric(RUBRIC)
    pack_revision, _, references = load_references(LOCK)
    spec = build_spec(definition, definition.cases[0], 1, PACK, LOCK)
    assert len(definition.cases) == 10
    assert sum(rubric.weights.values()) == 100
    assert pack_revision == "tovi-pack-v1-8f7e487b5ac5279b"
    assert [(item.artifact_id, item.sha256) for item in references] == [
        (
            "75191e77-70fb-435a-a858-c3763d331058",
            "7d5347ca28b59c90dedef906662690e87d40c29c6a8655c90b578c13cc9fdc14",
        ),
        (
            "2a3073eb-27c0-47ad-846b-c84340b27c51",
            "d40aa2d0c48a87de2e6d248626c5ff9e3567ab5741e917b17857439b2c271676",
        ),
        (
            "0ae4c139-b1ed-49dc-8758-e95a2d429a15",
            "ef75adf4c91afe41038d19db2ea54c6fc866cae896447acfe2f2ccf0c14fd645",
        ),
    ]
    assert spec.prompt() == build_spec(definition, definition.cases[0], 1, PACK, LOCK).prompt()
    assert "9:16" in spec.prompt()
    assert "No web, image-search" in spec.prompt()


def test_planning_records_capabilities_and_full_plan_has_40_requests() -> None:
    providers = [GeminiImageProvider(), OpenAIImageProvider()]
    plans = plan_requests(load_benchmark(CASES), providers, pack_path=PACK, lock_path=LOCK)
    assert len(plans) == 40
    assert {item.model for item in plans} == {
        "gemini-3.1-flash-image",
        "gpt-image-2.5-sunburst",
    }
    assert all(item.capabilities["grounding_enabled"] is False for item in plans)
    assert all(
        len(item.translated_request["supplied_reference_artifact_ids"]) == 3 for item in plans
    )


def test_provider_translation_and_response_extraction(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    paths = []
    refs = []
    for index, color in enumerate(("red", "green", "blue")):
        data = png_bytes(color)
        path = tmp_path / f"ref-{index}.png"
        path.write_bytes(data)
        paths.append(path)
        refs.append(
            ReferenceImage(
                role=("front", "three_quarter", "profile")[index],
                artifact_id=str(uuid4()),
                sha256=sha256(data).hexdigest(),
                mime_type="image/png",
            )
        )
    spec = simple_spec(catalog, tuple(refs))
    output = base64.b64encode(png_bytes()).decode()
    openai_transport = FakeTransport(
        HttpResponse(
            200,
            {"x-request-id": "oa-123"},
            json.dumps({"data": [{"b64_json": output}], "usage": {"input_tokens": 4}}).encode(),
        )
    )
    openai = OpenAIImageProvider(transport=openai_transport, api_key="test")
    openai_result = openai.generate(spec, tuple(paths))
    assert openai_result.image_bytes == png_bytes()
    assert openai_result.provider_request_id == "oa-123"
    assert b"gpt-image-2.5-sunburst" in openai_transport.calls[0][2]
    assert openai_transport.calls[0][2].count(b'name="image[]"') == 3



def test_malformed_and_api_responses_map_to_failures(tmp_path: Path, catalog: BrandCatalog) -> None:
    data = png_bytes()
    paths = tuple(tmp_path / f"r-{index}.png" for index in range(3))
    for path in paths:
        path.write_bytes(data)
    refs = tuple(
        ReferenceImage(
            role=str(i),
            artifact_id=str(uuid4()),
            sha256=sha256(data).hexdigest(),
            mime_type="image/png",
        )
        for i in range(3)
    )
    spec = simple_spec(catalog, refs)
    malformed = OpenAIImageProvider(
        transport=FakeTransport(HttpResponse(200, {}, b"{}")), api_key="test"
    )
    with pytest.raises(ProviderFailure, match="malformed"):
        malformed.generate(spec, paths)
    throttled = OpenAIImageProvider(
        transport=FakeTransport(HttpResponse(429, {}, b'{"error":{"message":"slow down"}}')),
        api_key="test",
    )
    with pytest.raises(ProviderFailure) as error:
        throttled.generate(spec, paths)
    assert error.value.outcome == "retryable_failure"


def runner_fixture(
    tmp_path: Path, catalog: BrandCatalog
) -> tuple[BenchmarkRunner, BenchmarkStore, CanonicalImageSpec]:
    database = Database(tmp_path / "state.db")
    database.migrate()
    database.register_catalog(catalog)
    root = tmp_path / "assets"
    root.mkdir()
    returned = root / ".benchmark-returned"
    returned.mkdir()
    refs = []
    with database.connect() as connection:
        for index, color in enumerate(("red", "green", "blue")):
            artifact_id = str(uuid4())
            data = png_bytes(color)
            relative = f"refs/{artifact_id}.png"
            path = root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
            provenance = Provenance(
                source_kind="deterministic",
                acquired_at=datetime.now(UTC),
                provider="test-fixture",
            )
            connection.execute(
                "INSERT INTO artifact_versions VALUES "
                "(?, 'brand', NULL, ?, 'character_reference', ?, 1, ?, ?, ?, "
                "'image/png', ?, ?)",
                (
                    artifact_id,
                    catalog.version.revision_id,
                    f"ref_{index}",
                    relative,
                    sha256(data).hexdigest(),
                    len(data),
                    provenance.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            refs.append(
                ReferenceImage(
                    role=str(index),
                    artifact_id=artifact_id,
                    sha256=sha256(data).hexdigest(),
                    mime_type="image/png",
                )
            )
        connection.commit()
    assets = AssetStore(root, database, generated_source_roots=[returned])
    state = BenchmarkStore(database)
    return BenchmarkRunner(state, assets, returned), state, simple_spec(catalog, tuple(refs))


def make_plan(spec: CanonicalImageSpec, provider: FakeProvider) -> PlannedRequest:
    translated = provider.translate(spec).model_dump(mode="json")
    identity = {
        "canonical_spec": spec.model_dump(mode="json"),
        "provider": provider.provider,
        "model": provider.model,
        "translated_request": translated,
    }
    fingerprint = sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PlannedRequest(
        provider=provider.provider,
        model=provider.model,
        case_id=spec.case_id,
        attempt=spec.attempt,
        input_fingerprint=fingerprint,
        canonical_spec=spec.model_dump(mode="json"),
        canonical_prompt=spec.prompt(),
        translated_request=translated,
        capabilities=provider.capabilities.model_dump(mode="json"),
    )


def test_success_is_immutable_blind_rights_unknown_and_idempotent(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    provider = FakeProvider(
        ProviderResult(
            image_bytes=png_bytes(),
            mime_type="image/png",
            provider_request_id="fake-remote-1",
            usage={"images": 1},
            actual_cost_amount=0.25,
            cost_currency="USD",
            pricing_policy="fake-pricing-2026-01",
        )
    )
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    second = runner.run(plan, provider)
    assert first["status"] == "succeeded"
    assert second["action"] == "reused"
    assert provider.calls == 1
    row = state.requests()[0]
    assert row["provider"] == "fake" and row["model"] == "fake-image-v1"
    assert row["blind_id"].startswith("vb_")
    assert "fake" not in row["blind_id"]
    with state.database.connect() as connection:
        rights = connection.execute(
            "SELECT status FROM rights_decisions WHERE artifact_id = ?", (row["artifact_id"],)
        ).fetchall()
    assert [item["status"] for item in rights] == ["unknown"]
    assert blind_review_queue(state.requests()) == [
        {
            "blind_id": row["blind_id"],
            "artifact_id": row["artifact_id"],
            "case_id": "red_apple",
            "attempt": 1,
        }
    ]


def test_failure_is_durable_and_ambiguous_is_never_blindly_retried(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    provider = FakeProvider(ProviderFailure("lost", outcome="ambiguous"))
    plan = make_plan(spec, provider)
    assert runner.run(plan, provider)["status"] == "ambiguous"
    assert runner.run(plan, provider)["action"] == "manual_reconciliation_required"
    assert provider.calls == 1
    assert state.requests()[0]["error_reason"] == "lost"


def scorecard(
    blind_id: str,
    reviewer: str,
    identity: int = 4,
    teaching: int = 4,
    *,
    role: str = "initial",
    hard: tuple[str, ...] = (),
) -> Scorecard:
    return Scorecard(
        blind_id=blind_id,
        reviewer=reviewer,
        review_role=role,
        scores=Scores(
            character_identity=identity,
            teaching_accuracy=teaching,
            composition=4,
            reference_fidelity=4,
            image_quality=4,
            production_fit=4,
        ),
        evidence_note="visible mismatch" if identity < 3 or teaching < 3 else None,
        hard_failure_reasons=hard,
    )


def test_score_validation_gates_averaging_and_adjudication() -> None:
    rubric = load_rubric(RUBRIC)
    with pytest.raises(ValueError):
        Scores(
            character_identity=5,
            teaching_accuracy=4,
            composition=4,
            reference_fidelity=4,
            image_quality=4,
            production_fit=4,
        )
    average = resolve_reviews((scorecard("b", "a", 4, 4), scorecard("b", "b", 3, 3)), rubric)
    assert average.scores["character_identity"] == 3.5
    assert average.weighted_score == pytest.approx(93.125)
    assert average.usable
    with pytest.raises(ValueError, match="adjudication"):
        resolve_reviews((scorecard("b", "a", 4), scorecard("b", "b", 2)), rubric)
    median_score = resolve_reviews(
        (
            scorecard("b", "a", 4),
            scorecard("b", "b", 2, role="initial"),
            scorecard("b", "c", 3, role="adjudication"),
        ),
        rubric,
    )
    assert median_score.scores["character_identity"] == 3
    assert median_score.usable
    identity_fail = resolve_reviews((scorecard("b", "a", 2), scorecard("b", "b", 2)), rubric)
    teaching_fail = resolve_reviews((scorecard("b", "a", 4, 2), scorecard("b", "b", 4, 2)), rubric)
    hard_fail = resolve_reviews(
        (
            scorecard("b", "a", hard=("wrong target color",)),
            scorecard("b", "b"),
        ),
        rubric,
    )
    assert not identity_fail.usable and not teaching_fail.usable and not hard_fail.usable


def test_known_and_unknown_cost_aggregation_and_median_latency(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    rubric = load_rubric(RUBRIC)
    first_provider = FakeProvider(
        ProviderResult(
            image_bytes=png_bytes(),
            mime_type="image/png",
            actual_cost_amount=0.5,
            cost_currency="USD",
            pricing_policy="fake-v1",
        )
    )
    first = runner.run(make_plan(spec, first_provider), first_provider)
    state.record_review(scorecard(first["blind_id"], "a"))
    state.record_review(scorecard(first["blind_id"], "b"))
    with pytest.raises(ValueError, match="role limit"):
        state.record_review(scorecard(first["blind_id"], "c"))
    report = aggregate(state, rubric)["providers"][0]
    assert report["actual_spend"] == 0.5
    assert report["usable_outputs_per_dollar"] == 2
    assert report["usable_outputs_per_request"] == 1
    assert report["median_latency_seconds"] is not None

    unknown_spec = spec.model_copy(update={"attempt": 2})
    unknown_provider = FakeProvider(ProviderResult(image_bytes=png_bytes(), mime_type="image/png"))
    runner.run(make_plan(unknown_spec, unknown_provider), unknown_provider)
    report = aggregate(state, rubric)["providers"][0]
    assert report["actual_spend"] is None
    assert report["usable_outputs_per_dollar"] is None


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (429, "retryable_failure"),
        (408, "ambiguous"),
        (500, "ambiguous"),
        (503, "ambiguous"),
        (400, "terminal_failure"),
        (401, "terminal_failure"),
    ],
)
def test_http_failure_classification_and_request_id(
    tmp_path: Path, catalog: BrandCatalog, status: int, outcome: str
) -> None:
    data = png_bytes()
    paths = tuple(tmp_path / f"r-{i}.png" for i in range(3))
    for path in paths:
        path.write_bytes(data)
    refs = tuple(
        ReferenceImage(
            role=str(i),
            artifact_id=str(uuid4()),
            sha256=sha256(data).hexdigest(),
            mime_type="image/png",
        )
        for i in range(3)
    )
    provider = OpenAIImageProvider(
        transport=FakeTransport(HttpResponse(status, {"x-request-id": "remote-123"}, b"{}")),
        api_key="test",
    )
    with pytest.raises(ProviderFailure) as failure:
        provider.generate(simple_spec(catalog, refs), paths)
    assert failure.value.outcome == outcome
    assert failure.value.provider_request_id == "remote-123"


@pytest.mark.parametrize("provider_type", [OpenAIImageProvider])
def test_malformed_2xx_is_ambiguous(
    tmp_path: Path, catalog: BrandCatalog, provider_type: Any
) -> None:
    data = png_bytes()
    paths = tuple(tmp_path / f"r-{i}.png" for i in range(3))
    for path in paths:
        path.write_bytes(data)
    refs = tuple(
        ReferenceImage(
            role=str(i),
            artifact_id=str(uuid4()),
            sha256=sha256(data).hexdigest(),
            mime_type="image/png",
        )
        for i in range(3)
    )
    provider = provider_type(
        transport=FakeTransport(HttpResponse(200, {"x-request-id": "remote-456"}, b"{}")),
        api_key="test",
    )
    with pytest.raises(ProviderFailure) as failure:
        provider.generate(simple_spec(catalog, refs), paths)
    assert failure.value.outcome == "ambiguous"
    assert failure.value.provider_request_id == "remote-456"


@pytest.mark.parametrize("error", ["url", "timeout"])
def test_transport_loss_is_ambiguous(error: str) -> None:
    import urllib.error

    from tovitunes.benchmark.providers import UrllibTransport

    with pytest.MonkeyPatch.context() as patch:

        def lost(*args: Any, **kwargs: Any) -> None:
            if error == "timeout":
                raise TimeoutError("timed out")
            raise urllib.error.URLError("lost")

        patch.setattr("urllib.request.urlopen", lost)
        with pytest.raises(ProviderFailure) as failure:
            UrllibTransport().send("https://example.invalid", {}, b"data", 1)
    assert failure.value.outcome == "ambiguous"


def _received_result(
    runner: BenchmarkRunner,
    state: BenchmarkStore,
    spec: CanonicalImageSpec,
    *,
    mime_type: str = "image/png",
    byte_count: int | None = None,
    digest: str | None = None,
) -> tuple[str, FakeProvider]:
    provider = FakeProvider(ProviderResult(image_bytes=png_bytes(), mime_type="image/png"))
    plan = make_plan(spec, provider)
    row = state.prepare(
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
    state.transition(request_id, "remote_started")
    data = png_bytes()
    runner._staged_path(request_id, mime_type).write_bytes(data)
    state.record_receipt(
        request_id,
        provider_request_id="remote-789",
        returned_sha256=digest or sha256(data).hexdigest(),
        returned_byte_count=byte_count if byte_count is not None else len(data),
        mime_type=mime_type,
        latency_seconds=1.25,
        usage={"images": 1},
        actual_cost_amount=None,
        cost_currency=None,
        pricing_policy=None,
        response_metadata={"safe": True},
    )
    return request_id, provider


def test_reconcile_staged_receipt_and_idempotence(tmp_path: Path, catalog: BrandCatalog) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    request_id, provider = _received_result(runner, state, spec)
    first = runner.reconcile(request_id)
    second = runner.reconcile(request_id)
    assert first["action"] == "ingested_staged_result"
    assert second["action"] == "finalized_mapping"
    assert first["artifact_id"] == second["artifact_id"]
    assert provider.calls == 0
    assert not runner._staged_path(request_id, "image/png").exists()
    assert state.get_request(request_id)["status"] == "succeeded"
    with state.database.connect() as connection:
        rights = connection.execute(
            "SELECT status FROM rights_decisions WHERE artifact_id = ?", (first["artifact_id"],)
        ).fetchone()
        approval = connection.execute(
            "SELECT status FROM approval_decisions WHERE artifact_id = ?", (first["artifact_id"],)
        ).fetchone()
    assert rights["status"] == "unknown"
    assert approval["status"] == "pending"


def test_reconcile_orphan_artifact_and_output_mapping(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    request_id, provider = _received_result(runner, state, spec)
    artifact_id = runner._ingest_result(request_id, runner._staged_path(request_id, "image/png"))
    restored = runner.reconcile(request_id)
    assert restored["action"] == "restored_mapping"
    assert restored["artifact_id"] == artifact_id
    assert provider.calls == 0

    second_spec = spec.model_copy(update={"attempt": 2})
    second_id, _ = _received_result(runner, state, second_spec)
    second_artifact = runner._ingest_result(second_id, runner._staged_path(second_id, "image/png"))
    record = runner.assets.get(second_artifact)
    state.record_output(second_id, second_artifact, record.identity.slot_key, 90, 160, "image/png")
    assert state.get_request(second_id)["status"] == "remote_started"
    assert runner.reconcile(second_id)["action"] == "finalized_mapping"
    assert state.get_request(second_id)["status"] == "succeeded"


@pytest.mark.parametrize("damage", ["sha", "byte_count", "mime"])
def test_staged_bytes_must_match_receipt(
    tmp_path: Path, catalog: BrandCatalog, damage: str
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    data = png_bytes()
    request_id, provider = _received_result(
        runner,
        state,
        spec,
        digest="f" * 64 if damage == "sha" else None,
        byte_count=len(data) + 1 if damage == "byte_count" else None,
        mime_type="image/jpeg" if damage == "mime" else "image/png",
    )
    with pytest.raises(ValueError):
        runner.reconcile(request_id)
    assert state.get_request(request_id)["status"] == "remote_started"
    assert provider.calls == 0


def test_wrong_artifact_identity_and_duplicate_orphans_fail_closed(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    request_id, _ = _received_result(runner, state, spec)
    source = runner._staged_path(request_id, "image/png")
    artifact_id = runner._ingest_result(request_id, source)
    other_spec = spec.model_copy(update={"attempt": 2})
    other_id, _ = _received_result(runner, state, other_spec)
    other_artifact = runner._ingest_result(other_id, runner._staged_path(other_id, "image/png"))
    with pytest.raises(ValueError, match="identity"):
        state.validate_output(request_id, other_artifact, runner.assets)
    runner._ingest_result(request_id, source)
    with pytest.raises(ValueError, match="multiple"):
        runner.reconcile(request_id)
    assert state.output(request_id) is None
    assert artifact_id != other_artifact


def test_no_local_evidence_never_generates_and_success_trigger_requires_mapping(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    provider = FakeProvider(ProviderResult(image_bytes=png_bytes(), mime_type="image/png"))
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    assert first["status"] == "succeeded"
    second_spec = spec.model_copy(update={"attempt": 2})
    second_plan = make_plan(second_spec, provider)
    row = state.prepare(
        benchmark_version=second_spec.benchmark_version,
        prompt_version=second_spec.prompt_version,
        pack_revision_id=second_spec.pack_revision_id,
        brand_revision_id=second_spec.brand_revision_id,
        case_id=second_spec.case_id,
        attempt=second_spec.attempt,
        provider=provider.provider,
        model=provider.model,
        fingerprint=second_plan.input_fingerprint,
        canonical_spec=second_plan.canonical_spec,
        translated_request=second_plan.translated_request,
        capabilities=second_plan.capabilities,
    )
    request_id = row["request_id"]
    state.transition(request_id, "remote_started")
    assert runner.reconcile(request_id)["action"] == "provider_side_reconciliation_required"
    assert runner.run(second_plan, provider)["action"] == "manual_reconciliation_required"
    assert provider.calls == 1
    with state.database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE visual_benchmark_requests SET status = 'succeeded' WHERE request_id = ?",
                (request_id,),
            )


def test_safe_429_can_retry_same_attempt(tmp_path: Path, catalog: BrandCatalog) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    provider = FakeProvider(ProviderFailure("throttled", outcome="retryable_failure"))
    plan = make_plan(spec, provider)
    assert runner.run(plan, provider)["status"] == "retryable_failure"
    provider.result = ProviderResult(image_bytes=png_bytes(), mime_type="image/png")
    assert runner.run(plan, provider)["status"] == "succeeded"
    assert provider.calls == 2
    assert len(state.requests()) == 1


@pytest.mark.parametrize("fault", ["before_ingest", "after_ingest"])
def test_run_crash_boundaries_reconcile_without_regeneration(
    tmp_path: Path, catalog: BrandCatalog, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    provider = FakeProvider(ProviderResult(image_bytes=png_bytes(), mime_type="image/png"))
    plan = make_plan(spec, provider)
    if fault == "before_ingest":
        original = runner._ingest_result
        monkeypatch.setattr(
            runner, "_ingest_result", lambda *_: (_ for _ in ()).throw(RuntimeError())
        )
    else:
        original = state.finalize_success
        monkeypatch.setattr(
            state, "finalize_success", lambda *_: (_ for _ in ()).throw(RuntimeError())
        )
    first = runner.run(plan, provider)
    assert first["status"] == "remote_started"
    request_id = first["request_id"]
    assert state.receipt(request_id) is not None
    if fault == "before_ingest":
        monkeypatch.setattr(runner, "_ingest_result", original)
    else:
        monkeypatch.setattr(state, "finalize_success", original)
    assert runner.run(plan, provider)["action"] == "manual_reconciliation_required"
    assert runner.reconcile(request_id)["status"] == "succeeded"
    assert provider.calls == 1


@pytest.mark.parametrize("defect", ["dependencies", "provider", "model"])
def test_orphan_with_wrong_identity_or_dependencies_is_rejected(
    tmp_path: Path, catalog: BrandCatalog, defect: str
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    request_id, _ = _received_result(runner, state, spec)
    source = runner._staged_path(request_id, "image/png")
    references = spec.references
    dependencies = tuple(
        InputDependency(ref.artifact_id, f"benchmark reference {ref.role}") for ref in references
    )
    if defect == "dependencies":
        dependencies = dependencies[:-1]
    artifact = runner.assets.ingest(
        source,
        owner_scope="brand",
        owner_id=spec.brand_revision_id,
        kind="benchmark_image",
        slot_key="vb_wrong" + uuid4().hex,
        provenance=Provenance(
            source_kind="provider",
            acquired_at=datetime.now(UTC),
            provider="other" if defect == "provider" else "fake",
            model="other" if defect == "model" else "fake-image-v1",
            request_id="remote-789",
            local_request_id=request_id,
            prompt_version=spec.prompt_version,
            input_artifact_ids=tuple(ref.artifact_id for ref in references),
        ),
        dependencies=dependencies,
        expected_media_type="image/png",
    )
    with pytest.raises(ValueError):
        runner.reconcile(request_id)
    assert state.output(request_id) is None
    assert state.get_request(request_id)["status"] == "remote_started"
    assert (
        runner.assets.get(artifact.identity.artifact_id).sha256 == sha256(png_bytes()).hexdigest()
    )


def test_succeeded_file_is_revalidated_on_reuse(tmp_path: Path, catalog: BrandCatalog) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    provider = FakeProvider(ProviderResult(image_bytes=png_bytes(), mime_type="image/png"))
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    assert first["status"] == "succeeded"
    runner.assets.path_for(first["artifact_id"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="invalid"):
        runner.run(plan, provider)
    assert provider.calls == 1


def test_known_bad_returned_image_is_terminal(tmp_path: Path, catalog: BrandCatalog) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    provider = FakeProvider(ProviderResult(image_bytes=b"not a png", mime_type="image/png"))
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    assert first["status"] == "terminal_failure"
    assert state.receipt(first["request_id"]) is not None
    assert runner.run(plan, provider)["action"] == "new_attempt_required"
    assert provider.calls == 1


def test_receipted_result_cannot_be_marked_safe_to_retry(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    request_id, provider = _received_result(runner, state, spec)
    with pytest.raises(ValueError, match="cannot become safely retryable"):
        state.transition(request_id, "retryable_failure")
    assert state.get_request(request_id)["status"] == "remote_started"
    assert provider.calls == 0


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_local_reference_preflight_preserves_same_attempt(
    tmp_path: Path, catalog: BrandCatalog, damage: str
) -> None:
    kind = "openai"
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = BoundaryTransport(state, _boundary_response(kind))
    provider = _boundary_provider(kind, transport)
    plan = make_plan(spec, provider)
    reference_path = runner.assets.path_for(spec.references[0].artifact_id)
    original = reference_path.read_bytes()
    if damage == "corrupt":
        reference_path.write_bytes(b"corrupt reference")
    else:
        reference_path.unlink()
    failed = runner.run(plan, provider)
    request_id = failed["request_id"]
    assert failed["status"] == "retryable_failure"
    assert state.get_request(request_id)["error_kind"] == "local_preflight"
    assert state.get_request(request_id)["attempt"] == 1
    assert state.receipt(request_id) is None
    assert transport.calls == transport.hooks == 0

    reference_path.write_bytes(original)
    success = runner.run(plan, provider)
    assert success["status"] == "succeeded"
    assert success["request_id"] == request_id
    assert state.get_request(request_id)["attempt"] == 1
    assert transport.calls == transport.hooks == 1
    assert transport.before == ["retryable_failure"]
    assert transport.at_send == ["remote_started"]


def test_missing_credential_is_local_preflight(
    tmp_path: Path, catalog: BrandCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = "openai"
    key_name = "OPENAI_API_KEY"
    monkeypatch.delenv(key_name, raising=False)
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = BoundaryTransport(state, _boundary_response(kind))
    provider = _boundary_provider(kind, transport, api_key=None)
    plan = make_plan(spec, provider)
    failed = runner.run(plan, provider)
    assert failed["status"] == "retryable_failure"
    assert state.receipt(failed["request_id"]) is None
    assert transport.calls == transport.hooks == 0
    assert state.get_request(failed["request_id"])["error_kind"] == "local_preflight"
    monkeypatch.setenv(key_name, "test")
    success = runner.run(plan, provider)
    assert success["status"] == "succeeded"
    assert success["request_id"] == failed["request_id"]
    assert transport.calls == transport.hooks == 1
    assert transport.at_send == ["remote_started"]


@pytest.mark.parametrize("outcome", ["timeout", "http_500", "http_429"])
def test_post_boundary_outcome_stays_fail_closed(
    tmp_path: Path, catalog: BrandCatalog, outcome: str
) -> None:
    kind = "openai"
    runner, state, spec = runner_fixture(tmp_path, catalog)
    response: HttpResponse | Exception = (
        TimeoutError("transport lost")
        if outcome == "timeout"
        else _boundary_response(kind, 500 if outcome == "http_500" else 429)
    )
    transport = BoundaryTransport(state, response)
    provider = _boundary_provider(kind, transport)
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    expected = "retryable_failure" if outcome == "http_429" else "ambiguous"
    assert first["status"] == expected
    assert transport.calls == transport.hooks == 1
    assert transport.before == ["prepared"]
    assert transport.at_send == ["remote_started"]
    assert state.receipt(first["request_id"]) is None
    transport.response = _boundary_response(kind)
    second = runner.run(plan, provider)
    if outcome == "http_429":
        assert second["status"] == "succeeded"
        assert second["request_id"] == first["request_id"]
        assert transport.calls == transport.hooks == 2
        assert transport.at_send == ["remote_started", "remote_started"]
    else:
        assert second["action"] == "manual_reconciliation_required"
        assert transport.calls == transport.hooks == 1


def test_urllib_hook_runs_after_request_construction_before_urlopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    from tovitunes.benchmark.providers import UrllibTransport

    calls: list[str] = []

    def broken_request(*args: Any, **kwargs: Any) -> None:
        calls.append("request")
        raise ValueError("bad local request")

    monkeypatch.setattr(urllib.request, "Request", broken_request)
    with pytest.raises(ValueError, match="bad local request"):
        UrllibTransport().send(
            "https://example.invalid",
            {},
            b"body",
            1,
            on_remote_start=lambda: calls.append("remote_start"),
        )
    assert calls == ["request"]


class VertexTestTransport(httpx.BaseTransport):
    def __init__(self, response: httpx.Response | Exception, state: BenchmarkStore | None = None):
        self.response = response
        self.state = state
        self.calls: list[httpx.Request] = []
        self.at_send: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.state is not None:
            request_id = self.state.requests()[0]["request_id"]
            self.at_send.append(self.state.get_request(request_id)["status"])
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def vertex_response(*, image_count: int = 1) -> httpx.Response:
    part = {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png_bytes()).decode()}}
    return httpx.Response(200, json={
        "responseId": "vertex-123",
        "modelVersion": "gemini-3.1-flash-image",
        "candidates": [{"content": {"role": "model", "parts": [part] * image_count}}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10},
    })


def vertex_provider(
    transport: VertexTestTransport,
    *,
    project: str | None = "test-project",
    location: str | None = None,
    credentials_loader: Callable[[], Credentials] | None = None,
) -> GeminiImageProvider:
    credentials = Credentials(token="fake-secret-token", quota_project_id="test-project")
    return GeminiImageProvider(
        transport=transport,
        project=project,
        location=location or "global",
        credentials_loader=credentials_loader or (lambda: credentials),
    )


def test_vertex_dry_run_needs_no_credentials_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    provider = GeminiImageProvider(credentials_loader=lambda: pytest.fail("ADC used in dry run"))
    plan = plan_requests(load_benchmark(CASES), [provider], pack_path=PACK, lock_path=LOCK,
                         case_ids={"red_apple"}, attempts=1)[0]
    translated = plan.translated_request
    assert translated["body"]["backend"] == "Vertex AI"
    assert translated["body"]["location"] == "global"
    assert translated["body"]["model"] == "gemini-3.1-flash-image"
    assert translated["body"]["response_modalities"] == ["TEXT", "IMAGE"]
    assert translated["body"]["image_config"]["aspect_ratio"] == "9:16"
    assert translated["body"]["image_config"]["image_size"] == "1K"
    assert translated["body"]["tools"] == []
    assert len(translated["supplied_reference_artifact_ids"]) == 3
    assert all("sha256" in item for item in translated["body"]["input"][1:])
    assert plan.canonical_prompt and plan.input_fingerprint


def test_vertex_missing_project_preflight_is_retryable(
    tmp_path: Path, catalog: BrandCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = VertexTestTransport(vertex_response(), state)
    auth_calls = 0

    def load_credentials() -> Credentials:
        nonlocal auth_calls
        auth_calls += 1
        return Credentials(token="fake-secret-token")

    provider = vertex_provider(transport, project=None, credentials_loader=load_credentials)
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    assert first["status"] == "retryable_failure"
    assert state.get_request(first["request_id"])["error_kind"] == "local_preflight"
    assert state.receipt(first["request_id"]) is None
    assert transport.calls == []
    assert auth_calls == 0
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    repaired = runner.run(plan, provider)
    assert repaired["status"] == "succeeded"
    assert repaired["request_id"] == first["request_id"]
    assert transport.at_send == ["remote_started"]
    assert auth_calls == 1


def test_vertex_invalid_location_fails_before_auth_or_generation(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = VertexTestTransport(vertex_response(), state)
    provider = vertex_provider(transport, location="bad/location",
                               credentials_loader=lambda: pytest.fail("ADC used"))
    result = runner.run(make_plan(spec, provider), provider)
    assert result["status"] == "retryable_failure"
    assert state.receipt(result["request_id"]) is None
    assert not transport.calls


def test_vertex_default_adc_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tovitunes.benchmark import providers

    def unavailable(**kwargs: Any) -> None:
        raise ValueError("secret credential file path")

    monkeypatch.setattr(providers.google.auth, "default", unavailable)
    with pytest.raises(ProviderFailure, match="ADC|Application Default Credentials") as error:
        providers._vertex_credentials()
    assert "secret" not in str(error.value)


def test_vertex_location_environment_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "eu")
    provider = GeminiImageProvider(credentials_loader=lambda: pytest.fail("ADC used"))
    definition = load_benchmark(CASES)
    spec = build_spec(definition, definition.cases[0], 1, PACK, LOCK)
    assert provider.location == "eu"
    assert provider.translate(spec).body["location"] == "eu"


def test_vertex_adc_failure_then_same_attempt_repairs(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = VertexTestTransport(vertex_response(), state)
    available = False

    def load_credentials() -> Credentials:
        if not available:
            raise ProviderFailure("ADC unavailable", outcome="terminal_failure")
        return Credentials(token="fake-secret-token")

    provider = vertex_provider(transport, credentials_loader=load_credentials)
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    assert first["status"] == "retryable_failure"
    assert state.receipt(first["request_id"]) is None
    assert not transport.calls
    available = True
    second = runner.run(plan, provider)
    assert second["status"] == "succeeded"
    assert second["request_id"] == first["request_id"]
    assert len(transport.calls) == 1
    assert transport.at_send == ["remote_started"]
    assert state.get_request(second["request_id"])["attempt"] == 1


@pytest.mark.parametrize("location", [None, "us"])
def test_vertex_sdk_request_contract_and_metadata(
    tmp_path: Path, catalog: BrandCatalog, monkeypatch: pytest.MonkeyPatch,
    location: str | None,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "unused-developer-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "unused-google-key")
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = VertexTestTransport(vertex_response(), state)
    provider = vertex_provider(transport, location=location)
    result = runner.run(make_plan(spec, provider), provider)
    assert result["status"] == "succeeded"
    assert transport.at_send == ["remote_started"]
    assert len(transport.calls) == 1
    request = transport.calls[0]
    body = json.loads(request.content)
    assert "googleapis.com" in str(request.url)
    assert f"locations/{location or 'global'}" in str(request.url)
    assert "gemini-3.1-flash-image" in str(request.url)
    assert "projects/test-project" in str(request.url)
    assert request.headers["authorization"] == "Bearer fake-secret-token"
    assert request.headers["x-goog-user-project"] == "test-project"
    assert "x-goog-api-key" not in request.headers
    assert body["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16"
    assert body["generationConfig"]["imageConfig"]["imageSize"] == "1K"
    assert body["generationConfig"].get("tools", []) == []
    parts = body["contents"][0]["parts"]
    assert parts[0]["text"] == spec.prompt()
    assert len(parts) == 4
    assert [
        base64.urlsafe_b64decode(part["inlineData"]["data"] + "==") for part in parts[1:]
    ] == [
        runner.assets.path_for(ref.artifact_id).read_bytes() for ref in spec.references
    ]
    receipt = state.receipt(result["request_id"])
    assert receipt is not None
    assert receipt["provider_request_id"] == "vertex-123"
    assert json.loads(receipt["usage_json"])["total_token_count"] == 10
    assert receipt["actual_cost_amount"] is None
    metadata = json.loads(receipt["response_metadata_json"])
    assert metadata["backend"] == "Vertex AI"
    assert metadata["location"] == (location or "global")
    assert metadata["project"] == "test-project"
    assert "fake-secret-token" not in json.dumps(receipt)
    assert "unused-developer-key" not in json.dumps(receipt)
    assert "unused-google-key" not in json.dumps(receipt)
    persisted_request = json.dumps(state.get_request(result["request_id"]))
    assert "fake-secret-token" not in persisted_request
    assert "unused-developer-key" not in persisted_request
    assert "unused-google-key" not in persisted_request


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_vertex_reference_preflight_and_repair(
    tmp_path: Path, catalog: BrandCatalog, damage: str
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = VertexTestTransport(vertex_response(), state)
    provider = vertex_provider(transport)
    path = runner.assets.path_for(spec.references[0].artifact_id)
    original = path.read_bytes()
    if damage == "corrupt":
        path.write_bytes(b"bad")
    else:
        path.unlink()
    plan = make_plan(spec, provider)
    failed = runner.run(plan, provider)
    assert failed["status"] == "retryable_failure"
    assert state.receipt(failed["request_id"]) is None
    assert not transport.calls
    path.write_bytes(original)
    success = runner.run(plan, provider)
    assert success["status"] == "succeeded"
    assert success["request_id"] == failed["request_id"]
    assert transport.at_send == ["remote_started"]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(429, json={"error": {"message": "quota"}}), "retryable_failure"),
        (httpx.Response(408, json={"error": {"message": "timeout"}}), "ambiguous"),
        (httpx.Response(500, json={"error": {"message": "server"}}), "ambiguous"),
        (httpx.ReadTimeout("timeout"), "ambiguous"),
        (httpx.Response(200, json={}), "ambiguous"),
    ],
)
def test_vertex_remote_failures_preserve_boundary(
    tmp_path: Path, catalog: BrandCatalog,
    response: httpx.Response | Exception, expected: str,
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = VertexTestTransport(response, state)
    provider = vertex_provider(transport)
    plan = make_plan(spec, provider)
    first = runner.run(plan, provider)
    assert first["status"] == expected
    assert transport.at_send == ["remote_started"]
    assert len(transport.calls) == 1
    assert state.receipt(first["request_id"]) is None
    transport.response = vertex_response()
    second = runner.run(plan, provider)
    if expected == "retryable_failure":
        assert second["status"] == "succeeded"
        assert second["request_id"] == first["request_id"]
        assert transport.at_send == ["remote_started", "remote_started"]
    else:
        assert second["action"] == "manual_reconciliation_required"
        assert len(transport.calls) == 1


def test_vertex_multiple_images_are_ambiguous(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    transport = VertexTestTransport(vertex_response(image_count=2), state)
    provider = vertex_provider(transport)
    result = runner.run(make_plan(spec, provider), provider)
    assert result["status"] == "ambiguous"
    assert state.receipt(result["request_id"]) is None


def test_vertex_text_and_one_image_succeeds_without_persisting_text(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    payload = vertex_response().json()
    generated_text = "generated response text is not a benchmark artifact"
    payload["candidates"][0]["content"]["parts"].insert(0, {"text": generated_text})
    transport = VertexTestTransport(httpx.Response(200, json=payload), state)
    provider = vertex_provider(transport)
    result = runner.run(make_plan(spec, provider), provider)
    assert result["status"] == "succeeded"
    assert transport.at_send == ["remote_started"]
    assert len(transport.calls) == 1
    output = state.output(result["request_id"])
    assert output is not None
    assert len(state.candidates(result["request_id"])) == 1
    receipt = state.receipt(result["request_id"])
    assert receipt is not None
    assert receipt["returned_sha256"] == sha256(png_bytes()).hexdigest()
    assert generated_text not in json.dumps(receipt)
    assert generated_text not in json.dumps(state.get_request(result["request_id"]))


def test_vertex_uses_header_request_id_when_response_id_is_absent(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    runner, state, spec = runner_fixture(tmp_path, catalog)
    payload = vertex_response().json()
    payload.pop("responseId")
    transport = VertexTestTransport(httpx.Response(200, json=payload,
                                                    headers={"x-request-id": "header-123"}), state)
    provider = vertex_provider(transport)
    result = runner.run(make_plan(spec, provider), provider)
    assert result["status"] == "succeeded"
    receipt = state.receipt(result["request_id"])
    assert receipt is not None
    assert receipt["provider_request_id"] == "header-123"
