"""Episode identity is pinned before any expensive generation."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from tovitunes.catalog import BrandCatalog
from tovitunes.domain.creative import EpisodeSpec as EpisodeSpec

if TYPE_CHECKING:
    from tovitunes.creative.learning import LearningBrief


class PinnedCharacterPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: str
    revision_id: str


class Episode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str
    external_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    brand_revision_id: str
    curriculum_revision_id: str | None
    learning_source: Literal["legacy_curriculum", "generated_learning_brief"] = "legacy_curriculum"
    learning_brief_id: str | None = None
    learning_policy_revision_id: str | None = None
    subject: str | None = None
    concept_id: str
    objective_id: str
    objective: str
    target_vocabulary: tuple[str, ...]
    language: str
    target_duration_seconds: int = Field(gt=0)
    character_packs: tuple[PinnedCharacterPack, ...] = Field(min_length=1)
    lifecycle: Literal["draft", "active", "held", "complete", "archived"] = "draft"
    created_at: datetime

    @model_serializer(mode="wrap")
    def legacy_wire_identity(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        # Preserve exact pre-migration prompt/receipt fingerprints for legacy resumes.
        if self.learning_source == "legacy_curriculum":
            for key in (
                "learning_source",
                "learning_brief_id",
                "learning_policy_revision_id",
                "subject",
            ):
                data.pop(key, None)
        return data

    @model_validator(mode="after")
    def unique_character_packs(self) -> "Episode":
        if self.learning_source == "legacy_curriculum":
            if self.curriculum_revision_id is None or any(
                (self.learning_brief_id, self.learning_policy_revision_id, self.subject)
            ):
                raise ValueError("legacy episode requires its original curriculum pins")
        elif self.curriculum_revision_id is not None or not all(
            (self.learning_brief_id, self.learning_policy_revision_id, self.subject)
        ):
            raise ValueError("generated episode requires exact learning brief and policy pins")
        ids = [pack.character_id for pack in self.character_packs]
        if len(ids) != len(set(ids)):
            raise ValueError("episode pins more than one pack for a character")
        return self

    @classmethod
    def from_learning_brief(
        cls, catalog: BrandCatalog, brief: "LearningBrief", external_key: str
    ) -> "Episode":
        from tovitunes.creative.learning import subject_slug

        return cls(
            episode_id=str(uuid4()),
            external_key=external_key,
            brand_revision_id=catalog.version.revision_id,
            curriculum_revision_id=None,
            learning_source="generated_learning_brief",
            learning_brief_id=brief.brief_id,
            learning_policy_revision_id=brief.learning_policy_revision_id,
            subject=brief.subject,
            concept_id=subject_slug(brief.subject) + "-" + brief.idea_fingerprint[:12],
            objective_id="learning." + brief.idea_fingerprint,
            objective=brief.objective,
            target_vocabulary=brief.target_vocabulary,
            language=brief.language,
            target_duration_seconds=brief.target_duration_seconds,
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
