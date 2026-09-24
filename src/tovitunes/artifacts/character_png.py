"""Pixel-level validation for transparent character animation sprites."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import Image, UnidentifiedImageError


class InvalidCharacterSprite(ValueError):
    pass


@dataclass(frozen=True)
class SpriteFacts:
    width: int
    height: int
    alpha_bbox: tuple[int, int, int, int]
    transparent_pixels: int
    semitransparent_pixels: int
    opaque_pixels: int


def inspect_sprite_png(path: Path) -> SpriteFacts:
    """Decode the image and reject opaque or empty backgrounds without flattening edges."""
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as image:
            if image.format != "PNG" or image.mode != "RGBA":
                raise InvalidCharacterSprite("sprite must be an RGBA PNG")
            image.load()
            alpha = image.getchannel("A")
            histogram = alpha.histogram()
            bbox = alpha.getbbox()
            if bbox is None:
                raise InvalidCharacterSprite("sprite has no visible pixels")
            total = image.width * image.height
            transparent = histogram[0]
            opaque = histogram[255]
            semitransparent = total - transparent - opaque
            if transparent == 0 or transparent < total // 20:
                raise InvalidCharacterSprite("sprite is effectively opaque")
            corners = tuple(
                cast(int, alpha.getpixel(point))
                for point in (
                    (0, 0),
                    (image.width - 1, 0),
                    (0, image.height - 1),
                    (image.width - 1, image.height - 1),
                )
            )
            if any(value > 8 for value in corners):
                raise InvalidCharacterSprite("visible pixels contaminate a canvas corner")
            return SpriteFacts(
                image.width, image.height, bbox, transparent, semitransparent, opaque
            )
    except (OSError, UnidentifiedImageError) as exc:
        raise InvalidCharacterSprite("PNG cannot be decoded") from exc
