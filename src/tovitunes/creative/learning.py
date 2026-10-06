"""Versioned broad learning policy and immutable selected educational facts."""

import re
from datetime import datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tovitunes.creative.provider import canonical
from tovitunes.creative.similarity import normalize_topic


class LearningPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    version: Literal["open-learning-v1"] = "open-learning-v1"
    language: Literal["en"] = "en"
    ages: tuple[int, int] = (3, 6)
    duration_seconds: tuple[int, int] = (30, 45)
    mascot: Literal["tovi"] = "tovi"
    max_vocabulary: int = 4
    constraints: tuple[str, ...] = (
        "One clear primary objective demonstrable through visible objects or actions.",
        "Simple English, concrete early vocabulary, music-friendly repetition.",
        "Safe familiar preschool activities; no dangerous imitation or adult themes.",
        "No political content, purchasing manipulation, copyrighted character imitation.",
        "No celebrity or named-artist music imitation; Tovi is the only cast member.",
        "Feasible with Qwen illustrations, ACE-Step music and Renderer V4.",
    )
    example_domains: tuple[str, ...] = (
        "colors",
        "numbers",
        "shapes",
        "opposites",
        "animals",
        "body parts",
        "actions",
        "nature",
        "everyday objects",
        "emotions",
        "early vocabulary",
    )

    @property
    def revision_id(self) -> str:
        return (
            "learning-policy:"
            + sha256(canonical(self.model_dump(mode="json")).encode()).hexdigest()
        )


LEARNING_POLICY = LearningPolicy()


class TopicCandidate(BaseModel):
    """Model-owned editorial fields only; no database or artifact identities."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, allow_inf_nan=False
    )

    subject: str = Field(min_length=3, max_length=120)
    domain: str = Field(min_length=2, max_length=80)
    objective: str = Field(min_length=10, max_length=240)
    target_vocabulary: tuple[str, ...] = Field(min_length=1, max_length=4)
    premise: str = Field(min_length=3, max_length=400)
    hook: str = Field(min_length=3, max_length=200)
    setting: str = Field(min_length=3, max_length=120)
    example_objects: tuple[str, ...] = Field(min_length=1, max_length=4)
    song_angle: str = Field(min_length=3, max_length=300)
    working_title: str = Field(min_length=3, max_length=100)
    score: float = Field(ge=0, le=10)
    reason: str = Field(min_length=3, max_length=300)

    @field_validator("target_vocabulary", "example_objects")
    @classmethod
    def simple_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[a-z]+(?:[ -][a-z]+){0,2}", v) for v in values):
            raise ValueError("terms require one to three simple lowercase English words")
        if len({normalize_topic(v) for v in values}) != len(values):
            raise ValueError("terms must be distinct")
        return values


class TopicPool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[TopicCandidate, ...] = Field(min_length=1, max_length=30)


class LearningBrief(TopicCandidate):
    schema_version: Literal[1] = 1
    brief_id: str = Field(pattern=r"^learning:[0-9a-f]{64}$")
    idea_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_subject: str = Field(min_length=1)
    learning_policy_revision_id: str = Field(pattern=r"^learning-policy:[0-9a-f]{64}$")
    language: Literal["en"] = "en"
    target_duration_seconds: int = Field(ge=30, le=45)
    created_at: datetime


def subject_slug(subject: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_topic(subject)).strip("-") or "learning"
    if len(slug) > 48:
        slug = slug[:35].rstrip("-") + "-" + sha256(subject.encode()).hexdigest()[:12]
    return slug
