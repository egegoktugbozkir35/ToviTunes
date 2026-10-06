"""Start the localhost operator dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from tovitunes.config import load_config
from tovitunes.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="ToviTunes local operator dashboard")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    config = load_config(args.config)
    print(f"ToviTunes: http://127.0.0.1:{args.port}")
    print(f"Config: {args.config.resolve()}")
    print(f"Database: {config.database_path}")
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
