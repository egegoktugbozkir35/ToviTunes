"""Inspect planning state and perform offline character-pack intake."""

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from tovitunes.artifacts.character_intake import ingest_prepared, prepare_assets
from tovitunes.artifacts.character_pack import assess_pack_assets
from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import load_brand
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
    character = subcommands.add_parser("character-pack")
    character_commands = character.add_subparsers(dest="character_command", required=True)
    for command in ("prepare", "ingest"):
        sub = character_commands.add_parser(command)
        sub.add_argument("--recipe", type=Path, required=True)
        sub.add_argument("--source-dir", type=Path, required=True)
        sub.add_argument("--prepared-dir", type=Path, required=True)
    character_commands.add_parser("assess")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "character-pack":
        if args.character_command == "prepare":
            report = prepare_assets(args.recipe, args.source_dir, args.prepared_dir)
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.character_command == "ingest":
            database = Database(config.database_path)
            database.migrate()
            store = AssetStore(
                config.data_root, database, generated_source_roots=[args.prepared_dir]
            )
            catalog = load_brand(config.brand_root)
            manifest = config.brand_root / catalog.characters[0].pack_file
            report = ingest_prepared(
                args.recipe, args.source_dir, args.prepared_dir, store, catalog, manifest
            )
            print(json.dumps(report, sort_keys=True))
            return 0
        if not config.database_path.is_file() or not config.data_root.is_dir():
            parser.error("an existing database and asset root are required")
        catalog = load_brand(config.brand_root)
        store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
        assessment = assess_pack_assets(catalog.packs[0], store, catalog.version.revision_id)
        print(json.dumps(asdict(assessment), sort_keys=True))
        return 0 if assessment.ready else 1
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
                    "objective_approval": snapshot.objective_approval,
                    "character_pack_readiness": snapshot.character_pack_readiness,
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
