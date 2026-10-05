"""Inspect planning state and perform offline character-pack intake."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
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
from tovitunes.benchmark.providers import (
    GeminiImageProvider,
    ImageProvider,
    OpenAIImageProvider,
    ProviderFailure,
    QwenComfyUIImageProvider,
)
from tovitunes.benchmark.runner import (
    BenchmarkRunner,
    aggregate,
    blind_review_queue,
    plan_requests,
)
from tovitunes.catalog import load_brand
from tovitunes.config import load_config
from tovitunes.creative.metadata import MetadataWriter
from tovitunes.creative.nvidia import NvidiaNIMClient
from tovitunes.creative.provider import DurableStructuredGenerator
from tovitunes.creative.workflow import CreativeWorkflow, call_report, call_snapshot, eligibility
from tovitunes.music.ace_step import AceStepLocalProvider
from tovitunes.music.analysis import AnalysisConfig
from tovitunes.music.analysis_runtime import prepare_models, runtime_doctor
from tovitunes.music.benchmark import MusicBenchmark
from tovitunes.music.benchmark import plan as plan_music
from tovitunes.music.models import MusicReview, TimingAnalysis, load_brief, load_lyrics
from tovitunes.music.models import load_rubric as load_music_rubric
from tovitunes.music.providers import FakeMusicProvider, MusicProvider
from tovitunes.music.timing_runtime import prepare_timing
from tovitunes.music.vertex_lyria import VertexLyriaProvider
from tovitunes.persistence.db import Database
from tovitunes.pipeline.planner import Goal, load_snapshot, plan, requirements
from tovitunes.pipeline.production import ProductionHandoff, plan_handoff


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tovitunes")
    parser.add_argument("--config", type=Path, required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    local_generation = subcommands.add_parser("local-generation")
    local_generation.add_subparsers(dest="local_generation_command", required=True).add_parser(
        "doctor"
    )
    creative = subcommands.add_parser("creative")
    creative_commands = creative.add_subparsers(dest="creative_command", required=True)
    creative_commands.add_parser("eligible")
    creative_commands.add_parser("doctor")
    creative_generate = creative_commands.add_parser("generate-next")
    creative_resume = creative_generate.add_mutually_exclusive_group()
    creative_resume.add_argument("--run-id")
    creative_resume.add_argument("--episode-key")
    creative_generate.add_argument(
        "--live",
        action="store_true",
        help="explicit live smoke run (normal generation is also live)",
    )
    creative_metadata = creative_commands.add_parser("metadata")
    creative_metadata.add_argument("--episode-key", required=True)
    production = subcommands.add_parser("production")
    production_commands = production.add_subparsers(dest="production_command", required=True)
    storyboard = production_commands.add_parser("prepare-storyboard")
    storyboard.add_argument("--concept", required=True)
    storyboard.add_argument("--episode-key", required=True)
    storyboard.add_argument("--music-blind-id", required=True)
    storyboard.add_argument("--analysis-version", type=int, required=True)
    storyboard.add_argument("--dry-run", action="store_true")
    render = production_commands.add_parser("render")
    render.add_argument("--episode-key", required=True)
    render_v4 = production_commands.add_parser("render-v4")
    render_v4.add_argument("--episode-key", required=True)
    environment = subcommands.add_parser("environment")
    environment_commands = environment.add_subparsers(dest="environment_command", required=True)
    env_plan = environment_commands.add_parser("plan")
    env_plan.add_argument("--theme", default="preschool-world-v1")
    env_plan.add_argument("--attempt", type=int, default=1)
    env_generate = environment_commands.add_parser("generate-set")
    env_generate.add_argument("--theme", default="preschool-world-v1")
    env_generate.add_argument("--attempt", type=int, default=1)
    env_generate.add_argument("--confirm-provider-generation", action="store_true")
    env_contact = environment_commands.add_parser("contact-sheet")
    env_contact.add_argument("--set-artifact-id", required=True)
    env_contact.add_argument("--output", type=Path, required=True)
    env_compare = environment_commands.add_parser("compare")
    env_compare.add_argument("--flash-set-artifact-id", required=True)
    env_compare.add_argument("--pro-set-artifact-id", required=True)
    env_compare.add_argument("--output", type=Path, required=True)
    for command in ("inspect", "approve", "reject", "select"):
        sub = environment_commands.add_parser(command)
        sub.add_argument("--set-artifact-id", required=True)
        if command in {"approve", "reject"}:
            sub.add_argument("--actor", required=True)
            sub.add_argument("--reason", required=True)
    lesson_objects = subcommands.add_parser("lesson-objects")
    lesson_object_commands = lesson_objects.add_subparsers(
        dest="lesson_object_command", required=True
    )
    lesson_object_commands.add_parser("plan")
    lesson_generate = lesson_object_commands.add_parser("generate")
    lesson_generate.add_argument("--confirm-provider-generation", action="store_true")
    lesson_generate.add_argument(
        "--output", type=Path, default=Path("outputs/PROP_ART_V2_REVIEW.png")
    )
    lesson_candidates = lesson_object_commands.add_parser("generate-candidates")
    lesson_candidates.add_argument("--confirm-provider-generation", action="store_true")
    lesson_candidates.add_argument("--candidates-per-object", type=int, default=4)
    lesson_candidates.add_argument(
        "--output", type=Path, default=Path("outputs/PROP_ART_V2_REVIEW.png")
    )
    lesson_finalize = lesson_object_commands.add_parser("finalize-review")
    lesson_finalize.add_argument("--apple-candidate-id", required=True)
    lesson_finalize.add_argument("--ball-candidate-id", required=True)
    lesson_finalize.add_argument("--actor", required=True)
    lesson_finalize.add_argument("--reason", required=True)
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
    music_plan = music_commands.add_parser("plan")
    music_plan.add_argument(
        "--provider", choices=("offline_fake", "google"), default="offline_fake"
    )
    music_plan.add_argument("--attempt", type=int)
    music_run = music_commands.add_parser("run")
    music_run.add_argument("--offline-fake", action="store_true")
    music_run.add_argument("--provider", choices=("google",))
    music_run.add_argument("--dry-run", action="store_true")
    music_run.add_argument("--attempt", type=int)
    music_spec = music_commands.add_parser("run-spec")
    music_spec.add_argument("--brief-file", type=Path, required=True)
    music_spec.add_argument("--lyrics-file", type=Path, required=True)
    music_spec.add_argument("--attempt", type=int, default=1)
    music_spec.add_argument("--confirm-provider-generation", action="store_true")
    music_provider_resume = music_commands.add_parser("provider-resume")
    music_provider_resume.add_argument("--request-id", required=True)
    audio_analysis = music_commands.add_parser("analyze-audio")
    audio_analysis.add_argument("--blind-id", required=True)
    audio_analysis.add_argument("--analysis-version", type=int, default=1)
    audio_analysis.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    audio_analysis.add_argument("--asr-model", default="small.en")
    audio_analysis.add_argument("--allow-model-download", action="store_true")
    doctor = music_commands.add_parser("analysis-doctor")
    doctor.add_argument("--asr-model", default="small.en")
    doctor.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    models = music_commands.add_parser("analysis-models")
    model_commands = models.add_subparsers(dest="model_command", required=True)
    prepare = model_commands.add_parser("prepare")
    prepare.add_argument(
        "--asr-model", choices=("small.en", "medium.en", "large-v3"), default="small.en"
    )
    prepare.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    prepare.add_argument("--allow-model-download", action="store_true", required=True)
    prepare_timing_parser = model_commands.add_parser("prepare-timing")
    prepare_timing_parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    prepare_timing_parser.add_argument("--allow-model-download", action="store_true", required=True)
    music_commands.add_parser("status")
    music_commands.add_parser("review-export")
    music_commands.add_parser("review-report")
    policy_evaluate = music_commands.add_parser("policy-evaluate")
    policy_evaluate.add_argument(
        "--type",
        choices=("lyrics", "rights", "qa", "qa-analysis", "approval", "timing"),
        required=True,
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
    if args.command == "lesson-objects":
        from tovitunes.render.lesson_objects import (
            contact_sheet,
            finalize_review,
            generate,
            generate_candidates,
        )
        from tovitunes.render.lesson_objects import plan as plan_lesson_objects

        try:
            if args.lesson_object_command == "plan":
                lesson_result = plan_lesson_objects(config)
            elif args.lesson_object_command == "finalize-review":
                lesson_result = finalize_review(
                    config,
                    apple_candidate_id=args.apple_candidate_id,
                    ball_candidate_id=args.ball_candidate_id,
                    actor=args.actor,
                    reason=args.reason,
                )
            elif args.lesson_object_command == "generate-candidates":
                lesson_result = generate_candidates(
                    config,
                    confirmed=args.confirm_provider_generation,
                    candidates_per_object=args.candidates_per_object,
                )
                lesson_result["contact_sheet"] = str(
                    contact_sheet(config, lesson_result, args.output.resolve())
                )
            else:
                lesson_result = generate(config, confirmed=args.confirm_provider_generation)
                lesson_result["contact_sheet"] = str(
                    contact_sheet(config, lesson_result, args.output.resolve())
                )
        except ProviderFailure as exc:
            parser.error(
                json.dumps(
                    {
                        "error": str(exc),
                        "outcome": exc.outcome,
                        "provider_request_id": exc.provider_request_id,
                        "diagnostics": exc.diagnostics,
                    },
                    sort_keys=True,
                )
            )
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            parser.error(str(exc))
        print(json.dumps(lesson_result, sort_keys=True))
        return 0
    if args.command == "local-generation":
        status: dict[str, object] = {}
        for name, generation in (
            ("environment", config.environment_generation),
            ("lesson_object", config.lesson_object_generation),
        ):
            if generation.provider == "qwen_comfyui":
                status[name] = QwenComfyUIImageProvider(
                    generation.workflow_path,
                    base_url=generation.base_url,
                    purpose="environment" if name == "environment" else "lesson_object",
                ).health()
            else:
                status[name] = {"status": "legacy_provider", "provider": generation.provider}
        status["music"] = (
            AceStepLocalProvider(config.music_generation).health()
            if config.music_generation.provider == "ace_step_local"
            else {"status": "legacy_provider", "provider": config.music_generation.provider}
        )
        print(json.dumps(status, sort_keys=True))
        return 0
    if args.command == "environment":
        from tovitunes.render.environment_sets import (
            comparison_sheet,
            decide_set,
            export_contact_sheet,
            generate_set,
            inspect_set,
            select_set,
        )
        from tovitunes.render.environment_sets import (
            plan as plan_environment,
        )

        try:
            if args.environment_command == "plan":
                environment_result = plan_environment(
                    config, theme=args.theme, attempt=args.attempt
                )
            elif args.environment_command == "generate-set":
                environment_result = generate_set(
                    config,
                    confirmed=args.confirm_provider_generation,
                    theme=args.theme,
                    attempt=args.attempt,
                )
            elif args.environment_command == "inspect":
                environment_result = inspect_set(config, args.set_artifact_id)
            elif args.environment_command == "contact-sheet":
                environment_result = {
                    "contact_sheet": str(
                        export_contact_sheet(config, args.set_artifact_id, args.output)
                    )
                }
            elif args.environment_command == "compare":
                environment_result = {
                    "comparison_sheet": str(
                        comparison_sheet(
                            config,
                            args.flash_set_artifact_id,
                            args.pro_set_artifact_id,
                            args.output,
                        )
                    )
                }
            elif args.environment_command in {"approve", "reject"}:
                environment_result = decide_set(
                    config,
                    args.set_artifact_id,
                    actor=args.actor,
                    reason=args.reason,
                    status="approved" if args.environment_command == "approve" else "rejected",
                )
            else:
                environment_result = select_set(config, args.set_artifact_id)
        except (ValueError, KeyError, OSError, RuntimeError, ProviderFailure) as exc:
            parser.error(str(exc))
        print(json.dumps(environment_result, sort_keys=True))
        return 0
    if args.command == "creative":
        database = Database(config.database_path)
        database.migrate()
        catalog = load_brand(config.brand_root)
        if args.creative_command == "eligible":
            print(json.dumps(eligibility(database, catalog), sort_keys=True))
            return 0
        if args.creative_command == "doctor":
            print(
                json.dumps(
                    {
                        "provider": config.creative_llm.provider,
                        "model": config.creative_llm.model,
                        "timeout_seconds": config.creative_llm.timeout_seconds,
                        "key_configured": bool(
                            os.getenv(config.creative_llm.api_key_env, "").strip()
                        ),
                        "provider_calls": dict.fromkeys(
                            (
                                "subject",
                                "episode_spec",
                                "lyrics",
                                "music_spec",
                                "metadata",
                                "repair",
                            ),
                            0,
                        ),
                    },
                    sort_keys=True,
                )
            )
            return 0
        before = call_snapshot(database)
        transport = NvidiaNIMClient(config.creative_llm)
        try:
            workflow = CreativeWorkflow(
                config, DurableStructuredGenerator(database, transport), catalog=catalog
            )
            if args.creative_command == "metadata":
                creative_result = MetadataWriter(workflow).generate(args.episode_key)
            else:
                creative_result = workflow.generate_next(
                    run_id=args.run_id, episode_key=args.episode_key
                )
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            print(
                json.dumps({"provider_calls": call_report(database, before)}, sort_keys=True),
                file=sys.stderr,
            )
            parser.error(str(exc))
        finally:
            transport.close()
        print(json.dumps(creative_result, sort_keys=True))
        return 0
    if args.command == "production":
        try:
            if args.production_command in {"render", "render-v4"}:
                from tovitunes.render.production import ProductionRenderer

                render_result = ProductionRenderer(config).render(
                    args.episode_key, visual_story=args.production_command == "render-v4"
                )
                print(json.dumps(render_result, sort_keys=True))
                return 0
            inputs = (args.concept, args.episode_key, args.music_blind_id, args.analysis_version)
            handoff_result = (
                plan_handoff(config, *inputs).report()
                if args.dry_run
                else ProductionHandoff(config).prepare(*inputs)
            )
        except (ValueError, KeyError, OSError, RuntimeError, TimeoutError) as exc:
            parser.error(str(exc))
        print(json.dumps(handoff_result, sort_keys=True))
        return 0
    if args.command == "music-benchmark":
        if args.music_command == "run-spec":
            if not args.confirm_provider_generation:
                parser.error("run-spec requires --confirm-provider-generation")
            if config.music_generation.provider != "ace_step_local":
                parser.error("run-spec requires ace_step_local music_generation")
            music_provider = AceStepLocalProvider(config.music_generation)
            brief = load_brief(args.brief_file)
            lyrics = load_lyrics(args.lyrics_file)
            music_plans = plan_music(brief, lyrics, [music_provider], attempt=args.attempt)
            database = Database(config.database_path)
            database.migrate()
            benchmark = MusicBenchmark(database, config.data_root / "music-benchmark")
            results = [benchmark.run(item, music_provider) for item in music_plans]
            print(
                json.dumps(
                    {"provider": music_provider.provider, "results": results}, sort_keys=True
                )
            )
            return 0 if all(r["status"] == "succeeded" for r in results) else 1
        if args.music_command in {"analysis-doctor", "analysis-models"}:
            cache_root = config.data_root / "music-benchmark" / ".analysis-models"
            with redirect_stdout(sys.stderr):
                if args.music_command == "analysis-doctor":
                    runtime_result = runtime_doctor(cache_root, args.asr_model, args.device)
                elif args.model_command == "prepare-timing":
                    runtime_result = prepare_timing(
                        cache_root, args.device, allow_download=args.allow_model_download
                    )
                else:
                    runtime_result = prepare_models(
                        cache_root,
                        args.asr_model,
                        args.device,
                        allow_download=args.allow_model_download,
                    )
            print(json.dumps(runtime_result, sort_keys=True))
            return 1 if runtime_result.get("status") == "failed" else 0
        root = config.brand_root.parent.parent
        if args.music_command in {"plan", "run"}:
            brief = load_brief(root / "benchmarks/music/colors_red_v1.yaml")
            lyrics = load_lyrics(root / "benchmarks/music/colors_red_lyrics_v1.yaml")
            if args.music_command == "run" and args.offline_fake and args.provider:
                parser.error("choose one music provider")
            if args.music_command == "run" and not (args.offline_fake or args.provider):
                parser.error("choose --offline-fake or --provider google")
            if (
                args.attempt is not None
                and not 1 <= args.attempt <= brief.candidate_count_per_provider
            ):
                parser.error("attempt must be an integer in the planned candidate set")
            provider_name = (
                args.provider if args.music_command == "plan" else args.provider or "offline_fake"
            )
            provider = VertexLyriaProvider() if provider_name == "google" else FakeMusicProvider()
            try:
                music_plans = plan_music(brief, lyrics, [provider], attempt=args.attempt)
            except ValueError as exc:
                parser.error(str(exc))
            if args.music_command == "plan" or args.dry_run:
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
            results = [benchmark.run(item, provider) for item in music_plans]
            print(json.dumps({"provider": provider.provider, "results": results}, sort_keys=True))
            return 0 if all(r["status"] == "succeeded" for r in results) else 1
        if not config.database_path.is_file():
            parser.error("an existing music benchmark database is required")
        database = Database(config.database_path)
        database.migrate()
        benchmark = MusicBenchmark(database, config.data_root / "music-benchmark")
        if args.music_command == "status":
            print(json.dumps(benchmark.status(), sort_keys=True))
        elif args.music_command == "analyze-audio":
            audio_report, reused = benchmark.analyze_audio(
                args.blind_id,
                args.analysis_version,
                AnalysisConfig(
                    asr_model=args.asr_model,
                    device=args.device,
                    allow_model_download=args.allow_model_download,
                ),
            )
            evaluation = benchmark.evaluate_analysis_qa(args.blind_id, args.analysis_version)
            print(
                json.dumps(
                    {
                        "analysis_ref": (
                            f"music_audio_analysis:{args.blind_id}:{args.analysis_version}"
                        ),
                        "analysis_json_sha256": sha256(
                            audio_report.model_dump_json().encode()
                        ).hexdigest(),
                        "audio_sha256": audio_report.audio_sha256,
                        "database_path": str(config.database_path),
                        "retained_audio_path": str(
                            benchmark.audio_root
                            / (
                                audio_report.request_id
                                + (".mp3" if audio_report.source_format == "audio/mpeg" else ".wav")
                            )
                        ),
                        "blind_id": args.blind_id,
                        "duration_seconds": audio_report.duration_seconds,
                        "qa_status": evaluation["status"],
                        "reused": reused,
                        "rhythm_status": audio_report.rhythm.status,
                        "transcription_status": audio_report.transcription.status,
                        "timing_status": "candidate_pending",
                        "timing_evidence_status": (
                            "complete"
                            if audio_report.timing.downbeat_seconds
                            and audio_report.timing.words
                            and audio_report.timing.lyric_lines
                            and audio_report.timing.sections
                            else "incomplete"
                        ),
                        "warnings": audio_report.warnings,
                    },
                    sort_keys=True,
                )
            )
        elif args.music_command == "provider-resume":
            request = benchmark.request(args.request_id)
            if (request["provider"], request["model"]) == ("google", VertexLyriaProvider.model):
                resume_provider: MusicProvider = VertexLyriaProvider()
            elif (request["provider"], request["model"]) == (
                "ace_step_local",
                AceStepLocalProvider.model,
            ):
                resume_provider = AceStepLocalProvider(
                    config.music_generation
                    if config.music_generation.provider == "ace_step_local"
                    else None
                )
            else:
                parser.error("provider-resume does not support the stored provider")
            print(
                json.dumps(
                    benchmark.provider_resume(args.request_id, resume_provider),
                    sort_keys=True,
                )
            )
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
                elif args.type == "qa-analysis":
                    if args.version is None:
                        parser.error("analysis QA policy requires --version")
                    evaluation = benchmark.evaluate_analysis_qa(args.blind_id, args.version)
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
        for requirement in requirements(
            goal, snapshot.scene_ids, snapshot.production_audio_handoff
        ):
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
