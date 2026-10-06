"""Small local process supervisor. Only processes created here are owned here."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

import httpx

from tovitunes.config import LocalServiceLaunchConfig, RuntimeConfig


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


class LocalServiceSupervisor:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        probe: Callable[[str], bool] = healthy,
        spawn: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config, self.probe, self.spawn = config, probe, spawn
        self.clock, self.sleep = clock, sleep
        self._lock = threading.RLock()
        self._processes: dict[str, Any] = {}
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
                    return [
                        str(python),
                        str(root / "main.py"),
                        "--listen",
                        "127.0.0.1",
                        "--port",
                        port,
                    ], root
        elif name.startswith("ollama"):
            executable = shutil.which("ollama")
            if executable:
                return [executable, "serve"], Path(executable).parent
        return None

    def start(self, name: str) -> None:
        with self._lock:
            if name not in self.urls or self._closing.is_set():
                return
            if self._states[name]["status"] == "starting":
                return
            if self.probe(self.urls[name]):
                self._states[name] = {"status": "ready", "owned": name in self._processes}
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
            if process is not None and process.poll() is None:
                self._states[name]["status"] = "starting"
            else:
                if name in self._logs:
                    self._logs.pop(name).close()
                root = self.config.data_root / "logs/services"
                root.mkdir(parents=True, exist_ok=True)
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
                    self._processes[name] = self.spawn(
                        command[0],
                        cwd=command[1],
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
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
                    self._states[name] = {"status": "ready", "owned": True}
                return
            if self._processes[name].poll() is not None:
                break
            self.sleep(0.5)
        with self._lock:
            self._states[name] = {
                "status": "failed",
                "owned": True,
                "message": "Service did not become ready. See local service logs.",
            }

    def start_background(self, name: str | None = None) -> None:
        for service in [name] if name else self.urls:
            thread = threading.Thread(target=self.start, args=(service,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def status(self, *, refresh: bool = True) -> dict[str, dict[str, Any]]:
        with self._lock:
            if refresh and self.clock() - self._checked >= 5:
                self._checked = self.clock()
                for name, url in self.urls.items():
                    if self._states[name]["status"] != "starting":
                        if self.probe(url):
                            self._states[name] = {
                                "status": "ready",
                                "owned": name in self._processes,
                            }
                        elif self._states[name]["status"] == "ready":
                            self._states[name]["status"] = "unavailable"
            return {name: dict(state) for name, state in self._states.items()}

    def close(self) -> None:
        self._closing.set()
        with self._lock:
            for process in self._processes.values():
                if process.poll() is None:
                    # Terminate an owned Windows process tree, including uv's Python child.
                    if os.name == "nt" and isinstance(process, subprocess.Popen):
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            for log in self._logs.values():
                log.close()
