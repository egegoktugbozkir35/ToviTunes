"""Semantic visual events between composition and the existing motion grammar."""

import hashlib
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from tovitunes.domain.storyboard import ProductionModel
from tovitunes.domain.visual_plan import VisualRequirement
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
        points = ((0.15, 0.28), (0.85, 0.28), (0.50, 0.15))
        props = tuple(
            p.model_copy(
                update={
                    "slot": ("middle_left", "middle_right", "upper_center")[i],
                    "center": points[i],
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


def display_intent(requirement: VisualRequirement, description: str) -> bool:
    """Read existing semantics; retained V2 plans defaulted every scene element to ground."""
    explicit_display = re.search(
        r"\b(float\w*|illustration cards?|display cards?|floating display|sunny swirl)\b",
        description.casefold(),
    )
    conceptual = re.search(
        r"\b(sun|moon|clouds?|musical notes?|symbols?)\b",
        requirement.semantic_label.casefold(),
    )
    return bool(
        explicit_display
        or conceptual
        or not requirement.grounded
        or requirement.kind == "color_swatch"
        or requirement.motion == "float"
    )


def stage_visual_quality(
    composition: SceneComposition,
    display_keys: set[str],
    previous: SceneComposition | None = None,
    *,
    visible_bounds: dict[str, tuple[float, float, float, float]] | None = None,
) -> SceneComposition:
    """Small staging alternative using admitted object metadata and approved character lanes."""
    if composition.micro_scene and previous:
        return previous.model_copy(update={"scene_id": composition.scene_id})
    props = composition.props
    count = len(props)
    if (previous and composition.resolved_action == "enter"
            and tuple(p.type for p in props) == tuple(p.type for p in previous.props)):
        # A farewell retaining a floating recap keeps that deliberate display intent.
        display_keys = display_keys | {p.type for p in previous.props
                                       if p.placement_mode == "display"}
    all_ground = bool(props) and all(p.type not in display_keys and p.grounded for p in props)
    group = count == 3 and all_ground
    character_slot = "lower_left" if props else "lower_center"
    placements = []
    tangible_index = 0
    for i, p in enumerate(props):
        display = p.type in display_keys or not p.grounded
        if group:
            # One supported foreground row, with Tovi visibly presenting the group.
            x, bottom, width = ((0.81, 0.87, 0.29), (0.50, 0.87, 0.28),
                                (0.19, 0.87, 0.28))[i]
        elif count == 1:
            x, bottom, width = 0.785, 0.76, 0.34
        elif all_ground:
            x, bottom, width = 0.785, (0.76, 0.52)[i], (0.34, 0.27)[i]
        elif not display:
            x, bottom, width = 0.785, (0.76, 0.52)[tangible_index], (0.34, 0.27)[tangible_index]
            tangible_index += 1
        else:
            x, bottom, width = 0.785, (0.72, 0.48, 0.24)[i], (0.34, 0.30, 0.28)[i]
        center_y = bottom - width * 9 / 32 if display else bottom
        if count == 1 and display:
            center_y = 0.36
        if not display and visible_bounds and p.type in visible_bounds:
            # Reserve/check the complete source square. Place its readable feet on
            # the intended support without cropping artwork or deleting faint detail.
            bottom += (1 - visible_bounds[p.type][3]) * width * 9 / 16
            center_y = bottom
        placement = p.model_copy(
            update={
                "center": (x, center_y),
                "width": width,
                "slot": "middle_right" if display else "lower_left" if x < 0.5 else "lower_right",
                "placement_mode": "display" if display else "ground",
                "surface_y": None if display else bottom,
                "motion": p.motion if not display else "static",
            }
        )
        # Consecutive presentations of the same asset keep their size and placement.
        if previous and tuple(q.type for q in previous.props) == tuple(q.type for q in props):
            old = previous.props[i]
            if (
                old.placement_mode == placement.placement_mode
                and previous.character_slot == character_slot
            ):
                placement = old.model_copy(update={"motion": "static"})
        placements.append(placement)
    state = {"character_slot": character_slot, "props": [p.model_dump() for p in placements]}
    return composition.model_copy(
        update={
            "props": tuple(placements),
            "character_slot": character_slot,
            "character_width": 0.54 if props else 0.68,
            "character_height": 0.48,
            "ground_plane_y": 0.65 if group else 0.74,
            "character_facing": "right" if props else "none",
            # A farewell wave in a gathered group should not traverse a friend.
            "resolved_action": "present" if (
                composition.resolved_action == "question"
                or composition.resolved_action == "enter" and (group or previous is not None)
            )
            else composition.resolved_action,
            "composition_style": "surface_group"
            if group
            else "focal_display"
            if display_keys
            else "surface_focus",
            "primary_prop_slot": placements[0].slot if placements else None,
            "secondary_prop_slots": tuple(p.slot for p in placements[1:]),
            "visual_state_id": hashlib.sha256(
                json.dumps(state, sort_keys=True).encode()
            ).hexdigest()[:20],
        }
    )


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
