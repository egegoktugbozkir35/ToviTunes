"""Reviewed lesson-object generation, normalization, resolution, and review export."""

import io
import json
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

from PIL import Image, ImageDraw, ImageFont
from pydantic import Field

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.benchmark.models import CanonicalImageSpec
from tovitunes.benchmark.providers import GeminiImageProvider, ImageProvider, ProviderFailure
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.domain.storyboard import ProductionModel
from tovitunes.persistence.db import Database
from tovitunes.render.composition import LEGACY_PROP_STYLE_VERSION, LESSON_OBJECT_STYLE_VERSION
from tovitunes.render.props import LESSON_RED, deterministic_swatch, prop_image

PROMPT_VERSION = "lesson_object_assets_v2"
OBJECT_KEYS = ("red_apple", "red_ball")
CANVAS = (1024, 1024)
ANCHORS = {"red_apple": "bottom_center", "red_ball": "bottom_center"}
SOURCE_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

OBJECT_BRIEFS = {
    "red_apple": (
        "One isolated red apple. Premium polished 2D children's illustration; clearly an apple; "
        "organic asymmetric silhouette; red is educationally dominant; soft dimensional shading; "
        "subtle natural surface variation; simple brown stem and friendly green leaf. No face, "
        "eyes, limbs, character personality, text, logo, background, cast environment, "
        "photorealism, shiny "
        "plastic, toy-sphere shading, extreme specular highlights, or franchise resemblance."
    ),
    "red_ball": (
        "One isolated preschool toy ball. Clearly spherical and immediately identifiable as a "
        "ball; "
        "dominant red color; premium polished 2D children's illustration; believable toy/rubber "
        "material; subtle dimensional shading; one or two restrained curved seams if useful. No "
        "face, "
        "text, logo, character features, background, generic red-orb appearance, plastic gloss, or "
        "franchise resemblance."
    ),
}


class LessonObjectAsset(ProductionModel):
    object_key: str
    visual_role: Literal["teaching_object"] = "teaching_object"
    asset_artifact_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_artifact_id: str
    source_kind: Literal["provider"] = "provider"
    provider: str
    model: str
    dimensions: tuple[int, int] = CANVAS
    transparency: Literal["rgba_trimmed_alpha"] = "rgba_trimmed_alpha"
    anchor: Literal["bottom_center"] = "bottom_center"
    review_status: Literal["pending", "approved"] = "pending"
    rights_status: Literal["review_required", "commercial_use_confirmed"] = "review_required"
    lesson_color: str = LESSON_RED


class LessonObjectCandidate(ProductionModel):
    object_key: str
    candidate_index: int = Field(ge=1, le=4)
    candidate_slot: str
    source_artifact_id: str
    normalized_artifact_id: str | None = None
    source_sha256: str
    normalized_sha256: str | None = None
    provider: str
    model: str
    location: str
    requested_resolution: str
    provider_request_id: str | None = None
    local_request_id: str
    source_dimensions: tuple[int, int]
    normalized_dimensions: tuple[int, int] | None = None
    mime_type: str
    usage_metadata: dict[str, Any] | None = None
    technical_validation_status: Literal["valid", "rejected"]
    technical_rejection_reason: str | None = None
    review_status: Literal["pending"] = "pending"
    rights_status: Literal["review_required"] = "review_required"
    generation_timestamp: datetime


def _store(config: RuntimeConfig) -> tuple[AssetStore, str]:
    database = Database(config.database_path)
    database.migrate()
    catalog = load_brand(config.brand_root)
    database.register_catalog(catalog)
    return AssetStore(config.data_root, database), catalog.version.revision_id


def _provider(config: RuntimeConfig) -> GeminiImageProvider:
    generation = config.environment_generation
    if (
        generation.model != "gemini-3-pro-image"
        or generation.location != "global"
        or generation.image_size != "2K"
    ):
        raise ValueError("lesson-object generation requires gemini-3-pro-image/global/2K")
    return GeminiImageProvider(
        model=generation.model,
        location=generation.location,
        image_size=generation.image_size,
        aspect_ratio="1:1",
    )


def _spec(object_key: str, brand_id: str) -> CanonicalImageSpec:
    if object_key not in OBJECT_KEYS:
        raise ValueError("unsupported lesson object")
    return CanonicalImageSpec(
        benchmark_version=LESSON_OBJECT_STYLE_VERSION,
        prompt_version=PROMPT_VERSION,
        case_id=object_key,
        attempt=1,
        scene_brief=OBJECT_BRIEFS[object_key],
        teaching_check=(
            f"The semantic identity is exactly {object_key}; curriculum lesson_color remains "
            f"{LESSON_RED} independently of illustration pixels."
        ),
        common_brief=(
            "Create one centered reusable transparent-background asset for compositing into "
            "ToviTunes and Nano Banana Pro environments. Preserve generous transparent padding."
        ),
        composition_brief=(
            "Square 1:1 canvas. One complete object, centered, isolated, no crop, "
            "no cast environment."
        ),
        reference_instructions="No reference image. Do not add a character or environment.",
        pack_revision_id="lesson-object-only",
        brand_revision_id=brand_id,
        references=(),
        palette={"authoritative_lesson_red": LESSON_RED},
        identity_rules=(
            "Output a PNG with real alpha transparency.",
            "Keep the object visually compatible with polished rounded preschool 2D art.",
        ),
        forbidden_changes=(
            "No background or opaque rectangular canvas.",
            "No text, logo, watermark, face, eyes, limbs, or character personality.",
        ),
        negative_constraints=(
            "No photorealism, 3D render, famous franchise, or named artist imitation.",
            "No white, colored, checkerboard, or scenic background; use transparent alpha.",
        ),
        aspect_ratio="1:1",
    )


def plan(config: RuntimeConfig) -> dict[str, object]:
    brand_id = load_brand(config.brand_root).version.revision_id
    provider = _provider(config)
    return {
        "style_version": LESSON_OBJECT_STYLE_VERSION,
        "provider": provider.provider,
        "model": provider.model,
        "location": provider.location,
        "image_size": provider.image_size,
        "request_count": 2,
        "provider_calls": 0,
        "requests": [
            {"object_key": key, "prompt": _spec(key, brand_id).prompt()} for key in OBJECT_KEYS
        ],
    }


def _validate_transparent_source(data: bytes, mime_type: str) -> Image.Image:
    if mime_type != "image/png":
        raise ValueError("LESSON_OBJECT_TRANSPARENCY_BLOCKED")
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        if opened.format != "PNG" or opened.mode != "RGBA":
            raise ValueError("LESSON_OBJECT_TRANSPARENCY_BLOCKED")
        image = cast(Image.Image, opened.copy())
    alpha = image.getchannel("A")
    bounds = alpha.getbbox()
    if bounds is None or alpha.getextrema()[0] == 255:
        raise ValueError("LESSON_OBJECT_TRANSPARENCY_BLOCKED")
    corners = (
        (0, 0),
        (image.width - 1, 0),
        (0, image.height - 1),
        (image.width - 1, image.height - 1),
    )
    if any(cast(int, alpha.getpixel(point)) > 8 for point in corners):
        raise ValueError("LESSON_OBJECT_TRANSPARENCY_BLOCKED")
    return image


def normalize(image: Image.Image) -> Image.Image:
    if image.mode != "RGBA":
        raise ValueError("lesson object source must be RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("lesson object has empty alpha bounds")
    trimmed = image.crop(bounds)
    max_width, max_height = round(CANVAS[0] * 0.84), round(CANVAS[1] * 0.84)
    scale = min(max_width / trimmed.width, max_height / trimmed.height)
    fitted = trimmed.resize(
        (max(1, round(trimmed.width * scale)), max(1, round(trimmed.height * scale))),
        Image.Resampling.LANCZOS,
    )
    output = Image.new("RGBA", CANVAS)
    output.alpha_composite(fitted, ((CANVAS[0] - fitted.width) // 2, CANVAS[1] - fitted.height))
    return output


def _pending(store: AssetStore, artifact_id: str) -> None:
    now = datetime.now(UTC)
    store.record_approval(
        ApprovalDecision(
            target_id=artifact_id,
            target_kind="artifact",
            status="pending",
            actor="machine:tovitunes.lesson_objects",
            reason="Canonical candidate awaits human visual review.",
            policy_version=PROMPT_VERSION,
            decided_at=now,
        )
    )
    store.record_rights(
        RightsDecision(
            artifact_id=artifact_id,
            status="review_required",
            actor="machine:tovitunes.lesson_objects",
            rationale="Provider output requires human rights review before production selection.",
            policy_version=PROMPT_VERSION,
            decided_at=now,
        )
    )


def generate(
    config: RuntimeConfig,
    *,
    confirmed: bool,
    provider: ImageProvider | None = None,
) -> dict[str, object]:
    if not confirmed:
        raise ValueError("live image generation requires --confirm-provider-generation")
    configured = provider or _provider(config)
    if isinstance(configured, GeminiImageProvider):
        configured.preflight()
    store, brand_id = _store(config)
    working = config.data_root / ".lesson-object-working"
    working.mkdir(exist_ok=True)
    assets: list[LessonObjectAsset] = []
    provider_calls = 0
    with tempfile.TemporaryDirectory(dir=working) as dirname:
        stage = Path(dirname)
        trusted = AssetStore(config.data_root, store.database, generated_source_roots=[stage])
        for object_key in OBJECT_KEYS:
            spec = _spec(object_key, brand_id)
            provider_calls += 1
            result = configured.generate(spec, (), on_remote_start=lambda: None)
            suffix = SOURCE_SUFFIXES.get(result.mime_type)
            if suffix is None:
                raise ValueError("LESSON_OBJECT_TRANSPARENCY_BLOCKED")
            source_path = stage / f"{object_key}-source{suffix}"
            source_path.write_bytes(result.image_bytes)
            source = trusted.ingest(
                source_path,
                owner_scope="brand",
                owner_id=brand_id,
                kind="lesson_object_source",
                slot_key=object_key,
                provenance=Provenance(
                    source_kind="provider",
                    acquired_at=datetime.now(UTC),
                    provider=configured.provider,
                    model=configured.model,
                    request_id=result.provider_request_id,
                    local_request_id=spec.fingerprint(),
                    prompt_version=PROMPT_VERSION,
                ),
            )
            _pending(trusted, source.identity.artifact_id)
            normalized = normalize(
                _validate_transparent_source(result.image_bytes, result.mime_type)
            )
            normalized_path = stage / f"{object_key}.png"
            normalized.save(normalized_path, format="PNG", optimize=False)
            record = trusted.ingest(
                normalized_path,
                owner_scope="brand",
                owner_id=brand_id,
                kind="lesson_object",
                slot_key=object_key,
                provenance=Provenance(
                    source_kind="deterministic",
                    acquired_at=datetime.now(UTC),
                    provider="tovitunes.lesson_objects.normalize",
                    model=LESSON_OBJECT_STYLE_VERSION,
                    input_artifact_ids=(source.identity.artifact_id,),
                ),
                dependencies=[InputDependency(source.identity.artifact_id, "provider_source")],
                expected_media_type="image/png",
            )
            _pending(trusted, record.identity.artifact_id)
            assets.append(
                LessonObjectAsset(
                    object_key=object_key,
                    asset_artifact_id=record.identity.artifact_id,
                    sha256=record.sha256,
                    source_artifact_id=source.identity.artifact_id,
                    provider=configured.provider,
                    model=configured.model,
                )
            )
        manifest_path = stage / "lesson-object-assets-v2.json"
        manifest_path.write_text(
            json.dumps([a.model_dump(mode="json") for a in assets], sort_keys=True),
            encoding="utf-8",
        )
        manifest = trusted.ingest(
            manifest_path,
            owner_scope="brand",
            owner_id=brand_id,
            kind="lesson_object_manifest",
            slot_key=LESSON_OBJECT_STYLE_VERSION,
            provenance=Provenance(
                source_kind="deterministic",
                acquired_at=datetime.now(UTC),
                provider="tovitunes.lesson_objects",
                model=LESSON_OBJECT_STYLE_VERSION,
                input_artifact_ids=tuple(a.asset_artifact_id for a in assets),
            ),
            dependencies=[InputDependency(a.asset_artifact_id, a.object_key) for a in assets],
        )
        _pending(trusted, manifest.identity.artifact_id)
    return {
        "style_version": LESSON_OBJECT_STYLE_VERSION,
        "provider_calls": provider_calls,
        "assets": [a.model_dump(mode="json") for a in assets],
        "manifest_artifact_id": manifest.identity.artifact_id,
    }


def _candidate_slot(object_key: str, candidate_index: int) -> str:
    if object_key not in OBJECT_KEYS or not 1 <= candidate_index <= 4:
        raise ValueError("unsupported lesson-object candidate identity")
    return f"{object_key}_candidate_{candidate_index:02d}"


def _source_dimensions(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        return int(opened.width), int(opened.height)


def _request_audit_entry(
    object_key: str, candidate_index: int, local_request_id: str
) -> dict[str, object]:
    return {
        "object_key": object_key,
        "candidate_index": candidate_index,
        "local_request_id": local_request_id,
        "prepared": True,
        "remote_started": False,
        "outcome": None,
        "provider_request_id": None,
        "diagnostics": {},
        "technically_rejected_after_success": False,
    }


def generate_candidates(
    config: RuntimeConfig,
    *,
    confirmed: bool,
    candidates_per_object: int = 4,
    provider: ImageProvider | None = None,
) -> dict[str, object]:
    """Generate an explicit, bounded review set without changing canonical selection."""
    if not confirmed:
        raise ValueError("live image generation requires --confirm-provider-generation")
    if not 1 <= candidates_per_object <= 4:
        raise ValueError("candidates-per-object must be between 1 and 4")
    planned = len(OBJECT_KEYS) * candidates_per_object
    if planned > 8:
        raise ValueError("lesson-object candidate generation exceeds the eight-call ceiling")
    configured = provider or _provider(config)
    if isinstance(configured, GeminiImageProvider):
        configured.preflight()
    store, brand_id = _store(config)
    working = config.data_root / ".lesson-object-working"
    working.mkdir(exist_ok=True)
    candidates: list[LessonObjectCandidate] = []
    requests: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(dir=working) as dirname:
        stage = Path(dirname)
        trusted = AssetStore(config.data_root, store.database, generated_source_roots=[stage])
        for object_key in OBJECT_KEYS:
            for candidate_index in range(1, candidates_per_object + 1):
                spec = _spec(object_key, brand_id).model_copy(update={"attempt": candidate_index})
                local_request_id = spec.fingerprint()
                audit = _request_audit_entry(object_key, candidate_index, local_request_id)
                requests.append(audit)

                def mark_remote_started(entry: dict[str, object] = audit) -> None:
                    entry["remote_started"] = True

                try:
                    result = configured.generate(
                        spec, (), on_remote_start=mark_remote_started
                    )
                except ProviderFailure as exc:
                    audit["outcome"] = exc.outcome
                    audit["provider_request_id"] = exc.provider_request_id
                    audit["diagnostics"] = exc.diagnostics
                    if object_key == "red_apple" and candidate_index == 1 and (
                        exc.outcome == "terminal_failure"
                        or exc.diagnostics.get("http_status") == 403
                        or exc.diagnostics.get("canonical_status") == "PERMISSION_DENIED"
                    ):
                        raise
                    continue

                audit["outcome"] = "succeeded"
                audit["provider_request_id"] = result.provider_request_id
                slot = _candidate_slot(object_key, candidate_index)
                suffix = SOURCE_SUFFIXES.get(result.mime_type)
                if suffix is None:
                    audit["technically_rejected_after_success"] = True
                    audit["technical_rejection_reason"] = "unsupported provider MIME type"
                    continue
                generated_at = datetime.now(UTC)
                source_path = stage / f"{slot}-source{suffix}"
                source_path.write_bytes(result.image_bytes)
                source = trusted.ingest(
                    source_path,
                    owner_scope="brand",
                    owner_id=brand_id,
                    kind="lesson_object_source",
                    slot_key=slot,
                    provenance=Provenance(
                        source_kind="provider",
                        acquired_at=generated_at,
                        provider=configured.provider,
                        model=configured.model,
                        request_id=result.provider_request_id,
                        local_request_id=local_request_id,
                        prompt_version=PROMPT_VERSION,
                    ),
                )
                _pending(trusted, source.identity.artifact_id)
                source_dimensions = _source_dimensions(result.image_bytes)
                normalized_record = None
                rejection_reason = None
                try:
                    normalized = normalize(
                        _validate_transparent_source(result.image_bytes, result.mime_type)
                    )
                except ValueError as exc:
                    rejection_reason = str(exc)
                    audit["technically_rejected_after_success"] = True
                    audit["technical_rejection_reason"] = rejection_reason
                else:
                    normalized_path = stage / f"{slot}.png"
                    normalized.save(normalized_path, format="PNG", optimize=False)
                    normalized_record = trusted.ingest(
                        normalized_path,
                        owner_scope="brand",
                        owner_id=brand_id,
                        kind="lesson_object_candidate",
                        slot_key=slot,
                        provenance=Provenance(
                            source_kind="deterministic",
                            acquired_at=generated_at,
                            provider="tovitunes.lesson_objects.normalize",
                            model=LESSON_OBJECT_STYLE_VERSION,
                            input_artifact_ids=(source.identity.artifact_id,),
                        ),
                        dependencies=[
                            InputDependency(source.identity.artifact_id, "provider_source")
                        ],
                        expected_media_type="image/png",
                    )
                    _pending(trusted, normalized_record.identity.artifact_id)
                candidates.append(
                    LessonObjectCandidate(
                        object_key=object_key,
                        candidate_index=candidate_index,
                        candidate_slot=slot,
                        source_artifact_id=source.identity.artifact_id,
                        normalized_artifact_id=(
                            normalized_record.identity.artifact_id if normalized_record else None
                        ),
                        source_sha256=source.sha256,
                        normalized_sha256=(
                            normalized_record.sha256 if normalized_record else None
                        ),
                        provider=configured.provider,
                        model=configured.model,
                        location=str(getattr(configured, "location", "unknown")),
                        requested_resolution=str(
                            getattr(configured, "image_size", "unknown")
                        ),
                        provider_request_id=result.provider_request_id,
                        local_request_id=local_request_id,
                        source_dimensions=source_dimensions,
                        normalized_dimensions=CANVAS if normalized_record else None,
                        mime_type=result.mime_type,
                        usage_metadata=result.usage,
                        technical_validation_status=(
                            "valid" if normalized_record else "rejected"
                        ),
                        technical_rejection_reason=rejection_reason,
                        generation_timestamp=generated_at,
                    )
                )
        manifest_data = {
            "style_version": LESSON_OBJECT_STYLE_VERSION,
            "prompt_version": PROMPT_VERSION,
            "generation_budget": planned,
            "requests": requests,
            "candidates": [item.model_dump(mode="json") for item in candidates],
        }
        manifest_path = stage / "lesson-object-candidates-v2.json"
        manifest_path.write_text(
            json.dumps(manifest_data, sort_keys=True), encoding="utf-8"
        )
        manifest = trusted.ingest(
            manifest_path,
            owner_scope="brand",
            owner_id=brand_id,
            kind="lesson_object_candidate_manifest",
            slot_key=LESSON_OBJECT_STYLE_VERSION,
            provenance=Provenance(
                source_kind="deterministic",
                acquired_at=datetime.now(UTC),
                provider="tovitunes.lesson_objects.candidates",
                model=LESSON_OBJECT_STYLE_VERSION,
                input_artifact_ids=tuple(
                    item.normalized_artifact_id or item.source_artifact_id
                    for item in candidates
                ),
            ),
            dependencies=[
                InputDependency(
                    item.normalized_artifact_id or item.source_artifact_id,
                    item.candidate_slot,
                )
                for item in candidates
            ],
        )
        _pending(trusted, manifest.identity.artifact_id)
    audit_totals = {
        "prepared": len(requests),
        "remote_started": sum(bool(item["remote_started"]) for item in requests),
        "succeeded": sum(item["outcome"] == "succeeded" for item in requests),
        "terminal_failure": sum(item["outcome"] == "terminal_failure" for item in requests),
        "retryable_failure": sum(
            item["outcome"] == "retryable_failure" for item in requests
        ),
        "ambiguous": sum(item["outcome"] == "ambiguous" for item in requests),
        "technically_rejected_after_success": sum(
            bool(item["technically_rejected_after_success"]) for item in requests
        ),
        "actual_live_image_requests": sum(
            bool(item["remote_started"]) for item in requests
        ),
    }
    return {
        "style_version": LESSON_OBJECT_STYLE_VERSION,
        "generation_budget": planned,
        "audit": audit_totals,
        "requests": requests,
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "manifest_artifact_id": manifest.identity.artifact_id,
    }


def resolve_reviewed_assets(
    store: AssetStore, brand_id: str, object_keys: tuple[str, ...] = OBJECT_KEYS
) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for object_key in object_keys:
        record = store.selected("brand", brand_id, "lesson_object", object_key)
        if record is None:
            raise ValueError(f"selected reviewed lesson-object asset unavailable: {object_key}")
        if record.identity.slot_key != object_key or record.mime_type != "image/png":
            raise ValueError("renderer lesson-object identity mismatch")
        path = store.path_for(record.identity.artifact_id)
        with Image.open(path) as image:
            image.load()
            bounds = image.getchannel("A").getbbox()
            if (
                image.mode != "RGBA"
                or image.size != CANVAS
                or bounds is None
                or bounds[3] != CANVAS[1]
            ):
                raise ValueError("selected lesson-object RGBA contract failed")
        resolved[object_key] = path
    return resolved


def contact_sheet(config: RuntimeConfig, generation: dict[str, object], output: Path) -> Path:
    store, _ = _store(config)
    raw_assets = generation.get("candidates", generation.get("assets"))
    if not isinstance(raw_assets, list):
        raise ValueError("generation result lacks lesson objects")
    candidate_mode = "candidates" in generation
    cards: list[tuple[str, Image.Image]] = [
        (
            "SWATCH — Legacy",
            prop_image("red_swatch", 420, style_version=LEGACY_PROP_STYLE_VERSION),
        ),
        ("SWATCH — V2 deterministic", deterministic_swatch(420)),
        (
            "APPLE — Legacy",
            prop_image("red_apple", 420, style_version=LEGACY_PROP_STYLE_VERSION),
        ),
        (
            "BALL — Legacy",
            prop_image("red_ball", 420, style_version=LEGACY_PROP_STYLE_VERSION),
        ),
    ]
    generated_cards: dict[str, list[tuple[str, Image.Image]]] = {
        "red_apple": [],
        "red_ball": [],
    }
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        artifact_id = item.get("normalized_artifact_id", item.get("asset_artifact_id"))
        if not isinstance(artifact_id, str):
            continue
        object_key = str(item["object_key"])
        index = int(item.get("candidate_index", 1))
        generated_cards[object_key].append(
            (
                f"{object_key.removeprefix('red_').upper()} — Candidate {index:02d}",
                prop_image(
                    object_key,
                    420,
                    reviewed_asset_path=store.path_for(artifact_id),
                    style_version=LESSON_OBJECT_STYLE_VERSION,
                ),
            )
        )
    if not candidate_mode:
        generated_cards["red_apple"][0] = (
            "APPLE — Candidate 01", generated_cards["red_apple"][0][1]
        )
        generated_cards["red_ball"][0] = (
            "BALL — Candidate 01", generated_cards["red_ball"][0][1]
        )
    ordered_cards = cards[:2]
    ordered_cards.extend([cards[2], *generated_cards["red_apple"]])
    ordered_cards.extend([cards[3], *generated_cards["red_ball"]])
    columns = 5
    card_width, card_height = 330, 385
    rows = (len(ordered_cards) + columns - 1) // columns
    sheet = Image.new("RGB", (1770, 110 + rows * 420), "#F4F1EA")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=22)
    title_font = ImageFont.load_default(size=38)
    draw.text((60, 35), "ToviTunes Prop Art V2 — Human Review", fill="#243047", font=title_font)
    for index, (label, image) in enumerate(ordered_cards):
        column, row = index % columns, index // columns
        x, y = 40 + column * 345, 105 + row * 420
        draw.rounded_rectangle(
            (x, y, x + card_width, y + card_height),
            radius=24,
            fill="white",
            outline="#D8D4CA",
            width=3,
        )
        checker = Image.new("RGB", (290, 290), "white")
        check = ImageDraw.Draw(checker)
        for cy in range(0, 290, 29):
            for cx in range(0, 290, 29):
                if (cx // 30 + cy // 30) % 2:
                    check.rectangle((cx, cy, cx + 28, cy + 28), fill="#E9E9E9")
        preview = image.resize((290, 290), Image.Resampling.LANCZOS)
        checker.paste(preview, (0, 0), preview)
        sheet.paste(checker, (x + 20, y + 70))
        draw.text((x + 18, y + 20), label, fill="#252525", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="PNG")
    return output


def asset_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
