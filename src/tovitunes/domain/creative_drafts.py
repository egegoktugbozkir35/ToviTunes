"""New creative contracts. Historical domain.creative schemas remain unchanged."""

from typing import Annotated

from pydantic import ConfigDict, Field, model_validator

from tovitunes.domain.creative import EpisodeSpec, LyricLine, LyricsSpec, MusicSpec, StoryBeat
from tovitunes.domain.storyboard import ProductionModel


class CreativeDraftModel(ProductionModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class EpisodeIdeaDraft(CreativeDraftModel):
    premise: str = Field(min_length=1, max_length=1000)
    hook: str = Field(min_length=1, max_length=500)
    setting: str = Field(default="a welcoming preschool world", min_length=1, max_length=500)
    progression: tuple[Annotated[str, Field(min_length=1, max_length=1000)], ...] = Field(
        min_length=1, max_length=12
    )


class LyricsDraft(CreativeDraftModel):
    lines: tuple[LyricLine, ...] = Field(min_length=1, max_length=24)


class MusicDraft(CreativeDraftModel):
    mood: str = Field(min_length=1, max_length=500)
    instrumentation: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = Field(
        min_length=1, max_length=12
    )
    vocal_direction: str = Field(min_length=1, max_length=500)
    tempo_bpm: int | None = Field(default=None, ge=60, le=180)


class EpisodeSpecV3(EpisodeSpec):
    schema_version: int = Field(default=3, ge=3, le=3)
    story_beats: tuple[StoryBeat, ...] = ()
    creative_draft: EpisodeIdeaDraft

    @model_validator(mode="after")
    def beats_cover_objective(self) -> "EpisodeSpecV3":
        # V3 retains learning facts as provenance, with no frozen beat taxonomy.
        if self.story_beats or self.concept.cast != ("tovi",):
            raise ValueError("V3 uses creative progression and the approved Tovi cast")
        if (
            self.concept.premise != self.creative_draft.premise
            or self.concept.hook != self.creative_draft.hook
            or self.concept.setting != self.creative_draft.setting
            or self.desired_structure != "\n".join(self.creative_draft.progression)
        ):
            raise ValueError("compiled episode differs from creative draft")
        return self


class LyricsSpecV2(LyricsSpec):
    schema_version: int = Field(default=2, ge=2, le=2)

    @model_validator(mode="after")
    def lyrics_include_target_words(self) -> "LyricsSpecV2":
        # Vocabulary is topic context, never an exact word coverage requirement in V2.
        return self


class MusicSpecV2(MusicSpec):
    schema_version: int = Field(default=2, ge=2, le=2)
    tempo_bpm: int | None = Field(default=None, ge=60, le=180)


def parse_episode_spec(value: object) -> EpisodeSpec:
    if isinstance(value, dict) and value.get("schema_version") == 3:
        return EpisodeSpecV3.model_validate(value)
    return EpisodeSpec.model_validate(value)


def parse_lyrics(value: object) -> LyricsSpec:
    if isinstance(value, dict) and value.get("schema_version") == 2:
        return LyricsSpecV2.model_validate(value)
    return LyricsSpec.model_validate(value)


def parse_music_spec(value: object) -> MusicSpec:
    if isinstance(value, dict) and value.get("schema_version") == 2:
        return MusicSpecV2.model_validate(value)
    return MusicSpec.model_validate(value)
