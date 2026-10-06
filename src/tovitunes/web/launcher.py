"""One-click entry point: claim the UI port, supervise local services, open Studio."""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn

from tovitunes.config import load_config
from tovitunes.web.app import create_app
from tovitunes.web.supervisor import LocalServiceSupervisor, healthy


def resolve_config(path: Path) -> Path:
    if path.is_file():
        return path
    fallback = path.parent / "config.example.yaml"
    if path.name == "config.yaml" and fallback.is_file():
        return fallback
    raise FileNotFoundError("ToviTunes configuration is unavailable")


def open_when_ready(
    url: str,
    *,
    probe: Callable[[str], bool] = healthy,
    opener: Callable[[str], object] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    for _ in range(100):
        if probe(url + "/api/health"):
            opener(url)
            return
        sleep(0.2)


def launch(config_path: Path, port: int = 8766) -> None:
    url = f"http://127.0.0.1:{port}"
    # Claim before spawning any services. Repeated double-clicks reuse the same Studio.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            if healthy(url + "/api/health"):
                webbrowser.open(url)
                return
            raise RuntimeError("Studio port is in use by another application") from None
        listener.listen(128)
        config = load_config(resolve_config(config_path))
        supervisor = LocalServiceSupervisor(config)
        app = create_app(config, supervisor=supervisor)
        supervisor.start_background()
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
        try:
            uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port)).run(sockets=[listener])
        finally:
            app.state.jobs.close()
            supervisor.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Open ToviTunes Studio and its local services")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument(
        "--port", type=int, default=8766, choices=range(1024, 65536), metavar="PORT"
    )
    args = parser.parse_args()
    launch(args.config, args.port)


if __name__ == "__main__":
    main()
