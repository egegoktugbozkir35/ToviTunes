# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
# Two-stage render/atomic output adapted 2026-09-27; see docs/RENDER_DONOR_NOTICE.md.
"""Optional MoviePy composition in an isolated, deadline-bounded worker process."""

import json
import math
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from PIL import Image

from tovitunes.render.character import pose_position, position
from tovitunes.render.environment import ambient_sprite, halo_sprite
from tovitunes.render.models import CharacterAnimation
from tovitunes.render.motion import SceneMotionPlan, camera_state, prop_state, smooth
from tovitunes.render.props import prop_image


def ball_position(t: float, bbox: list[int], canvas_width: int) -> tuple[float, float]:
    u = min(1.0, max(0.0, t / 0.95))
    return canvas_width + (bbox[0] - canvas_width) * (1 - (1 - u) ** 3), float(bbox[1])


def prop_position(t: float, prop: dict[str, Any], canvas_width: int) -> tuple[float, float]:
    """Ground-contact translation for any registered rolling object."""
    bbox = prop["bbox"]
    if prop["motion"] == "roll_in":
        if not prop["grounded"] or prop["placement_mode"] != "ground":
            raise ValueError("rolling prop must be grounded")
        if abs(bbox[3] - prop["ground_plane_y"]) > 1:
            raise ValueError("rolling target is off the ground plane")
        # Enter from the target's nearest edge, avoiding the character on the opposite side.
        start_x = (
            canvas_width if (bbox[0] + bbox[2]) / 2 >= canvas_width / 2 else -(bbox[2] - bbox[0])
        )
        u = min(1.0, max(0.0, t / 0.95))
        return start_x + (bbox[0] - start_x) * (1 - (1 - u) ** 3), float(bbox[1])
    return float(bbox[0]), float(bbox[1])


def build_scene(payload: dict[str, Any], stack: ExitStack) -> Any:
    if "motion" in payload:
        return build_dynamic_scene(payload, stack)
    # Imports stay inside the adapter; minimal installs can still plan and inspect.
    import numpy as np
    from moviepy import CompositeVideoClip, ImageClip

    duration = payload["end"] - payload["start"]
    canvas = tuple(payload["canvas"])
    plan = CharacterAnimation.model_validate(payload["animation"])
    with Image.open(payload["background_path"]) as image:
        background = ImageClip(np.array(image.convert("RGB"))).with_duration(duration)
    with Image.open(payload["sprite_path"]) as image:
        sprite = (
            image.convert("RGBA").crop(plan.crop_bbox).resize(plan.size, Image.Resampling.LANCZOS)
        )
        tovi = ImageClip(np.array(sprite), transparent=True).with_duration(duration)
    tovi = tovi.with_position(lambda t: position(plan, t))
    layers = [background, tovi]
    for prop in payload["metadata"]["props"]:
        if prop["motion"] == "roll_in":
            bbox = prop["bbox"]
            ball = ImageClip(
                np.array(prop_image(prop["type"], bbox[2] - bbox[0])), transparent=True
            )
            ball = ball.with_duration(duration).with_position(
                lambda t, p=prop: prop_position(t, p, canvas[0])
            )
            layers.insert(1, ball)
    clip = CompositeVideoClip(layers, size=canvas).with_duration(duration)
    for layer in layers:
        stack.callback(layer.close)
    stack.callback(clip.close)
    return clip


def build_dynamic_scene(payload: dict[str, Any], stack: ExitStack) -> Any:
    import numpy as np
    from moviepy import CompositeVideoClip, ImageClip

    duration = payload["end"] - payload["start"]
    w, h = payload["canvas"]
    plan = CharacterAnimation.model_validate(payload["animation"])
    motion = SceneMotionPlan.model_validate(payload["motion"])
    layers: list[Any] = []

    def clip_for(image: Image.Image) -> Any:
        clip = ImageClip(np.array(image), transparent=image.mode == "RGBA").with_duration(duration)
        stack.callback(clip.close)
        return clip

    def opacity(clip: Any, gain: Any) -> Any:
        mask = clip.mask.transform(lambda get_frame, t: get_frame(t) * gain(t))
        stack.callback(mask.close)
        return clip.with_mask(mask)

    with Image.open(payload["background_path"]) as image:
        layers.append(clip_for(image.convert("RGB")))
    # Cached transparent decor occupies explicit depth planes with different movement rates.
    foreground: list[Any] = []
    for ambient in motion.ambient_tracks:
        size = round(w * ambient.width)
        asset = ambient_sprite(ambient.kind, size)
        clip = clip_for(asset)

        def ambient_position(t: float, a: Any = ambient, s: int = size) -> tuple[float, float]:
            clock = motion.ambient_time_offset + t
            drift = a.drift * math.sin(2 * math.pi * clock / a.period + a.phase)
            dy = 0.004 * math.sin(2 * math.pi * clock / a.period + a.phase)
            if motion.settle_start is not None and t >= motion.settle_start:
                # Last two seconds hold all ambient transforms, too.
                frozen = min(t, max(motion.settle_start, duration - 1.5))
                clock = motion.ambient_time_offset + frozen
                drift = a.drift * math.sin(2 * math.pi * clock / a.period + a.phase)
                dy = 0.004 * math.sin(2 * math.pi * clock / a.period + a.phase)
            return w * (a.center[0] + drift) - s / 2, h * (a.center[1] + dy) - s / 2

        clip = clip.with_position(ambient_position)
        if ambient.kind in {"note", "sparkle"}:

            def gain(t: float, a: Any = ambient) -> float:
                fade = 1.0
                if motion.settle_start is not None:
                    fade = 1 - smooth((t - motion.settle_start) / 0.7)
                return float(
                    a.opacity
                    * (0.65 + 0.35 * math.sin(math.pi * (t + a.phase) / a.period) ** 2)
                    * fade
                )

            clip = opacity(clip, gain)
        else:
            clip = clip.with_opacity(ambient.opacity)
        (foreground if ambient.plane == "foreground" else layers).append(clip)
    props = {p["type"]: p for p in payload["metadata"]["props"]}
    for track in motion.prop_tracks:
        prop = props[track.prop_key]
        size = prop["bbox"][2] - prop["bbox"][0]
        asset = prop_image(track.prop_key, size)
        clip = clip_for(asset).resized(lambda t, p=track: prop_state(p, motion, t)[2])
        if any(e.motion in {"wiggle", "roll_in"} for e in track.events):
            clip = clip.rotated(lambda t, p=track: prop_state(p, motion, t)[3], expand=True)

        clip.memoize = True

        def prop_place(t: float, p: Any = track, c: Any = clip) -> tuple[float, float]:
            x, bottom, _, _ = prop_state(p, motion, t)
            frame = c.get_frame(t)
            return w * x - frame.shape[1] / 2, h * bottom - frame.shape[0]

        clip = clip.with_position(prop_place)
        reveal_events = tuple(e for e in track.events if e.motion == "reveal")
        if reveal_events:
            reveal_size = round(size * 1.45)
            reveal_halo = clip_for(halo_sprite(reveal_size)).with_position(
                lambda t, p=track, s=reveal_size, prop_size=size: (
                    prop_state(p, motion, t)[0] * w - s / 2,
                    prop_state(p, motion, t)[1] * h - prop_size / 2 - s / 2,
                )
            )

            def reveal_gain(t: float, events: Any = reveal_events) -> float:
                return max(
                    (
                        0.75 * math.sin(math.pi * (t - e.start) / (e.end - e.start)) ** 2
                        for e in events
                        if e.start <= t <= e.end
                    ),
                    default=0.0,
                )

            layers.append(opacity(reveal_halo, reveal_gain))
        keyword_events = tuple(
            k for k in motion.keyword_emphasis_events if k.prop_key == track.prop_key
        )
        if keyword_events:
            halo_size = round(size * 1.32)
            halo = clip_for(halo_sprite(halo_size)).with_position(
                lambda t, p=track, s=halo_size, prop_size=size: (
                    prop_state(p, motion, t)[0] * w - s / 2,
                    prop_state(p, motion, t)[1] * h - prop_size / 2 - s / 2,
                )
            )

            def halo_gain(t: float, events: Any = keyword_events) -> float:
                return max(
                    (
                        math.sin(
                            math.pi
                            * (t + motion.scene_start - k.word_start)
                            / (k.word_end - k.word_start)
                        )
                        ** 2
                        for k in events
                        if k.word_start <= t + motion.scene_start <= k.word_end
                    ),
                    default=0.0,
                )

            layers.append(opacity(halo, halo_gain))
        layers.append(clip)
    for i, pose in enumerate(plan.pose_sequence):
        with Image.open(payload["sprite_paths"][pose.sprite_artifact_id]) as image:
            asset = (
                image.convert("RGBA")
                .crop(pose.crop_bbox)
                .resize(pose.size, Image.Resampling.LANCZOS)
            )

        def entry_scale(t: float) -> float:
            if motion.scene_entry_effect in {"soft_pop", "focus_in"}:
                return 0.98 + 0.02 * smooth(t / motion.scene_entry_seconds)
            return 1.0

        clip = clip_for(asset).resized(entry_scale)

        def character_place(t: float, p: Any = pose) -> tuple[float, float]:
            x, y = pose_position(plan, p, t)
            scale = entry_scale(t)
            dx = (
                -w * 0.008 * (1 - smooth(t / motion.scene_entry_seconds))
                if motion.scene_entry_effect == "side_reveal"
                else 0
            )
            return x + (p.size[0] - int(p.size[0] * scale)) / 2 + dx, y + p.size[1] - int(
                p.size[1] * scale
            )

        clip = clip.with_position(character_place)

        def pose_gain(t: float, index: int = i) -> float:
            clock = t + motion.clock_offset
            p = plan.pose_sequence[index]
            if clock < p.time:
                return 0.0
            fade_in = (
                1.0
                if index == 0 or p.transition == "cut"
                else smooth((clock - p.time) / p.transition_seconds)
            )
            if index + 1 < len(plan.pose_sequence):
                nxt = plan.pose_sequence[index + 1]
                fade_out = (
                    1.0
                    if clock < nxt.time
                    else (
                        0.0
                        if nxt.transition == "cut"
                        else 1 - smooth((clock - nxt.time) / nxt.transition_seconds)
                    )
                )
                return min(fade_in, fade_out)
            return fade_in

        layers.append(opacity(clip, pose_gain))
    layers.extend(foreground)
    # The background plate is opaque. Avoid MoviePy's redundant full-frame mask,
    # including its mask-slice bug when the intentionally arriving sprite is offscreen.
    scene = CompositeVideoClip(layers, size=(w, h), bg_color=(0, 0, 0)).with_duration(duration)
    stack.callback(scene.close)

    def camera_frame(get_frame: Any, t: float) -> Any:
        z, px, py = camera_state(motion.camera_track, t + motion.clock_offset)
        # Transform the composited scene once. Overscan is mathematically checked by QA.
        nw, nh = math.ceil(w * z), math.ceil(h * z)
        image = Image.fromarray(get_frame(t).astype("uint8")).resize(
            (nw, nh), Image.Resampling.BILINEAR
        )
        left = min(nw - w, max(0, round((nw - w) / 2 + px * w)))
        top = min(nh - h, max(0, round((nh - h) / 2 + py * h)))
        return np.array(image.crop((left, top, left + w, top + h)))

    final = scene.transform(camera_frame).with_duration(duration)
    stack.callback(final.close)
    return final


def encode_worker(request_path: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    os.environ["FFMPEG_BINARY"] = request["ffmpeg_path"]
    os.environ["IMAGEIO_FFMPEG_EXE"] = request["ffmpeg_path"]
    from moviepy import concatenate_videoclips

    with ExitStack() as stack:
        scenes = [build_scene(scene, stack) for scene in request["scenes"]]
        video = concatenate_videoclips(scenes, method="chain")
        stack.callback(video.close)
        video.write_videofile(
            request["video_path"],
            fps=30,
            codec="libx264",
            audio=False,
            preset="medium",
            threads=4,
            logger=None,
            ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p", "-map_metadata", "-1"],
        )


if __name__ == "__main__":
    encode_worker(Path(sys.argv[1]))
