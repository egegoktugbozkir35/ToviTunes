# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
# Adapted 2026-09-27 for ToviTunes; see docs/RENDER_DONOR_NOTICE.md.

"""Pure geometry helper for fitting a source video onto a canvas.

Computes crop + resize boxes for ``cover`` (fill canvas, crop overflow)
and ``contain`` (letterbox, no crop) modes. No MoviePy/ffmpeg dependency â€”
pure arithmetic so it is trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


def visible_alpha_bounds(image: Image.Image) -> tuple[int, int, int, int]:
    """Measure the opaque silhouette, ignoring faint shadows and tiny edge outliers.

    Measurement only: callers still render the complete, unchanged source image.
    Alpha 64 rejects faint matte/shadow pixels; 0.2% cumulative mass per edge
    prevents an isolated opaque pixel from defining a foot or apparent size.
    """
    alpha = image.getchannel("A").point(lambda value: value if value >= 64 else 0)
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError("image has no readable alpha silhouette")
    core = alpha.crop(bounds)
    axes = []
    for vertical in (False, True):
        length = core.height if vertical else core.width
        masses = []
        for i in range(length):
            strip = core.crop((0, i, core.width, i + 1) if vertical
                              else (i, 0, i + 1, core.height))
            masses.append(sum(v * count for v, count in enumerate(strip.histogram())))
        tail = sum(masses) * 0.002
        start, end = 0, length
        accumulated = 0
        while start < end - 1 and accumulated + masses[start] < tail:
            accumulated += masses[start]
            start += 1
        accumulated = 0
        while end > start + 1 and accumulated + masses[end - 1] < tail:
            end -= 1
            accumulated += masses[end]
        axes.append((start, end))
    return (bounds[0] + axes[0][0], bounds[1] + axes[1][0],
            bounds[0] + axes[0][1], bounds[1] + axes[1][1])


@dataclass
class LayoutBox:
    """Crop window on the source + output size after resize."""

    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    out_w: int
    out_h: int


def compute_fit_box(
    src_size: tuple[int, int],
    canvas_size: tuple[int, int],
    *,
    mode: str = "cover",
) -> LayoutBox:
    """Compute the crop+resize box to fit ``src_size`` onto ``canvas_size``.

    ``mode="cover"``: scale to fill the canvas, center-crop the overflow.
    The output size equals the canvas (no letterbox).

    ``mode="contain"``: scale to fit entirely inside the canvas, no crop.
    The output size may be smaller than the canvas (letterbox); the
    renderer centers the fitted clip on the background.
    """
    sw, sh = src_size
    cw, ch = canvas_size
    if sw <= 0 or sh <= 0 or cw <= 0 or ch <= 0:
        raise ValueError(
            f"source and canvas sizes must be positive (got src={src_size}, canvas={canvas_size})"
        )

    src_aspect = sw / sh
    canvas_aspect = cw / ch

    if mode == "cover":
        if src_aspect > canvas_aspect:
            # Source wider than canvas â†’ crop width to match canvas aspect.
            new_w = int(round(sh * canvas_aspect))
            x = (sw - new_w) // 2
            return LayoutBox(crop_x=x, crop_y=0, crop_w=new_w, crop_h=sh, out_w=cw, out_h=ch)
        else:
            # Source taller than (or equal to) canvas â†’ crop height.
            new_h = int(round(sw / canvas_aspect))
            y = (sh - new_h) // 2
            return LayoutBox(crop_x=0, crop_y=y, crop_w=sw, crop_h=new_h, out_w=cw, out_h=ch)

    if mode == "contain":
        scale = min(cw / sw, ch / sh)
        fw = int(round(sw * scale))
        fh = int(round(sh * scale))
        return LayoutBox(crop_x=0, crop_y=0, crop_w=sw, crop_h=sh, out_w=fw, out_h=fh)

    raise ValueError(f"mode must be 'cover' or 'contain', got {mode!r}")
