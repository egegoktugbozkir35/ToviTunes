"""Reviewed, immutable environment plates consumed without provider calls by render."""

import io
import json
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageOps, ImageStat
from pydantic import Field

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.benchmark.models import CanonicalImageSpec, ReferenceImage
from tovitunes.benchmark.providers import (
    GeminiImageProvider,
    ImageProvider,
    ProviderFailure,
    ProviderResult,
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


class EnvironmentSet(ProductionModel):
    schema_version: int = 1
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
            else "No reference image; establish the visual style for all following plates."
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
    provider = GeminiImageProvider()
    return {
        "set_id": theme,
        "provider": provider.provider,
        "model": provider.model,
        "request_count": len(ROLES),
        "provider_calls": 0,
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
                    if role != ROLES[0]
                    else None,
                ).prompt(),
                "reference_assets": ["meadow_wide"] if role != ROLES[0] else [],
            }
            for role in ROLES
        ],
    }


def _validate_image(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        image = ImageOps.exif_transpose(opened)
        if image.width < 900 or image.height < 1600:
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
) -> dict[str, object]:
    if not confirmed:
        raise ValueError("live image generation requires --confirm-provider-generation")
    if theme != THEME or attempt < 1:
        raise ValueError("unsupported environment theme or attempt")
    provider = provider or GeminiImageProvider()
    if isinstance(provider, GeminiImageProvider):
        provider.preflight()
    db, store, brand_id = _store(config)
    working = config.data_root / ".environment-working"
    working.mkdir(exist_ok=True)
    counts = {key: 0 for key in ("prepared", "remote_started", "succeeded", "failed", "ambiguous")}
    plates: list[EnvironmentPlate] = []
    with tempfile.TemporaryDirectory(dir=working) as dirname:
        stage = Path(dirname)
        store = AssetStore(config.data_root, db, generated_source_roots=[stage])
        for role in ROLES:
            master = plates[0] if plates else None
            reference = (
                ReferenceImage(
                    role="world_master",
                    artifact_id=master.artifact_id,
                    sha256=master.sha256,
                    mime_type="image/png",
                )
                if master
                else None
            )
            spec = _spec(role, brand_id, attempt, reference)
            fingerprint = spec.fingerprint()
            with closing(db.connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM environment_requests WHERE set_id=? AND role=? AND attempt=?",
                    (theme, role, attempt),
                ).fetchone()
                if row is None:
                    request_id = str(uuid4())
                    conn.execute(
                        "INSERT INTO environment_requests"
                        "(request_id,set_id,role,attempt,fingerprint,provider,model,status,"
                        "spec_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,"
                        "'prepared',?,?,?)",
                        (
                            request_id,
                            theme,
                            role,
                            attempt,
                            fingerprint,
                            provider.provider,
                            provider.model,
                            spec.canonical_json(),
                            _now(),
                            _now(),
                        ),
                    )
                    conn.commit()
                    counts["prepared"] += 1
                    status = "prepared"
                else:
                    request_id, status = row["request_id"], row["status"]
                    if row["fingerprint"] != fingerprint or row["model"] != provider.model:
                        raise ValueError("existing environment request differs; use a new attempt")
                    if status == "succeeded":
                        record = store.get(row["artifact_id"])
                        if not store.inspect(record.identity.artifact_id).valid:
                            raise ValueError("persisted environment plate is invalid")
                        plates.append(
                            EnvironmentPlate(
                                role=role,
                                artifact_id=record.identity.artifact_id,
                                sha256=record.sha256,
                                provider=provider.provider,
                                model=provider.model,
                                local_request_id=request_id,
                                provider_request_id=row["provider_request_id"],
                                generated_at=record.provenance.acquired_at,
                                source_references=spec.references
                                and (spec.references[0].artifact_id,)
                                or (),
                            )
                        )
                        continue
                    if status != "prepared":
                        raise ValueError(f"request {request_id} is {status}; no automatic resend")
            started = False
            received_result: ProviderResult | None = None

            def remote_start() -> None:
                nonlocal started
                if started:
                    raise ValueError("provider started the same request twice")
                _transition(db, request_id, "remote_started")
                counts["remote_started"] += 1
                started = True

            try:
                result = provider.generate(
                    spec,
                    (store.path_for(master.artifact_id),) if master else (),
                    on_remote_start=remote_start,
                )
                received_result = result
                if not started:
                    raise ValueError("provider returned without remote-start receipt")
                image = _validate_image(result.image_bytes)
                path = stage / f"{role}.png"
                image.save(path, format="PNG")
                provenance = Provenance(
                    source_kind="provider",
                    acquired_at=datetime.now(UTC),
                    provider=provider.provider,
                    model=provider.model,
                    request_id=result.provider_request_id,
                    local_request_id=request_id,
                    prompt_version=PROMPT_VERSION,
                    input_artifact_ids=(master.artifact_id,) if master else (),
                )
                record = store.ingest(
                    path,
                    owner_scope="brand",
                    owner_id=brand_id,
                    kind="environment_plate",
                    slot_key=role,
                    provenance=provenance,
                    dependencies=[InputDependency(master.artifact_id, "world_style_reference")]
                    if master
                    else [],
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
                    response_metadata=result.response_metadata,
                )
                counts["succeeded"] += 1
                plates.append(
                    EnvironmentPlate(
                        role=role,
                        artifact_id=record.identity.artifact_id,
                        sha256=record.sha256,
                        provider=provider.provider,
                        model=provider.model,
                        local_request_id=request_id,
                        provider_request_id=result.provider_request_id,
                        generated_at=record.provenance.acquired_at,
                        source_references=(master.artifact_id,) if master else (),
                    )
                )
            except ProviderFailure as exc:
                status = (
                    exc.outcome if started or exc.outcome != "ambiguous" else "terminal_failure"
                )
                _transition(
                    db, request_id, status, provider_id=exc.provider_request_id, error=str(exc)
                )
                counts["ambiguous" if status == "ambiguous" else "failed"] += 1
                raise
            except Exception as exc:
                status = (
                    "terminal_failure"
                    if received_result is not None or not started
                    else "ambiguous"
                )
                _transition(
                    db,
                    request_id,
                    status,
                    provider_id=received_result.provider_request_id if received_result else None,
                    response_bytes=received_result.image_bytes if received_result else None,
                    response_mime=received_result.mime_type if received_result else None,
                    response_metadata=received_result.response_metadata
                    if received_result
                    else None,
                    error=str(exc),
                )
                counts["ambiguous" if status == "ambiguous" else "failed"] += 1
                raise
        environment = EnvironmentSet(set_id=theme, plates=tuple(plates))
        manifest_path = stage / "environment_set.json"
        manifest_path.write_text(environment.model_dump_json(), encoding="utf-8")
        digest = sha256(manifest_path.read_bytes()).hexdigest()
        manifest = store.find_version("brand", brand_id, "environment_set", "main", digest)
        if manifest is None:
            manifest = store.ingest(
                manifest_path,
                owner_scope="brand",
                owner_id=brand_id,
                kind="environment_set",
                slot_key="main",
                provenance=Provenance(
                    source_kind="deterministic",
                    acquired_at=datetime.now(UTC),
                    provider="tovitunes.environment_sets",
                    model=PROMPT_VERSION,
                    input_artifact_ids=tuple(p.artifact_id for p in plates),
                ),
                dependencies=[InputDependency(p.artifact_id, "environment_plate") for p in plates],
            )
        review_output = config.data_root.parent / "outputs"
        sheet = contact_sheet(
            store,
            environment,
            review_output / f"{theme}_attempt_{attempt}_environment_contact_sheet.png",
        )
        with closing(db.connect()) as conn:
            decision = conn.execute(
                "SELECT status FROM approval_decisions WHERE artifact_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (manifest.identity.artifact_id,),
            ).fetchone()
        return {
            "set_id": theme,
            "manifest_artifact_id": manifest.identity.artifact_id,
            "plate_artifact_ids": {p.role: p.artifact_id for p in plates},
            "contact_sheet": str(sheet),
            "review_status": decision[0] if decision else "pending",
            "provider_calls": counts,
        }


def contact_sheet(store: AssetStore, environment: EnvironmentSet, output: Path) -> Path:
    sheet = Image.new("RGB", (960, 1708), "white")
    for index, plate in enumerate(environment.plates):
        with Image.open(store.path_for(plate.artifact_id)) as source:
            thumb = source.convert("RGB").resize((464, 824), Image.Resampling.LANCZOS)
            sheet.paste(thumb, ((index % 2) * 480 + 8, (index // 2) * 854 + 8))
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
    for plate in environment.plates:
        store.record_approval(
            ApprovalDecision(
                target_id=plate.artifact_id,
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
