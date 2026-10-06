"""Additive, episode-scoped creative visuals; asset keys are data, never a registry."""

from typing import Literal

from pydantic import Field, model_validator

from tovitunes.creative.validation import contains, safe_text
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec
from tovitunes.domain.episode import Episode
from tovitunes.domain.storyboard import Action, ProductionModel

COLORS_V1 = {
    "red": "#E53935",
    "blue": "#246BDF",
    "yellow": "#FFD43B",
    "green": "#39A85A",
    "orange": "#FF932B",
    "purple": "#8B55CC",
    "pink": "#F58EB7",
    "black": "#202124",
    "white": "#FFFFFF",
}
ENVIRONMENT_ROLES = ("meadow_wide", "lesson_garden", "play_path", "celebration_meadow")


class VisualRequirement(ProductionModel):
    asset_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["lesson_object", "decoration", "scene_element", "color_swatch"]
    display_name: str = Field(min_length=1, max_length=100)
    semantic_label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    educational_role: Literal["teaching", "supporting", "decorative"]
    educational_claims: tuple[str, ...] = ()
    target_color: str | None = None
    allow_face: bool = False
    grounded: bool = True
    motion: Literal["static", "roll", "float"] = "static"


class VisualLine(ProductionModel):
    lyric_index: int = Field(ge=0)
    lyric_text: str = Field(min_length=1)
    required_assets: tuple[str, ...] = Field(max_length=3)
    visual_focus: str = Field(min_length=1, max_length=500)
    tovi_action: Action
    character_id: Literal["tovi"] = "tovi"
    environment_role: str


class EnvironmentRequirement(ProductionModel):
    role: Literal["meadow_wide", "lesson_garden", "play_path", "celebration_meadow"]
    description: str = Field(min_length=1, max_length=1000)


class EpisodeVisualPlan(ProductionModel):
    schema_version: Literal[1] = 1
    prompt_version: Literal["episode_visual_plan_v1"] = "episode_visual_plan_v1"
    episode_id: str
    concept_id: str
    objective_id: str
    episode_spec_artifact_id: str
    lyrics_artifact_id: str
    music_spec_artifact_id: str
    required_assets: tuple[VisualRequirement, ...]
    scenes: tuple[VisualLine, ...] = Field(min_length=1)
    environments: tuple[EnvironmentRequirement, ...]
    world_contract: Literal["preschool-world-v1"] = "preschool-world-v1"
    reuse_shared_environment: bool = True
    intro_environment_role: str = "meadow_wide"
    outro_environment_role: str = "celebration_meadow"

    @model_validator(mode="after")
    def references(self) -> "EpisodeVisualPlan":
        keys = [asset.asset_key for asset in self.required_assets]
        roles = tuple(env.role for env in self.environments)
        if len(set(keys)) != len(keys) or roles != ENVIRONMENT_ROLES:
            raise ValueError("asset keys must be unique and environment roles complete/ordered")
        if tuple(scene.lyric_index for scene in self.scenes) != tuple(range(len(self.scenes))):
            raise ValueError("visual scenes must preserve every exact lyric index in order")
        for scene in self.scenes:
            if (
                not set(scene.required_assets) <= set(keys)
                or len(set(scene.required_assets)) != len(scene.required_assets)
                or scene.environment_role not in roles
            ):
                raise ValueError("visual scene references missing or duplicate assets/environment")
        if self.intro_environment_role not in roles or self.outro_environment_role not in roles:
            raise ValueError("intro/outro environment must exist")
        used = {key for scene in self.scenes for key in scene.required_assets}
        if used != set(keys):
            raise ValueError("every required illustration must be used by a lyric scene")
        return self


def validate_visual_plan(
    plan: EpisodeVisualPlan,
    episode: Episode,
    spec: EpisodeSpec,
    lyrics: LyricsSpec,
    creative_ids: tuple[str, str, str],
) -> None:
    if (
        (plan.episode_id, plan.concept_id, plan.objective_id)
        != (episode.episode_id, episode.concept_id, episode.objective_id)
        or (plan.episode_spec_artifact_id, plan.lyrics_artifact_id, plan.music_spec_artifact_id)
        != creative_ids
        or tuple(scene.lyric_text for scene in plan.scenes)
        != tuple(line.text for line in lyrics.lines)
    ):
        raise ValueError(
            "visual plan differs from pinned episode, creative artifacts or exact lyrics"
        )
    corpus = " ".join([spec.model_dump_json(), *(line.text for line in lyrics.lines)])
    claims = {episode.objective, *episode.target_vocabulary}
    if episode.objective_id.startswith("colors.") and not any(
        asset.kind == "color_swatch" and asset.target_color == episode.concept_id
        for asset in plan.required_assets
    ):
        raise ValueError("Colors lessons require the deterministic target swatch")
    for asset in plan.required_assets:
        safe_text(asset.display_name + " " + asset.description + " " + asset.semantic_label)
        if contains(asset.semantic_label, "tovi"):
            raise ValueError(
                "Tovi must resolve from the approved character pack, not generated imagery"
            )
        if not set(asset.educational_claims) <= claims:
            raise ValueError("visual asset invents an educational claim outside the curriculum")
        if asset.kind == "color_swatch":
            if asset.target_color not in {*COLORS_V1, "rainbow"}:
                raise ValueError("swatch requires a committed Colors V1 palette entry")
        elif not contains(corpus, asset.semantic_label):
            raise ValueError("visual entity is unsupported by selected lyrics/story treatment")
        if asset.allow_face and not any(
            contains(corpus, word) for word in ("face", "smile", "smiling", "happy")
        ):
            raise ValueError("face treatment requires explicit selected creative evidence")
        if episode.objective_id.startswith("colors.") and asset.target_color is not None:
            if asset.target_color != episode.concept_id:
                raise ValueError("teaching color differs from the episode concept")
        for scene in plan.scenes:
            if asset.asset_key in scene.required_assets and asset.kind == "lesson_object":
                # Story-supported illustrations may be present in scenes without naming them.
                if not contains(corpus, asset.semantic_label):
                    raise ValueError("lesson object has no creative evidence")
    for scene in plan.scenes:
        safe_text(scene.visual_focus)
    for env in plan.environments:
        safe_text(env.description)
