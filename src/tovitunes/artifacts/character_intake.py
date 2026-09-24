"""Repeatable, offline intake of owner-supplied character art."""

import io
import json
import os
import re
from collections import deque
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from PIL import Image, ImageFilter
from PIL import __version__ as pillow_version
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tovitunes.artifacts.character_png import (
    MouthFacts,
    SpriteFacts,
    inspect_mouth_component,
    inspect_sprite_png,
    validate_mouth_states,
    visible_art_bbox,
)
from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.catalog import BrandCatalog, load_brand
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.character import CharacterAssetPack
from tovitunes.domain.review import ApprovalDecision, RightsDecision

_ROLE = re.compile(r"^(view|mouth|sprite)/[a-z][a-z0-9_]*$")
_SOURCE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_FILENAME = re.compile(r"^[a-z][a-z0-9_-]*\.png$")


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_uri: str
    uses_originals: bool = False
    rights_basis: Literal["openai_chatgpt_output"] | None = None

    @model_validator(mode="after")
    def safe_file(self) -> "SourceSpec":
        if not _FILENAME.fullmatch(self.filename):
            raise ValueError("source filename must be a simple lowercase PNG name")
        if not self.source_uri:
            raise ValueError("source URI is required")
        if self.rights_basis and not self.source_uri.startswith("generation://"):
            raise ValueError("OpenAI output rights require a generation URI")
        return self


class MouthNormalization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_canvas: tuple[int, int]
    output_canvas: tuple[int, int]
    anchor: tuple[int, int]
    alpha_threshold: int = Field(default=8, ge=1, le=32)

    @model_validator(mode="after")
    def valid_canvas(self) -> "MouthNormalization":
        if min(*self.source_canvas, *self.output_canvas) <= 0:
            raise ValueError("mouth canvases must be positive")
        if not (
            0 < self.anchor[0] < self.output_canvas[0]
            and 0 < self.anchor[1] < self.output_canvas[1]
        ):
            raise ValueError("mouth anchor must be inside the output canvas")
        return self


class RightsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str
    policy_version: str

    @model_validator(mode="after")
    def official_terms(self) -> "RightsEvidence":
        if not self.uri.startswith("https://openai.com/policies/") or not self.policy_version:
            raise ValueError("OpenAI output rights require official terms and a policy version")
        return self


class AssetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    source: str
    rect: tuple[int, int, int, int]
    method: Literal["crop", "component", "mouth_component", "pad"]
    canvas: tuple[int, int] | None = None
    trim: bool = False
    padding: int = 0
    art_review: Literal["approved", "needs_review"]
    quality_note: str | None = None

    @model_validator(mode="after")
    def valid_role_and_geometry(self) -> "AssetSpec":
        if not _ROLE.fullmatch(self.role):
            raise ValueError("invalid character role")
        x0, y0, x1, y1 = self.rect
        if min(x0, y0) < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError("invalid crop rectangle")
        if self.canvas and (self.canvas[0] < x1 - x0 or self.canvas[1] < y1 - y0):
            raise ValueError("canvas must contain the unscaled crop")
        if self.trim and self.canvas:
            raise ValueError("trim and fixed canvas are mutually exclusive")
        if self.method == "mouth_component" and (
            not self.role.startswith("mouth/") or self.canvas or self.trim or self.padding
        ):
            raise ValueError("mouth normalization is reserved for mouth components")
        if self.method == "pad" and (self.padding < 16 or self.canvas or self.trim):
            raise ValueError("padded sprite needs transparent padding only")
        if self.method != "pad" and self.padding:
            raise ValueError("padding is reserved for padded sprites")
        if self.role.startswith("view/") and self.method != "crop":
            raise ValueError("reference views use lossless rectangular crops")
        if self.art_review == "needs_review" and not self.quality_note:
            raise ValueError("an unapproved candidate needs a quality note")
        return self


class IntakeRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    sources: dict[str, SourceSpec]
    assets: tuple[AssetSpec, ...]
    superseded_assets: tuple[AssetSpec, ...] = ()
    mouth_normalization: MouthNormalization | None = None
    rights_evidence: RightsEvidence | None = None

    @model_validator(mode="after")
    def valid_mapping(self) -> "IntakeRecipe":
        if any(not _SOURCE_KEY.fullmatch(key) for key in self.sources):
            raise ValueError("invalid source key")
        roles = [asset.role for asset in self.assets]
        if len(roles) != len(set(roles)):
            raise ValueError("character roles must be unique")
        if any(asset.source not in self.sources for asset in self.assets):
            raise ValueError("asset refers to an unknown source")
        if any(asset.source not in self.sources for asset in self.superseded_assets):
            raise ValueError("superseded asset refers to an unknown source")
        if any(asset.role not in roles for asset in self.superseded_assets):
            raise ValueError("superseded asset has no active replacement")
        if any(asset.method == "mouth_component" for asset in self.assets):
            if self.mouth_normalization is None:
                raise ValueError("mouth components require a normalization contract")
        if any(spec.rights_basis for spec in self.sources.values()):
            if self.rights_evidence is None:
                raise ValueError("OpenAI generated sources require rights evidence")
        if any(spec.uses_originals for spec in self.sources.values()) and not {
            "original_profile",
            "original_banner",
        }.issubset(self.sources):
            raise ValueError("original references are missing")
        return self


def load_recipe(path: Path) -> IntakeRecipe:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return IntakeRecipe.model_validate(raw)


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_path(root: Path, spec: SourceSpec) -> Path:
    path = root / spec.filename
    if path.is_symlink():
        raise ValueError(f"source symlink is not trusted: {spec.filename}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"source is outside trusted root: {spec.filename}")
    if _digest(resolved) != spec.sha256:
        raise ValueError(f"source hash differs from recipe: {spec.filename}")
    try:
        with Image.open(resolved) as image:
            if image.format != "PNG":
                raise ValueError(f"source is not a PNG: {spec.filename}")
            image.load()
    except OSError as exc:
        raise ValueError(f"source PNG cannot be decoded: {spec.filename}") from exc
    return resolved


def _largest_component(image: Image.Image, threshold: int = 5) -> Image.Image:
    """Keep one connected opaque region plus its original antialiased fringe."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    alpha = rgba.getchannel("A").tobytes()
    seen = bytearray(width * height)
    largest: list[int] = []
    for start, value in enumerate(alpha):
        if value <= threshold or seen[start]:
            continue
        component: list[int] = []
        queue = deque([start])
        seen[start] = 1
        while queue:
            index = queue.popleft()
            component.append(index)
            x, y = index % width, index // width
            for neighbor in (
                index - 1 if x else -1,
                index + 1 if x + 1 < width else -1,
                index - width if y else -1,
                index + width if y + 1 < height else -1,
            ):
                if neighbor >= 0 and not seen[neighbor] and alpha[neighbor] > threshold:
                    seen[neighbor] = 1
                    queue.append(neighbor)
        if len(component) > len(largest):
            largest = component
    if not largest:
        raise ValueError("crop contains no substantial alpha component")
    mask = bytearray(width * height)
    for index in largest:
        mask[index] = 255
    fringe = Image.frombytes("L", (width, height), bytes(mask)).filter(ImageFilter.MaxFilter(3))
    keep = fringe.tobytes()
    pixels = bytearray(rgba.tobytes())
    for index, value in enumerate(keep):
        if not value:
            pixels[index * 4 + 3] = 0
    return Image.frombytes("RGBA", (width, height), bytes(pixels))


def _extract(
    image: Image.Image, spec: AssetSpec, mouth_normalization: MouthNormalization | None
) -> tuple[Image.Image, tuple[str, ...], dict[str, object] | None]:
    x0, y0, x1, y1 = spec.rect
    if x1 > image.width or y1 > image.height:
        raise ValueError(f"crop exceeds source bounds: {spec.role}")
    part = image.crop(spec.rect)
    if spec.role.startswith("view/"):
        return part.convert("RGB"), (), None
    part = part.convert("RGBA")
    alignment: dict[str, object] | None = None
    if spec.method == "mouth_component":
        if mouth_normalization is None or image.size != mouth_normalization.source_canvas:
            raise ValueError("mouth source differs from normalization contract")
        if spec.rect != (0, 0, *image.size):
            raise ValueError("mouth component must preserve the complete source frame")
        mouth = inspect_mouth_component(part, alpha_threshold=mouth_normalization.alpha_threshold)
        bbox = mouth.visible_bbox
        source_anchor = ((bbox[0] + bbox[2]) // 2, bbox[1])
        offset = (
            mouth_normalization.anchor[0] - source_anchor[0],
            mouth_normalization.anchor[1] - source_anchor[1],
        )
        canvas = Image.new("RGBA", mouth_normalization.output_canvas, (0, 0, 0, 0))
        if (
            min(offset) < 0
            or offset[0] + part.width > canvas.width
            or offset[1] + part.height > canvas.height
        ):
            raise ValueError("mouth source cannot fit without clipping")
        canvas.alpha_composite(part, offset)
        alignment = {
            "source_anchor": source_anchor,
            "common_anchor": mouth_normalization.anchor,
            "paste_offset": offset,
            "visible_bbox": (
                bbox[0] + offset[0],
                bbox[1] + offset[1],
                bbox[2] + offset[0],
                bbox[3] + offset[1],
            ),
            "source_aperture_bbox": mouth.aperture_bbox,
            "aperture_pixels": mouth.aperture_pixels,
        }
        return canvas, (), alignment
    if spec.method == "pad":
        visible_art_bbox(part)
        canvas = Image.new(
            "RGBA",
            (part.width + 2 * spec.padding, part.height + 2 * spec.padding),
            (0, 0, 0, 0),
        )
        canvas.alpha_composite(part, (spec.padding, spec.padding))
        return canvas, (), None
    if spec.method == "component":
        part = _largest_component(part)
    raw_bbox = part.getchannel("A").getbbox()
    if raw_bbox is None:
        raise ValueError(f"crop is empty: {spec.role}")
    warnings: list[str] = []
    edge = part.getchannel("A")
    if any(
        sum(value > 32 for value in edge.crop(box).tobytes()) > 12
        for box in (
            (0, 0, 1, part.height),
            (part.width - 1, 0, part.width, part.height),
        )
    ):
        warnings.append("visible pixels meet a crop seam")
    if spec.trim:
        part = part.crop(raw_bbox)
        padded = Image.new("RGBA", (part.width + 32, part.height + 32), (0, 0, 0, 0))
        padded.alpha_composite(part, (16, 16))
        part = padded
    elif spec.canvas:
        canvas = Image.new("RGBA", spec.canvas, (0, 0, 0, 0))
        canvas.alpha_composite(part, ((canvas.width - part.width) // 2, 0))
        part = canvas
    return part, tuple(warnings), alignment


def _write_png(path: Path, image: Image.Image) -> None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    content = buffer.getvalue()
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError(f"prepared file already has different bytes: {path.name}")
        return
    staging = path.with_suffix(".png.tmp")
    staging.write_bytes(content)
    os.replace(staging, path)


def prepare_assets(recipe_path: Path, source_dir: Path, output_dir: Path) -> dict[str, object]:
    recipe = load_recipe(recipe_path)
    source_root = source_dir.resolve(strict=True)
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("source directory is not trusted")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_root = output_dir.resolve(strict=True)
    if (
        output_dir.is_symlink()
        or output_root.is_relative_to(source_root)
        or source_root.is_relative_to(output_root)
    ):
        raise ValueError("prepared directory must be separate from sources")
    sources = {key: _source_path(source_root, spec) for key, spec in recipe.sources.items()}
    assets: list[dict[str, object]] = []
    mouth_facts: dict[str, MouthFacts] = {}
    for spec in recipe.assets:
        with Image.open(sources[spec.source]) as image:
            image.load()
            output, warnings, alignment = _extract(image, spec, recipe.mouth_normalization)
        name = spec.role.replace("/", "__") + ".png"
        destination = output_root / name
        _write_png(destination, output)
        facts: SpriteFacts | None = None
        if not spec.role.startswith("view/"):
            facts = inspect_sprite_png(destination)
        if spec.method == "mouth_component":
            normalization = recipe.mouth_normalization
            assert normalization is not None
            mouth_facts[spec.role.split("/", 1)[1]] = inspect_mouth_component(
                output, alpha_threshold=normalization.alpha_threshold
            )
        assets.append(
            {
                "role": spec.role,
                "source": spec.source,
                "filename": name,
                "sha256": _digest(destination),
                "size": output.size,
                "alpha_bbox": facts.alpha_bbox if facts else None,
                "warnings": warnings,
                "mouth_alignment": alignment,
                "art_review": spec.art_review,
                "quality_note": spec.quality_note,
            }
        )
    if mouth_facts:
        validate_mouth_states(mouth_facts)
    report: dict[str, object] = {
        "recipe_sha256": _digest(recipe_path),
        "preparation_tool": f"tovitunes-character-intake/pillow-{pillow_version}",
        "sources": {key: spec.sha256 for key, spec in recipe.sources.items()},
        "mouth_normalization": (
            recipe.mouth_normalization.model_dump() if recipe.mouth_normalization else None
        ),
        "assets": assets,
    }
    (output_root / "prepare-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _approval(
    store: AssetStore,
    artifact_id: str,
    status: Literal["approved", "needs_review"],
    actor: str,
    reason: str,
) -> None:
    store.record_approval(
        ApprovalDecision(
            target_id=artifact_id,
            target_kind="artifact",
            status=status,
            actor=actor,
            reason=reason,
            policy_version="tovi-visual-v1",
            decided_at=datetime.now(UTC),
        )
    )


def _record_openai_rights(
    recipe: IntakeRecipe, store: AssetStore, source_ids: dict[str, str], actor: str
) -> tuple[str, ...]:
    """Document only explicitly identified OpenAI outputs and their intake derivatives."""
    evidence = recipe.rights_evidence
    if evidence is None:
        return ()
    cleared: list[str] = []
    for key, spec in recipe.sources.items():
        if spec.rights_basis != "openai_chatgpt_output":
            continue
        source_id = source_ids[key]
        allowed_roles = {
            item.role for item in (*recipe.assets, *recipe.superseded_assets) if item.source == key
        }
        with closing(store.database.connect()) as connection:
            rows = connection.execute(
                "SELECT consumer_artifact_id FROM artifact_dependencies "
                "WHERE input_artifact_id = ?",
                (source_id,),
            ).fetchall()
        candidate_ids = [source_id]
        for row in rows:
            record = store.get(row["consumer_artifact_id"])
            if any(
                record.provenance.source_uri == f"intake://tovi-v1/{key}/{role}"
                for role in allowed_roles
            ):
                candidate_ids.append(record.identity.artifact_id)
        for artifact_id in candidate_ids:
            with closing(store.database.connect()) as connection:
                latest = connection.execute(
                    "SELECT status, evidence_uri FROM rights_decisions WHERE artifact_id = ? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (artifact_id,),
                ).fetchone()
            if latest is not None and latest["status"] in {
                "blocked",
                "review_required",
                "commercial_use_confirmed",
            }:
                continue
            rationale = (
                f"Owner identifies {spec.source_uri} as ChatGPT image output; "
                "OpenAI terms assign OpenAI's interest in Output to the user. "
                "Rights to original input references remain separate."
            )
            store.record_rights(
                RightsDecision(
                    artifact_id=artifact_id,
                    status="commercial_use_confirmed",
                    actor=actor,
                    evidence_uri=evidence.uri,
                    rationale=rationale,
                    policy_version=evidence.policy_version,
                    decided_at=datetime.now(UTC),
                )
            )
            cleared.append(artifact_id)
    return tuple(cleared)


def ingest_prepared(
    recipe_path: Path,
    source_dir: Path,
    prepared_dir: Path,
    store: AssetStore,
    catalog: BrandCatalog,
    manifest_path: Path,
    *,
    actor: str = "project-owner",
) -> dict[str, object]:
    """Register immutable versions, reviewed art and documented output rights."""
    recipe = load_recipe(recipe_path)
    source_root = source_dir.resolve(strict=True)
    prepared_root = prepared_dir.resolve(strict=True)
    if not actor.strip() or source_root.is_symlink() or prepared_root.is_symlink():
        raise ValueError("untrusted intake path or empty actor")
    report_path = prepared_root / "prepare-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("recipe_sha256") != _digest(recipe_path):
        raise ValueError("preparation report uses another recipe revision")
    prepared = {item["role"]: item for item in report["assets"]}
    if set(prepared) != {asset.role for asset in recipe.assets}:
        raise ValueError("preparation report has missing or extra roles")
    mouth_facts: dict[str, MouthFacts] = {}
    for asset_spec in recipe.assets:
        item = prepared[asset_spec.role]
        name = asset_spec.role.replace("/", "__") + ".png"
        if item["filename"] != name or item["source"] != asset_spec.source:
            raise ValueError(f"preparation mapping differs: {asset_spec.role}")
        path = prepared_root / name
        if path.is_symlink() or path.resolve(strict=True).parent != prepared_root:
            raise ValueError(f"prepared path is not trusted: {name}")
        if _digest(path) != item["sha256"]:
            raise ValueError(f"prepared hash changed: {name}")
        if asset_spec.art_review == "approved" and item["warnings"]:
            raise ValueError(f"approved crop has unresolved quality warnings: {asset_spec.role}")
        if asset_spec.role.startswith("view/"):
            continue
        inspect_sprite_png(path)
        if asset_spec.method == "mouth_component":
            normalization = recipe.mouth_normalization
            assert normalization is not None
            with Image.open(path) as mouth_image:
                mouth_image.load()
                if mouth_image.size != normalization.output_canvas:
                    raise ValueError("mouth output canvas differs from normalization contract")
                facts = inspect_mouth_component(
                    mouth_image, alpha_threshold=normalization.alpha_threshold
                )
            bbox = facts.visible_bbox
            if ((bbox[0] + bbox[2]) // 2, bbox[1]) != normalization.anchor:
                raise ValueError(f"mouth anchor differs from contract: {asset_spec.role}")
            mouth_facts[asset_spec.role.split("/", 1)[1]] = facts
        elif asset_spec.method == "pad":
            with Image.open(path) as padded_image:
                padded_image.load()
                visible_art_bbox(padded_image)
    if mouth_facts:
        validate_mouth_states(mouth_facts)
    store.database.register_catalog(catalog)
    owner_id = catalog.version.revision_id
    source_ids: dict[str, str] = {}
    for key, source_spec in recipe.sources.items():
        path = _source_path(source_root, source_spec)
        dependencies = (
            [
                InputDependency(source_ids[original], "original Tovi visual reference")
                for original in ("original_profile", "original_banner")
            ]
            if source_spec.uses_originals
            else []
        )
        provenance = Provenance(
            source_kind="manual",
            acquired_at=datetime.now(UTC),
            operator=actor,
            source_uri=source_spec.source_uri,
            input_artifact_ids=tuple(item.artifact_id for item in dependencies),
        )
        slot = "source_" + key
        existing = store.find_version(
            "brand", owner_id, "character_reference", slot, source_spec.sha256
        )
        record = existing or store.ingest(
            path,
            owner_scope="brand",
            owner_id=owner_id,
            kind="character_reference",
            slot_key=slot,
            provenance=provenance,
            dependencies=dependencies,
            expected_media_type="image/png",
        )
        artifact_id = record.identity.artifact_id
        source_ids[key] = artifact_id
        if existing is None:
            _approval(store, artifact_id, "approved", actor, "owner supplied visual reference")
        store.select(artifact_id)
    role_ids: dict[str, str] = {}
    selected: list[str] = []
    replaced_roles = {item.role for item in recipe.superseded_assets}
    for asset_spec in recipe.assets:
        item = prepared[asset_spec.role]
        name = asset_spec.role.replace("/", "__") + ".png"
        path = prepared_root / name
        digest = _digest(path)
        if digest != item["sha256"]:
            raise ValueError(f"prepared hash changed during intake: {name}")
        kind = "character_reference" if asset_spec.role.startswith("view/") else "character_sprite"
        slot = "tovi_v1_" + asset_spec.role.replace("/", "_")
        existing = store.find_version("brand", owner_id, kind, slot, digest)
        source_id = source_ids[asset_spec.source]
        provenance = Provenance(
            source_kind="deterministic",
            acquired_at=datetime.now(UTC),
            provider=str(report["preparation_tool"]),
            source_uri=f"intake://tovi-v1/{asset_spec.source}/{asset_spec.role}",
            input_artifact_ids=(source_id,),
        )
        record = existing or store.ingest(
            path,
            owner_scope="brand",
            owner_id=owner_id,
            kind=kind,
            slot_key=slot,
            provenance=provenance,
            dependencies=[InputDependency(source_id, "deterministic character crop")],
            expected_media_type="image/png",
        )
        artifact_id = record.identity.artifact_id
        role_ids[asset_spec.role] = artifact_id
        if existing is None:
            _approval(
                store,
                artifact_id,
                asset_spec.art_review,
                actor,
                asset_spec.quality_note
                or (
                    "Replaces held PR #10 candidate; isolated sprite and visual inspection passed"
                    if asset_spec.role in replaced_roles
                    else "approved Tovi v1 visual direction; technical crop passed"
                ),
            )
        if asset_spec.art_review == "approved":
            store.select(artifact_id)
            selected.append(asset_spec.role)
    rights_decision_ids = _record_openai_rights(recipe, store, source_ids, actor)
    expected_tail = ("tovitunes", "characters", "tovi", "packs", "v1", "pack.yaml")
    if catalog.definition.brand_id != "tovitunes" or manifest_path.parts[-6:] != expected_tail:
        raise ValueError("manifest path does not match the Tovi brand")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("pack_id") != "tovi-pack-v1":
        raise ValueError("unexpected character pack manifest")
    raw["asset_artifact_ids"] = role_ids
    pack = CharacterAssetPack.model_validate(raw)
    if pack.readiness != "draft":
        raise ValueError("intake cannot approve a character pack")
    with manifest_path.open("w", encoding="utf-8", newline="\n") as manifest_stream:
        manifest_stream.write(yaml.safe_dump(raw, sort_keys=False))
    updated = load_brand(manifest_path.parents[4])
    store.database.register_catalog(updated)
    from tovitunes.artifacts.character_pack import assess_pack_assets

    assessment = assess_pack_assets(updated.packs[0], store, owner_id)
    intake_report: dict[str, object] = {
        "brand_revision_id": owner_id,
        "source_artifact_ids": source_ids,
        "role_artifact_ids": role_ids,
        "selected_roles": selected,
        "rights_state": ("documented_openai_outputs" if recipe.rights_evidence else "unknown"),
        "rights_evidence_uri": recipe.rights_evidence.uri if recipe.rights_evidence else None,
        "rights_decision_artifact_ids": rights_decision_ids,
        "pack_ready": assessment.ready,
        "blocking_issues": assessment.issues,
    }
    (prepared_root / "intake-report.json").write_text(
        json.dumps(intake_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return intake_report
