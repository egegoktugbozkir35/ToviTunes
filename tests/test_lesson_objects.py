"""Lesson-object v2 stays deterministic, review-gated, and provider-free in tests."""

import io
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tovitunes.artifacts.store import AssetStore
from tovitunes.benchmark.providers import ProviderFailure, ProviderResult
from tovitunes.config import EnvironmentGenerationConfig, RuntimeConfig
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.persistence.db import Database
from tovitunes.render import lesson_objects
from tovitunes.render.composition import (
    LEGACY_PROP_STYLE_VERSION,
    LESSON_OBJECT_STYLE_VERSION,
)
from tovitunes.render.lesson_objects import (
    OBJECT_KEYS,
    ReviewedChoice,
    _checked_review_choice,
    _prepare_qwen_white_background,
    contact_sheet,
    generate,
    generate_candidates,
    plan,
    resolve_reviewed_assets,
)
from tovitunes.render.props import LESSON_RED, deterministic_swatch, prop_image


class FakeTransparentProvider:
    provider = "google"
    model = "gemini-3-pro-image"
    location = "global"
    image_size = "2K"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, spec, reference_paths, *, on_remote_start=None):
        assert not reference_paths
        if on_remote_start:
            on_remote_start()
        self.calls.append(spec.case_id)
        image = Image.new("RGBA", (1536, 1536))
        draw = ImageDraw.Draw(image)
        if spec.case_id == "red_apple":
            draw.ellipse((330, 260, 1200, 1380), fill=LESSON_RED)
        else:
            draw.ellipse((250, 250, 1286, 1286), fill=LESSON_RED)
        data = io.BytesIO()
        image.save(data, format="PNG")
        return ProviderResult(
            image_bytes=data.getvalue(),
            mime_type="image/png",
            provider_request_id=f"fake-{spec.case_id}",
        )


def runtime(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        database_path=tmp_path / "tovitunes.db",
        data_root=tmp_path / "data",
        brand_root=Path("brands/tovitunes").resolve(),
        environment_generation=EnvironmentGenerationConfig(
            model="gemini-3-pro-image", location="global", image_size="2K"
        ),
    )


def approve_select(store: AssetStore, artifact_id: str) -> None:
    now = datetime.now(UTC)
    store.record_approval(
        ApprovalDecision(
            target_id=artifact_id,
            target_kind="artifact",
            status="approved",
            actor="human:test",
            policy_version="test",
            decided_at=now,
        )
    )
    store.record_rights(
        RightsDecision(
            artifact_id=artifact_id,
            status="commercial_use_confirmed",
            actor="human:test",
            evidence_uri="test://review",
            policy_version="test",
            decided_at=now,
        )
    )
    store.select(artifact_id)


def test_swatch_is_deterministic_and_does_not_use_sphere_shading(monkeypatch) -> None:
    import tovitunes.render.props as props

    monkeypatch.setattr(
        props,
        "sphere_shading",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sphere shading")),
    )
    first = deterministic_swatch(512)
    second = prop_image("red_swatch", 512, style_version=LESSON_OBJECT_STYLE_VERSION)
    assert sha256(first.tobytes()).digest() == sha256(second.tobytes()).digest()
    assert first.getchannel("A").getbbox() is not None
    assert first.getpixel((256, 256))[:3] == (229, 57, 53)


def test_generation_uses_exactly_two_fake_calls_and_leaves_assets_pending(tmp_path: Path) -> None:
    config = runtime(tmp_path)
    provider = FakeTransparentProvider()
    assert plan(config)["provider_calls"] == 0
    result = generate(config, confirmed=True, provider=provider)
    assert provider.calls == list(OBJECT_KEYS)
    assert result["provider_calls"] == 2
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    for item in result["assets"]:
        record = store.get(item["asset_artifact_id"])
        assert store.inspect(record.identity.artifact_id).valid
        assert (
            store.selected("brand", record.identity.owner_id, "lesson_object", item["object_key"])
            is None
        )
        with Image.open(store.path_for(record.identity.artifact_id)) as image:
            assert image.mode == "RGBA" and image.size == (1024, 1024)
            assert image.getchannel("A").getbbox() is not None
    with pytest.raises(ValueError, match="unavailable"):
        resolve_reviewed_assets(store, store.get(result["manifest_artifact_id"]).identity.owner_id)


def test_approved_resolver_checks_sha_alpha_identity_and_transforms(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    result = generate(config, confirmed=True, provider=FakeTransparentProvider())
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    brand_id = store.get(result["manifest_artifact_id"]).identity.owner_id
    for item in result["assets"]:
        approve_select(store, item["source_artifact_id"])
        approve_select(store, item["asset_artifact_id"])
    approve_select(store, result["manifest_artifact_id"])
    paths = resolve_reviewed_assets(store, brand_id)
    apple = prop_image(
        "red_apple",
        360,
        reviewed_asset_path=paths["red_apple"],
        style_version=LESSON_OBJECT_STYLE_VERSION,
    )
    ball = prop_image(
        "red_ball",
        360,
        reviewed_asset_path=paths["red_ball"],
        style_version=LESSON_OBJECT_STYLE_VERSION,
    )
    assert apple.resize((270, 270)).rotate(12, expand=True).getbbox() is not None
    assert ball.resize((432, 432)).rotate(90, expand=True).getbbox() is not None
    path = paths["red_ball"]
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="invalid"):
        resolve_reviewed_assets(store, brand_id)


def test_historical_legacy_props_remain_reproducible() -> None:
    legacy = prop_image("red_apple", 300, style_version=LEGACY_PROP_STYLE_VERSION)
    assert legacy.tobytes() == prop_image("red_apple", 300).tobytes()
    assert legacy.mode == "RGBA" and legacy.size == (300, 300)


def test_generation_rejects_opaque_provider_output(tmp_path: Path) -> None:
    class Opaque(FakeTransparentProvider):
        def generate(self, spec, reference_paths, *, on_remote_start=None):
            result = super().generate(spec, reference_paths, on_remote_start=on_remote_start)
            with Image.open(io.BytesIO(result.image_bytes)) as image:
                opaque = image.convert("RGB")
            data = io.BytesIO()
            opaque.save(data, format="PNG")
            return result.model_copy(update={"image_bytes": data.getvalue()})

    with pytest.raises(ValueError, match="LESSON_OBJECT_TRANSPARENCY_BLOCKED"):
        generate(runtime(tmp_path), confirmed=True, provider=Opaque())


def test_candidate_generation_uses_eight_distinct_pending_slots(tmp_path: Path) -> None:
    config = runtime(tmp_path)
    provider = FakeTransparentProvider()
    result = generate_candidates(config, confirmed=True, provider=provider)
    assert provider.calls == ["red_apple"] * 4 + ["red_ball"] * 4
    assert result["audit"] == {
        "prepared": 8,
        "remote_started": 8,
        "succeeded": 8,
        "terminal_failure": 0,
        "retryable_failure": 0,
        "ambiguous": 0,
        "technically_rejected_after_success": 0,
        "actual_live_image_requests": 8,
    }
    candidates = result["candidates"]
    assert len(candidates) == 8
    assert len({item["candidate_slot"] for item in candidates}) == 8
    assert all(item["review_status"] == "pending" for item in candidates)
    assert all(item["rights_status"] == "review_required" for item in candidates)
    assert all(item["normalized_dimensions"] == [1024, 1024] for item in candidates)
    output = contact_sheet(config, result, tmp_path / "review.png")
    with Image.open(output) as sheet:
        assert sheet.width >= 1700 and sheet.height >= 1300


def test_candidate_generation_retains_rejected_source_and_continues(tmp_path: Path) -> None:
    class FirstOpaque(FakeTransparentProvider):
        def generate(self, spec, reference_paths, *, on_remote_start=None):
            result = super().generate(spec, reference_paths, on_remote_start=on_remote_start)
            if len(self.calls) != 1:
                return result
            with Image.open(io.BytesIO(result.image_bytes)) as image:
                opaque = image.convert("RGB").convert("RGBA")
            data = io.BytesIO()
            opaque.save(data, format="PNG")
            return result.model_copy(update={"image_bytes": data.getvalue()})

    result = generate_candidates(runtime(tmp_path), confirmed=True, provider=FirstOpaque())
    first = result["candidates"][0]
    assert first["technical_validation_status"] == "rejected"
    assert first["source_artifact_id"]
    assert first["normalized_artifact_id"] is None
    assert len(result["candidates"]) == 8
    assert result["audit"]["technically_rejected_after_success"] == 1


def test_first_terminal_access_failure_stops_candidate_run(tmp_path: Path) -> None:
    class Blocked(FakeTransparentProvider):
        def generate(self, spec, reference_paths, *, on_remote_start=None):
            if on_remote_start:
                on_remote_start()
            self.calls.append(spec.case_id)
            raise ProviderFailure(
                "Vertex AI returned HTTP 403 (PERMISSION_DENIED): blocked",
                outcome="terminal_failure",
                diagnostics={
                    "http_status": 403,
                    "canonical_status": "PERMISSION_DENIED",
                    "provider_message": "blocked",
                },
            )

    provider = Blocked()
    with pytest.raises(ProviderFailure, match="PERMISSION_DENIED"):
        generate_candidates(runtime(tmp_path), confirmed=True, provider=provider)
    assert provider.calls == ["red_apple"]


def test_qwen_raw_source_is_retained_and_exterior_white_becomes_alpha(tmp_path: Path) -> None:
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((44, 35, 211, 220), fill=(220, 42, 40))
    draw.ellipse((100, 90, 125, 115), fill=(252, 252, 252))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    prepared = _prepare_qwen_white_background(raw, "image/png")
    assert prepared.getpixel((0, 0))[3] == 0
    assert prepared.getpixel((112, 102))[3] == 255

    class OpaqueQwen(FakeTransparentProvider):
        provider = "qwen_comfyui"
        model = "qwen-image-2.1-q8"

        def generate(self, spec, reference_paths, *, on_remote_start=None):
            self.calls.append(spec.case_id)
            if on_remote_start:
                on_remote_start()
            return ProviderResult(
                image_bytes=raw,
                mime_type="image/png",
                provider_request_id=f"local-{spec.case_id}",
                response_metadata={"seed": spec.attempt, "workflow_sha256": "f" * 64},
            )

    config = runtime(tmp_path)
    result = generate_candidates(
        config, confirmed=True, candidates_per_object=1, provider=OpaqueQwen()
    )
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    assert result["audit"]["actual_live_image_requests"] == 2
    for candidate in result["candidates"]:
        assert candidate["source_sha256"] == sha256(raw).hexdigest()
        assert store.path_for(candidate["source_artifact_id"]).read_bytes() == raw
        assert candidate["technical_validation_status"] == "valid"
        assert candidate["review_status"] == "pending"
        assert candidate["rights_status"] == "review_required"
        assert candidate["generation_metadata"]["workflow_sha256"] == "f" * 64
        with Image.open(store.path_for(candidate["normalized_artifact_id"])) as normalized:
            assert normalized.mode == "RGBA"
            assert normalized.size == (1024, 1024)
            assert normalized.getpixel((0, 0))[3] == 0


def test_qwen_unsafe_background_fails_closed() -> None:
    image = Image.new("RGB", (256, 256), (220, 224, 230))
    ImageDraw.Draw(image).ellipse((50, 50, 200, 200), fill=(220, 42, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    with pytest.raises(ValueError, match="LESSON_OBJECT_WHITE_BACKGROUND_UNSAFE"):
        _prepare_qwen_white_background(buffer.getvalue(), "image/png")


class FakeReviewedQwen(FakeTransparentProvider):
    provider = "qwen_comfyui"
    model = "qwen-image-2.1-q8"

    def generate(self, spec, reference_paths, *, on_remote_start=None):
        assert not reference_paths
        if on_remote_start:
            on_remote_start()
        self.calls.append(spec.case_id)
        image = Image.new("RGB", (256, 256), "white")
        ImageDraw.Draw(image).ellipse((42, 38, 214, 222), fill=LESSON_RED)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return ProviderResult(
            image_bytes=buffer.getvalue(),
            mime_type="image/png",
            provider_request_id=f"fake-{spec.case_id}",
            response_metadata={"seed": spec.attempt},
        )


@pytest.fixture
def review_setup(tmp_path: Path, monkeypatch):
    config = runtime(tmp_path)
    provider = FakeReviewedQwen()
    generated = generate_candidates(
        config, confirmed=True, candidates_per_object=2, provider=provider
    )
    choices = {}
    for object_key in OBJECT_KEYS:
        item = next(
            c
            for c in generated["candidates"]
            if c["object_key"] == object_key and c["candidate_index"] == 2
        )
        choices[object_key] = ReviewedChoice(
            object_key=object_key,
            candidate_id=item["normalized_artifact_id"],
            candidate_sha256=item["normalized_sha256"],
            source_id=item["source_artifact_id"],
            source_sha256=item["source_sha256"],
            provider_request_id=item["provider_request_id"],
            seed=2,
        )
    monkeypatch.setattr(lesson_objects, "REVIEWED_CHOICES", choices)
    monkeypatch.setattr(lesson_objects, "REVIEW_MANIFEST_ID", generated["manifest_artifact_id"])
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    brand_id = store.get(generated["manifest_artifact_id"]).identity.owner_id
    return config, provider, generated, choices, store, brand_id


def _finalize_fake(config, choices):
    return lesson_objects.finalize_review(
        config,
        apple_candidate_id=choices["red_apple"].candidate_id,
        ball_candidate_id=choices["red_ball"].candidate_id,
        actor="human:test-reviewer",
        reason="Human visual review chose candidate 02 for the pilot.",
    )


def test_finalization_selects_dependencies_and_preserves_exact_bytes(review_setup, monkeypatch):
    config, provider, generated, choices, store, brand_id = review_setup
    before_calls = list(provider.calls)
    selection_order = []
    original_select = AssetStore.select

    def recorded_select(self, artifact_id):
        record = self.get(artifact_id)
        selection_order.append((record.identity.kind, record.identity.slot_key))
        return original_select(self, artifact_id)

    monkeypatch.setattr(AssetStore, "select", recorded_select)
    report = _finalize_fake(config, choices)
    assert provider.calls == before_calls  # no provider generation during finalization
    assert report["image_generation_calls"] == 0
    assert selection_order == [
        ("lesson_object_source", "red_apple_candidate_02"),
        ("lesson_object_candidate", "red_apple_candidate_02"),
        ("lesson_object", "red_apple"),
        ("lesson_object_source", "red_ball_candidate_02"),
        ("lesson_object_candidate", "red_ball_candidate_02"),
        ("lesson_object", "red_ball"),
        ("lesson_object_manifest", "lesson_object_assets_v2"),
    ]
    for item in report["assets"]:
        key = item["object_key"]
        canonical = store.get(item["canonical_artifact_id"])
        assert canonical.identity.slot_key == key
        assert canonical.sha256 == choices[key].candidate_sha256
        assert (
            store.path_for(canonical.identity.artifact_id).read_bytes()
            == store.path_for(choices[key].candidate_id).read_bytes()
        )
        assert store.selected("brand", brand_id, "lesson_object", key) == canonical
        assert lesson_objects._current_review_state(store, canonical.identity.artifact_id) == (
            "approved",
            "review_required",
        )
        for dependency_id in (choices[key].source_id, choices[key].candidate_id):
            assert lesson_objects._current_review_state(store, dependency_id) == (
                "approved",
                "review_required",
            )
    manifest = store.selected(
        "brand", brand_id, "lesson_object_manifest", "lesson_object_assets_v2"
    )
    assert manifest.identity.artifact_id == report["canonical_manifest_artifact_id"]
    assert manifest.provenance.input_artifact_ids == tuple(
        item["canonical_artifact_id"] for item in report["assets"]
    )
    assert lesson_objects._current_review_state(store, manifest.identity.artifact_id) == (
        "approved",
        "review_required",
    )
    assert set(resolve_reviewed_assets(store, brand_id)) == set(OBJECT_KEYS)
    assert (
        store.read_json(generated["manifest_artifact_id"])["candidates"] == generated["candidates"]
    )


@pytest.mark.parametrize("wrong_key", ["red_apple", "red_ball"])
def test_finalization_rejects_wrong_candidate_id(review_setup, wrong_key):
    config, _, _, choices, store, brand_id = review_setup
    other_key = "red_ball" if wrong_key == "red_apple" else "red_apple"
    ids = {key: choice.candidate_id for key, choice in choices.items()}
    ids[wrong_key] = choices[other_key].candidate_id
    with pytest.raises(ValueError, match="pinned human choices"):
        lesson_objects.finalize_review(
            config,
            apple_candidate_id=ids["red_apple"],
            ball_candidate_id=ids["red_ball"],
            actor="human:test-reviewer",
            reason="reviewed",
        )
    assert (
        store.selected("brand", brand_id, "lesson_object_manifest", "lesson_object_assets_v2")
        is None
    )


@pytest.mark.parametrize("object_key", OBJECT_KEYS)
def test_review_validation_rejects_wrong_candidate_slot(review_setup, object_key):
    _, _, generated, choices, store, brand_id = review_setup
    other = "red_ball" if object_key == "red_apple" else "red_apple"
    with pytest.raises(ValueError, match="candidate identity or bytes differ"):
        _checked_review_choice(
            store,
            brand_id,
            replace(choices[object_key], object_key=other),
            generated["candidates"],
        )


@pytest.mark.parametrize("problem", ["sha", "source", "rejected", "rights_blocked"])
def test_review_validation_fails_closed(review_setup, problem):
    _, _, generated, choices, store, brand_id = review_setup
    choice = choices["red_apple"]
    if problem == "sha":
        choice = replace(choice, candidate_sha256="0" * 64)
    elif problem == "source":
        choice = replace(choice, source_id=choices["red_ball"].source_id)
    elif problem == "rejected":
        store.record_approval(
            ApprovalDecision(
                target_id=choice.candidate_id,
                target_kind="artifact",
                status="rejected",
                actor="human:test-reviewer",
                reason="rejected",
                policy_version="test",
                decided_at=datetime.now(UTC),
            )
        )
    else:
        store.record_rights(
            RightsDecision(
                artifact_id=choice.candidate_id,
                status="blocked",
                actor="human:test-reviewer",
                policy_version="test",
                decided_at=datetime.now(UTC),
            )
        )
    with pytest.raises(ValueError):
        _checked_review_choice(store, brand_id, choice, generated["candidates"])


def test_review_validation_rejects_missing_source_dependency(review_setup):
    config, _, generated, choices, store, brand_id = review_setup
    choice = choices["red_apple"]
    original = store.get(choice.candidate_id)
    stage = config.database_path.parent / "candidate-without-dependency.png"
    stage.write_bytes(store.path_for(choice.candidate_id).read_bytes())
    trusted = AssetStore(config.data_root, store.database, generated_source_roots=[stage.parent])
    detached = trusted.ingest(
        stage,
        owner_scope="brand",
        owner_id=brand_id,
        kind="lesson_object_candidate",
        slot_key="red_apple_candidate_02",
        provenance=original.provenance,
        expected_media_type="image/png",
    )
    with pytest.raises(ValueError, match="source dependency differs"):
        _checked_review_choice(
            trusted,
            brand_id,
            replace(choice, candidate_id=detached.identity.artifact_id),
            generated["candidates"],
        )


def test_manifest_not_selected_when_canonical_object_selection_fails(review_setup, monkeypatch):
    config, _, _, choices, store, brand_id = review_setup
    original_select = AssetStore.select

    def fail_ball(self, artifact_id):
        record = self.get(artifact_id)
        if record.identity.kind == "lesson_object" and record.identity.slot_key == "red_ball":
            raise ValueError("ball canonical selection failed")
        return original_select(self, artifact_id)

    monkeypatch.setattr(AssetStore, "select", fail_ball)
    with pytest.raises(ValueError, match="ball canonical selection failed"):
        _finalize_fake(config, choices)
    assert (
        store.selected("brand", brand_id, "lesson_object_manifest", "lesson_object_assets_v2")
        is None
    )
