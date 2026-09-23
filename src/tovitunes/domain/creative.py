"""Typed pre-music creative contracts; no timed storyboard is inferred here."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EpisodeConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    premise: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    cast: tuple[str, ...] = Field(min_length=1)
    setting: str = Field(min_length=1)


class StoryBeat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    beat_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    purpose: Literal["hook", "teach", "practice", "payoff"]
    action: str = Field(min_length=1)
    teaching_vocabulary: tuple[str, ...] = ()


class EpisodeSpec(BaseModel):
    """Reviewed objective, premise and beats before lyrics or music exist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=2, ge=2, le=2)
    episode_id: str
    concept_id: str
    objective_id: str
    concept: EpisodeConcept
    teaching_vocabulary: tuple[str, ...] = Field(min_length=1)
    story_beats: tuple[StoryBeat, ...] = Field(min_length=1)
    desired_structure: str = Field(min_length=1)

    @model_validator(mode="after")
    def beats_cover_objective(self) -> "EpisodeSpec":
        ids = [beat.beat_id for beat in self.story_beats]
        if len(ids) != len(set(ids)):
            raise ValueError("story beat IDs must be unique")
        taught = {word.casefold() for beat in self.story_beats for word in beat.teaching_vocabulary}
        if not {word.casefold() for word in self.teaching_vocabulary}.issubset(taught):
            raise ValueError("story beats must cover target vocabulary")
        return self


class LyricLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: Literal["intro", "verse", "chorus", "bridge", "outro"]
    text: str = Field(min_length=1)


class LyricsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    episode_id: str
    objective_id: str
    episode_spec_artifact_id: str
    target_vocabulary: tuple[str, ...] = Field(min_length=1)
    lines: tuple[LyricLine, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def lyrics_include_target_words(self) -> "LyricsSpec":
        text = " ".join(line.text for line in self.lines)
        missing = [
            word
            for word in self.target_vocabulary
            if not re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE)
        ]
        if missing:
            raise ValueError(f"lyrics omit target vocabulary: {missing}")
        return self


class MusicSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["intro", "verse", "chorus", "bridge", "outro"]
    target_seconds: float = Field(gt=0)


class MusicSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    episode_id: str
    objective_id: str
    lyrics_artifact_id: str
    target_vocabulary: tuple[str, ...] = Field(min_length=1)
    target_duration_seconds: int = Field(gt=0)
    mood: str = Field(min_length=1)
    instrumentation: tuple[str, ...] = Field(min_length=1)
    vocal_direction: str = Field(min_length=1)
    tempo_bpm: int | None = Field(default=None, ge=50, le=220)
    sections: tuple[MusicSection, ...] = Field(min_length=1)
    no_artist_imitation: bool = True

    @model_validator(mode="after")
    def safe_duration_and_style(self) -> "MusicSpec":
        if not self.no_artist_imitation:
            raise ValueError("named-artist imitation is not allowed")
        if (
            abs(
                sum(section.target_seconds for section in self.sections)
                - self.target_duration_seconds
            )
            > 2
        ):
            raise ValueError("music sections must cover target duration")
        return self

