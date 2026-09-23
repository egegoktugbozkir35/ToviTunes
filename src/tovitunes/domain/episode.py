"""Episode identity is pinned before any expensive generation."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tovitunes.catalog import BrandCatalog


class PinnedCharacterPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: str
    revision_id: str


class Episode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str
    external_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    brand_revision_id: str
    curriculum_revision_id: str
    concept_id: str
    objective_id: str
    objective: str
    target_vocabulary: tuple[str, ...]
    language: str
    target_duration_seconds: int = Field(gt=0)
    character_packs: tuple[PinnedCharacterPack, ...] = Field(min_length=1)
    lifecycle: Literal["draft", "active", "held", "complete", "archived"] = "draft"
    created_at: datetime

    @model_validator(mode="after")
    def unique_character_packs(self) -> "Episode":
        ids = [pack.character_id for pack in self.character_packs]
        if len(ids) != len(set(ids)):
            raise ValueError("episode pins more than one pack for a character")
        return self

    @classmethod
    def create(cls, catalog: BrandCatalog, concept_id: str, external_key: str) -> "Episode":
        concept = catalog.curriculum.get(concept_id)
        duration = (
            catalog.definition.duration_min_seconds + catalog.definition.duration_max_seconds
        ) // 2
        return cls(
            episode_id=str(uuid4()),
            external_key=external_key,
            brand_revision_id=catalog.version.revision_id,
            curriculum_revision_id=catalog.curriculum_revision.revision_id,
            concept_id=concept.concept_id,
            objective_id=concept.objective_id,
            objective=concept.objective,
            target_vocabulary=concept.target_vocabulary,
            language=catalog.definition.language,
            target_duration_seconds=duration,
            character_packs=tuple(
                sorted(
                    (
                        PinnedCharacterPack(character_id=p.character_id, revision_id=p.revision_id)
                        for p in catalog.pack_revisions
                    ),
                    key=lambda p: p.character_id,
                )
            ),
            created_at=datetime.now(UTC),
        )


class EpisodeSpec(BaseModel):
    """Reviewed creative specification before music and timed scenes exist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    episode_id: str
    objective_id: str
    premise: str
    cast: tuple[str, ...] = Field(min_length=1)
    setting: str
    teaching_vocabulary: tuple[str, ...] = Field(min_length=1)
    story_beats: tuple[str, ...] = Field(min_length=1)
    lyrics: str
    desired_structure: str

