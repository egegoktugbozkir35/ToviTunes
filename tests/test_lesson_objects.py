"""Lesson-object v2 stays deterministic, review-gated, and provider-free in tests."""

import io
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tovitunes.artifacts.store import AssetStore
from tovitunes.benchmark.providers import ProviderResult
from tovitunes.config import EnvironmentGenerationConfig, RuntimeConfig
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.persistence.db import Database
from tovitunes.render.composition import (
    LEGACY_PROP_STYLE_VERSION,
    LESSON_OBJECT_STYLE_VERSION,
)
from tovitunes.render.lesson_objects import (
    OBJECT_KEYS,
    generate,
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
