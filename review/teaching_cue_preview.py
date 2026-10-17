"""Offline static PR48 cue comparison; reads retained PNGs, never a DB or provider."""

import argparse
import base64
import json
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from tovitunes.render.composition import EPISODE_ASSET_STYLE_VERSION
from tovitunes.render.props import dotted_circle_brushing, prop_image


def checker(size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGBA", size, "#B8C4CE")
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], 16):
        for x in range(0, size[0], 16):
            if (x // 16 + y // 16) % 2:
                draw.rectangle((x, y, x + 15, y + 15), fill="#E1E7EC")
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--sprite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, out = args.evidence_root.resolve(strict=True), args.output.resolve()
    if out == root or root in out.parents or out in root.parents:
        raise ValueError("output must be separate from retained evidence")
    out.mkdir(parents=True, exist_ok=True)
    placement = json.loads((root / "composition-placement.json").read_text())
    cue_path = out / "dotted-circle-brushing-v1.png"
    dotted_circle_brushing().save(cue_path)
    cue = prop_image("cue", 108, reviewed_asset_path=cue_path,
                     style_version=EPISODE_ASSET_STYLE_VERSION)
    cue.save(out / "cue-at-108px.png")
    with Image.open(args.sprite) as opened:
        sprite = opened.convert("RGBA").crop(tuple(placement["sprite_crop_bbox"]))
    sprite = sprite.resize(tuple(placement["sprite_size"]), Image.Resampling.LANCZOS)
    bbox = placement["circle_bbox"]
    width = bbox[2] - bbox[0]
    assert width == bbox[3] - bbox[1] == 324
    with Image.open(root / "indoor_bathroom/processed.png") as opened:
        background = opened.convert("RGBA")
    assert background.size == tuple(placement["canvas"])
    new = prop_image("cue", width, reviewed_asset_path=cue_path,
                     style_version=EPISODE_ASSET_STYLE_VERSION)
    with Image.open(root / "pale_circle/normalized-v1.png") as opened:
        old = opened.convert("RGBA").resize((width, width), Image.Resampling.LANCZOS)
    for name, image in (("before-v1-diagnostic", old), ("after-deterministic", new)):
        frame = background.copy()
        frame.alpha_composite(image, tuple(bbox[:2]))
        frame.alpha_composite(sprite, tuple(placement["sprite_position"]))
        frame.convert("RGB").resize((360, 640), Image.Resampling.LANCZOS).save(
            out / f"{name}-360x640.png"
        )
    font = ImageFont.load_default(size=18)
    sheet = Image.new("RGB", (760, 710), "#17202B")
    draw = ImageDraw.Draw(sheet)
    for x, name, title in (
        (10, "before-v1-diagnostic", "Before: failed Qwen / V1 diagnostic"),
        (390, "after-deterministic", "After: explicit dotted brushing path"),
    ):
        draw.text((x, 10), title, font=font, fill="white")
        with Image.open(out / f"{name}-360x640.png") as opened:
            sheet.paste(opened, (x, 42))
    draw.text((10, 686), "360 x 640 each | same bathroom, Tovi and cue box | static preview",
              font=font, fill="white")
    sheet.save(out / "phone-comparison.png")
    contrast = Image.new("RGB", (640, 190), "#17202B")
    draw = ImageDraw.Draw(contrast)
    for i, (label, color) in enumerate((
        ("Transparency", None), ("White", "white"), ("Dark", "#17202B"),
        ("Warm scene", "#EDD1AF"),
    )):
        tile = checker((144, 144)) if color is None else Image.new("RGBA", (144, 144), color)
        tile.alpha_composite(cue, (18, 18))
        contrast.paste(tile.convert("RGB"), (i * 160 + 8, 35))
        draw.text((i * 160 + 8, 8), label, font=font, fill="white")
    contrast.save(out / "transparency-at-phone-size.png")
    alpha = cue.getchannel("A")
    assert alpha.crop((33, 33, 75, 75)).getextrema() == (0, 0)
    assert all(alpha.getpixel(p) == 0 for p in ((0, 0), (107, 0), (0, 107), (107, 107)))
    inputs = [root / "composition-placement.json", root / "indoor_bathroom/processed.png",
              root / "pale_circle/normalized-v1.png", args.sprite]
    report = {
        "cue_version": "dotted_circle_brushing_v1", "new_provider_calls": 0,
        "episode_renders": 0, "production_writes": 0,
        "historical_v2_status": json.loads(
            (root / "pale_circle/processing.json").read_text())["v2"],
        "placement": placement, "cue_mobile_size": [108, 108],
        "opaque_pixels_at_phone_size": sum(a >= 240 for a in alpha.getdata()),
        "transparent_center": True, "transparent_corners": True,
        "inputs": {str(p): sha256(p.read_bytes()).hexdigest() for p in inputs},
        "outputs": {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("*.png"))},
        "scope": "Static symbol proof only; retained failed source stays blocked and unselected.",
    }
    (out / "preview-evidence.json").write_text(json.dumps(report, indent=2))
    doc = ["<!doctype html><html lang=en><meta charset=utf-8><title>Brushing cue proof</title>",
           "<style>body{font:16px system-ui;background:#17202b;color:white;max-width:900px;",
           "margin:30px auto}img{max-width:100%;height:auto}</style>",
           "<h1>Explicit dotted brushing cue</h1>",
           "<p>Offline static preview. Zero new provider calls. Qwen V2 remains blocked.",
           " Existing media is unchanged. Two clockwise arrows and ten dots; hollow center.</p>"]
    for name in ("phone-comparison.png", "transparency-at-phone-size.png"):
        encoded = base64.b64encode((out / name).read_bytes()).decode()
        doc.append(f'<p><img alt="{name}" src="data:image/png;base64,{encoded}"></p>')
    (out / "visual-proof.html").write_text("\n".join(doc), encoding="utf-8")
    print(json.dumps({"output": str(out), "phone_opaque_pixels": report[
        "opaque_pixels_at_phone_size"]}))


if __name__ == "__main__":
    main()
