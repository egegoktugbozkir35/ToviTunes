"""Canonical song inputs, review rubric and editable timing artifact."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MusicBrief(StrictModel):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    episode_key: str
    objective: str
    examples: tuple[str, ...]
    language: Literal["en"]
    age_min: int = Field(ge=3)
    age_max: int = Field(le=6)
    preferred_duration_seconds: tuple[int, int]
    maximum_duration_seconds: int = Field(le=45)
    target_bpm: int
    bpm_range: tuple[int, int]
    style: str
    arrangement: str
    vocal_direction: str
    sections: tuple[str, ...]
    avoid: tuple[str, ...]
    candidate_count_per_provider: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_song(self) -> "MusicBrief":
        low, high = self.preferred_duration_seconds
        bpm_low, bpm_high = self.bpm_range
        if not (0 < low <= high <= self.maximum_duration_seconds):
            raise ValueError("invalid song duration range")
        if not (60 <= bpm_low <= self.target_bpm <= bpm_high <= 180):
            raise ValueError("invalid movement tempo range")
        if self.age_min > self.age_max or len(set(self.sections)) != len(self.sections):
            raise ValueError("invalid age or song sections")
        return self


class LyricLine(StrictModel):
    section: str
    text: str = Field(min_length=1)


class LyricCandidate(StrictModel):
    schema_version: Literal[1]
    id: str
    brief_id: str
    approval: Literal["pending", "approved", "rejected"] = "pending"
    educational_claims: tuple[str, ...]
    rhyme_notes: str
    syllable_notes: str
    lines: tuple[LyricLine, ...] = Field(min_length=1)

    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class CanonicalMusicSpec(StrictModel):
    brief: MusicBrief
    lyrics: LyricCandidate
    attempt: int = Field(gt=0)

    @model_validator(mode="after")
    def matching_inputs(self) -> "CanonicalMusicSpec":
        if self.lyrics.brief_id != self.brief.id:
            raise ValueError("lyric candidate belongs to another brief")
        return self


def fingerprint(
    spec: CanonicalMusicSpec, provider: str, model: str, translated: dict[str, object]
) -> str:
    payload = {
        "spec": spec.model_dump(mode="json"),
        "provider": provider,
        "model": model,
        "translated": translated,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


AXES = (
    "educational_correctness",
    "lyric_intelligibility",
    "hook_memorability",
    "preschool_appropriateness",
    "beat_timing_usefulness",
    "music_vocal_quality",
    "production_fit",
    "rights_provenance_completeness",
)


class MusicRubric(StrictModel):
    schema_version: Literal[1]
    weights: dict[str, int]
    hard_failures: tuple[str, ...]

    @model_validator(mode="after")
    def valid_weights(self) -> "MusicRubric":
        if tuple(self.weights) != AXES or sum(self.weights.values()) != 100:
            raise ValueError("music rubric must contain ordered axes totaling 100")
        if any(weight <= 0 for weight in self.weights.values()):
            raise ValueError("music rubric weights must be positive")
        return self

    def score(self, scores: dict[str, int]) -> float:
        if set(scores) != set(AXES) or any(not 0 <= value <= 4 for value in scores.values()):
            raise ValueError("invalid music review scores")
        return sum(self.weights[axis] * scores[axis] / 4 for axis in AXES)


class MusicReview(StrictModel):
    blind_id: str
    reviewer: str
    scores: dict[str, int]
    evidence: str
    hard_failures: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_scores(self) -> "MusicReview":
        if set(self.scores) != set(AXES) or any(
            not 0 <= value <= 4 for value in self.scores.values()
        ):
            raise ValueError("all eight rubric axes require integer 0–4 scores")
        if (
            self.hard_failures or any(v < 3 or v == 4 for v in self.scores.values())
        ) and not self.evidence:
            raise ValueError("exceptional, weak or hard-failure scores require evidence")
        return self


class TimeRange(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "TimeRange":
        if self.end <= self.start:
            raise ValueError("time range must increase")
        return self


class TimedText(TimeRange):
    text: str


class TimingAnalysis(StrictModel):
    schema_version: Literal[1] = 1
    version: int = Field(gt=0)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0)
    estimated_bpm: float | None = Field(default=None, gt=0)
    beat_seconds: tuple[float, ...] = ()
    downbeat_seconds: tuple[float, ...] = ()
    sections: tuple[TimedText, ...] = ()
    lyric_lines: tuple[TimedText, ...] = ()
    words: tuple[TimedText, ...] = ()
    phonemes: tuple[TimedText, ...] = ()
    accents: tuple[float, ...] = ()
    intro: TimeRange | None = None
    outro: TimeRange | None = None
    approval: Literal["pending", "approved", "rejected"] = "pending"
    corrected_by: str | None = None

    @model_validator(mode="after")
    def within_audio(self) -> "TimingAnalysis":
        points = (self.beat_seconds, self.downbeat_seconds, self.accents)
        if any(
            any(t < 0 or t > self.duration_seconds for t in group) or tuple(sorted(group)) != group
            for group in points
        ):
            raise ValueError("timing points must be ordered and within audio")
        ranges: tuple[TimeRange, ...] = (
            *self.sections,
            *self.lyric_lines,
            *self.words,
            *self.phonemes,
            *(item for item in (self.intro, self.outro) if item is not None),
        )
        if any(item.end > self.duration_seconds for item in ranges):
            raise ValueError("timing ranges exceed audio")
        return self


def load_brief(path: Path) -> MusicBrief:
    return MusicBrief.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_lyrics(path: Path) -> LyricCandidate:
    return LyricCandidate.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_rubric(path: Path) -> MusicRubric:
    return MusicRubric.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
