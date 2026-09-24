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


@dataclass(frozen=True)
class MouthFacts:
    visible_bbox: tuple[int, int, int, int]
    aperture_bbox: tuple[int, int, int, int] | None
    aperture_pixels: int
    blue_pixels: int


def visible_art_bbox(
    image: Image.Image, *, alpha_threshold: int = 8, padding: int = 16
) -> tuple[int, int, int, int]:
    if image.mode != "RGBA":
        raise InvalidCharacterSprite("sprite must be RGBA")
    visible = image.getchannel("A").point(lambda value: 255 if value > alpha_threshold else 0)
    bbox = visible.getbbox()
    if bbox is None:
        raise InvalidCharacterSprite("sprite has no visible pixels")
    if min(bbox[0], bbox[1], image.width - bbox[2], image.height - bbox[3]) < padding:
        raise InvalidCharacterSprite("visible artwork lacks transparent padding")
    return bbox


def validate_mouth_states(facts: dict[str, MouthFacts]) -> None:
    """Reject missing or near-equivalent lip shapes before visual approval."""
    if set(facts) != {"closed", "small_open", "wide_a", "e_smile", "o_round"}:
        raise InvalidCharacterSprite("all five mouth states are required")
    if any(item.aperture_bbox is None for item in facts.values()):
        raise InvalidCharacterSprite("every mouth state needs a readable aperture")
    closed = facts["closed"].aperture_pixels
    small = facts["small_open"].aperture_pixels
    wide = facts["wide_a"].aperture_pixels
    smile = facts["e_smile"].aperture_pixels
    if not (closed * 3 < small and small * 2 < wide and smile * 1.4 < wide):
        raise InvalidCharacterSprite("closed, small, wide and smile openings are not distinct")
    e_box = facts["e_smile"].aperture_bbox
    o_box = facts["o_round"].aperture_bbox
    assert e_box is not None and o_box is not None
    e_aspect = (e_box[2] - e_box[0]) / (e_box[3] - e_box[1])
    o_aspect = (o_box[2] - o_box[0]) / (o_box[3] - o_box[1])
    if not (e_aspect > o_aspect * 1.2 and o_aspect < 2.0):
        raise InvalidCharacterSprite("smile and round openings are not distinct")


def inspect_mouth_component(
    image: Image.Image, *, padding: int = 16, alpha_threshold: int = 8
) -> MouthFacts:
    """Check an isolated warm beak without mistaking alpha-1 fringe for artwork."""
    if image.mode != "RGBA":
        raise InvalidCharacterSprite("mouth component must be RGBA")
    bbox = visible_art_bbox(image, alpha_threshold=alpha_threshold, padding=padding)
    blue_pixels = 0
    aperture_pixels = 0
    aperture_box: tuple[int, int, int, int] | None = None
    aperture_x0, aperture_y0 = image.width, image.height
    aperture_x1 = aperture_y1 = 0
    visible_pixels = 0
    for y in range(bbox[1], bbox[3]):
        for x in range(bbox[0], bbox[2]):
            red, green, blue, opacity = cast(tuple[int, int, int, int], image.getpixel((x, y)))
            if opacity <= 128:
                continue
            visible_pixels += 1
            if blue > red * 1.3 and blue > green * 1.1:
                blue_pixels += 1
            if red < 170 and green < 70 and blue < 70:
                aperture_pixels += 1
                aperture_x0 = min(aperture_x0, x)
                aperture_y0 = min(aperture_y0, y)
                aperture_x1 = max(aperture_x1, x + 1)
                aperture_y1 = max(aperture_y1, y + 1)
    if visible_pixels == 0 or blue_pixels > visible_pixels // 100:
        raise InvalidCharacterSprite("mouth component contains non-beak blue artwork")
    if aperture_pixels:
        aperture_box = (aperture_x0, aperture_y0, aperture_x1, aperture_y1)
    return MouthFacts(bbox, aperture_box, aperture_pixels, blue_pixels)


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
