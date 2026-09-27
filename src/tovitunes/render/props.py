"""Pillow-only preschool scene art and machine-readable educational composition."""

import json
from typing import Any

from PIL import Image, ImageColor, ImageDraw
from PIL.PngImagePlugin import PngInfo

from tovitunes.domain.storyboard import TimedScene
from tovitunes.render import VERSION
from tovitunes.render.models import CharacterAnimation

# Deliberate lesson constant, separate from the blue/cream character palette.
LESSON_RED = "#E53935"
PROP_TYPES = {"red_swatch", "red_apple", "red_ball"}


def prop_image(kind: str, size: int = 560) -> Image.Image:
    if kind not in PROP_TYPES or size <= 0:
        raise ValueError("unsupported prop or invalid size")
    image = Image.new("RGBA", (600, 600))
    draw = ImageDraw.Draw(image)
    shadow, highlight = "#BC292B", "#FFD3CE"
    if kind == "red_swatch":
        draw.rounded_rectangle((25, 42, 575, 568), radius=105, fill=shadow)
        draw.rounded_rectangle((25, 25, 575, 545), radius=105, fill=LESSON_RED)
        draw.arc((53, 48, 210, 203), 175, 270, fill=highlight, width=14)
    elif kind == "red_apple":
        draw.line((302, 139, 323, 55), fill="#865037", width=27)
        draw.ellipse((329, 57, 456, 125), fill="#53A65B")
        draw.line((335, 99, 443, 83), fill="#367C45", width=5)
        draw.polygon(
            [
                (300, 149),
                (216, 125),
                (111, 171),
                (62, 306),
                (116, 486),
                (215, 559),
                (300, 540),
                (390, 559),
                (491, 480),
                (538, 300),
                (491, 172),
                (387, 125),
            ],
            fill=shadow,
        )
        draw.ellipse((65, 125, 367, 531), fill=LESSON_RED)
        draw.ellipse((238, 125, 535, 531), fill=LESSON_RED)
        draw.rounded_rectangle((167, 296, 448, 538), radius=100, fill=LESSON_RED)
        draw.arc((115, 174, 278, 385), 155, 255, fill=highlight, width=22)
    else:
        draw.ellipse((27, 30, 573, 576), fill=shadow)
        draw.ellipse((27, 20, 573, 556), fill=LESSON_RED)
        draw.arc((74, 63, 315, 303), 165, 270, fill=highlight, width=24)
        draw.ellipse((138, 114, 161, 137), fill="#FFF0E9")
    return image.resize((size, size), Image.Resampling.LANCZOS)


def scene_art(
    scene: TimedScene,
    palette: dict[str, str],
    canvas: tuple[int, int],
    storyboard_id: str,
) -> tuple[Image.Image, dict[str, Any]]:
    w, h = canvas
    # Work at production resolution, then downsample only for tiny real CI fixtures.
    image = Image.new("RGB", (1080, 1920))
    draw = ImageDraw.Draw(image)
    top = ImageColor.getrgb("#EAF7FF")
    bottom = ImageColor.getrgb(palette["belly_cream"])
    for y in range(1920):
        mix = y / 1919
        draw.line(
            (0, y, 1080, y), fill=tuple(round(a * (1 - mix) + b * mix) for a, b in zip(top, bottom))
        )
    draw.ellipse((-310, 1340, 1390, 2340), fill="#D5EACA")
    draw.ellipse((-210, 1445, 1290, 2430), fill="#E6F0D8")
    # Quiet, low contrast decoration; the lesson objects and Tovi dominate.
    for x, y, r in ((106, 190, 24), (943, 294, 34), (154, 1040, 16), (925, 1100, 20)):
        draw.ellipse((x - r, y - r, x + r, y + r), fill="#D4EBF7")
    for x, y in ((-75, 353), (881, 132)):
        draw.rounded_rectangle((x, y, x + 276, y + 71), radius=36, fill="#F8FCFF")
    draw.ellipse((225, 1720, 855, 1800), fill="#C6DDBA")
    props: list[dict[str, Any]] = []
    count = len(scene.required_props)
    for i, kind in enumerate(scene.required_props):
        if kind not in PROP_TYPES:
            raise ValueError(f"unsupported prop: {kind}")
        if count == 1:
            x, y, size = 260, 370, 560
        elif count == 2:
            x, y, size = (100, 540, 395) if i == 0 else (585, 540, 395)
        else:
            x, y, size = ((366, 245, 348), (114, 600, 330), (636, 600, 330))[i]
        animated = kind == "red_ball" and count == 1
        bbox = [
            round(x * w / 1080),
            round(y * h / 1920),
            round((x + size) * w / 1080),
            round((y + size) * h / 1920),
        ]
        props.append(
            {
                "type": kind,
                "bbox": bbox,
                "lesson_color": LESSON_RED,
                "count": 1,
                "motion": "roll_in" if animated else "static",
            }
        )
        if not animated:
            prop = prop_image(kind, size)
            image.paste(prop, (x, y), prop)
    metadata = {
        "renderer_version": VERSION,
        "scene_id": scene.scene_id,
        "storyboard_artifact_id": storyboard_id,
        "canvas": list(canvas),
        "palette": palette,
        "props": props,
        "contains_tovi": False,
    }
    return image.resize(canvas, Image.Resampling.LANCZOS), metadata


def png_info(metadata: dict[str, Any]) -> PngInfo:
    info = PngInfo()
    info.add_text(
        "tovitunes_composition", json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    )
    return info


def validate_props(
    scene: TimedScene,
    metadata: dict[str, Any],
    plan: CharacterAnimation,
    canvas: tuple[int, int],
) -> None:
    props = metadata["props"]
    if tuple(p["type"] for p in props) != scene.required_props or any(
        p["count"] != 1 or p["lesson_color"] != LESSON_RED for p in props
    ):
        raise ValueError("educational prop QA failed")
    w, h = canvas
    cx, cy = plan.end_position
    # Conservative resting-motion envelope, excluding the intentional initial entrance.
    character = (
        cx - plan.amplitude,
        cy - plan.amplitude * 2,
        cx + plan.size[0] + plan.amplitude,
        cy + plan.size[1],
    )
    for prop in props:
        x0, y0, x1, y1 = prop["bbox"]
        if not (w * 0.04 <= x0 < x1 <= w * 0.96 and h * 0.04 <= y0 < y1 <= h * 0.96):
            raise ValueError("lesson prop exceeds safe canvas bounds")
        if x0 < character[2] and x1 > character[0] and y0 < character[3] and y1 > character[1]:
            raise ValueError("Tovi motion obscures educational prop")
