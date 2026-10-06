"""Reviewed, immutable environment plates consumed without provider calls by render."""

import io
import json
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat
from pydantic import Field

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.benchmark.models import CanonicalImageSpec, ReferenceImage
from tovitunes.benchmark.providers import (
    GeminiImageProvider,
    ImageProvider,
    ProviderFailure,
    ProviderResult,
    QwenComfyUIImageProvider,
)
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.review import ApprovalDecision
from tovitunes.domain.storyboard import ProductionModel
from tovitunes.persistence.db import Database

PROMPT_VERSION = "environment_world_v1"
THEME = "preschool-world-v1"
ROLES = ("meadow_wide", "lesson_garden", "play_path", "celebration_meadow")
ROLE_BRIEFS = {
    "meadow_wide": "Wide establishing meadow, distant rounded hills, clear lower middle stage.",
    "lesson_garden": (
        "Same meadow world, simple stylized tree and branch at upper right; "
        "clear ground below for a separate animated apple."
    ),
    "play_path": (
        "Same meadow world, broad gently curved visible ground path across the lower middle, "
        "clear runway for a separate rolling object."
    ),
    "celebration_meadow": (
        "Same meadow world, inviting open performance clearing, "
        "subtle festive flowers at edges, clear central stage."
    ),
}
CANVAS = (1080, 1920)
# Documented Gemini 3.1 Flash Image output for 1K portrait 9:16.
MIN_SOURCE_SIZE = (768, 1376)
SOURCE_DIMENSIONS = {
    ("gemini-3.1-flash-image", "1K"): (768, 1376),
    ("gemini-3-pro-image", "1K"): (768, 1376),
    ("gemini-3-pro-image", "2K"): (1536, 2752),
    ("gemini-3-pro-image", "4K"): (3072, 5504),
}
SOURCE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class EnvironmentPlate(ProductionModel):
    role: str
    artifact_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: tuple[int, int] = CANVAS
    provider: str
    model: str
    local_request_id: str
    provider_request_id: str | None = None
    generated_at: datetime
    source_references: tuple[str, ...] = ()
    requested_image_size: str | None = None
    location: str | None = None
    source_artifact_id: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_dimensions: tuple[int, int] | None = None


class EnvironmentSet(ProductionModel):
    schema_version: Literal[1, 2] = 2
    set_id: str
    prompt_version: str = PROMPT_VERSION
    theme: str = THEME
    visual_theme: str = "premium rounded preschool 2D meadow, soft dimensional shading"
    plates: tuple[EnvironmentPlate, ...]
    staging_regions: dict[str, tuple[float, float, float, float]] = Field(
        default_factory=lambda: {
            "tovi": (0.16, 0.40, 0.72, 0.94),
            "left_object": (0.07, 0.32, 0.43, 0.81),
            "right_object": (0.57, 0.32, 0.93, 0.81),
        }
    )
    review_status: str = "pending"
    provider: str | None = None
    model: str | None = None
    location: str | None = None
    requested_image_size: str | None = None
    generation_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    def plate(self, role: str) -> EnvironmentPlate:
        return next(p for p in self.plates if p.role == role)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _store(config: RuntimeConfig) -> tuple[Database, AssetStore, str]:
    db = Database(config.database_path)
    db.migrate()
    catalog = load_brand(config.brand_root)
    db.register_catalog(catalog)
    return db, AssetStore(config.data_root, db), catalog.version.revision_id


def _configured_provider(config: RuntimeConfig) -> ImageProvider:
    generation = config.environment_generation
    if generation.provider == "qwen_comfyui":
        return QwenComfyUIImageProvider(
            workflow_path=generation.workflow_path,
            base_url=generation.base_url,
            width=generation.width,
            height=generation.height,
            steps=generation.steps,
            cfg=generation.cfg,
            sampler=generation.sampler,
            scheduler=generation.scheduler,
            timeout_seconds=generation.timeout_seconds,
            poll_interval_seconds=generation.poll_interval_seconds,
            purpose="environment",
        )
    return GeminiImageProvider(
        model=generation.model,
        location=generation.location,
        image_size=generation.image_size,
    )


def _provider_settings(config: RuntimeConfig, provider: ImageProvider) -> tuple[str, str, str, str]:
    generation = config.environment_generation
    location = getattr(provider, "location", generation.location)
    image_size = getattr(provider, "image_size", generation.image_size)
    return provider.provider, provider.model, str(location), str(image_size)


def _supports_reference_images(provider: ImageProvider) -> bool:
    capabilities = getattr(provider, "capabilities", None)
    return bool(capabilities.reference_images) if capabilities is not None else True


def _generation_fingerprint(
    theme: str,
    provider: str,
    model: str,
    location: str,
    image_size: str,
    qwen_profile: dict[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {
        "theme": theme,
        "prompt_version": PROMPT_VERSION,
        "roles": ROLES,
        "provider": provider,
        "model": model,
        "location": location,
        "image_size": image_size,
    }
    if qwen_profile is not None:
        payload["qwen_profile"] = qwen_profile
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _qwen_profile(provider: ImageProvider) -> dict[str, object] | None:
    if not isinstance(provider, QwenComfyUIImageProvider):
        return None
    return {
        "workflow_sha256": provider._workflow()[1],
        "width": provider.width,
        "height": provider.height,
        "steps": provider.steps,
        "cfg": provider.cfg,
        "sampler": provider.sampler,
        "scheduler": provider.scheduler,
    }


def _set_id(theme: str, fingerprint: str) -> str:
    return f"{theme}-{fingerprint[:16]}"


def _request_fingerprint(spec: CanonicalImageSpec, generation_fingerprint: str) -> str:
    return sha256(f"{spec.fingerprint()}:{generation_fingerprint}".encode()).hexdigest()


def _spec(
    role: str, brand_id: str, attempt: int, reference: ReferenceImage | None
) -> CanonicalImageSpec:
    return CanonicalImageSpec(
        benchmark_version="production_environment_v1",
        prompt_version=PROMPT_VERSION,
        case_id=role,
        attempt=attempt,
        scene_brief=ROLE_BRIEFS[role],
        teaching_check=(
            "The background is decorative only; no curriculum claims or teaching objects."
        ),
        common_brief=(
            "Premium preschool 2D cartoon animation background. One coherent friendly meadow "
            "world across every plate: rounded shape language, soft dimensional shading, "
            "vibrant controlled palette, warm even light, layered sky/hills/vegetation, "
            "substantial depth, clean readable shapes at phone size. Keep the lower and middle "
            "stage open and quiet for separately composited character and lesson objects. "
            "Put detail behind the focus plane. Safe padding for gentle camera moves."
        ),
        composition_brief=(
            "Portrait 9:16 environmental set plate only. No characters or lesson props."
        ),
        reference_instructions=(
            "Use the supplied master environment as a style and world continuity reference. "
            "Change only the staging region described above; do not copy any character or object."
            if reference
            else (
                "No reference image. Use the shared textual meadow world and rounded "
                "visual style contract exactly for this role; change only the staging details."
            )
        ),
        pack_revision_id="environment-only",
        brand_revision_id=brand_id,
        references=(reference,) if reference else (),
        palette={"sky": "#B8E9F8", "grass": "#92C970", "sunlight": "#FFF3C4"},
        identity_rules=(
            "Environment only; Tovi is a separate deterministic render layer.",
            "Keep the clean Tovi zone and two clear lesson-object positions.",
        ),
        forbidden_changes=(
            "NO bird mascot, NO blue bird, NO character, NO animal protagonist, NO person.",
            "NO apple, ball, swatch or other prominent educational target object.",
            "NO text, letters, logos, watermarks or signs.",
        ),
        negative_constraints=(
            "No photorealism, 3D render, anime, hard clutter or franchise imitation.",
            "No people, birds, animals, characters, text, logos or watermarks.",
            "No apples, balls, swatches or prominent lesson objects.",
        ),
    )


def plan(config: RuntimeConfig, *, theme: str = THEME, attempt: int = 1) -> dict[str, object]:
    if theme != THEME or attempt < 1:
        raise ValueError("unsupported environment theme or attempt")
    brand_id = load_brand(config.brand_root).version.revision_id
    provider = _configured_provider(config)
    provider_name, model, location, image_size = _provider_settings(config, provider)
    fingerprint = _generation_fingerprint(
        theme, provider_name, model, location, image_size, _qwen_profile(provider)
    )
    return {
        "set_id": _set_id(theme, fingerprint),
        "theme": theme,
        "provider": provider_name,
        "model": model,
        "requested_image_size": image_size,
        "location": location,
        "generation_fingerprint": fingerprint,
        "role_count": len(ROLES),
        "request_count": len(ROLES),
        "provider_calls": 0,
        "live_calls": 0,
        "requests": [
            {
                "role": role,
                "prompt": _spec(
                    role,
                    brand_id,
                    attempt,
                    ReferenceImage(
                        role="world_master",
                        artifact_id="pending-master",
                        sha256="0" * 64,
                        mime_type="image/png",
                    )
                    if role != ROLES[0] and _supports_reference_images(provider)
                    else None,
                ).prompt(),
                "reference_assets": ["meadow_wide"]
                if role != ROLES[0] and _supports_reference_images(provider)
                else [],
            }
            for role in ROLES
        ],
    }


def _validate_image(
    data: bytes, expected_source_dimensions: tuple[int, int] | None = None
) -> Image.Image:
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        image = ImageOps.exif_transpose(opened)
        if expected_source_dimensions is not None and image.size != expected_source_dimensions:
            raise ValueError(
                "provider image dimensions differ from the requested image contract: "
                f"expected {expected_source_dimensions}, received {image.size}"
            )
        if expected_source_dimensions is None and (
            image.width < MIN_SOURCE_SIZE[0] or image.height < MIN_SOURCE_SIZE[1]
        ):
            raise ValueError("provider image is too small for a 1080x1920 plate")
        if abs(image.width / image.height - 9 / 16) > 0.025:
            raise ValueError("provider image is not portrait 9:16")
        if "A" in image.getbands() and image.getchannel("A").getextrema() != (255, 255):
            raise ValueError("environment plate contains transparency")
        rgb = image.convert("RGB")
        if max(ImageStat.Stat(rgb.resize((32, 32))).stddev) < 8:
            raise ValueError("environment plate is blank or nearly uniform")
        if max(ImageStat.Stat(rgb).mean) < 20:
            raise ValueError("environment plate is black")
        return rgb.resize(CANVAS, Image.Resampling.LANCZOS)


def _response_metadata(result: ProviderResult) -> dict[str, object]:
    """Retain safe response evidence before technical validation can fail."""
    metadata: dict[str, object] = dict(result.response_metadata)
    if result.usage is not None:
        metadata["usage_metadata"] = result.usage
    try:
        with Image.open(io.BytesIO(result.image_bytes)) as opened:
            metadata["source_dimensions"] = [opened.width, opened.height]
    except (OSError, ValueError):
        # The validator remains authoritative for corruption failures.
        pass
    return metadata


def _transition(
    db: Database,
    request_id: str,
    status: str,
    *,
    provider_id: str | None = None,
    artifact_id: str | None = None,
    response_bytes: bytes | None = None,
    response_mime: str | None = None,
    response_metadata: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    with closing(db.connect()) as conn:
        row = conn.execute(
            "SELECT status FROM environment_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        allowed = {
            "prepared": {"remote_started", "terminal_failure", "retryable_failure"},
            "remote_started": {"succeeded", "terminal_failure", "retryable_failure", "ambiguous"},
        }
        if row is None or status not in allowed.get(row["status"], set()):
            raise ValueError("invalid environment request transition")
        conn.execute(
            "UPDATE environment_requests SET status=?,"
            "provider_request_id=COALESCE(?,provider_request_id),"
            "artifact_id=COALESCE(?,artifact_id),"
            "response_sha256=COALESCE(?,response_sha256),"
            "response_byte_count=COALESCE(?,response_byte_count),"
            "response_mime=COALESCE(?,response_mime),"
            "response_metadata_json=COALESCE(?,response_metadata_json),"
            "remote_started_at=CASE WHEN ?='remote_started' THEN ? ELSE remote_started_at END,"
            "error_reason=?,updated_at=? WHERE request_id=?",
            (
                status,
                provider_id,
                artifact_id,
                sha256(response_bytes).hexdigest() if response_bytes is not None else None,
                len(response_bytes) if response_bytes is not None else None,
                response_mime,
                json.dumps(response_metadata, sort_keys=True) if response_metadata else None,
                status,
                _now(),
                error,
                _now(),
                request_id,
            ),
        )
        conn.commit()


def generate_set(
    config: RuntimeConfig,
    *,
    confirmed: bool,
    theme: str = THEME,
    attempt: int = 1,
    provider: ImageProvider | None = None,
    episode_id: str | None = None,
    role_briefs: dict[str, str] | None = None,
    assert_owner: Callable[[], None] | None = None,
) -> dict[str, object]:
    if episode_id is not None and assert_owner is None:
        raise ValueError("episode environment generation requires production ownership")
    assert_owner = assert_owner or (lambda: None)
    assert_owner()
    if not confirmed:
        raise ValueError("live image generation requires --confirm-provider-generation")
    if theme != THEME or attempt < 1:
        raise ValueError("unsupported environment theme or attempt")
    provider = provider or _configured_provider(config)
    if isinstance(provider, GeminiImageProvider):
        provider.preflight()
    provider_name, model, location, image_size = _provider_settings(config, provider)
    generation_fingerprint = _generation_fingerprint(
        theme, provider_name, model, location, image_size, _qwen_profile(provider)
    )
    if episode_id is not None:
        generation_fingerprint = sha256(
            json.dumps(
                {
                    "generation": generation_fingerprint,
                    "episode_id": episode_id,
                    "role_briefs": role_briefs,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    set_id = _set_id(theme, generation_fingerprint)
    expected_source_dimensions = (
        (provider.width, provider.height)
        if isinstance(provider, QwenComfyUIImageProvider)
        else SOURCE_DIMENSIONS.get((model, image_size))
    )
    db, store, brand_id = _store(config)
    owner_scope: Literal["episode", "brand"] = "episode" if episode_id else "brand"
    owner_id = episode_id or brand_id
    working = config.data_root / ".environment-working"
    working.mkdir(exist_ok=True)
    counts = {key: 0 for key in ("prepared", "remote_started", "succeeded", "failed", "ambiguous")}
    plates: list[EnvironmentPlate] = []
    with tempfile.TemporaryDirectory(dir=working) as dirname:
        stage = Path(dirname)
        store = AssetStore(config.data_root, db, generated_source_roots=[stage])
        for role in ROLES:
            assert_owner()
            master = plates[0] if plates else None
            master_reference_id: str | None = None
            reference: ReferenceImage | None = None
            if master is not None and _supports_reference_images(provider):
                master_reference_id = master.source_artifact_id or master.artifact_id
                reference = ReferenceImage(
                    role="world_master",
                    artifact_id=master_reference_id,
                    sha256=(master.source_sha256 or master.sha256),
                    mime_type=store.get(master_reference_id).mime_type,
                )
            spec = _spec(role, brand_id, attempt, reference)
            if role_briefs is not None:
                if set(role_briefs) != set(ROLES):
                    raise ValueError("plan must supply all environment roles")
                spec = spec.model_copy(
                    update={
                        "scene_brief": role_briefs[role],
                        "forbidden_changes": ("No characters, prominent lesson objects or text.",),
                        "negative_constraints": (
                            "No photorealism, franchise imitation or unsafe imagery.",
                        ),
                    }
                )
            fingerprint = _request_fingerprint(spec, generation_fingerprint)
            with closing(db.connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM environment_requests WHERE set_id=? AND role=? AND attempt=?",
                    (set_id, role, attempt),
                ).fetchone()
                if row is None:
                    request_id = str(uuid4())
                    conn.execute(
                        "INSERT INTO environment_requests"
                        "(request_id,set_id,role,attempt,fingerprint,provider,model,status,"
                        "spec_json,created_at,updated_at,episode_id) VALUES(?,?,?,?,?,?,?,"
                        "'prepared',?,?,?,?)",
                        (
                            request_id,
                            set_id,
                            role,
                            attempt,
                            fingerprint,
                            provider_name,
                            model,
                            spec.canonical_json(),
                            _now(),
                            _now(),
                            episode_id,
                        ),
                    )
                    conn.commit()
                    counts["prepared"] += 1
                    status = "prepared"
                else:
                    request_id, status = row["request_id"], row["status"]
                    if (
                        row["fingerprint"] != fingerprint
                        or row["provider"] != provider_name
                        or row["model"] != model
                    ):
                        raise ValueError("existing environment request differs; use a new attempt")
                    if status == "succeeded":
                        record = store.get(row["artifact_id"])
                        if not store.inspect(record.identity.artifact_id).valid:
                            raise ValueError("persisted environment plate is invalid")
                        stored_metadata = json.loads(row["response_metadata_json"] or "{}")
                        plates.append(
                            EnvironmentPlate(
                                role=role,
                                artifact_id=record.identity.artifact_id,
                                sha256=record.sha256,
                                provider=provider_name,
                                model=model,
                                local_request_id=request_id,
                                provider_request_id=row["provider_request_id"],
                                generated_at=record.provenance.acquired_at,
                                source_references=spec.references
                                and (spec.references[0].artifact_id,)
                                or (),
                                requested_image_size=image_size,
                                location=location,
                                source_artifact_id=stored_metadata.get("source_artifact_id"),
                                source_sha256=stored_metadata.get("response_sha256"),
                                source_dimensions=stored_metadata.get("source_dimensions"),
                            )
                        )
                        continue
                    if status != "prepared":
                        raise ValueError(f"request {request_id} is {status}; no automatic resend")
            started = False
            received_result: ProviderResult | None = None
            response_metadata: dict[str, object] | None = None

            def remote_start() -> None:
                nonlocal started
                assert_owner()
                if started:
                    raise ValueError("provider started the same request twice")
                _transition(db, request_id, "remote_started")
                counts["remote_started"] += 1
                started = True

            try:
                result = provider.generate(
                    spec,
                    (store.path_for(master_reference_id),) if master_reference_id else (),
                    on_remote_start=remote_start,
                )
                received_result = result
                response_metadata = _response_metadata(result)
                response_metadata["continuity_mode"] = (
                    "image_reference" if master_reference_id else "shared_text_world_contract"
                )
                response_metadata["reference_images_supported"] = _supports_reference_images(
                    provider
                )
                if not started:
                    raise ValueError("provider returned without remote-start receipt")
                with Image.open(io.BytesIO(result.image_bytes)) as source_image:
                    source_dimensions = (source_image.width, source_image.height)
                response_metadata["requested_image_size"] = image_size
                image = _validate_image(result.image_bytes, expected_source_dimensions)
                response_metadata["normalized_dimensions"] = list(image.size)
                response_metadata["technical_validation"] = "passed"
                response_metadata["response_sha256"] = sha256(result.image_bytes).hexdigest()
                source_record = None
                source_suffix = SOURCE_SUFFIXES.get(result.mime_type)
                provider_provenance = Provenance(
                    source_kind="provider",
                    acquired_at=datetime.now(UTC),
                    provider=provider_name,
                    model=model,
                    request_id=result.provider_request_id,
                    local_request_id=request_id,
                    prompt_version=PROMPT_VERSION,
                    input_artifact_ids=(master_reference_id,) if master_reference_id else (),
                )
                if source_suffix is not None:
                    source_path = stage / f"{role}_source{source_suffix}"
                    source_path.write_bytes(result.image_bytes)
                    source_record = store.ingest(
                        source_path,
                        owner_scope=owner_scope,
                        owner_id=owner_id,
                        kind="environment_source_plate",
                        slot_key=role,
                        provenance=provider_provenance,
                        dependencies=[InputDependency(master_reference_id, "world_style_reference")]
                        if master_reference_id
                        else [],
                        expected_media_type=result.mime_type,
                    )
                    response_metadata["source_artifact_id"] = source_record.identity.artifact_id
                path = stage / f"{role}.png"
                image.save(path, format="PNG")
                normalized_dependencies = []
                if source_record is not None:
                    normalized_dependencies.append(
                        InputDependency(source_record.identity.artifact_id, "provider_source")
                    )
                if master_reference_id:
                    normalized_dependencies.append(
                        InputDependency(master_reference_id, "world_style_reference")
                    )
                record = store.ingest(
                    path,
                    owner_scope=owner_scope,
                    owner_id=owner_id,
                    kind="environment_plate",
                    slot_key=role,
                    provenance=provider_provenance,
                    dependencies=normalized_dependencies,
                    expected_media_type="image/png",
                )
                _transition(
                    db,
                    request_id,
                    "succeeded",
                    provider_id=result.provider_request_id,
                    artifact_id=record.identity.artifact_id,
                    response_bytes=result.image_bytes,
                    response_mime=result.mime_type,
                    response_metadata=response_metadata,
                )
                counts["succeeded"] += 1
                plates.append(
                    EnvironmentPlate(
                        role=role,
                        artifact_id=record.identity.artifact_id,
                        sha256=record.sha256,
                        provider=provider_name,
                        model=model,
                        local_request_id=request_id,
                        provider_request_id=result.provider_request_id,
                        generated_at=record.provenance.acquired_at,
                        source_references=(master_reference_id,) if master_reference_id else (),
                        requested_image_size=image_size,
                        location=location,
                        source_artifact_id=(
                            source_record.identity.artifact_id if source_record else None
                        ),
                        source_sha256=sha256(result.image_bytes).hexdigest(),
                        source_dimensions=source_dimensions,
                    )
                )
            except ProviderFailure as exc:
                status = (
                    exc.outcome if started or exc.outcome != "ambiguous" else "terminal_failure"
                )
                _transition(
                    db,
                    request_id,
                    status,
                    provider_id=exc.provider_request_id,
                    response_metadata=exc.diagnostics or None,
                    error=str(exc),
                )
                counts["ambiguous" if status == "ambiguous" else "failed"] += 1
                raise
            except Exception as exc:
                status = (
                    "terminal_failure"
                    if received_result is not None or not started
                    else "ambiguous"
                )
                if response_metadata is not None:
                    response_metadata.setdefault("technical_validation", "failed")
                _transition(
                    db,
                    request_id,
                    status,
                    provider_id=received_result.provider_request_id if received_result else None,
                    response_bytes=received_result.image_bytes if received_result else None,
                    response_mime=received_result.mime_type if received_result else None,
                    response_metadata=response_metadata,
                    error=str(exc),
                )
                counts["ambiguous" if status == "ambiguous" else "failed"] += 1
                raise
        assert_owner()
        environment = EnvironmentSet(
            set_id=set_id,
            theme=theme,
            plates=tuple(plates),
            provider=provider_name,
            model=model,
            location=location,
            requested_image_size=image_size,
            generation_fingerprint=generation_fingerprint,
        )
        manifest_path = stage / "environment_set.json"
        manifest_path.write_text(environment.model_dump_json(), encoding="utf-8")
        digest = sha256(manifest_path.read_bytes()).hexdigest()
        manifest = store.find_version(owner_scope, owner_id, "environment_set", "main", digest)
        if manifest is None:
            manifest = store.ingest(
                manifest_path,
                owner_scope=owner_scope,
                owner_id=owner_id,
                kind="environment_set",
                slot_key="main",
                provenance=Provenance(
                    source_kind="deterministic",
                    acquired_at=datetime.now(UTC),
                    provider=f"tovitunes.environment_sets:{provider_name}",
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    input_artifact_ids=tuple(p.artifact_id for p in plates),
                ),
                dependencies=[InputDependency(p.artifact_id, "environment_plate") for p in plates],
            )
        review_output = config.data_root.parent / "outputs"
        sheet = contact_sheet(
            store,
            environment,
            review_output / f"{set_id}_attempt_{attempt}_environment_contact_sheet.png",
        )
        with closing(db.connect()) as conn:
            decision = conn.execute(
                "SELECT status FROM approval_decisions WHERE artifact_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (manifest.identity.artifact_id,),
            ).fetchone()
        return {
            "set_id": set_id,
            "theme": theme,
            "provider": provider_name,
            "model": model,
            "requested_image_size": image_size,
            "location": location,
            "generation_fingerprint": generation_fingerprint,
            "manifest_artifact_id": manifest.identity.artifact_id,
            "plate_artifact_ids": {p.role: p.artifact_id for p in plates},
            "source_artifact_ids": {p.role: p.source_artifact_id for p in plates},
            "contact_sheet": str(sheet),
            "review_status": decision[0] if decision else "pending",
            "provider_calls": counts,
        }


def contact_sheet(store: AssetStore, environment: EnvironmentSet, output: Path) -> Path:
    sheet = Image.new("RGB", (960, 1760), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    for index, plate in enumerate(environment.plates):
        cell_x = (index % 2) * 480
        cell_y = (index // 2) * 880
        with Image.open(store.path_for(plate.artifact_id)) as source:
            thumb = source.convert("RGB").resize((464, 825), Image.Resampling.LANCZOS)
            sheet.paste(thumb, (cell_x + 8, cell_y + 8))
        draw.rectangle((cell_x + 8, cell_y + 833, cell_x + 472, cell_y + 872), fill="#1d3557")
        draw.text((cell_x + 18, cell_y + 841), plate.role, fill="white", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return output


def export_contact_sheet(config: RuntimeConfig, artifact_id: str, output: Path) -> Path:
    _, store, _ = _store(config)
    environment = EnvironmentSet.model_validate(store.read_json(artifact_id))
    if tuple(plate.role for plate in environment.plates) != ROLES:
        raise ValueError("environment set lacks required plates")
    return contact_sheet(store, environment, output)


def comparison_sheet(
    config: RuntimeConfig,
    flash_artifact_id: str,
    pro_artifact_id: str,
    output: Path,
) -> Path:
    _, store, _ = _store(config)
    flash = EnvironmentSet.model_validate(store.read_json(flash_artifact_id))
    pro = EnvironmentSet.model_validate(store.read_json(pro_artifact_id))
    if tuple(plate.role for plate in flash.plates) != ROLES:
        raise ValueError("Flash environment set lacks required plates")
    if tuple(plate.role for plate in pro.plates) != ROLES:
        raise ValueError("Pro environment set lacks required plates")
    if pro.model != "gemini-3-pro-image":
        raise ValueError("comparison Pro set is not pinned to gemini-3-pro-image")
    sheet = Image.new("RGB", (960, 3440), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    for row, role in enumerate(ROLES):
        for column, (label, environment) in enumerate((("Flash", flash), ("Nano Banana Pro", pro))):
            plate = environment.plate(role)
            x = column * 480
            y = row * 860
            with Image.open(store.path_for(plate.artifact_id)) as source:
                thumb = source.convert("RGB").resize((464, 825), Image.Resampling.LANCZOS)
                sheet.paste(thumb, (x + 8, y + 8))
            draw.rectangle((x + 8, y + 833, x + 472, y + 852), fill="#1d3557")
            draw.text((x + 18, y + 834), f"{role} | {label}", fill="white", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return output


def inspect_set(config: RuntimeConfig, artifact_id: str) -> dict[str, object]:
    _, store, _ = _store(config)
    record = store.get(artifact_id)
    if record.identity.kind != "environment_set":
        raise ValueError("artifact is not an environment set")
    environment = EnvironmentSet.model_validate(store.read_json(artifact_id))
    with closing(store.database.connect()) as conn:
        decision = conn.execute(
            "SELECT status FROM approval_decisions WHERE artifact_id=? ORDER BY rowid DESC LIMIT 1",
            (artifact_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT status,remote_started_at FROM environment_requests WHERE set_id=?",
            (environment.set_id,),
        ).fetchall()
    calls = {
        "prepared": len(rows),
        "remote_started": sum(row["remote_started_at"] is not None for row in rows),
        "succeeded": sum(row["status"] == "succeeded" for row in rows),
        "failed": sum(row["status"] in {"terminal_failure", "retryable_failure"} for row in rows),
        "ambiguous": sum(row["status"] == "ambiguous" for row in rows),
    }
    return {
        "environment_set": environment.model_dump(mode="json"),
        "review_status": decision[0] if decision else "pending",
        "valid": store.inspect(artifact_id).valid,
        "provider_calls": calls,
    }


def decide_set(
    config: RuntimeConfig,
    artifact_id: str,
    *,
    actor: str,
    reason: str,
    status: Literal["approved", "rejected"],
) -> dict[str, object]:
    if status not in {"approved", "rejected"} or not actor.strip() or not reason.strip():
        raise ValueError("review requires approved/rejected, actor and reason")
    _, store, _ = _store(config)
    environment = EnvironmentSet.model_validate(store.read_json(artifact_id))
    if status == "approved":
        with closing(store.database.connect()) as conn:
            rejection = conn.execute(
                "SELECT 1 FROM approval_decisions WHERE artifact_id=? AND status='rejected' "
                "LIMIT 1",
                (artifact_id,),
            ).fetchone()
        if rejection:
            raise ValueError("rejected environment set requires a new generation attempt")
    review_artifact_ids = [
        artifact_id
        for plate in environment.plates
        for artifact_id in (
            *((plate.source_artifact_id,) if plate.source_artifact_id else ()),
            plate.artifact_id,
        )
    ]
    for artifact_id_to_review in review_artifact_ids:
        store.record_approval(
            ApprovalDecision(
                target_id=artifact_id_to_review,
                target_kind="artifact",
                status=status,
                actor=actor,
                reason=reason,
                policy_version="environment_human_v1",
                decided_at=datetime.now(UTC),
            )
        )
    store.record_approval(
        ApprovalDecision(
            target_id=artifact_id,
            target_kind="artifact",
            status=status,
            actor=actor,
            reason=reason,
            policy_version="environment_human_v1",
            decided_at=datetime.now(UTC),
        )
    )
    return {"set_id": environment.set_id, "review_status": status}


def select_set(config: RuntimeConfig, artifact_id: str) -> dict[str, object]:
    _, store, _ = _store(config)
    environment = EnvironmentSet.model_validate(store.read_json(artifact_id))
    for plate in environment.plates:
        if plate.source_artifact_id:
            store.select(plate.source_artifact_id)
    for plate in environment.plates:
        store.select(plate.artifact_id)
    store.select(artifact_id)
    return {"set_id": environment.set_id, "selected_artifact_id": artifact_id}


def selected_set(config: RuntimeConfig) -> tuple[EnvironmentSet, str]:
    _, store, brand_id = _store(config)
    record = store.selected("brand", brand_id, "environment_set", "main")
    if record is None:
        raise ValueError(
            "no selected approved environment set; run environment generate-set, "
            "review, approve, select"
        )
    environment = EnvironmentSet.model_validate(store.read_json(record.identity.artifact_id))
    if tuple(p.role for p in environment.plates) != ROLES:
        raise ValueError("selected environment set lacks required plates")
    for plate in environment.plates:
        if store.get(plate.artifact_id).sha256 != plate.sha256:
            raise ValueError("selected environment plate SHA differs")
    return environment, record.identity.artifact_id
