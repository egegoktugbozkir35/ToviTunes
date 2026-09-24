"""Typed benchmark inputs, canonical prompts, and human scorecards."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tovitunes.artifacts.character_lock import load_lock

AXES = (
    "character_identity",
    "teaching_accuracy",
    "composition",
    "reference_fidelity",
    "image_quality",
    "production_fit",
)
REFERENCE_ROLES = ("view/front", "view/three_quarter", "view/profile")


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    brief: str
    teaching_check: str


class BenchmarkDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    prompt_version: str
    common_brief: str
    minimum_requests_per_provider_case: int = Field(ge=2)
    cases: tuple[BenchmarkCase, ...]

    @model_validator(mode="after")
    def unique_cases(self) -> "BenchmarkDefinition":
        if len({item.id for item in self.cases}) != len(self.cases):
            raise ValueError("benchmark case IDs must be unique")
        return self


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    score_min: Literal[0]
    score_max: Literal[4]
    weights: dict[str, int]
    usable_minimums: dict[str, int]
    hard_gates: tuple[str, ...]
    release_gate: str

    @model_validator(mode="after")
    def valid_axes(self) -> "Rubric":
        if tuple(self.weights) != AXES or sum(self.weights.values()) != 100:
            raise ValueError("rubric must contain the six ordered axes totaling 100")
        if set(self.usable_minimums) != {"character_identity", "teaching_accuracy"}:
            raise ValueError("rubric usable minimums differ from the protocol")
        return self


class ReferenceImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    artifact_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str


class CanonicalImageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_version: str
    prompt_version: str
    case_id: str
    attempt: int = Field(gt=0)
    scene_brief: str
    teaching_check: str
    common_brief: str
    pack_revision_id: str
    brand_revision_id: str
    references: tuple[ReferenceImage, ...]
    palette: dict[str, str]
    identity_rules: tuple[str, ...]
    forbidden_changes: tuple[str, ...]
    aspect_ratio: Literal["9:16"] = "9:16"
    negative_constraints: tuple[str, ...] = (
        "No text, captions, logos, watermarks, or signatures.",
        "No named artist, franchise, or copyrighted-character imitation.",
        "No extra Tovi anatomy, human hands, human arms, or permanent clothing.",
        "No web, image-search, or external grounding inputs.",
    )

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def prompt(self) -> str:
        palette = ", ".join(f"{key}={self.palette[key]}" for key in sorted(self.palette))
        sections = (
            ("BENCHMARK", self.benchmark_version),
            ("PROMPT VERSION", self.prompt_version),
            ("COMMON BRIEF", self.common_brief),
            ("SCENE", self.scene_brief),
            ("TEACHING CHECK", self.teaching_check),
            ("COMPOSITION", "One 9:16 portrait image for a preschool educational Short."),
            (
                "REFERENCE INSTRUCTIONS",
                "Treat all supplied views as the same canonical Tovi character. "
                "Preserve identity; use the view that best supports the scene.",
            ),
            ("PALETTE", palette),
            ("IDENTITY RULES", " | ".join(self.identity_rules)),
            ("FORBIDDEN CHANGES", " | ".join(self.forbidden_changes)),
            ("NEGATIVE CONSTRAINTS", " | ".join(self.negative_constraints)),
        )
        return "\n".join(f"{heading}: {text}" for heading, text in sections)


class Scores(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    character_identity: int = Field(ge=0, le=4)
    teaching_accuracy: int = Field(ge=0, le=4)
    composition: int = Field(ge=0, le=4)
    reference_fidelity: int = Field(ge=0, le=4)
    image_quality: int = Field(ge=0, le=4)
    production_fit: int = Field(ge=0, le=4)


class Scorecard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    blind_id: str
    reviewer: str
    review_role: Literal["initial", "adjudication"] = "initial"
    scores: Scores
    notes: str | None = None
    evidence_note: str | None = None
    hard_failure_reasons: tuple[str, ...] = ()
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def evidence_for_core_errors(self) -> "Scorecard":
        if (
            self.scores.character_identity < 3 or self.scores.teaching_accuracy < 3
        ) and not self.evidence_note:
            raise ValueError("identity or teaching errors require an evidence note")
        return self


class ResolvedScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: dict[str, float]
    weighted_score: float
    usable: bool
    hard_failure_reasons: tuple[str, ...]
    needs_adjudication: bool


def load_benchmark(path: Path) -> BenchmarkDefinition:
    return BenchmarkDefinition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_rubric(path: Path) -> Rubric:
    return Rubric.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_scorecard(path: Path) -> Scorecard:
    return Scorecard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_references(lock_path: Path) -> tuple[str, str, tuple[ReferenceImage, ...]]:
    lock = load_lock(lock_path)
    artifacts = {item.artifact_id: item for item in lock.artifacts}
    result = []
    for role in REFERENCE_ROLES:
        artifact_id = lock.active_role_artifact_ids[role]
        item = artifacts[artifact_id]
        if not item.approval_decisions or item.approval_decisions[-1].status != "approved":
            raise ValueError(f"canonical benchmark reference is not approved: {role}")
        if (
            not item.rights_decisions
            or item.rights_decisions[-1].status != "commercial_use_confirmed"
        ):
            raise ValueError(f"canonical benchmark reference rights are unresolved: {role}")
        result.append(
            ReferenceImage(
                role=role,
                artifact_id=artifact_id,
                sha256=item.sha256,
                mime_type=item.mime_type,
            )
        )
    return lock.pack_revision_id, lock.brand_revision_id, tuple(result)


def build_spec(
    definition: BenchmarkDefinition,
    case: BenchmarkCase,
    attempt: int,
    pack_path: Path,
    lock_path: Path,
) -> CanonicalImageSpec:
    pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_revision_id, brand_revision_id, references = load_references(lock_path)
    if pack.get("readiness") != "approved":
        raise ValueError("visual benchmark requires an approved character pack")
    expected_ids = tuple(pack["asset_artifact_ids"][role] for role in REFERENCE_ROLES)
    if expected_ids != tuple(item.artifact_id for item in references):
        raise ValueError("pack reference IDs differ from the canonical artifact lock")
    visual_rules = pack["visual_rules"]
    return CanonicalImageSpec(
        benchmark_version=f"visual-benchmark-schema-{definition.schema_version}",
        prompt_version=definition.prompt_version,
        case_id=case.id,
        attempt=attempt,
        scene_brief=case.brief,
        teaching_check=case.teaching_check,
        common_brief=definition.common_brief,
        pack_revision_id=pack_revision_id,
        brand_revision_id=brand_revision_id,
        references=references,
        palette=pack["palette"],
        identity_rules=tuple(f"{key}: {value}" for key, value in visual_rules.items()),
        forbidden_changes=tuple(pack["forbidden_changes"]),
    )


def resolve_reviews(reviews: tuple[Scorecard, ...], rubric: Rubric) -> ResolvedScore:
    initial = [item for item in reviews if item.review_role == "initial"]
    adjudication = [item for item in reviews if item.review_role == "adjudication"]
    if len(initial) != 2 or len(adjudication) > 1:
        raise ValueError("exactly two initial reviews and at most one adjudication are required")
    differences = {
        axis: abs(getattr(initial[0].scores, axis) - getattr(initial[1].scores, axis))
        for axis in AXES
    }
    needs_adjudication = any(value >= 2 for value in differences.values())
    if needs_adjudication != bool(adjudication):
        raise ValueError(
            "one adjudication review is required exactly when an axis differs by 2 or more"
        )
    participants = initial + adjudication
    scores = {
        axis: (
            float(median(getattr(item.scores, axis) for item in participants))
            if adjudication
            else sum(getattr(item.scores, axis) for item in participants) / 2
        )
        for axis in AXES
    }
    reasons = tuple(
        sorted({reason for item in participants for reason in item.hard_failure_reasons})
    )
    weighted = sum(rubric.weights[axis] * scores[axis] / 4 for axis in AXES)
    usable = not reasons and all(
        scores[axis] >= minimum for axis, minimum in rubric.usable_minimums.items()
    )
    return ResolvedScore(
        scores=scores,
        weighted_score=weighted,
        usable=usable,
        hard_failure_reasons=reasons,
        needs_adjudication=needs_adjudication,
    )
