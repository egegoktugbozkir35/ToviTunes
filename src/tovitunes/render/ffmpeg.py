# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
# Adapted 2026-09-27 for ToviTunes; see docs/RENDER_DONOR_NOTICE.md.
"""Narrow donor-adapted discovery, bounded process execution and two-stage mux."""

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

ENCODE_TIMEOUT = 1800
MUX_TIMEOUT = 120
PROBE_TIMEOUT = 30


def resolve_binary(name: str) -> str:
    if name not in {"ffmpeg", "ffprobe"}:
        raise ValueError("only ffmpeg and ffprobe are supported")
    override = os.environ.get(f"TOVITUNES_{name.upper()}_BIN")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise ValueError(f"configured {name} binary is missing: {path}")
        return str(path.resolve())
    system = shutil.which(name)
    if not system:
        raise ValueError(
            f"system {name} unavailable; install it or set TOVITUNES_{name.upper()}_BIN"
        )
    return system


def run_process(argv: list[str], timeout: float) -> str:
    """Bound child lifetime; kill the owned process tree on encode deadline."""
    if timeout <= 0:
        raise ValueError("process deadline must be positive")
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            else:
                getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
            process.kill()
            process.communicate(timeout=10)
            raise TimeoutError(f"{Path(argv[0]).name} exceeded {timeout}s deadline") from exc
        if process.returncode:
            raise RuntimeError(
                f"{Path(argv[0]).name} failed (exit={process.returncode}): {stderr[-4000:]}"
            )
        return stdout


def doctor() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("ffmpeg", "ffprobe"):
        binary = resolve_binary(name)
        result[f"{name}_path"] = binary
        result[f"{name}_version"] = run_process([binary, "-version"], PROBE_TIMEOUT).splitlines()[0]
    return result


def probe(path: Path) -> dict[str, Any]:
    raw = run_process(
        [
            resolve_binary("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        PROBE_TIMEOUT,
    )
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("ffprobe returned invalid data")
    return value


def mux_command(video: Path, audio: Path, partial: Path, duration: float) -> list[str]:
    return [
        resolve_binary("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        str(duration),
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(partial),
    ]
