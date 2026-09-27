"""Optional Beat This 1.1.0 adapter and explicitly provisioned local final0 asset."""

from __future__ import annotations

import importlib.metadata
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.request import urlopen

from tovitunes.music.analysis_models import AnalyzerProvenance, RhythmEvidence
from tovitunes.music.analysis_runtime import (
    RuntimeFailure,
    _hash_file,
    diagnostic,
    model_environment,
)

PACKAGE_VERSION = "1.1.0"
MODEL = "final0"
CHECKPOINT_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"


def installed_version() -> str | None:
    try:
        return importlib.metadata.version("beat-this")
    except importlib.metadata.PackageNotFoundError:
        return None


def checkpoint_path(root: Path) -> Path:
    return root.resolve() / "torch/hub/checkpoints/beat_this-final0.ckpt"


def _inventory(root: Path) -> dict[str, Any]:
    path = root / "timing-inventory.json"
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}


def require_checkpoint(root: Path) -> tuple[Path, str]:
    path = checkpoint_path(root)
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeFailure("timing_checkpoint_missing: explicitly prepare final0")
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeFailure("timing_checkpoint_outside_project_cache")
    record = _inventory(root)
    digest = _hash_file(path)
    if (
        record.get("logical_model") != MODEL
        or record.get("package_version") != PACKAGE_VERSION
        or record.get("checkpoint_path") != str(path)
        or record.get("sha256") != digest
        or record.get("byte_size") != path.stat().st_size
        or record.get("offline_load_validated") is not True
    ):
        raise RuntimeFailure("timing_checkpoint_unverified: explicitly prepare final0")
    return path, digest


def _load_detector(path: Path, device: str) -> Any:
    if installed_version() != PACKAGE_VERSION:
        raise RuntimeFailure("timing_package_missing_or_wrong_version: install audio-timing extra")
    if not path.is_file():
        raise RuntimeFailure("timing_checkpoint_missing: explicitly prepare final0")
    import torch
    from beat_this.inference import Audio2Beats

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeFailure("timing_cuda_unavailable: select cpu for diagnosis")

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeFailure("timing_implicit_download_blocked: local checkpoint required")

    # Upstream retries a failed local load as a URL/short name. Disable that path too.
    with patch.object(torch.hub, "load_state_dict_from_url", blocked):
        return Audio2Beats(
            checkpoint_path=str(path), device=device, float16=device == "cuda", dbn=False
        )


def _download(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    try:
        with urlopen(CHECKPOINT_URL, timeout=120) as response, temporary.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
        if not temporary.stat().st_size:
            raise RuntimeFailure("timing_checkpoint_empty")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_timing(
    root: Path, device: str = "cpu", *, allow_download: bool = False
) -> dict[str, Any]:
    if not allow_download:
        raise RuntimeFailure("explicit_download_permission_required: use --allow-model-download")
    try:
        if installed_version() != PACKAGE_VERSION:
            raise RuntimeFailure(
                "timing_package_missing_or_wrong_version: install audio-timing extra"
            )
        path = checkpoint_path(root)
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeFailure("timing_checkpoint_outside_project_cache")
        reused = path.is_file() and path.stat().st_size > 0
        if not reused:
            _download(path)
        digest = _hash_file(path)
        # Never refresh an inventoried asset silently if its content has changed.
        previous = _inventory(root)
        if previous and previous.get("sha256") != digest:
            raise RuntimeFailure("timing_checkpoint_hash_mismatch")
        with model_environment(root, False):
            _load_detector(path, device)
        inventory = {
            "schema_version": 1,
            "prepared_at": datetime.now(UTC).isoformat(),
            "logical_model": MODEL,
            "package_version": PACKAGE_VERSION,
            "source_url": CHECKPOINT_URL,
            "checkpoint_path": str(path),
            "sha256": digest,
            "byte_size": path.stat().st_size,
            "device": device,
            "float16": device == "cuda",
            "dbn": False,
            "action": "reused" if reused else "downloaded",
            "offline_load_validated": True,
        }
        target = root / "timing-inventory.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        temporary.replace(target)
        return {"status": "prepared", **inventory}
    except Exception as exc:
        return {"status": "failed", "failure_reason": diagnostic("timing_preparation_failed", exc)}


def timing_doctor(root: Path, device: str) -> dict[str, Any]:
    path = checkpoint_path(root)
    result: dict[str, Any] = {
        "package_version": installed_version(),
        "logical_model": MODEL,
        "checkpoint_path": str(path),
        "cached": path.is_file(),
        "checkpoint_sha256": None,
        "requested_device": device,
        "model_load_ready": False,
        "offline_timing_ready": False,
    }
    try:
        with model_environment(root, False):
            verified, digest = require_checkpoint(root)
            result["checkpoint_sha256"] = digest
            _load_detector(verified, device)
        result.update(model_load_ready=True, offline_timing_ready=True)
    except Exception as exc:
        result["failure_reason"] = diagnostic("timing_preflight_failed", exc)
    return result


def _pcm_signal(decoded: Any) -> Any:
    import numpy as np

    return np.frombuffer(decoded.samples, dtype=np.float32).reshape(-1, decoded.nchannels)


def measured_rhythm(beats: Any, downbeats: Any, duration: float, brief: Any) -> RhythmEvidence:
    beat_seconds = tuple(float(t) for t in beats)
    downbeat_seconds = tuple(float(t) for t in downbeats)
    if len(beat_seconds) < 2 or not downbeat_seconds:
        raise RuntimeFailure("timing_detector_incomplete: beats and downbeats required")
    for points in (beat_seconds, downbeat_seconds):
        if any(not math.isfinite(t) or not 0 <= t <= duration for t in points) or any(
            b <= a for a, b in zip(points, points[1:])
        ):
            raise RuntimeFailure("timing_detector_invalid: timestamps unordered or out of bounds")
    # Upstream minimal postprocessing snaps detected downbeats to its measured beats.
    if not set(downbeat_seconds).issubset(beat_seconds):
        raise RuntimeFailure("timing_detector_invalid: downbeats must belong to measured beat grid")
    intervals = [b - a for a, b in zip(beat_seconds, beat_seconds[1:])]
    mean, deviation = statistics.mean(intervals), statistics.pstdev(intervals)
    return RhythmEvidence(
        status="complete",
        estimated_bpm=60 / mean,
        beat_seconds=beat_seconds,
        downbeat_seconds=downbeat_seconds,
        beat_count=len(beat_seconds),
        mean_interval_seconds=mean,
        interval_std_seconds=deviation,
        interval_cv=deviation / mean,
        target_bpm=brief.target_bpm,
        allowed_bpm_range=brief.bpm_range,
    )


def analyze_rhythm(
    decoded: Any, brief: Any, root: Path, requested_device: str, audio_sha: str
) -> RhythmEvidence:
    device = requested_device
    try:
        with model_environment(root, False):
            path, digest = require_checkpoint(root)
            if device == "auto":
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            detector = _load_detector(path, device)
            beats, downbeats = detector(_pcm_signal(decoded), decoded.sample_rate)
            result = measured_rhythm(beats, downbeats, decoded.duration, brief)
        provenance = AnalyzerProvenance(
            name="Beat This",
            version=PACKAGE_VERSION,
            model_name=MODEL,
            model_revision="sha256:" + digest,
            device=device,
            timestamp=datetime.now(UTC).isoformat(),
            source_audio_sha256=audio_sha,
            configuration={"float16": device == "cuda", "dbn": False},
        )
        return result.model_copy(update={"provenance": provenance})
    except Exception as exc:
        return RhythmEvidence(
            status="unavailable",
            beat_count=0,
            target_bpm=brief.target_bpm,
            allowed_bpm_range=brief.bpm_range,
            failure_reason=diagnostic("timing_analysis_failed", exc),
        )
