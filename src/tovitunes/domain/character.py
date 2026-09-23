"""Independently versioned character pack metadata."""

from typing import Literal

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

    schema_version: int = Field(default=1, ge=1, le=1)
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

    @model_validator(mode="after")
    def approved_pack_has_assets(self) -> "CharacterAssetPack":
        if self.readiness == "approved" and (not self.canonical_views or not self.reusable_sprites):
            raise ValueError("approved character pack requires references and sprites")
        return self


class CharacterPackRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    pack_id: str
    character_id: str
    version: str
    manifest_sha256: str
    readiness: Literal["draft", "approved"]

