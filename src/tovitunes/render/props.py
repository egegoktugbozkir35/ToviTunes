"""Shared supersampled preschool prop style and composition-driven scene artwork."""

import json
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter
from PIL.PngImagePlugin import PngInfo

from tovitunes.domain.storyboard import TimedScene
from tovitunes.render import VERSION
from tovitunes.render.composition import (
    LEGACY_PROP_STYLE_VERSION,
    LESSON_OBJECT_STYLE_VERSION,
    PROP_DEFINITIONS,
    CompositionRequest,
    SceneComposition,
    resolve_composition,
)
from tovitunes.render.models import CharacterAnimation

LESSON_RED = "#E53935"
PROP_TYPES = set(PROP_DEFINITIONS)
SUPERSAMPLE = 4
PROP_STYLE_CONTRACT = {
    "antialiased": True,
    "soft_shadow": True,
    "highlight": True,
    "outline_style": "soft_tonal_contour",
    "palette_source": "lesson/brand",
}

RENDER_STRATEGIES = {key: value.render_strategy for key, value in PROP_DEFINITIONS.items()}


def soft_shape(size: int, shapes: list[tuple[str, tuple[int, int, int, int], int]]) -> Image.Image:
    """Smooth overlapping rounded primitives; coordinates use a 600-unit drawing space."""
    mask = Image.new("L", (size, size))
    draw = ImageDraw.Draw(mask)
    scale = size / 600
    for kind, box, radius in shapes:
        bounds = tuple(round(v * scale) for v in box)
        if kind == "ellipse":
            draw.ellipse(bounds, fill=255)
        else:
            draw.rounded_rectangle(bounds, radius=round(radius * scale), fill=255)
    return mask


def soft_shadow(mask: Image.Image, size: int) -> Image.Image:
    shadow = Image.new("RGBA", (size, size), (94, 64, 58, 0))
    alpha = mask.filter(ImageFilter.GaussianBlur(size * 0.018)).point(lambda v: v // 5)
    shadow.putalpha(alpha)
    # Soft cast shadow beneath the silhouette; the final crop aligns its bottom to ground.
    shifted = Image.new("RGBA", (size, size))
    shifted.alpha_composite(shadow, (0, round(size * 0.018)))
    return shifted


def sphere_shading(mask: Image.Image, color: str) -> Image.Image:
    size = mask.width
    rgb = ImageColor.getrgb(color)
    surface = Image.new("RGBA", (size, size), (*rgb, 255))
    draw = ImageDraw.Draw(surface)
    # Soft radial volume shared by fruit, spheres and abstract teaching cards.
    # Keep a solid lesson-color core; gradual darker edges provide readable depth.
    for step in range(120):
        u = step / 119
        radius = size * (0.86 - 0.60 * u)
        tone = 0.78 + 0.22 * u
        draw.ellipse(
            (
                size * 0.42 - radius,
                size * 0.40 - radius,
                size * 0.42 + radius,
                size * 0.40 + radius,
            ),
            fill=tuple(round(c * tone) for c in rgb) + (255,),
        )
    surface.putalpha(mask)
    # A shared tonal rim, softened at supersampled resolution.
    rim = mask.filter(ImageFilter.MinFilter(5))
    contour = Image.new("RGBA", (size, size), (110, 40, 35, 0))
    from PIL import ImageChops

    contour.putalpha(ImageChops.subtract(mask, rim).point(lambda v: v // 3))
    surface.alpha_composite(contour)
    return surface


def soft_highlight(image: Image.Image, mask: Image.Image) -> None:
    size = image.width
    highlight = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(highlight)
    draw.ellipse((size * 0.20, size * 0.22, size * 0.32, size * 0.43), fill=(255, 235, 220, 155))
    highlight = highlight.filter(ImageFilter.GaussianBlur(size * 0.012))
    from PIL import ImageChops

    highlight.putalpha(ImageChops.multiply(highlight.getchannel("A"), mask))
    image.alpha_composite(highlight)


def leaf_shape(image: Image.Image) -> None:
    size = image.width
    scale = size / 600
    leaf = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(leaf)
    draw.ellipse(tuple(round(v * scale) for v in (330, 55, 465, 123)), fill="#59A963")
    draw.arc(
        tuple(round(v * scale) for v in (337, 64, 453, 123)),
        190,
        340,
        fill="#8EC783",
        width=max(1, round(7 * scale)),
    )
    draw.line(
        tuple(round(v * scale) for v in (343, 96, 442, 83)),
        fill="#3C804C",
        width=max(1, round(4 * scale)),
    )
    image.alpha_composite(leaf)


def deterministic_swatch(size: int = 560) -> Image.Image:
    """Purpose-built teaching card whose authoritative surface is lesson red."""
    if size <= 0:
        raise ValueError("invalid size")
    internal = size * SUPERSAMPLE
    scale = internal / 600
    mask = soft_shape(internal, [("rounded", (35, 35, 565, 565), 58)])
    image = soft_shadow(mask, internal)
    surface = Image.new("RGBA", (internal, internal), (*ImageColor.getrgb(LESSON_RED), 0))
    surface.putalpha(mask)
    # Paper/card depth: linear, restrained tonal bands rather than spherical light.
    overlay = Image.new("RGBA", (internal, internal))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        tuple(round(v * scale) for v in (43, 43, 557, 557)),
        radius=round(50 * scale),
        fill=(255, 255, 255, 0),
        outline=(255, 225, 220, 42),
        width=max(1, round(5 * scale)),
    )
    for row in range(round(48 * scale), round(180 * scale)):
        alpha = round(8 * (1 - (row - 48 * scale) / (132 * scale)))
        draw.line(
            (round(48 * scale), row, round(552 * scale), row),
            fill=(255, 255, 255, max(0, alpha)),
        )
    overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
    surface.alpha_composite(overlay)
    image.alpha_composite(surface)
    bounds = image.getchannel("A").getbbox()
    assert bounds is not None
    cropped = image.crop(bounds)
    output = Image.new("RGBA", (internal, internal))
    fitted = cropped.resize(
        (round(internal * 0.94), round(internal * 0.94)), Image.Resampling.LANCZOS
    )
    output.alpha_composite(fitted, ((internal - fitted.width) // 2, internal - fitted.height))
    return output.resize((size, size), Image.Resampling.LANCZOS)


def _reviewed_asset(path: Path, size: int) -> Image.Image:
    with Image.open(path) as opened:
        opened.load()
        if opened.format != "PNG" or opened.mode != "RGBA":
            raise ValueError("reviewed lesson object must be an RGBA PNG")
        image = cast(Image.Image, opened.copy())
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("reviewed lesson object has no visible pixels")
    return image.resize((size, size), Image.Resampling.LANCZOS)


def prop_image(
    kind: str,
    size: int = 560,
    *,
    reviewed_asset_path: Path | None = None,
    style_version: str = LEGACY_PROP_STYLE_VERSION,
) -> Image.Image:
    if kind not in PROP_TYPES or size <= 0:
        raise ValueError("unsupported prop or invalid size")
    if style_version == LESSON_OBJECT_STYLE_VERSION:
        strategy = RENDER_STRATEGIES[kind]
        if strategy == "deterministic_swatch":
            return deterministic_swatch(size)
        if reviewed_asset_path is None:
            raise ValueError(f"selected reviewed lesson-object asset unavailable: {kind}")
        return _reviewed_asset(reviewed_asset_path, size)
    if style_version != LEGACY_PROP_STYLE_VERSION:
        raise ValueError("unsupported prop style version")
    internal = size * SUPERSAMPLE
    if kind == "red_swatch":
        shapes = [("rounded", (25, 25, 575, 555), 105)]
    elif kind == "red_apple":
        # All organic contours are smooth, including the lower lobes; no polygon silhouette.
        shapes = [
            ("ellipse", (64, 130, 366, 542), 0),
            ("ellipse", (238, 130, 536, 542), 0),
            ("rounded", (170, 276, 430, 543), 112),
        ]
    else:
        shapes = [("ellipse", (27, 20, 573, 566), 0)]
    mask = soft_shape(internal, shapes)
    image = soft_shadow(mask, internal)
    image.alpha_composite(sphere_shading(mask, PROP_DEFINITIONS[kind].color))
    soft_highlight(image, mask)
    if kind == "red_apple":
        draw = ImageDraw.Draw(image)
        scale = internal / 600
        draw.line(
            tuple(round(v * scale) for v in (300, 145, 322, 58)),
            fill="#865037",
            width=max(1, round(25 * scale)),
        )
        leaf_shape(image)
    # Preserve the aspect ratio, align the visible alpha/cast shadow with the ground plane.
    bounds = image.getchannel("A").getbbox()
    assert bounds is not None
    cropped = image.crop(bounds)
    fit = min(internal / cropped.width, internal / cropped.height)
    fitted = cropped.resize(
        (round(cropped.width * fit), round(cropped.height * fit)), Image.Resampling.LANCZOS
    )
    output = Image.new("RGBA", (internal, internal))
    output.alpha_composite(fitted, ((internal - fitted.width) // 2, internal - fitted.height))
    return output.resize((size, size), Image.Resampling.LANCZOS)


def scene_art(
    scene: TimedScene,
    palette: dict[str, str],
    canvas: tuple[int, int],
    storyboard_id: str,
    composition: SceneComposition | None = None,
    *,
    background_only: bool = False,
    background_variant: str = "wide",
    lesson_asset_paths: dict[str, Path] | None = None,
    lesson_asset_metadata: dict[str, dict[str, str]] | None = None,
    prop_style_version: str = LEGACY_PROP_STYLE_VERSION,
) -> tuple[Image.Image, dict[str, Any]]:
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
    w, h = canvas
    image = Image.new("RGB", canvas)
    draw = ImageDraw.Draw(image)
    top = ImageColor.getrgb("#EAF7FF")
    bottom = ImageColor.getrgb(palette["belly_cream"])
    for row_y in range(h):
        mix = row_y / max(1, h - 1)
        draw.line(
            (0, row_y, w, row_y),
            fill=tuple(round(a * (1 - mix) + b * mix) for a, b in zip(top, bottom)),
        )
    ground = composition.ground_plane_y
    draw.ellipse((-w * 0.29, h * (ground - 0.22), w * 1.29, h * 1.22), fill="#D5EACA")
    draw.ellipse((-w * 0.20, h * (ground - 0.17), w * 1.20, h * 1.27), fill="#E6F0D8")
    for x, y, r in (
        (0.10, 0.10, 0.022),
        (0.87, 0.15, 0.03),
        (0.14, 0.54, 0.015),
        (0.86, 0.57, 0.018),
    ):
        draw.ellipse((w * (x - r), h * y - w * r, w * (x + r), h * y + w * r), fill="#D4EBF7")
    for x, y in ((-0.07, 0.18), (0.82, 0.07)):
        draw.rounded_rectangle(
            (w * x, h * y, w * (x + 0.26), h * (y + 0.037)), radius=round(w * 0.034), fill="#F8FCFF"
        )
    props: list[dict[str, Any]] = []
    for placement in composition.props:
        if placement.type not in PROP_TYPES:
            raise ValueError(f"unsupported prop: {placement.type}")
        bbox = placement.bbox(canvas, ground)
        prop_metadata = {
            **placement.model_dump(mode="json"),
            "bbox": bbox,
            "count": 1,
            "ground_plane_y": round(h * ground),
            "prop_style_version": prop_style_version,
            "render_strategy": RENDER_STRATEGIES[placement.type]
            if prop_style_version == LESSON_OBJECT_STYLE_VERSION
            else "legacy_procedural",
        }
        if placement.type in (lesson_asset_metadata or {}):
            prop_metadata.update((lesson_asset_metadata or {})[placement.type])
        props.append(prop_metadata)
        if placement.motion == "static" and not background_only:
            prop = prop_image(
                placement.type,
                bbox[2] - bbox[0],
                reviewed_asset_path=(lesson_asset_paths or {}).get(placement.type),
                style_version=prop_style_version,
            )
            image.paste(prop, (bbox[0], bbox[1]), prop)
    metadata = {
        "renderer_version": VERSION,
        "scene_id": scene.scene_id,
        "storyboard_artifact_id": storyboard_id,
        "canvas": list(canvas),
        "palette": palette,
        "props": props,
        "contains_tovi": False,
        "composition": composition.model_dump(mode="json"),
        "prop_style_version": prop_style_version,
        "prop_style_contract": PROP_STYLE_CONTRACT,
        "supersample": SUPERSAMPLE,
    }
    if background_only:
        from tovitunes.render.environment import LAYERS, THEME, background_plate

        image = background_plate(canvas, background_variant)
        metadata.update(
            {
                "background_only": True,
                "environmental_theme": THEME,
                "background_variant": background_variant,
                "environment_layers": list(LAYERS),
            }
        )
    return image, metadata


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
    composition = SceneComposition.model_validate(metadata["composition"])
    props = metadata["props"]
    expected = tuple(p.type for p in composition.props)
    if (
        tuple(p["type"] for p in props) != expected
        or not set(scene.required_props) <= set(expected)
        or any(
            p["count"] != 1 or p["lesson_color"] != PROP_DEFINITIONS[p["type"]].color for p in props
        )
        or metadata["prop_style_version"]
        not in {LESSON_OBJECT_STYLE_VERSION, LEGACY_PROP_STYLE_VERSION}
    ):
        raise ValueError("educational prop QA failed")
    w, h = canvas
    cx, cy = plan.end_position
    character = (
        cx - plan.amplitude,
        cy - plan.amplitude * 3,
        cx + plan.size[0] + plan.amplitude,
        cy + plan.size[1],
    )
    for prop, placement in zip(props, composition.props, strict=True):
        x0, y0, x1, y1 = prop["bbox"]
        if prop["bbox"] != placement.bbox(canvas, composition.ground_plane_y):
            raise ValueError("prop pixels differ from composition plan")
        if prop.get("render_strategy") == "reviewed_asset" and not all(
            prop.get(field) for field in ("asset_artifact_id", "asset_sha256", "anchor")
        ):
            raise ValueError("reviewed prop metadata is incomplete")
        if not (w * 0.04 <= x0 < x1 <= w * 0.96 and h * 0.04 <= y0 < y1 <= h * 0.96):
            raise ValueError("lesson prop exceeds safe canvas bounds")
        if x0 < character[2] and x1 > character[0] and y0 < character[3] and y1 > character[1]:
            raise ValueError("Tovi motion obscures educational prop")
        for other in props:
            a, b, c, d = other["bbox"]
            if other is not prop and x0 < c and x1 > a and y0 < d and y1 > b:
                raise ValueError("lesson props obscure each other")
