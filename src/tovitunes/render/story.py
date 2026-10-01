"""Semantic visual events between composition and the existing motion grammar."""

from typing import Literal

from pydantic import Field, model_validator

from tovitunes.domain.storyboard import ProductionModel
from tovitunes.render.composition import OutroPhase, PropDefinition, SceneComposition

STORY_VERSION = "visual_story_v1"
StoryAction = Literal[
    "introduce",
    "reveal",
    "drop_and_settle",
    "roll_through",
    "present",
    "compare",
    "question",
    "performance",
    "celebrate",
    "recap",
    "settle",
]
EnvironmentRole = Literal["meadow_wide", "lesson_garden", "play_path", "celebration_meadow"]


class StoryPhase(ProductionModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class VisualStoryPlan(ProductionModel):
    version: str = STORY_VERSION
    scene_id: str
    story_action: StoryAction
    environment_plate_role: EnvironmentRole
    setup_phase: StoryPhase
    action_phase: StoryPhase
    reaction_phase: StoryPhase
    resolution_phase: StoryPhase
    camera_intent: str
    character_intent: str
    primary_prop_intent: str | None = None
    secondary_prop_intents: tuple[str, ...] = ()
    keyword_reactions: bool = True
    continuity_source: str | None = None
    primary_focus: str

    @model_validator(mode="after")
    def contiguous(self) -> "VisualStoryPlan":
        phases = (self.setup_phase, self.action_phase, self.reaction_phase, self.resolution_phase)
        if phases[0].start != 0 or any(
            abs(a.end - b.start) > 1e-6 for a, b in zip(phases, phases[1:])
        ):
            raise ValueError("story phases must be contiguous from scene start")
        return self


class EpisodeVisualStoryPlan(ProductionModel):
    version: str = STORY_VERSION
    storyboard_artifact_id: str
    environment_set_artifact_id: str
    scenes: tuple[VisualStoryPlan, ...]


def _phases(duration: float, word_cue: float | None, outro: bool) -> tuple[StoryPhase, ...]:
    cuts: tuple[float, ...]
    if outro and duration > 4:
        celebrate = min(2.9, duration * 0.34)
        recap = duration - min(1.8, duration * 0.22)
        cuts = (0.0, celebrate * 0.30, celebrate, recap, duration)
    else:
        cue = max(duration * 0.25, min(duration * 0.68, word_cue or duration * 0.54))
        cuts = (
            0.0,
            min(duration * 0.18, cue * 0.48),
            cue,
            max(cue + duration * 0.12, duration * 0.88),
            duration,
        )
    # Short scenes still get four valid phases within the admitted interval.
    cuts = tuple(min(duration, max(0.0, x)) for x in cuts)
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        cuts = (0.0, duration * 0.18, duration * 0.64, duration * 0.90, duration)
    return tuple(StoryPhase(start=a, end=b) for a, b in zip(cuts, cuts[1:]))


def plan_story(
    composition: SceneComposition,
    duration: float,
    definitions: dict[str, PropDefinition],
    introduced: set[str],
    *,
    word_cue: float | None = None,
    scene_kind: str = "lyric",
) -> VisualStoryPlan:
    if duration <= 0:
        raise ValueError("story scene requires positive duration")
    props = composition.props
    primary = props[0] if props else None
    novel = primary is not None and primary.type not in introduced
    if scene_kind == "intro":
        action: StoryAction = "introduce"
    elif scene_kind == "outro":
        action = "celebrate"
    elif composition.micro_scene:
        action = "celebrate"
    elif composition.resolved_action == "sing" and len(props) > 1:
        action = "performance"
    elif composition.resolved_action == "question" and len(props) >= 2:
        action = "compare"
    elif primary and novel and definitions[primary.type].supports_drop:
        action = "drop_and_settle"
    elif primary and novel and definitions[primary.type].supports_roll:
        action = "roll_through"
    elif primary and novel and definitions[primary.type].supports_float:
        action = "reveal"
    elif primary:
        action = "present"
    else:
        action = "settle"
    role: EnvironmentRole = (
        "celebration_meadow"
        if action in {"performance", "celebrate"} or scene_kind == "outro"
        else "play_path"
        if action == "roll_through"
        else "meadow_wide"
        if action == "introduce"
        else "lesson_garden"
    )
    phases = _phases(duration, word_cue, scene_kind == "outro")
    continuity = (
        composition.inherited_from_scene_id
        if composition.visual_state_persistence == "inherit"
        else None
    )
    return VisualStoryPlan(
        scene_id=composition.scene_id,
        story_action=action,
        environment_plate_role=role,
        setup_phase=phases[0],
        action_phase=phases[1],
        reaction_phase=phases[2],
        resolution_phase=phases[3],
        camera_intent={
            "drop_and_settle": "focus_fall",
            "roll_through": "follow_roll",
            "compare": "include_both",
            "performance": "performance_push",
        }.get(action, "gentle_focus"),
        character_intent=composition.resolved_action,
        primary_prop_intent=primary.type if primary else None,
        secondary_prop_intents=tuple(p.type for p in props[1:]),
        continuity_source=continuity,
        primary_focus=primary.type if primary else "tovi",
    )


def stage_composition(composition: SceneComposition, story: VisualStoryPlan) -> SceneComposition:
    """Arrange props spatially for comparison and performance without changing curriculum."""
    composition = composition.model_copy(update={"ground_plane_y": 0.86, "character_height": 0.48})
    if story.story_action == "drop_and_settle" and len(composition.props) == 1:
        prop = composition.props[0].model_copy(
            update={"width": min(composition.props[0].width, 0.25)}
        )
        return composition.model_copy(update={"props": (prop,)})
    if story.story_action == "roll_through" and len(composition.props) == 1:
        prop = composition.props[0].model_copy(
            update={
                "center": (0.85, 0.86),
                "width": 0.20,
                "slot": "lower_right",
            }
        )
        return composition.model_copy(update={"props": (prop,)})
    if story.story_action == "compare" and len(composition.props) >= 2:
        props = tuple(
            p.model_copy(
                update={
                    "slot": "middle_left" if i == 0 else "middle_right",
                    "center": (0.15 if i == 0 else 0.85, 0.28),
                    "width": 0.20,
                    "placement_mode": "display",
                    "motion": "static",
                }
            )
            for i, p in enumerate(composition.props)
        )
        return composition.model_copy(
            update={
                "props": props,
                "character_slot": "lower_center",
                "character_facing": "none",
                "composition_style": "spatial_comparison",
                "character_height": 0.48,
            }
        )
    if story.story_action == "performance" and len(composition.props) >= 3:
        points = ((0.50, 0.27), (0.115, 0.62), (0.885, 0.62))
        props = tuple(
            p.model_copy(
                update={
                    "center": points[i],
                    "width": (0.26, 0.12, 0.12)[i],
                    "placement_mode": "display",
                    "motion": "static",
                }
            )
            for i, p in enumerate(composition.props[:3])
        )
        return composition.model_copy(
            update={
                "props": props,
                "character_slot": "lower_center",
                "composition_style": "performance_depth_arc",
                "character_height": 0.48,
            }
        )
    if composition.outro_phases:
        celebrate = story.action_phase.end
        recap = story.reaction_phase.end
        phases = (
            OutroPhase(name="celebrate", start=0, end=celebrate),
            OutroPhase(name="recap", start=celebrate, end=recap),
            OutroPhase(name="settle", start=recap, end=story.resolution_phase.end),
        )
        return composition.model_copy(update={"outro_phases": phases})
    return composition


def occupancy(
    character_bbox: tuple[float, float, float, float],
    prop_bboxes: tuple[tuple[float, float, float, float], ...],
    action_bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, object]:
    boxes = (character_bbox, *prop_bboxes, *((action_bbox,) if action_bbox else ()))
    top = min(b[1] for b in boxes)
    bottom = max(b[3] for b in boxes)
    left = min(b[0] for b in boxes)
    right = max(b[2] for b in boxes)
    return {
        "active_visual_bbox": (left, top, right, bottom),
        "vertical_occupancy": round(bottom - top, 4),
        "dead_space_warning": "excessive_dead_visual_space" if top > 0.39 else None,
    }
