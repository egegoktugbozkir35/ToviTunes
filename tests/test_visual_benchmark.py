import base64
import json
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image

from tovitunes.artifacts.store import AssetStore
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
        self, spec: CanonicalImageSpec, reference_paths: tuple[Path, ...]
    ) -> ProviderResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse:
        self.calls.append((url, headers, body))
        return self.response


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
            json.dumps(
                {"data": [{"b64_json": output}], "usage": {"input_tokens": 4}}
            ).encode(),
        )
    )
    openai = OpenAIImageProvider(transport=openai_transport, api_key="test")
    openai_result = openai.generate(spec, tuple(paths))
    assert openai_result.image_bytes == png_bytes()
    assert openai_result.provider_request_id == "oa-123"
    assert b'gpt-image-2.5-sunburst' in openai_transport.calls[0][2]
    assert openai_transport.calls[0][2].count(b'name="image[]"') == 3

    gemini_transport = FakeTransport(
        HttpResponse(
            200,
            {},
            json.dumps(
                {
                    "id": "gm-123",
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [
                                {"type": "image", "data": output, "mime_type": "image/png"}
                            ],
                        }
                    ],
                }
            ).encode(),
        )
    )
    gemini = GeminiImageProvider(transport=gemini_transport, api_key="test")
    gemini_result = gemini.generate(spec, tuple(paths))
    body = json.loads(gemini_transport.calls[0][2])
    assert gemini_result.provider_request_id == "gm-123"
    assert body["response_format"]["aspect_ratio"] == "9:16"
    assert body["tools"] == []
    assert len(body["input"]) == 4
    assert all("artifact_id" not in item for item in body["input"])


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
    throttled = GeminiImageProvider(
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
    identity_fail = resolve_reviews(
        (scorecard("b", "a", 2), scorecard("b", "b", 2)), rubric
    )
    teaching_fail = resolve_reviews(
        (scorecard("b", "a", 4, 2), scorecard("b", "b", 4, 2)), rubric
    )
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
