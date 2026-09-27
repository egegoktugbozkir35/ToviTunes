# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
# Two-stage render/atomic output adapted 2026-09-27; see docs/RENDER_DONOR_NOTICE.md.
"""Optional MoviePy composition in an isolated, deadline-bounded worker process."""

import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from PIL import Image

from tovitunes.render.character import position
from tovitunes.render.models import CharacterAnimation
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
        return ball_position(t, bbox, canvas_width)
    return float(bbox[0]), float(bbox[1])


def build_scene(payload: dict[str, Any], stack: ExitStack) -> Any:
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
