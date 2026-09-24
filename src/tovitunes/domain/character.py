"""Independently versioned character pack metadata."""

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CharacterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    character_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str
    display_name: str
    pack_file: str


class CharacterAssetPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=2, ge=2, le=2)
    pack_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    character_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str
    readiness: Literal["draft", "approved"]
    palette: dict[str, str]
    canonical_views: tuple[str, ...]
    expressions: tuple[str, ...]
    wing_poses: tuple[str, ...]
    mouth_states: tuple[str, ...]
    reusable_sprites: tuple[str, ...]
    rig_data: str | None = None
    allowed_accessories: tuple[str, ...]
    forbidden_changes: tuple[str, ...]
    visual_rules: dict[str, str] = Field(default_factory=dict)
    asset_artifact_ids: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def approved_pack_has_assets(self) -> "CharacterAssetPack":
        for name, value in self.palette.items():
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                raise ValueError(f"palette color {name} must be #RRGGBB")
        if self.readiness != "approved":
            return self
        required_views = {"front", "three_quarter", "profile"}
        required_mouths = {"closed", "small_open", "wide_a", "e_smile", "o_round"}
        required_rules = {"silhouette", "proportions", "eyes", "beak", "tuft"}
        if not required_views.issubset(self.canonical_views):
            raise ValueError("approved pack needs front, three-quarter and profile views")
        if not required_mouths.issubset(self.mouth_states):
            raise ValueError("approved pack needs the five initial mouth states")
        if (
            not self.palette
            or not self.reusable_sprites
            or not self.expressions
            or not self.wing_poses
            or not self.forbidden_changes
        ):
            raise ValueError("approved pack needs palette, poses, sprites and identity rules")
        if any(not self.visual_rules.get(name) for name in required_rules):
            raise ValueError("approved pack needs silhouette and facial geometry rules")
        required_slots = {
            *(f"view/{name}" for name in self.canonical_views),
            *(f"sprite/{name}" for name in self.reusable_sprites),
            *(f"mouth/{name}" for name in self.mouth_states),
        }
        if not required_slots.issubset(self.asset_artifact_ids):
            raise ValueError("approved pack needs artifact IDs for every required image")
        if any(
            not key.startswith(("view/", "sprite/", "mouth/")) for key in self.asset_artifact_ids
        ):
            raise ValueError("approved pack contains an unknown asset role")
        required_ids = [self.asset_artifact_ids[name] for name in sorted(required_slots)]
        if len(required_ids) != len(set(required_ids)):
            raise ValueError("approved pack cannot reuse one image for multiple required roles")
        for artifact_id in self.asset_artifact_ids.values():
            try:
                UUID(artifact_id)
            except ValueError as exc:
                raise ValueError("approved pack contains an invalid artifact ID") from exc
        if self.rig_data is not None:
            try:
                UUID(self.rig_data)
            except ValueError as exc:
                raise ValueError("approved pack contains an invalid rig artifact ID") from exc
        return self


class CharacterPackRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    pack_id: str
    character_id: str
    version: str
    manifest_sha256: str
    readiness: Literal["draft", "approved"]

