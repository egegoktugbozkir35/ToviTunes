"""Inspect durable episode requirements without invoking generation or publishing."""

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import load_config
from tovitunes.persistence.db import Database
from tovitunes.pipeline.planner import Goal, load_snapshot, plan, requirements


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tovitunes")
    parser.add_argument("--config", type=Path, required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "status"):
        sub = subcommands.add_parser(command)
        sub.add_argument("episode_id")
        sub.add_argument(
            "--goal", choices=("audio", "storyboard", "render", "release"), default="render"
        )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if not config.database_path.is_file() or not config.data_root.is_dir():
        parser.error("an existing database and asset root are required")
    database = Database(config.database_path)
    store = AssetStore(config.data_root, database, initialize=False)
    goal: Goal = args.goal
    snapshot = load_snapshot(store, args.episode_id)
    result = plan(snapshot, goal)
    if args.command == "plan":
        print(json.dumps(asdict(result), sort_keys=True))
    else:
        slots = []
        for requirement in requirements(goal, snapshot.scene_ids):
            fact = snapshot.slots.get((requirement.kind, requirement.slot_key))
            slots.append(
                {
                    "requirement": requirement.key,
                    "selected_artifact_id": fact.selected_id if fact else None,
                    "candidate_count": len(fact.candidates) if fact else 0,
                }
            )
        print(
            json.dumps(
                {
                    "episode_id": snapshot.episode_id,
                    "goal": goal,
                    "scene_ids": snapshot.scene_ids,
                    "next_action": asdict(result),
                    "requirements": slots,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

