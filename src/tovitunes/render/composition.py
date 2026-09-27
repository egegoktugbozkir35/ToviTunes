"""Deterministic staging policy; no curriculum, vocabulary or pixel coordinates."""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from tovitunes.domain.storyboard import ProductionModel
from tovitunes.render.models import SpriteRole

# 18 frames at 30 fps: too brief to read a fresh arrangement, enough for a gentle accent.
MIN_DISTINCT_VISUAL_SCENE_SECONDS = 0.6
LONG_TAIL_SECONDS = 3.0
LONG_CHARACTER_SCENE_SECONDS = 3.0
GROUND_PLANE_Y = 0.92
PROP_STYLE_VERSION = "preschool_soft_v1"
Direction = Literal["left", "right", "none"]

SLOTS: dict[str, tuple[float, float]] = {
    "upper_left": (0.20, 0.30),
    "upper_center": (0.50, 0.26),
    "upper_right": (0.80, 0.30),
    "middle_left": (0.20, 0.62),
    "middle_center": (0.50, 0.52),
    "middle_right": (0.80, 0.62),
    "lower_left": (0.32, GROUND_PLANE_Y),
    "lower_center": (0.50, GROUND_PLANE_Y),
    "lower_right": (0.68, GROUND_PLANE_Y),
}


@dataclass(frozen=True)
class ActionMetadata:
    sprite_role: SpriteRole
    gesture_direction: Direction = "none"
    preferred_prop_region: str = "middle_right"


ACTION_METADATA = {
    "enter": ActionMetadata("sprite/hello"),
    "point": ActionMetadata("sprite/pointing", "right"),
    "question": ActionMetadata("sprite/pointing", "right"),
    "present": ActionMetadata("sprite/neutral_full_body"),
    "idle": ActionMetadata("sprite/neutral_full_body"),
    "sing": ActionMetadata("sprite/singing"),
    "celebrate": ActionMetadata("sprite/hopping"),
}


@dataclass(frozen=True)
class PropDefinition:
    visual_class: str = "object"
    grounded: bool = True
    motion_class: str = "static"
    color: str = "#E53935"


# Vocabulary belongs to the supported drawing registry, never to layout selection.
PROP_DEFINITIONS = {
    "red_swatch": PropDefinition("abstract", False),
    "red_apple": PropDefinition(),
    "red_ball": PropDefinition(motion_class="roll"),
}


@dataclass(frozen=True)
class CompositionRequest:
    scene_id: str
    start: float
    end: float
    action: str
    required_props: tuple[str, ...] = ()
    kind: str = "lyric"
    # Renderer override only; the admitted storyboard and its timestamps stay untouched.
    explicit_visual_reset: bool = False


class PropPlacement(ProductionModel):
    type: str
    slot: str
    width: float
    center: tuple[float, float]
    grounded: bool
    placement_mode: Literal["ground", "display"]
    motion_class: str
    motion: Literal["static", "roll_in"] = "static"
    primary: bool
    lesson_color: str

    def bbox(self, canvas: tuple[int, int], ground: float) -> list[int]:
        w, h = canvas
        size = round(w * self.width)
        x = round(w * self.center[0] - size / 2)
        y = (
            round(h * ground) - size
            if self.placement_mode == "ground"
            else round(h * self.center[1] - size / 2)
        )
        return [x, y, x + size, y + size]


class OutroPhase(ProductionModel):
    name: Literal["celebrate", "recap", "settle"]
    start: float
    end: float


class SceneComposition(ProductionModel):
    scene_id: str
    composition_style: str
    character_slot: str
    character_width: float
    character_height: float = 0.40
    character_facing: Direction
    sprite_role: SpriteRole
    resolved_action: str
    primary_prop_slot: str | None
    secondary_prop_slots: tuple[str, ...]
    props: tuple[PropPlacement, ...]
    ground_plane_y: float = GROUND_PLANE_Y
    background_variant: str = "meadow_v1"
    visual_state_id: str
    visual_state_source: str
    visual_state_persistence: Literal["replace", "modify", "inherit"]
    inherited_from_scene_id: str | None = None
    visual_origin_start: float
    visual_origin_duration: float
    micro_scene: bool
    emphasis: Literal["gentle_pulse"] | None = None
    explicit_visual_reset: bool = False
    post_lyric_tail_seconds: float = 0.0
    outro_phases: tuple[OutroPhase, ...] = ()
    consecutive_identical_composition_count: int = 1
    prop_style_version: str = PROP_STYLE_VERSION


def resolve_composition(
    request: CompositionRequest,
    previous: SceneComposition | None = None,
    *,
    prop_definitions: dict[str, PropDefinition] | None = None,
    action_metadata: ActionMetadata | None = None,
    post_lyric_tail_seconds: float = 0.0,
    measured_downbeats: tuple[float, ...] = (),
) -> SceneComposition:
    definitions = PROP_DEFINITIONS if prop_definitions is None else prop_definitions
    action = action_metadata or ACTION_METADATA[request.action]
    duration = request.end - request.start
    if duration <= 0 or len(request.required_props) > 3:
        raise ValueError("composition requires positive duration and at most three props")
    micro = duration < MIN_DISTINCT_VISUAL_SCENE_SECONDS - 1e-9
    prior_types = tuple(p.type for p in previous.props) if previous else ()
    # A micro scene may retain a richer state only when every required target is already there.
    inherit = bool(
        previous
        and not request.explicit_visual_reset
        and (
            not request.required_props
            or (micro and set(request.required_props) <= set(prior_types))
        )
    )
    phases: tuple[OutroPhase, ...] = ()
    if request.kind == "outro" and post_lyric_tail_seconds > LONG_TAIL_SECONDS:
        # Measured evidence caps active rhythm; settling starts as soon as it runs out.
        last = max(
            (
                b - request.start + 0.34
                for b in measured_downbeats
                if request.start <= b < request.end
            ),
            default=0.0,
        )
        settle = min(max(0.8, last), duration - min(1.5, duration * 0.25))
        split = min(duration * 0.30, settle * 0.50)
        phases = (
            OutroPhase(name="celebrate", start=0, end=split),
            OutroPhase(name="recap", start=split, end=settle),
            OutroPhase(name="settle", start=settle, end=duration),
        )
    if inherit:
        assert previous is not None
        return previous.model_copy(
            update={
                "scene_id": request.scene_id,
                "sprite_role": previous.sprite_role if micro else action.sprite_role,
                "resolved_action": previous.resolved_action if micro else request.action,
                "character_facing": previous.character_facing
                if micro
                else action.gesture_direction,
                "props": tuple(p.model_copy(update={"motion": "static"}) for p in previous.props),
                "visual_state_persistence": "inherit",
                "inherited_from_scene_id": previous.scene_id,
                "micro_scene": micro,
                "emphasis": "gentle_pulse" if micro else None,
                "visual_origin_start": previous.visual_origin_start if micro else request.start,
                "visual_origin_duration": previous.visual_origin_duration if micro else duration,
                "post_lyric_tail_seconds": post_lyric_tail_seconds,
                "outro_phases": phases,
                "consecutive_identical_composition_count": (
                    previous.consecutive_identical_composition_count + 1
                ),
            }
        )
    count = len(request.required_props)
    direction = action.gesture_direction
    right = direction != "left"
    # Preserve an existing single target unless direction/ground-motion changes require restaging.
    same = bool(
        previous
        and prior_types == request.required_props
        and count == 1
        and request.action == "present"
    )
    if same:
        assert previous is not None
        char_slot, style = previous.character_slot, previous.composition_style
    elif request.action in {"point", "question"}:
        char_slot = "lower_left" if right else "lower_right"
        style = "character_left_object_right" if right else "character_right_object_left"
    elif count and (
        request.action == "present"
        or (
            request.action != "sing"
            and any(definitions[p].grounded for p in request.required_props)
        )
    ):
        right = not previous or previous.character_slot != "lower_left"
        char_slot = "lower_left" if right else "lower_right"
        style = "character_left_object_right" if right else "character_right_object_left"
    else:
        char_slot = "lower_center"
        style = "character_center_multi_object_arc" if count > 1 else "character_center_object_high"
    side = "right" if char_slot == "lower_left" else "left"
    placements = []
    for i, kind in enumerate(request.required_props):
        definition = definitions[kind]
        primary = i == 0
        if char_slot != "lower_center":
            slot = f"middle_{side}" if primary else f"upper_{side}"
            center = SLOTS[slot]
            width = 0.30 if primary else 0.24
            if count > 1:
                center = (center[0], (0.58, 0.36, 0.18)[i])
                if i == 2:
                    width = 0.19
        else:
            slot = ("upper_center", "upper_left", "upper_right")[i]
            center = SLOTS[slot]
            width = 0.30 if primary else 0.23
        # Recall/performance deliberately displays objects above Tovi. Resting/rolling
        # objects otherwise share the meadow ground, including a single presented object.
        display = not definition.grounded or count > 1 or request.action in {"question", "sing"}
        rolling = definition.motion_class == "roll" and not display and count == 1
        if previous and any(
            p.type == kind and p.placement_mode == "ground" for p in previous.props
        ):
            rolling = False  # A resting persistent object does not repeat its entrance.
        if not display:
            center = (0.80 if char_slot == "lower_left" else 0.20, GROUND_PLANE_Y)
            slot = f"lower_{side}"
        if same:
            assert previous is not None
            old = previous.props[i]
            center, slot, width = old.center, old.slot, old.width
            display = old.placement_mode == "display"
            rolling = False  # A persistent object does not repeatedly enter from the edge.
        placements.append(
            PropPlacement(
                type=kind,
                slot=slot,
                width=width,
                center=center,
                grounded=definition.grounded,
                placement_mode="display" if display else "ground",
                motion_class=definition.motion_class,
                motion="roll_in" if rolling else "static",
                primary=primary,
                lesson_color=definition.color,
            )
        )
    state = {
        "style": style,
        "character_slot": char_slot,
        "props": [p.model_dump(exclude={"motion"}) for p in placements],
        "background": "meadow_v1",
    }
    state_id = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:20]
    return SceneComposition(
        scene_id=request.scene_id,
        composition_style=style,
        character_slot=char_slot,
        character_width=0.54 if char_slot != "lower_center" else 0.60,
        character_facing=direction,
        sprite_role=action.sprite_role,
        resolved_action=request.action,
        primary_prop_slot=placements[0].slot if placements else None,
        secondary_prop_slots=tuple(p.slot for p in placements[1:]),
        props=tuple(placements),
        visual_state_id=state_id,
        visual_state_source=request.scene_id,
        visual_state_persistence="modify" if same else "replace",
        visual_origin_start=request.start,
        visual_origin_duration=duration,
        micro_scene=micro,
        explicit_visual_reset=request.explicit_visual_reset,
        post_lyric_tail_seconds=post_lyric_tail_seconds,
        outro_phases=phases,
        consecutive_identical_composition_count=(
            previous.consecutive_identical_composition_count + 1
            if previous and previous.composition_style == style
            else 1
        ),
    )


def validate_composition(
    request: CompositionRequest,
    plan: SceneComposition,
    previous: SceneComposition | None = None,
) -> None:
    types = tuple(p.type for p in plan.props)
    if not set(request.required_props) <= set(types) or len(types) != len(set(types)):
        raise ValueError("composition lost a required lesson target")
    if plan.visual_state_persistence == "inherit":
        if previous is None or not set(types) <= {p.type for p in previous.props}:
            raise ValueError("visual inheritance introduced untaught content")
    if request.action == "point" and plan.props and plan.character_facing != "none":
        cx = SLOTS[plan.character_slot][0]
        dx = plan.props[0].center[0] - cx
        if dx <= 0 if plan.character_facing == "right" else dx >= 0:
            raise ValueError("pointing target is outside gesture direction")
    for prop in plan.props:
        if prop.motion == "roll_in" and (not prop.grounded or prop.placement_mode != "ground"):
            raise ValueError("rolling motion must use the ground plane")
    if (
        plan.micro_scene
        and previous
        and not request.explicit_visual_reset
        and set(request.required_props) <= {p.type for p in previous.props}
    ):
        if (
            plan.visual_state_id != previous.visual_state_id
            or plan.character_slot != previous.character_slot
            or plan.background_variant != previous.background_variant
            or plan.sprite_role != previous.sprite_role
            or not plan.emphasis
        ):
            raise ValueError("micro scene caused a full visual reset")
    if request.kind == "outro" and plan.post_lyric_tail_seconds > LONG_TAIL_SECONDS:
        if tuple(p.name for p in plan.outro_phases) != ("celebrate", "recap", "settle"):
            raise ValueError("long outro needs celebration, recap and settling activity")
