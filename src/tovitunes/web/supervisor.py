"""Small local process supervisor. Only processes created here are owned here."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

import httpx
import yaml

from tovitunes.config import LocalServiceLaunchConfig, RuntimeConfig
from tovitunes.errors import ProductionStop

_SUPERVISORS: WeakValueDictionary[str, LocalServiceSupervisor] = WeakValueDictionary()
_REGISTRY_LOCK = threading.RLock()


class _WindowsJob:
    """Handle-based ownership of descendants; never look up or kill a reused PID."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process: Any) -> None:
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Could not establish service process ownership")

    def terminate(self) -> None:
        if not self.api.TerminateJobObject(self.handle, 1):
            raise OSError("Could not stop owned service job")

    def active(self) -> bool:
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("user", ctypes.c_int64), ("kernel", ctypes.c_int64),
                ("period_user", ctypes.c_int64), ("period_kernel", ctypes.c_int64),
                ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                ("active", wintypes.DWORD), ("terminated", wintypes.DWORD),
            ]

        value = Accounting()
        if not self.api.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None,
        ):
            raise OSError("Could not verify owned service job")
        return bool(value.active)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def healthy(url: str) -> bool:
    try:
        with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
            response = client.get(url)
            if response.status_code != 200:
                return False
            body = response.json()
            if url.endswith("/health"):
                body = body.get("data", body) if isinstance(body, dict) else {}
                return isinstance(body, dict) and body.get("status") in {"ok", "healthy", "ready"}
            return isinstance(body, dict) and (
                "system" in body
                if url.endswith("/system_stats")
                else "models" in body
                if url.endswith("/api/tags")
                else True
            )
    except (httpx.HTTPError, ValueError):
        return False


def conservative_profile(url: str) -> bool:
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
            argv = response.json()["system"]["argv"]
        return isinstance(argv, list) and {"--disable-dynamic-vram", "--lowvram"} <= set(argv)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return False


def idle_comfyui(url: str) -> bool:
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(url.removesuffix("/system_stats") + "/queue")
            response.raise_for_status()
            return bool(response.json() == {"queue_running": [], "queue_pending": []})
    except (httpx.HTTPError, ValueError):
        return False


class LocalServiceSupervisor:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        probe: Callable[[str], bool] = healthy,
        spawn: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        profile_probe: Callable[[str], bool] = conservative_profile,
        queue_probe: Callable[[str], bool] = idle_comfyui,
    ) -> None:
        self.config, self.probe, self.spawn = config, probe, spawn
        self.clock, self.sleep = clock, sleep
        self.profile_probe = profile_probe
        self.queue_probe = queue_probe
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._processes: dict[str, Any] = {}
        self._jobs: dict[str, _WindowsJob] = {}
        self._logs: dict[str, BinaryIO] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._checked = -100.0
        self._closing = threading.Event()
        self._threads: list[threading.Thread] = []
        self.urls: dict[str, str] = {}
        if config.music_generation.provider == "ace_step_local":
            self.urls["ace_step"] = config.music_generation.base_url + "/health"
        endpoints = {
            s.base_url
            for s in (config.lesson_object_generation, config.environment_generation)
            if s.provider == "qwen_comfyui"
        }
        for index, endpoint in enumerate(sorted(endpoints)):
            self.urls["comfyui" if index == 0 else "comfyui_environment"] = (
                endpoint + "/system_stats"
            )
        if config.creative_llm.fallback_to_ollama_on_endpoint_failure:
            self.urls["ollama"] = config.creative_llm.ollama.base_url + "/api/tags"
        if config.creative_topics.embedding.enabled:
            endpoint = config.creative_topics.embedding.base_url + "/api/tags"
            name = "ollama" if self.urls.get("ollama", endpoint) == endpoint else "ollama_embedding"
            self.urls[name] = endpoint
        for name in ("ace_step", "comfyui", "ollama", *self.urls):
            self._states[name] = {
                "status": "unavailable" if name in self.urls else "disabled",
                "owned": False,
            }
        # Studio and the composition root share only process ownership, not job state.
        with _REGISTRY_LOCK:
            _SUPERVISORS[str(config.database_path.resolve())] = self

    @classmethod
    def existing(cls, config: RuntimeConfig) -> LocalServiceSupervisor | None:
        with _REGISTRY_LOCK:
            supervisor = _SUPERVISORS.get(str(config.database_path.resolve()))
            if supervisor and not supervisor._closing.is_set():
                if supervisor.config != config:
                    raise ProductionStop("BLOCKED", "Service supervisor configuration differs")
                return supervisor
        return None

    def _owned(self, name: str) -> bool:
        process = self._processes.get(name)
        job = self._jobs.get(name)
        return job.active() if job else process is not None and process.poll() is None

    def _launch_config(self, name: str) -> LocalServiceLaunchConfig:
        if name.startswith("comfyui"):
            return self.config.local_services.comfyui
        if name.startswith("ollama"):
            return self.config.local_services.ollama
        return self.config.local_services.ace_step

    def discover(self, name: str) -> tuple[list[str], Path] | None:
        launch = self._launch_config(name)
        if launch.command:
            executable = shutil.which(launch.command[0])
            if executable is None or Path(executable).suffix.casefold() in {".bat", ".cmd"}:
                return None
            cwd = launch.cwd or Path(executable).parent
            if not cwd.is_dir():
                return None
            return [executable, *launch.command[1:]], cwd
        if not self.config.local_services.auto_discover:
            return None
        home = Path.home()
        port = str(urlsplit(self.urls[name]).port)
        if name == "ace_step":
            uv = shutil.which("uv")
            roots = (
                [Path(os.environ["TOVITUNES_ACE_STEP_HOME"])]
                if os.environ.get("TOVITUNES_ACE_STEP_HOME")
                else [home / "Desktop/ACE-Step-1.5", home / "ACE-Step-1.5"]
            )
            for root in roots:
                if uv and (root / "acestep/api_server.py").is_file():
                    return [
                        uv,
                        "run",
                        "--no-sync",
                        "acestep-api",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        port,
                    ], root
        elif name.startswith("comfyui"):
            roots = (
                [Path(os.environ["TOVITUNES_COMFYUI_HOME"])]
                if os.environ.get("TOVITUNES_COMFYUI_HOME")
                else [home / "Desktop/ComfyUI", home / "Documents/ComfyUI", home / "ComfyUI"]
            )
            if os.environ.get("APPDATA"):
                settings = Path(os.environ["APPDATA"]) / "ComfyUI/config.json"
                try:
                    desktop = json.loads(settings.read_text(encoding="utf-8"))
                    if isinstance(desktop, dict) and isinstance(desktop.get("basePath"), str):
                        roots.append(Path(desktop["basePath"]))
                except (OSError, ValueError):
                    pass
            if os.environ.get("LOCALAPPDATA"):
                local = Path(os.environ["LOCALAPPDATA"])
                roots.extend(
                    [
                        local / "Programs/ComfyUI/resources/ComfyUI",
                        local / "Comfy-Desktop/ComfyUI-Installs/ComfyUI/ComfyUI",
                    ]
                )
            for root in roots:
                candidates = [
                    root / ".venv/Scripts/python.exe",
                    root.parent / ".venv/Scripts/python.exe",
                    root.parent / "python_embeded/python.exe",
                ]
                python = next((p for p in candidates if p.is_file()), None)
                if python and (root / "main.py").is_file():
                    command = [
                        str(python),
                        str(root / "main.py"),
                        "--listen",
                        "127.0.0.1",
                        "--port",
                        port,
                    ]
                    # Inspect installed argument declarations; do not launch a probe process.
                    arguments = root / "comfy/cli_args.py"
                    supported = arguments.read_text(encoding="utf-8") if arguments.is_file() else ""
                    flags = ["--disable-dynamic-vram", "--lowvram"]
                    if all(flag in supported for flag in flags):
                        # Keep port last for stable operator inspection of discovered commands.
                        command[2:2] = flags
                    if os.environ.get("LOCALAPPDATA"):
                        shared = Path(os.environ["LOCALAPPDATA"]) / "Comfy-Desktop/ComfyUI-Shared"
                        if root.is_relative_to(Path(os.environ["LOCALAPPDATA"]) / "Comfy-Desktop"):
                            command.extend(["--use-split-cross-attention", "--cpu-vae"])
                            if (shared / "models").is_dir():
                                command.extend(
                                    [
                                        "--extra-model-paths-config",
                                        str(
                                            self.config.data_root
                                            / "logs/services/comfy-model-paths.yaml"
                                        ),
                                    ]
                                )
                    return command, root
        elif name.startswith("ollama"):
            executable = shutil.which("ollama")
            if executable:
                return [executable, "serve"], Path(executable).parent
        return None

    def start(self, name: str) -> None:
        with self._lifecycle_lock:
            try:
                self._start(name)
            except (OSError, ValueError):
                with self._lock:
                    self._states[name] = {
                        "status": "failed", "owned": name in self._processes,
                        "message": "Service startup failed; inspect local service logs and paths.",
                    }

    def _start(self, name: str) -> None:
        with self._lock:
            if name not in self.urls or self._closing.is_set():
                return
            if self._states[name]["status"] == "starting":
                return
            if self.probe(self.urls[name]):
                self._states[name] = {"status": "ready", "owned": self._owned(name)}
                return
            if name == "ace_step" or name.startswith("comfyui"):
                conflicts = [other for other in self.urls if other != name and (
                    other.startswith("comfyui") if name == "ace_step"
                    else other == "ace_step" or other.startswith("comfyui")
                )]
                if any(self._owned(other) or self.probe(self.urls[other]) for other in conflicts):
                    self._states[name] = {
                        "status": "blocked", "owned": False,
                        "message": "Another GPU service is resident; use the production handoff.",
                    }
                    return
            command = self.discover(name)
            if command is None:
                self._states[name] = {
                    "status": "failed",
                    "owned": False,
                    "message": "Service not found. Configure its executable and working folder.",
                }
                return
            process = self._processes.get(name)
            if process is not None and self._owned(name):
                self._states[name]["status"] = "starting"
            else:
                if name in self._logs:
                    self._logs.pop(name).close()
                old_job = self._jobs.pop(name, None)
                if old_job:
                    old_job.close()
                root = self.config.data_root / "logs/services"
                root.mkdir(parents=True, exist_ok=True)
                generated_mapping = str(root / "comfy-model-paths.yaml")
                if generated_mapping in command[0] and os.environ.get("LOCALAPPDATA"):
                    shared = Path(os.environ["LOCALAPPDATA"]) / "Comfy-Desktop/ComfyUI-Shared"
                    mapping = {
                        "tovitunes_comfy_desktop": {
                            "base_path": str(shared),
                            "is_default": True,
                            "diffusion_models": "models/diffusion_models",
                            "unet": "models/diffusion_models",
                            "text_encoders": "models/text_encoders",
                            "clip": "models/text_encoders",
                            "vae": "models/vae",
                        }
                    }
                    (root / "comfy-model-paths.yaml").write_text(
                        yaml.safe_dump(mapping), encoding="utf-8"
                    )
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
                log = (root / f"{name}-{stamp}.log").open("ab")
                self._logs[name] = log
                env = {
                    k: v
                    for k, v in os.environ.items()
                    if not any(word in k.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                }
                if name.startswith("ollama"):
                    env["OLLAMA_HOST"] = self.urls[name].removesuffix("/api/tags")
                try:
                    argv = command[0]
                    job = None
                    if os.name == "nt" and self.spawn is subprocess.Popen:
                        job = _WindowsJob()
                        self._jobs[name] = job
                        # The bootstrap cannot spawn the actual service until assigned to our job.
                        argv = [sys.executable, "-c", (
                            "import subprocess,sys; "
                            "ready=sys.stdin.buffer.readline(); "
                            "sys.exit(1) if ready != b'go\\n' else "
                            "sys.exit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL, "
                            "creationflags=subprocess.CREATE_NO_WINDOW))"
                        ), *argv]
                    process = self.spawn(
                        argv,
                        cwd=command[1],
                        env=env,
                        stdin=subprocess.PIPE if job else subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    self._processes[name] = process
                    if job:
                        try:
                            job.assign(process)
                            process.stdin.write(b"go\n")
                            process.stdin.close()
                        except OSError:
                            process.stdin.close()  # EOF exits the bootstrap without a provider.
                            raise
                except OSError:
                    log.close()
                    self._logs.pop(name, None)
                    self._states[name] = {
                        "status": "failed",
                        "owned": False,
                        "message": "Service could not start. See local service logs.",
                    }
                    return
                self._states[name] = {"status": "starting", "owned": True}
        deadline = self.clock() + self._launch_config(name).startup_timeout_seconds
        while not self._closing.is_set() and self.clock() < deadline:
            if self.probe(self.urls[name]):
                with self._lock:
                    self._states[name] = {"status": "ready", "owned": self._owned(name)}
                return
            if not self._owned(name):
                break
            self.sleep(0.5)
        with self._lock:
            self._states[name] = {
                "status": "failed",
                "owned": True,
                "message": "Service did not become ready. See local service logs.",
            }

    def start_background(self, name: str | None = None) -> None:
        # GPU services start only at a leased production boundary or an explicit retry.
        for service in [name] if name else [n for n in self.urls if n.startswith("ollama")]:
            thread = threading.Thread(target=self.start, args=(service,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def status(self, *, refresh: bool = True) -> dict[str, dict[str, Any]]:
        with self._lock:
            if refresh and self.clock() - self._checked >= 5:
                self._checked = self.clock()
                for name, url in self.urls.items():
                    if self._states[name]["status"] not in {"starting", "failed"}:
                        if self.probe(url):
                            self._states[name] = {
                                "status": "ready",
                                "owned": self._owned(name),
                            }
                        elif self._states[name]["status"] == "ready":
                            self._states[name]["status"] = "unavailable"
            return {name: dict(state) for name, state in self._states.items()}

    def _uncertain_work(self, name: str) -> bool:
        """Do not discard a task's only remaining retrieval/history service."""
        if not self.config.database_path.is_file():
            return False
        from tovitunes.artifacts.store import AssetStore
        from tovitunes.persistence.db import Database
        from tovitunes.render.environment_recovery import returned_environment_source
        from tovitunes.render.image_recovery import has_returned_image

        with closing(sqlite3.connect(
            self.config.database_path.resolve().as_uri() + "?mode=ro", uri=True,
        )) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if name == "ace_step" and "music_requests" in tables:
                return db.execute(
                    "SELECT 1 FROM music_requests WHERE status IN ('remote_started','ambiguous') "
                    "AND request_id NOT IN (SELECT request_id FROM music_receipts) LIMIT 1"
                ).fetchone() is not None
            if not name.startswith("comfyui"):
                return False
            if "production_image_receipts" in tables:
                exclusions = (
                    "AND request_id NOT IN "
                    "(SELECT request_id FROM image_native_crash_abandonments) "
                    if "image_native_crash_abandonments" in tables else ""
                )
                for row in db.execute(
                    "SELECT * FROM generation_requests WHERE kind='visual_asset' "
                    "AND status IN ('remote_started','ambiguous') " + exclusions,
                ):
                    if not has_returned_image(self.config, db, row):
                        return True
            if "environment_requests" in tables:
                store = AssetStore(self.config.data_root, Database(self.config.database_path),
                                   initialize=False)
                for row in db.execute(
                    "SELECT * FROM environment_requests "
                    "WHERE status IN ('remote_started','ambiguous')",
                ):
                    if returned_environment_source(store, row) is None:
                        return True
        return False

    def stop(self, name: str) -> bool:
        """Stop only retained handles. A live external endpoint remains an operator boundary."""
        with self._lifecycle_lock, self._lock:
            process = self._processes.get(name)
            if process is None:
                return not self.probe(self.urls[name])
            try:
                if self._uncertain_work(name) or (
                    name.startswith("comfyui") and self._owned(name)
                    and not self.queue_probe(self.urls[name])
                ):
                    raise OSError("Service has uncertain work or its idle queue is unverified")
                if name in self._jobs:
                    self._jobs[name].terminate()
                elif process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if name in self._jobs:
                    deadline = self.clock() + 5
                    while self._jobs[name].active() and self.clock() < deadline:
                        self.sleep(0.05)
                    if self._jobs[name].active():
                        raise OSError("Owned service descendants have not exited")
                if self.probe(self.urls[name]):
                    raise OSError("Service endpoint remains resident after owned process exit")
            except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired):
                try:
                    owned = self._owned(name)
                except OSError:
                    owned = False
                self._states[name] = {
                    "status": "failed", "owned": owned,
                    "message": "Service shutdown was not confirmed; inspect local service logs.",
                }
                return False
            self._processes.pop(name, None)
            job = self._jobs.pop(name, None)
            if job:
                job.close()
            log = self._logs.pop(name, None)
            if log:
                log.close()
            self._states[name] = {"status": "stopped", "owned": False}
            return True

    def prepare_gpu(
        self, name: str, *, assert_owner: Callable[[], None],
        admit_music: Callable[[], None] | None = None,
    ) -> None:
        with self._lifecycle_lock:
            assert_owner()
            if self._closing.is_set():
                raise ProductionStop("BLOCKED", "Service supervisor is shutting down")
            if name.startswith("comfyui"):
                if admit_music is None:
                    raise ProductionStop("BLOCKED", "GPU handoff requires admitted retained music")
                admit_music()
            conflicts = [n for n in self.urls if n != name and (
                n.startswith("comfyui") if name == "ace_step"
                else n == "ace_step" or n.startswith("comfyui")
            )]
            for other in conflicts:
                assert_owner()
                if not self.stop(other):
                    raise ProductionStop("BLOCKED", "GPU service could not be released safely", {
                        "service": other, "recovery_action": "inspect_service_ownership",
                    })
            assert_owner()
            self.start(name)
            assert_owner()
            if self.status(refresh=False)[name]["status"] != "ready":
                raise ProductionStop("BLOCKED", "GPU service is not ready", {"service": name})
            if name.startswith("comfyui") and not self.profile_probe(self.urls[name]):
                self._states[name] = {
                    "status": "blocked", "owned": self._owned(name),
                    "message": "Conservative ComfyUI profile is unverified; inspect its argv.",
                }
                raise ProductionStop(
                    "BLOCKED", "Conservative ComfyUI launch profile is unverified",
                    {"service": name},
                )

    def close(self) -> None:
        self._closing.set()
        for name in list(self._processes):
            self.stop(name)
