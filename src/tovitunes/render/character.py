"""Approved pose mapping, uniform layout and measured-beat position functions."""

import math

from PIL import Image

from tovitunes.domain.storyboard import BeatAnalysis, TimedScene
from tovitunes.render.layout import compute_fit_box
from tovitunes.render.models import CharacterAnimation, Motion, SpriteRole

ACTION_ROLES: dict[str, SpriteRole] = {
    "enter": "sprite/hello",
    "point": "sprite/pointing",
    "question": "sprite/pointing",
    "present": "sprite/neutral_full_body",
    "idle": "sprite/neutral_full_body",
    "sing": "sprite/singing",
    "celebrate": "sprite/hopping",
}


def animation_plan(
    scene: TimedScene,
    sprite: Image.Image,
    artifact_id: str,
    beats: BeatAnalysis,
    canvas: tuple[int, int],
    storyboard_id: str,
) -> CharacterAnimation:
    role = ACTION_ROLES.get(scene.tovi_action)
    if role is None:
        raise ValueError("unsupported Tovi action")
    if sprite.mode != "RGBA":
        raise ValueError("approved sprite must have alpha")
    alpha = sprite.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError("sprite has no visible alpha")
    cw, ch = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = canvas
    box = compute_fit_box((cw, ch), (round(w * 0.75), round(h * 0.40)), mode="contain")
    scale = min(box.out_w / cw, box.out_h / ch)
    size = (round(cw * scale), round(ch * scale))
    end = ((w - size[0]) / 2, h * 0.92 - size[1])
    start = (-size[0], end[1] + h * 0.02) if scene.tovi_action == "enter" else end
    b0, b1 = scene.beat_index_range
    d0, d1 = scene.downbeat_index_range
    motion: Motion = "bob" if scene.tovi_action == "idle" else scene.tovi_action
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
        beat_seconds=tuple(t - scene.start for t in beats.beat_seconds[b0:b1]),
        downbeat_seconds=tuple(t - scene.start for t in beats.downbeat_seconds[d0:d1]),
    )


def position(plan: CharacterAnimation, t: float) -> tuple[float, float]:
    x, y = plan.end_position
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
        return x, y - plan.amplitude * 2 * pulse
    if plan.motion_type == "sing":
        pulse = max(
            (math.sin(math.pi * (t - b) / 0.27) for b in plan.beat_seconds if 0 <= t - b <= 0.27),
            default=0.0,
        )
        return x, y - plan.amplitude * pulse
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
        and h * 0.04 <= y - plan.amplitude * 2
        and y + sh <= h * 0.96
    ):
        raise ValueError("Tovi resting bounds exceed safe margins")
    if plan.motion_type == "enter" and plan.start_position[0] + sw > 0:
        raise ValueError("enter must start at the intentional offscreen edge")
