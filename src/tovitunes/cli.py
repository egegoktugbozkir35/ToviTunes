"""Inspect planning state and perform offline character-pack intake."""

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from tovitunes.artifacts.character_intake import ingest_prepared, load_recipe, prepare_assets
from tovitunes.artifacts.character_lock import (
    export_pack_lock,
    load_lock,
    rehydrate_pack,
    validate_pack_lock,
)
from tovitunes.artifacts.character_pack import approve_pack_manifest, assess_pack_assets
from tovitunes.artifacts.store import AssetStore
from tovitunes.benchmark.models import load_benchmark, load_rubric, load_scorecard
from tovitunes.benchmark.persistence import BenchmarkStore
from tovitunes.benchmark.providers import GeminiImageProvider, ImageProvider, OpenAIImageProvider
from tovitunes.benchmark.runner import (
    BenchmarkRunner,
    aggregate,
    blind_review_queue,
    plan_requests,
)
from tovitunes.catalog import load_brand
from tovitunes.config import load_config
from tovitunes.music.benchmark import MusicBenchmark
from tovitunes.music.benchmark import plan as plan_music
from tovitunes.music.models import MusicReview, TimingAnalysis, load_brief, load_lyrics
from tovitunes.music.models import load_rubric as load_music_rubric
from tovitunes.music.providers import FakeMusicProvider
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
    for command in ("prepare", "ingest", "rehydrate"):
        sub = character_commands.add_parser(command)
        sub.add_argument("--recipe", type=Path, required=True)
        sub.add_argument("--source-dir", type=Path, required=True)
        sub.add_argument("--prepared-dir", type=Path, required=True)
        if command == "rehydrate":
            sub.add_argument("--lock", type=Path, required=True)
    for command in ("validate-lock", "export-lock"):
        sub = character_commands.add_parser(command)
        sub.add_argument("--recipe", type=Path, required=True)
        sub.add_argument("--lock", type=Path, required=True)
    character_commands.add_parser("approve")
    character_commands.add_parser("assess")
    visual = subcommands.add_parser("visual-benchmark")
    music = subcommands.add_parser("music-benchmark")
    music_commands = music.add_subparsers(dest="music_command", required=True)
    music_commands.add_parser("plan")
    music_run = music_commands.add_parser("run")
    music_run.add_argument("--offline-fake", action="store_true", required=True)
    music_commands.add_parser("status")
    music_commands.add_parser("review-export")
    music_commands.add_parser("review-report")
    policy_evaluate = music_commands.add_parser("policy-evaluate")
    policy_evaluate.add_argument(
        "--type", choices=("lyrics", "rights", "qa", "approval", "timing"), required=True
    )
    policy_evaluate.add_argument("--blind-id")
    policy_evaluate.add_argument("--brief-file", type=Path)
    policy_evaluate.add_argument("--lyrics-file", type=Path)
    policy_evaluate.add_argument("--evidence-file", type=Path)
    policy_evaluate.add_argument("--version", type=int)
    policy_status = music_commands.add_parser("policy-status")
    policy_status.add_argument("--blind-id")
    music_reconcile = music_commands.add_parser("reconcile")
    music_reconcile.add_argument("--request-id", required=True)
    music_review = music_commands.add_parser("review")
    music_review.add_argument("--scorecard", type=Path, required=True)
    lyric_decision = music_commands.add_parser("lyric-decision")
    lyric_decision.add_argument("--lyrics-file", type=Path, required=True)
    lyric_decision.add_argument("--status", choices=("approved", "rejected"), required=True)
    lyric_decision.add_argument("--actor", required=True)
    lyric_decision.add_argument("--evidence", required=True)
    music_decision = music_commands.add_parser("decision")
    music_decision.add_argument("--blind-id", required=True)
    music_decision.add_argument("--type", choices=("rights", "approval"), required=True)
    music_decision.add_argument("--status", required=True)
    music_decision.add_argument("--actor", required=True)
    music_decision.add_argument("--evidence", required=True)
    music_timing = music_commands.add_parser("timing-import")
    music_timing.add_argument("--blind-id", required=True)
    music_timing.add_argument("--file", type=Path, required=True)
    timing_decision = music_commands.add_parser("timing-decision")
    timing_decision.add_argument("--blind-id", required=True)
    timing_decision.add_argument("--version", type=int, required=True)
    timing_decision.add_argument("--status", choices=("approved", "rejected"), required=True)
    timing_decision.add_argument("--actor", required=True)
    timing_decision.add_argument("--evidence", required=True)
    visual_commands = visual.add_subparsers(dest="visual_command", required=True)
    run = visual_commands.add_parser("run")
    run.add_argument("--provider", action="append", choices=("google", "openai"))
    run.add_argument("--case", action="append", dest="cases")
    run.add_argument("--attempts", type=int)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--google-model", default="gemini-3.1-flash-image")
    run.add_argument("--openai-model", default="gpt-image-2.5-sunburst")
    run.add_argument("--cases-file", type=Path)
    run.add_argument("--pack", type=Path)
    run.add_argument("--lock", type=Path)
    status_parser = visual_commands.add_parser("status")
    status_parser.add_argument("--blind", action="store_true")
    reconcile_parser = visual_commands.add_parser("reconcile")
    reconcile_parser.add_argument("--request-id", required=True)
    review_parser = visual_commands.add_parser("review")
    review_parser.add_argument("--scorecard", type=Path, required=True)
    report_parser = visual_commands.add_parser("report")
    report_parser.add_argument("--rubric", type=Path)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "music-benchmark":
        root = config.brand_root.parent.parent
        if args.music_command in {"plan", "run"}:
            brief = load_brief(root / "benchmarks/music/colors_red_v1.yaml")
            lyrics = load_lyrics(root / "benchmarks/music/colors_red_lyrics_v1.yaml")
            fake = FakeMusicProvider()
            music_plans = plan_music(brief, lyrics, [fake])
            if args.music_command == "plan":
                print(
                    json.dumps(
                        {
                            "dry_run": True,
                            "live_calls": 0,
                            "planned_request_count": len(music_plans),
                            "requests": [p.model_dump(mode="json") for p in music_plans],
                        },
                        sort_keys=True,
                    )
                )
                return 0
            database = Database(config.database_path)
            database.migrate()
            benchmark = MusicBenchmark(database, config.data_root / "music-benchmark")
            results = [benchmark.run(item, fake) for item in music_plans]
            print(json.dumps({"offline_fake": True, "results": results}, sort_keys=True))
            return 0 if all(r["status"] == "succeeded" for r in results) else 1
        if not config.database_path.is_file():
            parser.error("an existing music benchmark database is required")
        database = Database(config.database_path)
        database.migrate()
        benchmark = MusicBenchmark(database, config.data_root / "music-benchmark")
        if args.music_command == "status":
            print(json.dumps(benchmark.status(), sort_keys=True))
        elif args.music_command == "review-export":
            print(json.dumps(benchmark.review_export(), sort_keys=True))
        elif args.music_command == "review":
            review = MusicReview.model_validate_json(args.scorecard.read_text(encoding="utf-8"))
            print(json.dumps({"review_id": benchmark.review(review)}, sort_keys=True))
        elif args.music_command == "review-report":
            rubric = load_music_rubric(root / "benchmarks/music/rubric.v1.yaml")
            print(json.dumps(benchmark.review_report(rubric), sort_keys=True))
        elif args.music_command == "policy-status":
            print(json.dumps(benchmark.policy_status(args.blind_id), sort_keys=True))
        elif args.music_command == "policy-evaluate":
            if args.type == "lyrics":
                if args.brief_file is None or args.lyrics_file is None:
                    parser.error("lyrics policy requires --brief-file and --lyrics-file")
                evaluation = benchmark.evaluate_lyrics(
                    load_brief(args.brief_file), load_lyrics(args.lyrics_file)
                )
            else:
                if args.blind_id is None:
                    parser.error("policy requires --blind-id")
                if args.type in {"rights", "qa"}:
                    if args.evidence_file is None:
                        parser.error("rights and QA policy require --evidence-file")
                    evidence = json.loads(args.evidence_file.read_text(encoding="utf-8"))
                    evaluation = (
                        benchmark.evaluate_rights(args.blind_id, evidence)
                        if args.type == "rights"
                        else benchmark.evaluate_qa(args.blind_id, evidence)
                    )
                elif args.type == "timing":
                    if args.version is None:
                        parser.error("timing policy requires --version")
                    evaluation = benchmark.evaluate_timing(args.blind_id, args.version)
                else:
                    evaluation = benchmark.evaluate_approval(args.blind_id)
            print(json.dumps(evaluation, sort_keys=True))
        elif args.music_command == "reconcile":
            print(json.dumps(benchmark.reconcile(args.request_id), sort_keys=True))
        elif args.music_command == "lyric-decision":
            lyrics = load_lyrics(args.lyrics_file)
            decision_id = benchmark.lyric_decision(lyrics, args.status, args.actor, args.evidence)
            print(json.dumps({"decision_id": decision_id}, sort_keys=True))
        elif args.music_command == "decision":
            decision_id = benchmark.decision(
                args.blind_id, args.type, args.status, args.actor, args.evidence
            )
            print(json.dumps({"decision_id": decision_id}, sort_keys=True))
        elif args.music_command == "timing-decision":
            decision_id = benchmark.timing_decision(
                args.blind_id, args.version, args.status, args.actor, args.evidence
            )
            print(json.dumps({"decision_id": decision_id}, sort_keys=True))
        else:
            timing = TimingAnalysis.model_validate_json(args.file.read_text(encoding="utf-8"))
            benchmark.save_timing(args.blind_id, timing)
            print(
                json.dumps({"blind_id": args.blind_id, "version": timing.version}, sort_keys=True)
            )
        return 0
    if args.command == "visual-benchmark":
        repository_root = config.brand_root.parent.parent
        if args.visual_command == "run":
            cases_file = args.cases_file or repository_root / "benchmarks/visual/cases.v1.yaml"
            pack_path = args.pack or config.brand_root / "characters/tovi/packs/v1/pack.yaml"
            lock_path = args.lock or pack_path.with_name("artifact-lock.yaml")
            provider_names = args.provider or ["google", "openai"]
            providers: list[ImageProvider] = []
            if "google" in provider_names:
                providers.append(GeminiImageProvider(args.google_model))
            if "openai" in provider_names:
                providers.append(OpenAIImageProvider(args.openai_model))
            plans = plan_requests(
                load_benchmark(cases_file),
                providers,
                pack_path=pack_path,
                lock_path=lock_path,
                case_ids=set(args.cases) if args.cases else None,
                attempts=args.attempts,
            )
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "dry_run": True,
                            "planned_request_count": len(plans),
                            "requests": [item.model_dump(mode="json") for item in plans],
                        },
                        sort_keys=True,
                    )
                )
                return 0
            missing_keys = [
                "OPENAI_API_KEY"
                for name in provider_names
                if name == "openai" and not os.environ.get("OPENAI_API_KEY")
            ]
            if missing_keys:
                parser.error(
                    "live visual benchmark requires environment variables: "
                    + ", ".join(missing_keys)
                )
            database = Database(config.database_path)
            database.migrate()
            database.register_catalog(load_brand(config.brand_root))
            returned_root = config.data_root / ".benchmark-returned"
            returned_root.mkdir(parents=True, exist_ok=True)
            assets = AssetStore(config.data_root, database, generated_source_roots=[returned_root])
            runner = BenchmarkRunner(BenchmarkStore(database), assets, returned_root)
            adapters = {(item.provider, item.model): item for item in providers}
            results = [runner.run(item, adapters[(item.provider, item.model)]) for item in plans]
            print(json.dumps({"results": results}, sort_keys=True))
            return 0 if all(item["status"] == "succeeded" for item in results) else 1
        if not config.database_path.is_file():
            parser.error("an existing benchmark database is required")
        benchmark_database = Database(config.database_path)
        benchmark_database.migrate()
        state = BenchmarkStore(benchmark_database)
        if args.visual_command == "reconcile":
            returned_root = config.data_root / ".benchmark-returned"
            returned_root.mkdir(parents=True, exist_ok=True)
            assets = AssetStore(
                config.data_root, benchmark_database, generated_source_roots=[returned_root]
            )
            reconciliation_result = BenchmarkRunner(state, assets, returned_root).reconcile(
                args.request_id
            )
            print(json.dumps(reconciliation_result, sort_keys=True))
            return 0 if reconciliation_result["status"] == "succeeded" else 1
        if args.visual_command == "status":
            rows = state.requests()
            output = blind_review_queue(rows) if args.blind else rows
            print(json.dumps(output, sort_keys=True))
            return 0
        if args.visual_command == "review":
            scorecard = load_scorecard(args.scorecard)
            review_id = state.record_review(scorecard)
            print(
                json.dumps({"blind_id": scorecard.blind_id, "review_id": review_id}, sort_keys=True)
            )
            return 0
        rubric_path = args.rubric or repository_root / "benchmarks/visual/rubric.v1.yaml"
        print(json.dumps(aggregate(state, load_rubric(rubric_path)), sort_keys=True))
        return 0
    if args.command == "character-pack":
        if args.character_command == "validate-lock":
            catalog = load_brand(config.brand_root)
            lock = load_lock(args.lock)
            validate_pack_lock(lock, catalog, load_recipe(args.recipe), args.recipe)
            print(
                json.dumps({"valid": True, "artifact_count": len(lock.artifacts)}, sort_keys=True)
            )
            return 0
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
        if args.character_command == "rehydrate":
            database = Database(config.database_path)
            database.migrate()
            store = AssetStore(
                config.data_root, database, generated_source_roots=[args.prepared_dir]
            )
            catalog = load_brand(config.brand_root)
            report = rehydrate_pack(
                args.recipe, args.lock, args.source_dir, args.prepared_dir, store, catalog
            )
            print(json.dumps(report, sort_keys=True))
            return 0
        if not config.database_path.is_file() or not config.data_root.is_dir():
            parser.error("an existing database and asset root are required")
        catalog = load_brand(config.brand_root)
        store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
        if args.character_command == "export-lock":
            lock = export_pack_lock(args.recipe, store, catalog, args.lock)
            print(json.dumps({"artifact_count": len(lock.artifacts)}, sort_keys=True))
            return 0
        if args.character_command == "approve":
            manifest = config.brand_root / catalog.characters[0].pack_file
            assessment = approve_pack_manifest(manifest, catalog, store)
            print(json.dumps(asdict(assessment), sort_keys=True))
            return 0
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
