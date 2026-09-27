"""Operator-owned analysis assets and cache-only runtime checks; no persistence/providers."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

PACKAGES = (
    "beat-this",
    "whisperx",
    "faster-whisper",
    "ctranslate2",
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
    "huggingface-hub",
    "nltk",
    "librosa",
    "numpy",
)
ALIGNMENT_MODEL = "WAV2VEC2_ASR_BASE_960H"
ALIGNMENT_FILE = "wav2vec2_fairseq_base_ls960_asr_ls960.pth"
ASR_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
TOKENIZER_FILES = ("collocations.tab", "sent_starters.txt", "abbrev_types.txt", "ortho_context.tab")


class RuntimeFailure(RuntimeError):
    """A bounded operator diagnosis that contains no downstream exception text."""


def diagnostic(stage: str, exc: Exception) -> str:
    if isinstance(exc, RuntimeFailure):
        return str(exc)[:240]
    # Never persist arbitrary library messages: they may contain signed URLs or credentials.
    kind = re.sub(r"[^a-zA-Z0-9_]", "", type(exc).__name__)[:60]
    return f"{stage}: {kind}; run analysis-doctor and verify the project cache"[:240]


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def ffmpeg_status() -> dict[str, Any]:
    executable = shutil.which("ffmpeg")
    if not executable:
        return {"status": "not_found", "path": None, "version": None}
    try:
        result = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, timeout=10, check=False
        )
        version = result.stdout.splitlines()[0][:240] if result.stdout else None
        return {
            "status": "found" if result.returncode == 0 else "failed",
            "path": executable,
            "version": version,
        }
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "failed", "path": executable, "version": None}


def _files_present(root: Path, names: tuple[str, ...]) -> bool:
    return all((root / name).is_file() and (root / name).stat().st_size > 0 for name in names)


def asr_files(model: str) -> tuple[str, ...]:
    if model == "large-v3":
        return (*ASR_FILES[:3], "vocabulary.json", "preprocessor_config.json")
    return ASR_FILES


def asr_snapshot(root: Path, model: str) -> Path | None:
    # Resolve the project-owned HF ref without importing HF or allowing a global-cache fallback.
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", model):
        return None
    repository = root / "asr" / f"models--Systran--faster-whisper-{model}"
    reference = repository / "refs" / "main"
    if not reference.is_file():
        return None
    revision = reference.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return None
    snapshot = repository / "snapshots" / revision
    return snapshot if _files_present(snapshot, asr_files(model)) else None


def tokenizer_ready(root: Path) -> bool:
    return _files_present(root / "nltk/tokenizers/punkt_tab/english", TOKENIZER_FILES)


def vad_asset() -> Path | None:
    spec = importlib.util.find_spec("whisperx")
    if spec is None or not spec.origin:
        return None
    path = Path(spec.origin).parent / "assets/pytorch_model.bin"
    return path if path.is_file() and path.stat().st_size > 0 else None


def cache_status(root: Path, model: str) -> dict[str, Any]:
    snapshot = asr_snapshot(root, model)
    return {
        "asr": {
            "cached": snapshot is not None,
            "path": str(snapshot) if snapshot else None,
            "revision": snapshot.name if snapshot else None,
        },
        "alignment": {
            "cached": _files_present(root / "alignment", (ALIGNMENT_FILE,)),
            "model": ALIGNMENT_MODEL,
            "path": str(root / "alignment" / ALIGNMENT_FILE),
        },
        "punkt_tab": {"cached": tokenizer_ready(root), "path": str(root / "nltk")},
        "vad": {"cached": vad_asset() is not None, "source": "WhisperX bundled pyannote VAD"},
    }


def require_cache(root: Path, model: str) -> Path:
    snapshot = asr_snapshot(root, model)
    if snapshot is None:
        raise RuntimeFailure("asr_model_missing_or_incomplete: prepare the selected model")
    if not _files_present(root / "alignment", (ALIGNMENT_FILE,)):
        raise RuntimeFailure("alignment_model_missing_or_incomplete: prepare English alignment")
    if not tokenizer_ready(root):
        raise RuntimeFailure("nltk_resource_missing_or_incomplete: prepare English punkt_tab")
    if vad_asset() is None:
        raise RuntimeFailure("vad_asset_missing: reinstall the locked WhisperX package")
    return snapshot


@contextmanager
def model_environment(root: Path, allow_download: bool) -> Iterator[None]:
    """Offline flags plus an outbound socket guard, including TorchAudio's downloader.

    These libraries use process-global settings. Call in a dedicated CLI process, not
    concurrently with network work. All settings and socket methods are restored.
    """
    values = {
        "HF_HOME": str(root / "hf"),
        "HF_HUB_CACHE": str(root / "asr"),
        "TORCH_HOME": str(root / "torch"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "NLTK_DATA": str(root / "nltk"),
        "HF_HUB_OFFLINE": "0" if allow_download else "1",
        "TRANSFORMERS_OFFLINE": "0" if allow_download else "1",
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeFailure("offline_network_blocked: cached assets are required")

    try:
        if allow_download:
            yield
        else:
            with (
                patch.object(socket.socket, "connect", blocked),
                patch.object(socket.socket, "connect_ex", blocked),
                patch.object(socket, "create_connection", blocked),
            ):
                yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def tokenizer_environment(root: Path) -> Iterator[None]:
    import nltk

    previous = nltk.data.path[:]
    nltk.data.path[:] = [str(root / "nltk")]
    try:
        yield
    finally:
        nltk.data.path[:] = previous


def prepare_tokenizer(root: Path, allow_download: bool) -> None:
    if tokenizer_ready(root):
        return
    if not allow_download:
        raise RuntimeFailure("nltk_resource_missing_or_incomplete: prepare English punkt_tab")
    import nltk

    nltk.download("punkt_tab", download_dir=str(root / "nltk"), quiet=True, raise_on_error=True)
    if not tokenizer_ready(root):
        raise RuntimeFailure("nltk_provisioning_failed: English punkt_tab is incomplete")


def runtime_doctor(root: Path, model: str = "small.en", device: str = "cpu") -> dict[str, Any]:
    from tovitunes.music.timing_runtime import timing_doctor

    versions = package_versions()
    ffmpeg = ffmpeg_status()
    caches = cache_status(root, model)
    cuda_available, cuda_count, gpu_name = False, 0, None
    torch_version, torch_cuda_build, cudnn_version, gpu_total_memory = None, None, None, None
    ct2_count, ct2_compute_types = 0, []
    ct2_probe_succeeded = False
    failures = []
    try:
        import torch

        torch_version = str(torch.__version__)
        torch_cuda_build = torch.version.cuda
        cudnn_version = torch.backends.cudnn.version()
        cuda_available = bool(torch.cuda.is_available())
        cuda_count = int(torch.cuda.device_count())
        if cuda_available and cuda_count:
            gpu_name = str(torch.cuda.get_device_name(0))[:120]
            gpu_total_memory = int(torch.cuda.get_device_properties(0).total_memory)
    except Exception as exc:
        failures.append(diagnostic("device_probe_failed", exc))
    selected = "cuda" if device == "auto" and cuda_available else device
    if selected == "auto":
        selected = "cpu"
    if selected == "cuda":
        try:
            with model_environment(root, False):
                import ctranslate2

                ct2_count = int(ctranslate2.get_cuda_device_count())
                if ct2_count:
                    ct2_compute_types = sorted(ctranslate2.get_supported_compute_types("cuda", 0))
                ct2_probe_succeeded = True
        except Exception as exc:
            failures.append(diagnostic("ctranslate2_device_probe_failed", exc))
    cuda_ready = bool(
        cuda_available and cuda_count and gpu_name and torch_cuda_build and cudnn_version
        and ct2_probe_succeeded and ct2_count and "float16" in ct2_compute_types
    )
    # Import public loaders to detect broken DLLs/dependencies without loading models.
    try:
        with model_environment(root, False):
            import whisperx.alignment  # noqa: F401
            import whisperx.asr  # noqa: F401
    except Exception as exc:
        failures.append(diagnostic("optional_dependency_missing_or_broken", exc))
    ready = (
        ffmpeg["status"] == "found"
        and not failures
        and all(item["cached"] for item in caches.values())
        and (selected != "cuda" or cuda_ready)
    )
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "python_executable": sys.executable,
        "ffmpeg": ffmpeg,
        "package_versions": versions,
        "whisperx_installed": versions["whisperx"] is not None,
        "cuda_available": cuda_available,
        "torch_version": torch_version,
        "torch_cuda_build": torch_cuda_build,
        "cudnn_version": cudnn_version,
        "cuda_device_count": cuda_count,
        "gpu_name": gpu_name,
        "gpu_total_memory_bytes": gpu_total_memory,
        "cuda_device_queries_ready": cuda_ready,
        "ctranslate2": {
            "cuda_device_count": ct2_count,
            "supported_compute_types": ct2_compute_types,
            "device_probe_succeeded": ct2_probe_succeeded,
            "readiness_kind": "device queries only; cached model inference required to verify DLLs",
        },
        "selected_device": selected,
        "asr_model": model,
        "cache_root": str(root),
        "caches": caches,
        "offline_ready": ready,
        "readiness_kind": "preflight; inference validates model integrity",
        "failures": failures,
        "timing": timing_doctor(root, selected),
    }


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_models(
    root: Path, model: str = "small.en", device: str = "cpu", *, allow_download: bool = False
) -> dict[str, Any]:
    if not allow_download:
        raise RuntimeFailure("explicit_download_permission_required: use --allow-model-download")
    if model not in {"small.en", "medium.en", "large-v3"}:
        raise RuntimeFailure(
            "unsupported_preparation_model: select small.en, medium.en or large-v3"
        )
    before = cache_status(root, model)
    stage = "optional_dependency_missing_or_broken"
    try:
        with model_environment(root, True):
            import whisperx
            from faster_whisper.utils import download_model

            stage = "asr_model_download_failed"
            if not before["asr"]["cached"]:
                download_model(model, cache_dir=str(root / "asr"), use_auth_token=False)
            stage = "nltk_provisioning_failed"
            prepare_tokenizer(root, True)
            stage = "alignment_model_provisioning_failed"
            whisperx.load_align_model(
                language_code="en",
                device=device,
                model_name=ALIGNMENT_MODEL,
                model_dir=str(root / "alignment"),
                model_cache_only=before["alignment"]["cached"],
            )
        stage = "cache_validation_failed"
        snapshot = require_cache(root, model)
        # Validate all loaders without downloads, including the bundled VAD. No audio inference.
        with model_environment(root, False), tokenizer_environment(root):
            stage = "asr_cache_corrupt_or_device_failed"
            whisperx.load_model(
                str(snapshot),
                device,
                compute_type="int8" if device == "cpu" else "float16",
                language="en",
                local_files_only=True,
                download_root=str(root / "asr"),
                use_auth_token=False,
            )
        stage = "inventory_failed"
        inventory_path = root / "inventory.json"
        previous = json.loads(inventory_path.read_text()) if inventory_path.is_file() else {}
        paths = {
            "asr": list(snapshot / name for name in asr_files(model)),
            "alignment": [root / "alignment" / ALIGNMENT_FILE],
            "punkt_tab": list(
                root / "nltk/tokenizers/punkt_tab/english" / n for n in TOKENIZER_FILES
            ),
            "vad": [asset] if (asset := vad_asset()) else [],
        }
        assets = []
        for name, files in paths.items():
            old: dict[str, Any] = next(
                (a for a in previous.get("assets", []) if a["asset"] == name), {}
            )
            reuse = before[name]["cached"]
            hashes = old.get("files") if reuse else None
            if (
                not hashes
                or {record["path"] for record in hashes} != {str(p) for p in files}
                or any(
                    record.get("size") != Path(record["path"]).stat().st_size for record in hashes
                )
            ):
                hashes = [
                    {"path": str(p), "size": p.stat().st_size, "sha256": _hash_file(p)}
                    for p in files
                ]
            assets.append(
                {
                    "asset": name,
                    "logical_name": model
                    if name == "asr"
                    else ALIGNMENT_MODEL
                    if name == "alignment"
                    else "English punkt_tab"
                    if name == "punkt_tab"
                    else "WhisperX bundled pyannote VAD",
                    "source_family": {
                        "asr": "Systran/Hugging Face",
                        "alignment": "TorchAudio",
                        "punkt_tab": "NLTK",
                        "vad": "WhisperX package",
                    }[name],
                    "revision": snapshot.name if name == "asr" else None,
                    "action": "reused" if reuse else "downloaded",
                    "files": hashes,
                }
            )
        inventory = {
            "schema_version": 1,
            "prepared_at": datetime.now(UTC).isoformat(),
            "package_versions": package_versions(),
            "cache_root": str(root),
            "device": device,
            "assets": assets,
            "offline_load_validated": True,
        }
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / "inventory.json.tmp"
        temporary.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        temporary.replace(inventory_path)
        return {"status": "prepared", "inventory_path": str(inventory_path), **inventory}
    except Exception as exc:
        return {
            "status": "failed",
            "failure_reason": diagnostic(stage, exc),
            "cache_root": str(root),
        }
