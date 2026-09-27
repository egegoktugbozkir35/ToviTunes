"""Approved pose mapping, uniform layout and measured-beat position functions."""

import math

from PIL import Image

from tovitunes.domain.storyboard import BeatAnalysis, TimedScene
from tovitunes.render.composition import (
    ACTION_METADATA,
    LONG_CHARACTER_SCENE_SECONDS,
    SLOTS,
    CompositionRequest,
    SceneComposition,
    resolve_composition,
)
from tovitunes.render.layout import compute_fit_box
from tovitunes.render.models import CharacterAnimation, Motion, PoseKeyframe, SpriteRole
from tovitunes.render.motion import PoseCue

ACTION_ROLES: dict[str, SpriteRole] = {a: m.sprite_role for a, m in ACTION_METADATA.items()}


def pose_keyframe(
    cue: PoseCue, image: Image.Image, artifact_id: str, perceived_height: int
) -> PoseKeyframe:
    if image.mode != "RGBA" or (bbox := image.getchannel("A").getbbox()) is None:
        raise ValueError("pose requires approved visible alpha sprite")
    scale = perceived_height / (bbox[3] - bbox[1])
    return PoseKeyframe(
        time=cue.time,
        sprite_role=cue.sprite_role,
        sprite_artifact_id=artifact_id,
        crop_bbox=bbox,
        scale=scale,
        size=(round((bbox[2] - bbox[0]) * scale), perceived_height),
        transition="cut" if cue.time == 0 else "short_crossfade",
    )


def pose_position(
    animation: CharacterAnimation, pose: PoseKeyframe, t: float
) -> tuple[float, float]:
    x, y = position(animation, t)
    # Visible alpha crops are normalized to one perceived height and a bottom-center base.
    return x + (animation.size[0] - pose.size[0]) / 2, y + animation.size[1] - pose.size[1]


def attach_poses(
    animation: CharacterAnimation, poses: tuple[PoseKeyframe, ...], canvas: tuple[int, int]
) -> CharacterAnimation:
    animation = CharacterAnimation.model_validate(
        {
            **animation.model_dump(),
            "pose_sequence": poses,
        }
    )
    # The widest approved alpha crop controls one shared bottom-center anchor.
    half_width = max(p.size[0] for p in poses) / 2
    center_x = animation.end_position[0] + animation.size[0] / 2
    safe_center = min(canvas[0] * 0.95 - half_width, max(canvas[0] * 0.05 + half_width, center_x))
    shift = safe_center - center_x
    return animation.model_copy(
        update={
            "end_position": (animation.end_position[0] + shift, animation.end_position[1]),
            "start_position": animation.start_position
            if animation.motion_type == "enter"
            else (animation.start_position[0] + shift, animation.start_position[1]),
        }
    )


def animation_plan(
    scene: TimedScene,
    sprite: Image.Image,
    artifact_id: str,
    beats: BeatAnalysis,
    canvas: tuple[int, int],
    storyboard_id: str,
    composition: SceneComposition | None = None,
    *,
    height_limit: float = 0.40,
) -> CharacterAnimation:
    if scene.tovi_action not in ACTION_ROLES:
        raise ValueError("unsupported Tovi action")
    composition = composition or resolve_composition(
        CompositionRequest(
            scene.scene_id,
            scene.start,
            scene.end,
            scene.tovi_action,
            scene.required_props,
            scene.kind,
        )
    )
    role = composition.sprite_role
    if sprite.mode != "RGBA":
        raise ValueError("approved sprite must have alpha")
    alpha = sprite.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError("sprite has no visible alpha")
    cw, ch = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = canvas
    box = compute_fit_box(
        (cw, ch),
        (
            round(w * composition.character_width),
            round(h * min(height_limit, composition.character_height)),
        ),
        mode="contain",
    )
    scale = min(box.out_w / cw, box.out_h / ch)
    size = (round(cw * scale), round(ch * scale))
    center = SLOTS[composition.character_slot]
    end = (w * center[0] - size[0] / 2, h * composition.ground_plane_y - size[1])
    action = composition.resolved_action
    start = (-size[0], end[1] + h * 0.02) if action == "enter" else end
    b0, b1 = scene.beat_index_range
    d0, d1 = scene.downbeat_index_range
    motion: Motion = "bob" if action == "idle" else action  # type: ignore[assignment]
    origin = composition.visual_origin_start
    return CharacterAnimation(
        storyboard_artifact_id=storyboard_id,
        scene_id=scene.scene_id,
        sprite_role=role,
        sprite_artifact_id=artifact_id,
        crop_bbox=bbox,
        visible_alpha_pixels=sum(alpha.histogram()[1:]),
        scale=scale,
        size=size,
        start_position=start,
        end_position=end,
        motion_type=motion,
        amplitude=h * 0.012,
        enter_seconds=min(0.85, scene.end - scene.start),
        beat_indices=(b0, b1),
        downbeat_indices=(d0, d1),
        beat_seconds=tuple(t - origin for t in beats.beat_seconds if origin <= t < scene.end),
        downbeat_seconds=tuple(
            t - origin for t in beats.downbeat_seconds if origin <= t < scene.end
        ),
        composition_style=composition.composition_style,
        character_slot=composition.character_slot,
        gesture_direction=composition.character_facing,
        visual_state_id=composition.visual_state_id,
        inherited_from_scene_id=composition.inherited_from_scene_id,
        micro_scene=composition.micro_scene,
        emphasis=composition.emphasis,
        duration_seconds=scene.end - scene.start,
        motion_time_offset=scene.start - origin,
        motion_duration_seconds=composition.visual_origin_duration,
        motion_has_drift=composition.visual_origin_duration > LONG_CHARACTER_SCENE_SECONDS,
        outro_phases=tuple((p.name, p.start, p.end) for p in composition.outro_phases),
        long_scene_activity=scene.end - scene.start > LONG_CHARACTER_SCENE_SECONDS,
    )


def position(plan: CharacterAnimation, t: float) -> tuple[float, float]:
    x, y = _position(plan, t)
    if plan.emphasis:
        # Zero at both boundaries; <1% of frame height and no color/background change.
        u = min(1.0, max(0.0, t / plan.duration_seconds))
        y -= plan.amplitude * 0.55 * math.sin(math.pi * u) ** 2
    return x, y


def _position(plan: CharacterAnimation, local_t: float) -> tuple[float, float]:
    t = local_t + plan.motion_time_offset
    x, y = plan.end_position
    if plan.outro_phases:
        phase = next(
            (p for p in plan.outro_phases if p[1] <= local_t < p[2]), plan.outro_phases[-1]
        )
        if phase[0] == "settle":
            # Ease from the final evidence-bound movement to a stable close.
            at = phase[1]
            pulse = max(
                (
                    math.sin(math.pi * (at - b) / 0.34)
                    for b in plan.downbeat_seconds
                    if 0 <= at - b <= 0.34
                ),
                default=0.0,
            )
            offset = plan.amplitude * 2 * pulse + plan.amplitude * 0.18
            settle_duration = phase[2] - at
            moving_seconds = settle_duration - min(1.2, settle_duration * 0.25)
            u = min(1.0, max(0.0, (local_t - at) / moving_seconds))
            return x, y - offset * (1 - u) ** 2
        factor = 2.0 if phase[0] == "celebrate" else 1.0
        # Smooth amplitude across the internal phase boundary.
        if phase[0] == "recap":
            factor += max(0.0, 1 - (local_t - phase[1]) / 0.5)
        pulse = max(
            (
                math.sin(math.pi * (t - b) / 0.34)
                for b in plan.downbeat_seconds
                if 0 <= t - b <= 0.34
            ),
            default=0.0,
        )
        settle_start = plan.outro_phases[-1][1]
        drift = plan.amplitude * 0.18 * math.sin(math.pi * local_t / (2 * settle_start))
        return x, y - plan.amplitude * factor * pulse - drift
    if plan.motion_type == "enter":
        u = min(1.0, max(0.0, t / plan.enter_seconds))
        ease = 1 - (1 - u) ** 3
        return tuple(a + (b - a) * ease for a, b in zip(plan.start_position, plan.end_position))  # type: ignore[return-value]
    if plan.motion_type == "celebrate":
        pulse = max(
            (
                math.sin(math.pi * (t - b) / 0.34)
                for b in plan.downbeat_seconds
                if 0 <= t - b <= 0.34
            ),
            default=0.0,
        )
        drift = (
            plan.amplitude * 0.15 * math.sin(math.pi * t / plan.motion_duration_seconds) ** 2
            if plan.motion_has_drift
            else 0.0
        )
        return x, y - plan.amplitude * 2 * pulse - drift
    if plan.motion_type == "sing":
        pulse = max(
            (math.sin(math.pi * (t - b) / 0.27) for b in plan.beat_seconds if 0 <= t - b <= 0.27),
            default=0.0,
        )
        drift = (
            plan.amplitude * 0.15 * math.sin(math.pi * t / plan.motion_duration_seconds) ** 2
            if plan.motion_has_drift
            else 0.0
        )
        return x, y - plan.amplitude * pulse - drift
    if plan.motion_type == "question":
        return x + plan.amplitude * 0.7 * math.sin(2 * math.pi * t / 2.0), y
    amplitude = plan.amplitude * 0.5 if plan.motion_type == "present" else plan.amplitude
    return x, y - amplitude * (0.5 + 0.5 * math.sin(2 * math.pi * t / 1.5))


def validate_layout(plan: CharacterAnimation, canvas: tuple[int, int]) -> None:
    w, h = canvas
    x, y = plan.end_position
    sw, sh = plan.size
    if not (
        w * 0.04 <= x
        and x + sw <= w * 0.96
        and h * 0.04 <= y - plan.amplitude * 3
        and y + sh <= h * 0.96
    ):
        raise ValueError("Tovi resting bounds exceed safe margins")
    if plan.motion_type == "enter" and plan.start_position[0] + sw > 0:
        raise ValueError("enter must start at the intentional offscreen edge")
