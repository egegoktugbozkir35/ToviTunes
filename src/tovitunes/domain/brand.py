"""Versioned brand and policy definitions."""

from pydantic import BaseModel, ConfigDict, Field


class BrandDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    brand_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str
    name: str
    language: str
    target_age_min: int = Field(ge=0)
    target_age_max: int = Field(ge=0)
    duration_min_seconds: int = Field(gt=0)
    duration_max_seconds: int = Field(gt=0)
    curriculum_file: str
    characters: tuple[str, ...] = Field(min_length=1)


class CreativeBible(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    version: str
    visual_direction: str
    music_direction: str


class SafetyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    version: str
    prohibited: tuple[str, ...]
    review_required: tuple[str, ...]


class BrandVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    brand_id: str
    version: str
    definition_sha256: str
    creative_sha256: str
    safety_sha256: str
    source_revision: str | None = None

