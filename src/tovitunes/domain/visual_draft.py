"""Small model proposal and deterministic V2 visual compiler; no lyric matching."""

import re
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from tovitunes.creative.validation import contains, safe_creative_text
from tovitunes.domain.creative_drafts import CreativeDraftModel
from tovitunes.domain.episode import Episode
from tovitunes.domain.storyboard import Action, ProductionModel
from tovitunes.domain.visual_plan import (
    ENVIRONMENT_ROLES,
    EnvironmentRequirement,
    EpisodeVisualPlan,
    TeachingCue,
    VisualRequirement,
)

VISUAL_PROMPT_V2: Literal["visual_creative_v2"] = "visual_creative_v2"
COMPILER_VERSION: Literal["visual_compiler_v2"] = "visual_compiler_v2"
EnvironmentRoleV2 = Literal["meadow_wide", "lesson_garden", "play_path", "celebration_meadow"]
ROLES: tuple[EnvironmentRoleV2, ...] = (
    "meadow_wide",
    "lesson_garden",
    "play_path",
    "celebration_meadow",
)


class CreativeObject(CreativeDraftModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    teaching_cue: SkipJsonSchema[TeachingCue | None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class CreativeScene(CreativeDraftModel):
    description: str = Field(min_length=1, max_length=1500)
    tovi_action: str = Field(default="idle", min_length=1, max_length=500)
    visual_focus: str | None = Field(default=None, min_length=1, max_length=500)
    objects: tuple[CreativeObject, ...] = Field(default=(), max_length=3)
    setting: str = Field(default="a friendly meadow", min_length=1, max_length=500)
    mood: str | None = Field(default=None, max_length=500)
    camera: str | None = Field(default=None, max_length=500)
    transition: str | None = Field(default=None, max_length=500)

    @field_validator("objects", mode="before")
    @classmethod
    def object_strings(cls, value: object) -> object:
        if isinstance(value, (tuple, list)):
            return [
                {"name": item, "description": item} if isinstance(item, str) else item
                for item in value
            ]
        return value


class CreativeVisualDraft(CreativeDraftModel):
    scenes: tuple[CreativeScene, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def safety(self) -> "CreativeVisualDraft":
        for scene in self.scenes:
            safe_creative_text(scene.model_dump_json())
            for obj in scene.objects:
                if any(
                    contains(obj.name + " " + (obj.description or ""), x)
                    for x in ("tovi", "person")
                ):
                    raise ValueError("character imagery must come from the approved Tovi pack")
        return self


class VisualSceneV2(ProductionModel):
    scene_id: str = Field(pattern=r"^scene_[0-9]{3}$")
    description: str = Field(min_length=1, max_length=1500)
    visual_focus: str = Field(min_length=1, max_length=1500)
    required_assets: tuple[str, ...] = Field(max_length=3)
    tovi_action: Action
    character_id: Literal["tovi"] = "tovi"
    environment_role: Literal["meadow_wide", "lesson_garden", "play_path", "celebration_meadow"]


class EpisodeVisualPlanV2(ProductionModel):
    schema_version: Literal[2] = 2
    prompt_version: Literal["visual_creative_v2"] = VISUAL_PROMPT_V2
    compiler_version: Literal["visual_compiler_v2"] = COMPILER_VERSION
    episode_id: str
    concept_id: str
    objective_id: str
    creative_artifact_ids: tuple[str, str, str]
    creative_draft_artifact_id: str
    creative_draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal: CreativeVisualDraft
    required_assets: tuple[VisualRequirement, ...]
    scenes: tuple[VisualSceneV2, ...] = Field(min_length=1, max_length=64)
    environments: tuple[EnvironmentRequirement, ...]
    world_contract: Literal["preschool-world-v1"] = "preschool-world-v1"
    reuse_shared_environment: bool = False
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def references(self) -> "EpisodeVisualPlanV2":
        keys = {a.asset_key for a in self.required_assets}
        if (
            len(keys) != len(self.required_assets)
            or tuple(e.role for e in self.environments) != ENVIRONMENT_ROLES
            or tuple(s.scene_id for s in self.scenes)
            != tuple(f"scene_{i:03d}" for i in range(1, len(self.scenes) + 1))
            or {key for s in self.scenes for key in s.required_assets} != keys
            or any(len(set(s.required_assets)) != len(s.required_assets) for s in self.scenes)
        ):
            raise ValueError("compiled visual references are invalid")
        return self


def action_for(text: str) -> tuple[Action, str | None]:
    normalized = re.sub(r"[^a-z ]", " ", text.casefold()).strip()
    if normalized in {"enter", "idle", "point", "present", "question", "sing", "celebrate"}:
        # Typed through the fixed table, never an unchecked renderer operation.
        return ACTIONS[normalized], None
    for word, action in ACTIONS.items():
        if re.search(rf"\b{word}\w*\b", normalized):
            return action, None
    return "idle", "Action represented by approved idle pose and gentle motion: " + text


ACTIONS: dict[str, Action] = {
    "enter": "enter",
    "idle": "idle",
    "point": "point",
    "present": "present",
    "question": "question",
    "sing": "sing",
    "celebrate": "celebrate",
    "wave": "enter",
    "dance": "celebrate",
    "hop": "celebrate",
    "clap": "celebrate",
    "show": "present",
    "hold": "present",
    "look": "question",
    "wonder": "question",
    "walk": "enter",
    "discover": "present",
    "explore": "idle",
}


def compile_visual_draft(
    proposal: CreativeVisualDraft,
    episode: Episode,
    creative_ids: tuple[str, str, str],
    draft_id: str,
    draft_sha: str,
) -> EpisodeVisualPlanV2:
    assets: dict[str, VisualRequirement] = {}
    settings = tuple(dict.fromkeys(s.setting.strip() for s in proposal.scenes))
    roles = {setting: ROLES[i % 4] for i, setting in enumerate(settings)}
    diagnostics = [
        "Topic and song are creative context; thematic relevance requires editorial review.",
        "Camera and transition suggestions use the renderer's gentle story camera and cuts.",
    ]
    if len(settings) > 4:
        diagnostics.append(
            "Settings beyond four share the first assigned plate for their role in proposal order."
        )
    scenes = []
    for index, scene in enumerate(proposal.scenes, 1):
        keys = []
        for obj in scene.objects:
            # Identity includes all requested art, avoiding name-only accidental asset reuse.
            key = "prop_" + sha256(obj.model_dump_json().encode()).hexdigest()[:16]
            assets[key] = VisualRequirement(
                asset_key=key,
                kind="scene_element",
                display_name=obj.name,
                semantic_label=obj.name[:80],
                description=obj.description or obj.name,
                educational_role="supporting",
                teaching_cue=obj.teaching_cue,
                grounded=obj.teaching_cue is None,
            )
            if key not in keys:
                keys.append(key)
        action, warning = action_for(scene.tovi_action)
        if warning:
            diagnostics.append(f"scene_{index:03d}: {warning}")
        scenes.append(
            VisualSceneV2(
                scene_id=f"scene_{index:03d}",
                description=scene.description,
                visual_focus=scene.visual_focus or scene.description,
                required_assets=tuple(keys),
                tovi_action=action,
                environment_role=roles[scene.setting.strip()],
            )
        )
    environments = tuple(
        EnvironmentRequirement(
            role=role,
            description=next((s for s in settings if roles[s] == role), settings[0]),
        )
        for role in ROLES
    )
    return EpisodeVisualPlanV2(
        episode_id=episode.episode_id,
        concept_id=episode.concept_id,
        objective_id=episode.objective_id,
        creative_artifact_ids=creative_ids,
        creative_draft_artifact_id=draft_id,
        creative_draft_sha256=draft_sha,
        proposal=proposal,
        required_assets=tuple(assets.values()),
        scenes=tuple(scenes),
        environments=environments,
        diagnostics=tuple(diagnostics),
    )


def parse_visual_plan(value: object) -> EpisodeVisualPlan | EpisodeVisualPlanV2:
    if isinstance(value, dict) and value.get("schema_version") == 2:
        return EpisodeVisualPlanV2.model_validate(value)
    return EpisodeVisualPlan.model_validate(value)


def validate_compiled_visual(
    plan: EpisodeVisualPlanV2, episode: Episode, creative_ids: tuple[str, str, str]
) -> None:
    expected = compile_visual_draft(
        plan.proposal,
        episode,
        creative_ids,
        plan.creative_draft_artifact_id,
        plan.creative_draft_sha256,
    )
    if plan != expected:
        raise ValueError("visual plan differs from deterministic compilation or episode identity")
