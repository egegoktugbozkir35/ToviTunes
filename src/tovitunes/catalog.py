"""Load and pin the complete brand catalog from trusted versioned YAML."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from tovitunes.domain.brand import BrandDefinition, BrandVersion, CreativeBible, SafetyPolicy
from tovitunes.domain.character import (
    CharacterAssetPack,
    CharacterDefinition,
    CharacterPackRevision,
)
from tovitunes.domain.curriculum import Curriculum, CurriculumRevision


def _within(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError(f"catalog path escapes brand root: {relative}")
    return path


def _read_yaml(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_bytes()
    value = yaml.safe_load(content)
    if not isinstance(value, dict):
        raise ValueError(f"catalog file must be a mapping: {path}")
    return value, sha256(content).hexdigest()


@dataclass(frozen=True)
class BrandCatalog:
    definition: BrandDefinition
    version: BrandVersion
    creative_bible: CreativeBible
    safety_policy: SafetyPolicy
    curriculum: Curriculum
    curriculum_revision: CurriculumRevision
    characters: tuple[CharacterDefinition, ...]
    packs: tuple[CharacterAssetPack, ...]
    pack_revisions: tuple[CharacterPackRevision, ...]


def load_brand(root: Path, *, source_revision: str | None = None) -> BrandCatalog:
    brand_root = root.resolve(strict=True)
    brand_raw, brand_hash = _read_yaml(_within(brand_root, "brand.yaml"))
    creative_raw, creative_hash = _read_yaml(_within(brand_root, "creative_bible.yaml"))
    safety_raw, safety_hash = _read_yaml(_within(brand_root, "safety_policy.yaml"))
    brand = BrandDefinition.model_validate(brand_raw)
    creative = CreativeBible.model_validate(creative_raw)
    safety = SafetyPolicy.model_validate(safety_raw)
    if brand.target_age_max < brand.target_age_min:
        raise ValueError("target age range is inverted")
    if brand.duration_max_seconds < brand.duration_min_seconds:
        raise ValueError("duration range is inverted")
    curriculum_raw, curriculum_hash = _read_yaml(_within(brand_root, brand.curriculum_file))
    curriculum = Curriculum.model_validate(curriculum_raw)
    characters: list[CharacterDefinition] = []
    packs: list[CharacterAssetPack] = []
    pack_revisions: list[CharacterPackRevision] = []
    for character_id in brand.characters:
        if (
            not character_id.isascii()
            or not character_id.replace("-", "").replace("_", "").isalnum()
        ):
            raise ValueError("unsafe character ID")
        character_raw, _ = _read_yaml(
            _within(brand_root, f"characters/{character_id}/character.yaml")
        )
        character = CharacterDefinition.model_validate(character_raw)
        if character.character_id != character_id:
            raise ValueError("character file ID does not match brand")
        pack_raw, pack_hash = _read_yaml(_within(brand_root, character.pack_file))
        pack = CharacterAssetPack.model_validate(pack_raw)
        if pack.character_id != character_id:
            raise ValueError("character pack ID does not match character")
        characters.append(character)
        packs.append(pack)
        pack_revisions.append(
            CharacterPackRevision(
                revision_id=f"{pack.pack_id}-{pack_hash[:16]}",
                pack_id=pack.pack_id,
                character_id=pack.character_id,
                version=pack.version,
                manifest_sha256=pack_hash,
                readiness=pack.readiness,
            )
        )
    brand_revision_digest = sha256(
        (brand_hash + creative_hash + safety_hash + (source_revision or "")).encode()
    ).hexdigest()
    return BrandCatalog(
        definition=brand,
        version=BrandVersion(
            revision_id=f"{brand.brand_id}-{brand_revision_digest[:16]}",
            brand_id=brand.brand_id,
            version=brand.version,
            definition_sha256=brand_hash,
            creative_sha256=creative_hash,
            safety_sha256=safety_hash,
            source_revision=source_revision,
        ),
        creative_bible=creative,
        safety_policy=safety,
        curriculum=curriculum,
        curriculum_revision=CurriculumRevision(
            revision_id=f"{curriculum.curriculum_id}-{curriculum_hash[:16]}",
            curriculum_id=curriculum.curriculum_id,
            version=curriculum.version,
            sha256=curriculum_hash,
        ),
        characters=tuple(characters),
        packs=tuple(packs),
        pack_revisions=tuple(pack_revisions),
    )

