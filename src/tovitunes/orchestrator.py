"""Canonical MPT application lifecycle specialized for ToviTunes domain stages."""

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tovitunes.benchmark.providers import ImageProvider
from tovitunes.config import RuntimeConfig
from tovitunes.continuation import plan_continuation
from tovitunes.creative.director import validate_pins
from tovitunes.creative.provider import StructuredGenerator
from tovitunes.creative.service import episode_by_key
from tovitunes.domain.visual_plan import EpisodeVisualPlan
from tovitunes.errors import CreativeChainExhausted, ProductionStop, ProviderError, StateError
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.music.providers import MusicProvider
from tovitunes.persistence.db import Database
from tovitunes.pipeline.targets import ProductionTarget, stages_for
from tovitunes.progress import PipelineProgress, ProgressReporter, report_progress
from tovitunes.publication.service import PublicationService
from tovitunes.render.episode_assets import ImageStageBlocked, generate_assets
from tovitunes.render.models import RenderManifest
from tovitunes.render.production import load_inputs, validate_manifest
from tovitunes.run_history import RunHistory
from tovitunes.services.context import StageContext
from tovitunes.services.music import MusicService
from tovitunes.services.render import RenderService
from tovitunes.services.visual import VisualService

ServiceFactory = Callable[[ProductionExecutionOwnership, Callable[[str, str], None]], StageContext]
PublisherFactory = Callable[[ProductionExecutionOwnership], PublicationService]


@dataclass(frozen=True)
class ProductionReference:
    """Explicit identity of one production to continue, including before episode reservation."""

    episode_key: str | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        if (self.episode_key is None) == (self.run_id is None):
            raise ValueError("Exactly one of episode_key or run_id is required")
        identity = self.episode_key if self.episode_key is not None else self.run_id
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("Production identity must be a nonempty string")


class Orchestrator:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        context_factory: ServiceFactory,
        music_factory: ServiceFactory,
        visual_factory: ServiceFactory,
        render_factory: ServiceFactory,
        publisher_factory: PublisherFactory,
        progress_reporter: ProgressReporter | None = None,
    ) -> None:
        self.config = config
        self.database = Database(config.database_path)
        self._factories = {
            "context": context_factory,
            "music": music_factory,
            "visual": visual_factory,
            "render": render_factory,
        }
        self._publisher_factory = publisher_factory
        self._reporter = progress_reporter
        self._milestone_percent = 0

    def _progress(
        self, stage: str, detail: str, key: str | None = None, percent: int | None = None
    ) -> None:
        report_progress(
            self._reporter,
            PipelineProgress(
                stage=stage,
                detail=detail,
                percent=self._milestone_percent if percent is None else percent,
                episode_key=key,
            ),
        )

    def generate(self, target: ProductionTarget = ProductionTarget.DRAFT) -> dict[str, Any]:
        return self._execute("generate", None, target)

    def resume(
        self,
        reference: ProductionReference | str,
        target: ProductionTarget = ProductionTarget.RENDER,
    ) -> dict[str, Any]:
        if isinstance(reference, str):
            reference = ProductionReference(episode_key=reference)
        if not isinstance(reference, ProductionReference):
            raise TypeError("Resume requires an explicit production reference")
        return self._execute("resume", reference, target)

    def _execute(
        self, operation: str, reference: ProductionReference | None, target: ProductionTarget
    ) -> dict[str, Any]:
        self._milestone_percent = 0
        self.database.migrate()
        history = RunHistory(self.database)
        key = reference.episode_key if reference else None
        run_id = reference.run_id if reference else None
        with ProductionExecutionOwnership(
            self.database, operation=operation, item_id=key or run_id
        ) as owner:
            diagnostic_id = history.start(operation, target.value, key)
            creative_result: dict[str, Any] = {}
            stage = "CREATIVE"
            services: dict[str, StageContext] = {}

            def service(name: str) -> StageContext:
                if name not in services:
                    owner.assert_owned()
                    services[name] = self._factories[name](
                        owner, lambda s, d: self._progress(s, d, key)
                    )
                return services[name]

            try:
                if reference is not None and run_id is not None:
                    # Resolve only the requested run. A bound episode uses normal continuation.
                    with closing(self.database.connect()) as db:
                        saved = db.execute(
                            "SELECT e.external_key FROM creative_runs c "
                            "LEFT JOIN episodes e ON e.episode_id=c.episode_id WHERE c.run_id=?",
                            (run_id,),
                        ).fetchone()
                    if saved is None:
                        raise KeyError(f"unknown creative run: {run_id}")
                    key = saved["external_key"]
                if key is None:
                    with service("context")._creative() as creative:
                        if reference is None:
                            run_id = creative.reserve_next_run()
                            owner.assert_owned()
                            with closing(self.database.connect()) as db:
                                db.execute(
                                    "INSERT INTO production_requests VALUES (?,?,?,?)",
                                    (
                                        str(uuid4()),
                                        run_id,
                                        target.value,
                                        datetime.now(UTC).isoformat(),
                                    ),
                                )
                                db.commit()
                        assert run_id is not None
                        creative_result = creative.prepare(run_id=run_id)
                        key = str(creative_result["episode_key"])
                assert key is not None
                # Recompute after acquiring ownership, then before every effect.
                plan = plan_continuation(self.config, key, target=target)
                if plan.get("historical"):
                    history.finish(diagnostic_id, "succeeded")
                    return {
                        **plan,
                        "historical": True,
                        "dry_run": False,
                        "status": "COMPLETE" if plan["target_complete"] else "BLOCKED",
                        "blocker": None
                        if plan["target_complete"]
                        else {"reason": "Historical production is retained read-only"},
                    }
                for _ in range(len(stages_for(target)) + 1):
                    owner.assert_owned()
                    plan = plan_continuation(self.config, key, target=target)
                    if plan["target_complete"]:
                        self._progress("COMPLETED", "Production complete", key, 100)
                        history.finish(diagnostic_id, "succeeded")
                        return {
                            **creative_result,
                            **plan,
                            "dry_run": False,
                            "run_id": run_id,
                            "provider_calls": creative_result.get("provider_calls", {}),
                            "generation_attempts": creative_result.get("generation_attempts", []),
                        }
                    stage = plan["next_stage"] or plan["current_stage"]
                    if not plan["allowed"]:
                        raise ProductionStop(
                            plan["status"],
                            "Durable continuation is blocked",
                            plan["blocker"]
                            if isinstance(plan.get("blocker"), dict)
                            else {
                                "reason": plan.get("blocker", "Check retained production evidence")
                            },
                        )
                    stage = plan["next_stage"]
                    self._milestone_percent = int(
                        100
                        * len(set(plan["reusable_stages"]) & set(stages_for(target)))
                        / len(stages_for(target))
                    )
                    self._progress(stage, "Running " + stage.lower(), key)
                    if stage == "CREATIVE":
                        with service("context")._creative() as creative:
                            creative_result = creative.prepare(episode_key=key)
                        continue
                    episode = episode_by_key(self.database, key)
                    if stage == "YOUTUBE":
                        self._publisher_factory(owner).publish(key)
                        continue
                    if stage == "RELEASE":
                        raise ProductionStop("BLOCKED", "Release gates require attention")
                    context = service("context")
                    validate_pins(episode, context.catalog, self.database)
                    if stage == "RENDER":
                        render = service("render")
                        assert isinstance(render, RenderService)
                        rendered = render._render(episode)
                        self._accept_render(context, key, rendered, owner)
                        continue
                    if stage == "MEDIA_QA":
                        render = service("render")
                        assert isinstance(render, RenderService)
                        render._media_qa(episode)
                        continue
                    if stage == "METADATA":
                        render = service("render")
                        assert isinstance(render, RenderService)
                        render._metadata(episode)
                        continue
                    inputs = context._creative_inputs(episode)
                    if stage in {"MUSIC", "AUDIO_ANALYSIS"}:
                        music = service("music")
                        assert isinstance(music, MusicService)
                        if stage == "MUSIC":
                            music._music(episode, inputs)
                        else:
                            with closing(self.database.connect()) as db:
                                binding = db.execute(
                                    "SELECT o.blind_id FROM episode_music_bindings b JOIN "
                                    "music_outputs o ON o.request_id=b.request_id WHERE "
                                    "b.episode_id=?",
                                    (episode.episode_id,),
                                ).fetchone()
                            if binding is None:
                                raise StateError("Retained audio has no music receipt")
                            music._analysis(episode, binding[0])
                        continue
                    visual = service("visual")
                    assert isinstance(visual, VisualService)
                    if stage == "VISUAL_PLAN":
                        visual._visual(episode, inputs)
                        continue
                    selected = context._selected(episode, "episode_visual_plan")
                    if selected is None:
                        raise StateError("Selected visual plan is missing")
                    plan_id = selected.identity.artifact_id
                    visual_plan = EpisodeVisualPlan.model_validate(context.store.read_json(plan_id))
                    if stage == "VISUAL_ASSETS":
                        if self.config.lesson_object_generation.provider != "qwen_comfyui":
                            raise ProductionStop(
                                "BLOCKED", "Production illustrations require qwen_comfyui"
                            )
                        generate_assets(
                            self.config,
                            context.store,
                            episode,
                            visual_plan,
                            plan_id,
                            context.working,
                            context.catalog.creative_bible.visual_direction,
                            context.image_provider,
                            progress=context.progress,
                            assert_owner=owner.assert_owned,
                        )
                        visual._environment(episode, visual_plan, plan_id)
                    elif stage == "STORYBOARD":
                        with closing(self.database.connect()) as db:
                            request = db.execute(
                                "SELECT request_id FROM episode_music_bindings WHERE episode_id=?",
                                (episode.episode_id,),
                            ).fetchone()
                        assets = {}
                        for asset in visual_plan.required_assets:
                            record = context._selected(episode, "visual_asset", asset.asset_key)
                            if record is None:
                                raise StateError("Selected visual asset is missing")
                            assets[asset.asset_key] = record.identity.artifact_id
                        environment = context._selected(episode, "episode_environment")
                        if request is None or environment is None:
                            raise StateError("Storyboard dependencies are missing")
                        payload = context.store.read_json(environment.identity.artifact_id)
                        assert isinstance(payload, dict)
                        render = service("render")
                        assert isinstance(render, RenderService)
                        render._storyboard(
                            episode,
                            inputs,
                            request[0],
                            visual_plan,
                            plan_id,
                            assets,
                            str(payload["environment_set_artifact_id"]),
                        )
                    else:
                        raise StateError("Unknown continuation stage")
                raise StateError("Stage completed without retaining its authoritative result")
            except (ProductionStop, ImageStageBlocked) as exc:
                history.finish(diagnostic_id, "failed")
                plan = (
                    plan_continuation(self.config, key, target=target)
                    if key
                    else plan_continuation(self.config, target=target)
                )
                return {
                    **plan,
                    "dry_run": False,
                    "status": exc.status,
                    "current_stage": stage,
                    "run_id": run_id,
                    "blocker": {
                        "reason": str(exc),
                        **(exc.evidence if isinstance(exc, ProductionStop) else {}),
                    },
                }
            except ProviderError as exc:
                history.finish(diagnostic_id, "failed")
                return {
                    "episode_key": key,
                    "target": target.value,
                    "status": "FAILED",
                    "dry_run": False,
                    "current_stage": stage,
                    "run_id": run_id,
                    "blocker": {
                        "error_kind": "chain_exhausted"
                        if isinstance(exc, CreativeChainExhausted)
                        else exc.category.value
                    },
                }
            except BaseException:
                history.finish(diagnostic_id, "interrupted")
                raise

    def _accept_render(
        self,
        context: StageContext,
        key: str,
        result: dict[str, Any],
        owner: ProductionExecutionOwnership,
    ) -> None:
        """Accept the adapter's trusted result before committing selected production truth."""
        from pathlib import Path

        final = context.store.get(str(result["final_render_id"]))
        path = Path(str(result["output_path"])).resolve()
        if (
            not path.is_relative_to(self.config.data_root.resolve())
            or path != context.store.path_for(final.identity.artifact_id)
            or path.stat().st_size <= 0
            or not context.store.inspect(final.identity.artifact_id).valid
        ):
            raise StateError("Renderer returned an untrusted media path")
        inputs = load_inputs(self.config, key, local_preview=True)
        manifest_id = str(result["render_manifest_id"])
        manifest = RenderManifest.model_validate(context.store.read_json(manifest_id))
        if (
            manifest.episode_id != inputs.storyboard.episode_id
            or final.identity.owner_scope != "episode"
            or final.identity.owner_id != inputs.storyboard.episode_id
            or final.identity.kind != "final_render"
        ):
            raise StateError("Renderer returned another episode's manifest")
        validate_manifest(context.store, manifest, inputs.storyboard)
        qa_id = str(result["media_qa_id"])
        qa_record = context.store.get(qa_id)
        if (
            qa_record.identity.owner_id != inputs.storyboard.episode_id
            or qa_record.identity.kind != "media_qa"
        ):
            raise StateError("Renderer returned another episode's QA")
        qa = context.store.read_json(qa_id)
        if (
            not isinstance(qa, dict)
            or qa.get("passed") is not True
            or (
                qa.get("render_artifact_id") != final.identity.artifact_id
                or qa.get("render_sha256") != final.sha256
            )
        ):
            raise StateError("Renderer result failed media QA identity")
        owner.assert_owned()
        context.store.admit_render_result(final.identity.artifact_id, qa_id)


def build_orchestrator(
    config: RuntimeConfig,
    *,
    creative_provider: StructuredGenerator | None = None,
    music_provider: MusicProvider | None = None,
    image_provider: ImageProvider | None = None,
    publisher_factory: PublisherFactory | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> Orchestrator:
    """MPT composition root; construct each stage dependency only when it is required."""

    def factory(cls: type[StageContext]) -> ServiceFactory:
        return lambda owner, progress: cls(
            config,
            owner,
            creative_provider=creative_provider,
            music_provider=music_provider,
            image_provider=image_provider,
            progress=progress,
        )

    return Orchestrator(
        config,
        context_factory=factory(StageContext),
        music_factory=factory(MusicService),
        visual_factory=factory(VisualService),
        render_factory=factory(RenderService),
        publisher_factory=publisher_factory
        or (lambda owner: PublicationService(config, ownership=owner)),
        progress_reporter=progress_reporter,
    )
