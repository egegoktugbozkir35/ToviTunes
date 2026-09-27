# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
# Adapted 2026-09-27 for ToviTunes; see docs/RENDER_DONOR_NOTICE.md.
"""Fail-closed donor ffprobe QA, narrowed to the portrait pilot contract."""

import math
from fractions import Fraction
from pathlib import Path
from typing import Any

from tovitunes.render.ffmpeg import MUX_TIMEOUT, probe, resolve_binary, run_process

DURATION_TOLERANCE = 2 / 30 + 0.005  # Two video frames plus container millisecond rounding.


def check_probe(
    data: dict[str, Any],
    duration: float,
    canvas: tuple[int, int] = (1080, 1920),
) -> dict[str, Any]:
    streams = data.get("streams", [])
    video: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "audio"), {})
    errors: list[str] = []
    try:
        fps = float(Fraction(video.get("avg_frame_rate", "0")))
        actual_duration = float(data.get("format", {}).get("duration", 0))
        video_duration = float(video.get("duration", 0))
        audio_duration = float(audio.get("duration", 0))
        sample_rate = int(audio.get("sample_rate", 0))
        channels = int(audio.get("channels", 0))
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        sar = video.get("sample_aspect_ratio", "1:1")
        aspect = width / height * float(Fraction(sar.replace(":", "/"))) if height else 0
    except (ValueError, TypeError, ZeroDivisionError):
        raise ValueError("malformed ffprobe stream metadata") from None
    checks = {
        "video_stream": bool(video),
        "audio_stream": bool(audio),
        "video_codec": video.get("codec_name") == "h264",
        "resolution": (width, height) == canvas,
        "fps": math.isfinite(fps) and abs(fps - 30) <= 0.01,
        "pixel_format": video.get("pix_fmt") == "yuv420p",
        "audio_codec": audio.get("codec_name") == "aac",
        "sample_rate": sample_rate in {32000, 44100, 48000},
        "channels": channels > 0,
        "aspect_ratio": abs(aspect - 9 / 16) <= 0.001,
        "duration": all(
            math.isfinite(d) and abs(d - duration) <= DURATION_TOLERANCE
            for d in (actual_duration, video_duration, audio_duration)
        ),
    }
    errors.extend(name for name, passed in checks.items() if not passed)
    return {
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "video_codec": video.get("codec_name"),
        "dimensions": [width, height],
        "fps": fps,
        "pixel_format": video.get("pix_fmt"),
        "duration": actual_duration,
        "video_duration": video_duration,
        "audio_duration": audio_duration,
        "audio": {
            "codec": audio.get("codec_name"),
            "sample_rate": sample_rate,
            "channels": channels,
        },
        "duration_tolerance": DURATION_TOLERANCE,
    }


def media_qa(path: Path, duration: float, canvas: tuple[int, int]) -> dict[str, Any]:
    result = check_probe(probe(path), duration, canvas)
    if result["passed"]:
        # Probe success alone does not prove all packets decode.
        run_process(
            [
                resolve_binary("ffmpeg"),
                "-v",
                "error",
                "-xerror",
                "-nostdin",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            MUX_TIMEOUT,
        )
        result["decodes_successfully"] = True
    return result
