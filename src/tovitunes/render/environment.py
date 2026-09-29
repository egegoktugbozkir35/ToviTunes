"""Reusable Pillow meadow assets, drawn once per scene rather than once per frame."""

from PIL import Image, ImageDraw, ImageFilter

THEME = "playful_meadow_v2"
LAYERS = ("far_background", "mid_background", "ground", "lesson_character", "foreground")


def background_plate(canvas: tuple[int, int], variant: str) -> Image.Image:
    if variant not in {"wide", "lesson_focus", "performance", "celebration"}:
        raise ValueError("unsupported meadow variant")
    w, h = canvas
    image = Image.new("RGB", canvas)
    draw = ImageDraw.Draw(image)
    top, bottom = (209, 237, 251), (249, 247, 220)
    for y in range(h):
        u = min(1, y / (h * 0.8))
        draw.line((0, y, w, y), fill=tuple(round(a + (b - a) * u) for a, b in zip(top, bottom)))
    # Low contrast hills stay behind the educational plane; same geography in every variant.
    shift = {"wide": 0, "lesson_focus": 0.015, "performance": -0.015, "celebration": 0}[variant]
    draw.ellipse((-w * 0.6, h * (0.52 + shift), w * 0.75, h * 1.05), fill="#C7E3CD")
    draw.ellipse((w * 0.38, h * (0.55 + shift), w * 1.65, h * 1.1), fill="#BCDDCA")
    draw.ellipse((-w * 0.35, h * 0.67, w * 1.35, h * 1.45), fill="#DCECCB")
    draw.ellipse((-w * 0.35, h * 0.86, w * 1.35, h * 1.3), fill="#EAF1D7")
    return image


def ambient_sprite(kind: str, size: int) -> Image.Image:
    """Antialiased transparent decor; no assets or provider calls required."""
    size = max(8, size)
    image = Image.new("RGBA", (size * 3, size * 3))
    draw = ImageDraw.Draw(image)
    s = image.width
    if kind == "cloud":
        for box in ((0.03, 0.45, 0.97, 0.70), (0.14, 0.32, 0.53, 0.72), (0.38, 0.25, 0.79, 0.74)):
            draw.ellipse(tuple(round(v * s) for v in box), fill="#FFFFFF")
    elif kind == "flower":
        draw.line((s * 0.5, s * 0.55, s * 0.5, s * 0.94), fill="#91B578", width=round(s * 0.04))
        for x, y in ((0.3, 0.28), (0.52, 0.2), (0.69, 0.4), (0.53, 0.57), (0.27, 0.51)):
            draw.ellipse(
                (s * (x - 0.14), s * (y - 0.14), s * (x + 0.14), s * (y + 0.14)), fill="#FFF7CB"
            )
        draw.ellipse((s * 0.37, s * 0.32, s * 0.61, s * 0.56), fill="#EAC87B")
    elif kind == "leaf":
        draw.ellipse((s * 0.18, s * 0.28, s * 0.93, s * 0.63), fill="#9EBC87")
        draw.ellipse((s * 0.4, s * 0.5, s * 0.87, s * 0.85), fill="#B7CF99")
        image = image.filter(ImageFilter.GaussianBlur(s * 0.015))
    elif kind == "note":
        draw.ellipse((s * 0.13, s * 0.63, s * 0.48, s * 0.84), fill="#7EB7D2")
        draw.line((s * 0.44, s * 0.74, s * 0.44, s * 0.17), fill="#7EB7D2", width=round(s * 0.075))
        draw.line((s * 0.44, s * 0.17, s * 0.8, s * 0.28), fill="#7EB7D2", width=round(s * 0.09))
    elif kind == "sparkle":
        draw.polygon(
            [
                (s * 0.5, s * 0.12),
                (s * 0.61, s * 0.4),
                (s * 0.88, s * 0.5),
                (s * 0.61, s * 0.6),
                (s * 0.5, s * 0.88),
                (s * 0.39, s * 0.6),
                (s * 0.12, s * 0.5),
                (s * 0.39, s * 0.4),
            ],
            fill="#EACB80",
        )
    else:
        raise ValueError("unsupported ambient asset")
    return image.resize((size, size), Image.Resampling.LANCZOS)


def halo_sprite(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for inset, alpha in ((0.04, 25), (0.095, 32), (0.15, 38)):
        draw.ellipse(
            (size * inset, size * inset, size * (1 - inset), size * (1 - inset)),
            outline=(255, 220, 100, alpha),
            width=max(2, round(size * 0.018)),
        )
    return image.filter(ImageFilter.GaussianBlur(size * 0.008))
