"""Plan-driven episode images using the existing Qwen adapter and normalization contract."""

import json
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from tovitunes.artifacts.store import ArtifactRecord, AssetStore, InputDependency
from tovitunes.benchmark.models import CanonicalImageSpec
from tovitunes.benchmark.providers import ImageProvider, ProviderFailure
from tovitunes.config import RuntimeConfig
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.episode import Episode
from tovitunes.domain.visual_plan import COLORS_V1, EpisodeVisualPlan, VisualRequirement
from tovitunes.persistence.requests import RequestLedger
from tovitunes.render.lesson_objects import _prepare_qwen_white_background, _provider, normalize

IMAGE_PROMPT = "episode_illustration_v1"


def swatch(color: str, size: int = 1024) -> Image.Image:
    if color not in {*COLORS_V1, "rainbow"} or size <= 0:
        raise ValueError("unsupported palette entry or invalid swatch size")
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    colors = (
        [COLORS_V1[key] for key in ("red", "orange", "yellow", "green", "blue", "purple")]
        if color == "rainbow"
        else [COLORS_V1[color]]
    )
    for index, value in enumerate(colors):
        draw.rectangle(
            (
                size * (0.06 + 0.88 * index / len(colors)),
                size * 0.06,
                size * (0.06 + 0.88 * (index + 1) / len(colors)),
                size * 0.94,
            ),
            fill=value,
        )
    return image


def image_spec(
    episode: Episode,
    requirement: VisualRequirement,
    visual_direction: str,
) -> CanonicalImageSpec:
    return CanonicalImageSpec(
        benchmark_version="episode_visual_assets_v1",
        prompt_version=IMAGE_PROMPT,
        case_id=requirement.asset_key,
        attempt=1,
        scene_brief=(
            requirement.description
            + (
                " Keep the white interior enclosed by a soft visible tonal outline for "
                "background removal."
                if requirement.target_color == "white"
                else ""
            )
        ),
        teaching_check=(
            f"Objective: {episode.objective}. Entity: {requirement.semantic_label}. "
            f"Dominant target color where specified: {requirement.target_color}."
        ),
        common_brief=visual_direction + " Rounded preschool 2D illustration with clear silhouette.",
        pack_revision_id=episode.character_packs[0].revision_id,
        brand_revision_id=episode.brand_revision_id,
        references=(),
        palette={"lesson": COLORS_V1.get(requirement.target_color or "", "#FFFFFF")},
        identity_rules=("Exactly the complete described entity; isolated on plain white.",),
        forbidden_changes=(
            "No text, logo, watermark, character, person or franchise likeness.",
            "A simple face only as explicitly described; no added character personality."
            if requirement.allow_face
            else "No face, eyes, limbs or character personality.",
        ),
        aspect_ratio="1:1",
        composition_brief="Square, centered complete object with generous removable white padding.",
        reference_instructions="Textual style contract only; no reference images.",
    )


def persist_file(
    store: AssetStore,
    episode: Episode,
    kind: str,
    slot: str,
    path: Path,
    deps: tuple[str, ...],
    provenance: Provenance | None = None,
) -> ArtifactRecord:
    digest = sha256(path.read_bytes()).hexdigest()
    record = store.find_version("episode", episode.episode_id, kind, slot, digest)
    if record is None:
        record = store.ingest(
            path,
            owner_scope="episode",
            owner_id=episode.episode_id,
            kind=kind,
            slot_key=slot,
            provenance=provenance
            or Provenance(
                source_kind="deterministic",
                acquired_at=datetime.now(UTC),
                provider="tovitunes.short_production",
                model="v1",
                input_artifact_ids=deps,
            ),
            dependencies=[InputDependency(dep, "production_input") for dep in deps],
        )
    else:
        with closing(store.database.connect()) as db:
            pinned = dict(
                db.execute(
                    "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                    "WHERE consumer_artifact_id=?",
                    (record.identity.artifact_id,),
                ).fetchall()
            )
        if pinned != {dep: store.get(dep).sha256 for dep in deps}:
            raise ValueError("existing production artifact has different dependencies")
    store.admit_preview(record.identity.artifact_id)
    return record


class ImageStageBlocked(RuntimeError):
    def __init__(self, status: str, request_id: str) -> None:
        super().__init__(f"Image request {request_id}: {status}; no automatic resend")
        self.status = status


def generate_assets(
    config: RuntimeConfig,
    store: AssetStore,
    episode: Episode,
    visual: EpisodeVisualPlan,
    plan_id: str,
    working: Path,
    visual_direction: str,
    provider: ImageProvider | None = None,
    *,
    progress: Callable[[str, str], None] | None = None,
    assert_owner: Callable[[], None],
) -> dict[str, str]:
    provider = provider or _provider(config)
    if provider.provider != "qwen_comfyui":
        raise ValueError("production illustrations require the existing Qwen adapter")
    ledger = RequestLedger(store.database)
    assets: dict[str, str] = {}
    for index, requirement in enumerate(visual.required_assets, 1):
        assert_owner()
        if progress:
            progress(
                "VISUAL_ASSETS",
                f"Generating or reusing visual {index} of {len(visual.required_assets)}",
            )
        slot = requirement.asset_key
        existing = store.selected("episode", episode.episode_id, "visual_asset", slot)
        if existing:
            with closing(store.database.connect()) as db:
                dep = db.execute(
                    "SELECT 1 FROM artifact_dependencies WHERE consumer_artifact_id=? "
                    "AND input_artifact_id=? AND input_sha256=?",
                    (existing.identity.artifact_id, plan_id, store.get(plan_id).sha256),
                ).fetchone()
            if not dep:
                raise ValueError("existing visual asset belongs to a different plan")
            with closing(store.database.connect()) as db:
                pending = db.execute(
                    "SELECT r.request_id,r.status,p.source_artifact_id FROM generation_requests r "
                    "JOIN production_image_receipts p ON p.request_id=r.request_id "
                    "WHERE r.episode_id=? AND r.slot_key=? AND r.kind='visual_asset' "
                    "AND r.status!='succeeded' ORDER BY r.rowid DESC LIMIT 1",
                    (episode.episode_id, slot),
                ).fetchone()
                if pending and pending["source_artifact_id"]:
                    source = store.get(pending["source_artifact_id"])
                    source_dep = db.execute(
                        "SELECT 1 FROM artifact_dependencies WHERE consumer_artifact_id=? "
                        "AND input_artifact_id=? AND input_sha256=?",
                        (existing.identity.artifact_id, source.identity.artifact_id, source.sha256),
                    ).fetchone()
                    if not source_dep or pending["status"] not in {"remote_started", "ambiguous"}:
                        raise ImageStageBlocked("BLOCKED", pending["request_id"])
                    db.execute(
                        "UPDATE production_image_receipts SET normalized_artifact_id=? "
                        "WHERE request_id=?",
                        (existing.identity.artifact_id, pending["request_id"]),
                    )
                    db.commit()
                    ledger.transition(
                        pending["request_id"],
                        "succeeded",
                        provider_request_id=source.provenance.request_id,
                    )
            assets[slot] = existing.identity.artifact_id
            continue
        path = working / f"{slot}.png"
        if requirement.kind == "color_swatch":
            swatch(requirement.target_color or "").save(path)
            assets[slot] = persist_file(
                store, episode, "visual_asset", slot, path, (plan_id,)
            ).identity.artifact_id
            continue
        spec = image_spec(episode, requirement, visual_direction)
        translated = provider.translate(spec)  # Local workflow loading only; never network.
        profile = {
            "canonical": spec.model_dump(mode="json"),
            "translated": translated.model_dump(mode="json"),
        }
        fingerprint = sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        with closing(store.database.connect()) as db:
            prior = db.execute(
                "SELECT * FROM generation_requests WHERE episode_id=? "
                "AND kind='visual_asset' AND slot_key=? ORDER BY rowid DESC LIMIT 1",
                (episode.episode_id, slot),
            ).fetchone()
        if prior and prior["input_fingerprint"] != fingerprint:
            raise ValueError("image request inputs changed; explicit reconciliation required")
        request_id = (
            prior["request_id"]
            if prior
            else ledger.prepare(
                episode.episode_id,
                "visual_asset",
                slot,
                provider.provider,
                provider.model,
                fingerprint,
            ).request_id
        )
        with closing(store.database.connect()) as db:
            db.execute(
                "INSERT OR IGNORE INTO production_image_receipts "
                "(request_id,spec_json,provider_profile_json) VALUES (?,?,?)",
                (request_id, spec.canonical_json(), json.dumps(profile, sort_keys=True)),
            )
            db.commit()
            receipt = db.execute(
                "SELECT * FROM production_image_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            # Recover ingest-before-receipt crashes from provenance, without another call.
            source = db.execute(
                "SELECT artifact_id FROM artifact_versions WHERE episode_id=? "
                "AND kind='visual_asset_source' AND slot_key=? "
                "AND json_extract(provenance_json,'$.local_request_id')=?",
                (episode.episode_id, slot, request_id),
            ).fetchone()
        source_record = store.get(source[0]) if source else None
        if source_record is None:
            if prior and prior["status"] != "prepared":
                raise ImageStageBlocked(
                    "AMBIGUOUS" if prior["status"] in {"remote_started", "ambiguous"} else "FAILED",
                    request_id,
                )
            started = False

            def remote_start() -> None:
                nonlocal started
                assert_owner()
                ledger.transition(request_id, "remote_started")
                started = True

            try:
                result = provider.generate(spec, (), on_remote_start=remote_start)
                if not started:
                    raise ValueError("image provider omitted remote-start tracking")
                source_path = working / f"{slot}_source.png"
                source_path.write_bytes(result.image_bytes)
                source_record = store.ingest(
                    source_path,
                    owner_scope="episode",
                    owner_id=episode.episode_id,
                    kind="visual_asset_source",
                    slot_key=slot,
                    provenance=Provenance(
                        source_kind="provider",
                        acquired_at=datetime.now(UTC),
                        provider=provider.provider,
                        model=provider.model,
                        local_request_id=request_id,
                        request_id=result.provider_request_id,
                        prompt_version=IMAGE_PROMPT,
                        input_artifact_ids=(plan_id,),
                    ),
                    dependencies=[InputDependency(plan_id, "visual_plan")],
                    expected_media_type="image/png",
                )
                metadata: dict[str, Any] = {
                    key: result.response_metadata[key]
                    for key in ("seed", "workflow_sha256", "width", "height", "steps")
                    if key in result.response_metadata
                }
                with closing(store.database.connect()) as db:
                    db.execute(
                        "UPDATE production_image_receipts SET source_artifact_id=?, "
                        "response_sha256=?,provider_request_id=?,response_metadata_json=? "
                        "WHERE request_id=?",
                        (
                            source_record.identity.artifact_id,
                            source_record.sha256,
                            result.provider_request_id,
                            json.dumps(metadata),
                            request_id,
                        ),
                    )
                    db.commit()
            except Exception as exc:
                outcome = exc.outcome if isinstance(exc, ProviderFailure) else "ambiguous"
                ledger.transition(
                    request_id,
                    "ambiguous" if started and outcome != "terminal_failure" else "failed",
                    provider_request_id=exc.provider_request_id
                    if isinstance(exc, ProviderFailure)
                    else None,
                    definitive_remote_failure=outcome == "terminal_failure",
                )
                raise ImageStageBlocked(
                    "AMBIGUOUS" if started and outcome != "terminal_failure" else "FAILED",
                    request_id,
                ) from exc
        assert_owner()
        store.admit_preview(source_record.identity.artifact_id)
        image = normalize(
            _prepare_qwen_white_background(
                store.path_for(source_record.identity.artifact_id).read_bytes(), "image/png"
            )
        )
        image.save(path)
        record = persist_file(
            store,
            episode,
            "visual_asset",
            slot,
            path,
            (plan_id, source_record.identity.artifact_id),
        )
        with closing(store.database.connect()) as db:
            db.execute(
                "UPDATE production_image_receipts SET source_artifact_id=?,response_sha256=?,"
                "normalized_artifact_id=? WHERE request_id=?",
                (
                    source_record.identity.artifact_id,
                    source_record.sha256,
                    record.identity.artifact_id,
                    request_id,
                ),
            )
            state = db.execute(
                "SELECT status FROM generation_requests WHERE request_id=?", (request_id,)
            ).fetchone()[0]
            db.commit()
        if state != "succeeded":
            ledger.transition(
                request_id,
                "succeeded",
                provider_request_id=(
                    receipt["provider_request_id"] or source_record.provenance.request_id
                ),
            )
        assets[slot] = record.identity.artifact_id
    return assets
