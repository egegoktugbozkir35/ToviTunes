"""Small subject and publication contracts, separate from authoritative creative specs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreativeSubjectCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    concept_id: str = Field(min_length=1, max_length=80)
    premise: str = Field(min_length=1, max_length=400)
    hook: str = Field(min_length=1, max_length=200)
    setting: str = Field(min_length=1, max_length=120)
    example_objects: tuple[str, ...] = Field(min_length=1, max_length=4)
    song_angle: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=300)


class CreativeSubjectPool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[CreativeSubjectCandidate, ...] = Field(min_length=3, max_length=5)


class EpisodePublicationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    youtube_title: str = Field(min_length=1, max_length=100)
    youtube_description: str = Field(min_length=1, max_length=1000)
    tags: tuple[str, ...] = Field(min_length=1, max_length=20)
    language: Literal["en"] = "en"
    made_for_kids: Literal[True] = True
    episode_id: str
    concept_id: str
    final_render_artifact_id: str
    final_render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("tags", mode="before")
    @classmethod
    def unique_tags(cls, values: object) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)) or not all(isinstance(v, str) for v in values):
            raise ValueError("tags must be strings")
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = " ".join(value.split())
            if clean and clean.casefold() not in seen:
                seen.add(clean.casefold())
                result.append(clean)
        return tuple(result)

    @model_validator(mode="after")
    def tag_budget(self) -> "EpisodePublicationMetadata":
        if any(len(tag) > 80 for tag in self.tags) or len(",".join(self.tags)) > 500:
            raise ValueError("metadata tags exceed aggregate 500-character budget")
        return self
