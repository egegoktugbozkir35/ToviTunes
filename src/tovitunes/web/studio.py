"""Product actions, durable library and bounded operator recovery projections."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig
from tovitunes.continuation import plan_continuation
from tovitunes.creative.learning import LearningBrief
from tovitunes.creative.service import episode_by_key
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.errors import FailureCategory
from tovitunes.orchestrator import Orchestrator, ProductionReference
from tovitunes.persistence.db import Database
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.pipeline.targets import ProductionTarget
from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.service import PublicationService
from tovitunes.web.diagnostics import OUTCOMES
from tovitunes.web.jobs import JobBusy, JobManager


class CreateAction(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    target: ProductionTarget


class ContinueAction(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    target: Literal[ProductionTarget.RENDER, ProductionTarget.PUBLISH]


def library(config: RuntimeConfig) -> list[dict[str, Any]]:
    """Provider-free evidence projection; never select, generate or infer from filenames."""
    if not config.database_path.is_file():
        return []
    database = Database(config.database_path)
    store = AssetStore(config.data_root, database, initialize=False, local_preview=True)
    with closing(database.connect()) as db:
        rows = db.execute("SELECT * FROM episodes ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        key = row["external_key"]
        plan = plan_continuation(config, key)
        stages = {s["name"]: s for s in plan["stages"]}
        draft_ready = plan["draft_ready"]
        title = str(row["concept_id"]).replace("_", " ").title()
        draft: dict[str, Any] | None = None
        if draft_ready:
            try:
                episode = episode_by_key(database, key)
                ids = stages["CREATIVE"]["evidence"]["artifact_ids"]
                spec = EpisodeSpec.model_validate(store.read_json(ids[0]))
                lyrics = LyricsSpec.model_validate(store.read_json(ids[1]))
                music = MusicSpec.model_validate(store.read_json(ids[2]))
                creative_music_spec(episode, spec, lyrics, music, tuple(ids))
                draft = {
                    "premise": spec.concept.premise,
                    "hook": spec.concept.hook,
                    "lyrics": [line.text for line in lyrics.lines],
                    "music_direction": music.vocal_direction,
                }
                if episode.learning_brief_id:
                    with closing(database.connect()) as db:
                        saved_brief = db.execute(
                            "SELECT brief_json FROM learning_briefs WHERE brief_id=?",
                            (episode.learning_brief_id,),
                        ).fetchone()
                    if saved_brief:
                        title = LearningBrief.model_validate_json(saved_brief[0]).working_title
            except (KeyError, ValueError, OSError):
                draft = None
        release = evaluate_release(config, key)
        render_ready = plan["render_ready"]
        history = PublicationService(config).history(row["episode_id"])
        successful = next((p for p in history if p.get("youtube_video_id")), None)
        at_target = plan["target_complete"]
        category = (
            "published"
            if at_target
            else "renders"
            if render_ready
            else "drafts"
            if draft_ready
            else "attention"
        )
        publication = (
            {
                field: successful.get(field)
                for field in (
                    "outcome",
                    "youtube_video_id",
                    "privacy_status",
                    "completed_at",
                    "watch_url",
                )
            }
            if successful
            else {"outcome": "ambiguous" if plan["status"] == "AMBIGUOUS" else "not_published"}
        )
        preview_url = None
        with closing(database.connect()) as db:
            previews = db.execute(
                "SELECT v.artifact_id,v.mime_type FROM artifact_selections s "
                "JOIN artifact_versions v ON v.artifact_id=s.artifact_id "
                "WHERE s.owner_scope='episode' AND s.owner_id=? "
                "AND s.kind IN ('visual_asset','scene_image') ORDER BY s.slot_key",
                (row["episode_id"],),
            ).fetchall()
        for preview in previews:
            if preview["mime_type"] in {"image/png", "image/jpeg", "image/webp"} and (
                store.inspect(preview["artifact_id"]).valid
            ):
                preview_url = f"/api/media/{preview['artifact_id']}"
                break
        result.append(
            {
                "episode_key": key,
                "title": title,
                "created_at": row["created_at"],
                "category": category,
                "draft_ready": draft_ready,
                "render_ready": render_ready,
                "publication": publication,
                "historical": bool(plan.get("historical")),
                "final_render_artifact_id": release.render_artifact_id if render_ready else None,
                "video_url": f"/api/media/{release.render_artifact_id}" if render_ready else None,
                "preview_url": preview_url,
                "draft": draft,
                "blocker": plan.get("blocker")
                if isinstance(plan.get("blocker"), str)
                else "Review or release checks require attention."
                if plan.get("blocker")
                else None,
                "can_render": plan_continuation(config, key, target=ProductionTarget.RENDER)[
                    "allowed"
                ]
                and not render_ready
                and not plan.get("historical"),
                "can_publish": plan["allowed"]
                and render_ready
                and not at_target
                and not plan.get("historical"),
            }
        )
    return result


def safe_production_result(result: dict[str, Any]) -> dict[str, Any]:
    """Studio exposes identities and known categories, never provider response bodies."""
    safe = {
        k: result[k]
        for k in (
            "episode_key",
            "status",
            "current_stage",
            "target",
            "historical",
            "final_render_id",
            "ready_local_preview",
            "run_id",
        )
        if k in result
    }
    blocker = result.get("blocker")
    if blocker:
        if isinstance(blocker, dict):
            safe["blocker"] = {
                k: blocker[k]
                for k in ("request_id", "provider", "model", "kind", "recovery_action")
                if k in blocker
            }
            category = blocker.get("error_kind")
            if category in {c.value for c in FailureCategory} | {"chain_exhausted"}:
                safe["blocker"]["error_kind"] = category
        else:
            safe["blocker"] = {}
        # Stage failures can include third-party text. Only our fixed messages cross the API.
        safe["blocker"]["reason"] = (
            "Configured creative model chain exhausted. See the per-model outcomes."
            if safe["blocker"].get("error_kind") == "chain_exhausted"
            else "Creative model " + OUTCOMES[safe["blocker"]["error_kind"]] + "."
            if safe["blocker"].get("error_kind") in OUTCOMES
            else (
                "ACE-Step's result is uncertain. Check the retained task "
                "without creating a new song."
            )
            if safe["blocker"].get("recovery_action") == "resume_music_task"
            else "YouTube outcome is uncertain. Reconciliation is required before continuing."
            if result.get("status") == "AMBIGUOUS" and result.get("current_stage") == "YOUTUBE"
            else "This provider result is uncertain. Automatic retry is disabled."
            if result.get("status") == "AMBIGUOUS"
            else "The provider task is still pending. Resume to check the same task."
            if result.get("status") == "PENDING_PROVIDER"
            else "Configured review or release policy requires attention."
            if result.get("status") == "NEEDS_REVIEW"
            else "This stage could not complete. Check Settings and resume its retained work."
        )
    if isinstance(blocker, dict) and isinstance(blocker.get("release"), dict):
        checks = blocker["release"].get("checks", [])
        failed = [c.get("name") for c in checks if isinstance(c, dict) and not c.get("passed")]
        if "production_human_review" in failed:
            safe["blocker"]["reason"] = "Configured policy requires review before publication."
        elif any(
            name in failed for name in ("commercial_rights_direct", "commercial_rights_inherited")
        ):
            safe["blocker"]["reason"] = "Commercial rights need clearance before publishing."
        elif "rights_not_blocked" in failed:
            safe["blocker"]["reason"] = "An asset's rights policy blocks publication."
        safe["blocker"]["gate_checks"] = failed
    return safe


def studio_router(
    config: RuntimeConfig,
    jobs: JobManager,
    workflow: Callable[[], Orchestrator],
    checked_key: Callable[[str], str],
) -> APIRouter:
    router = APIRouter(prefix="/api/studio")

    def submit(
        target: ProductionTarget,
        reference: ProductionReference | None,
        resume_job_id: str | None = None,
    ) -> dict[str, Any]:
        def runner() -> dict[str, Any]:
            flow = workflow()
            return safe_production_result(
                flow.resume(reference=reference, target=target)
                if reference is not None
                else flow.generate(target, retain_reservation=jobs.retain_reservation)
            )

        try:
            return jobs.submit(
                "studio",
                reference.episode_key if reference else None,
                runner,
                target=target,
                resume_job_id=resume_job_id,
            ).model_dump()
        except JobBusy as exc:
            raise HTTPException(409, "Another production task is active") from exc

    @router.post("/create", status_code=202)
    def create(action: CreateAction) -> dict[str, Any]:
        return submit(action.target, None)

    @router.post("/episodes/{episode_key}/continue", status_code=202)
    def continue_episode(episode_key: str, action: ContinueAction) -> dict[str, Any]:
        key = checked_key(episode_key)
        try:
            episode_by_key(Database(config.database_path), key)
        except KeyError as exc:
            raise HTTPException(404, "Episode unavailable") from exc
        return submit(action.target, ProductionReference(episode_key=key))

    @router.get("/library")
    def get_library() -> list[dict[str, Any]]:
        return library(config)

    @router.post("/jobs/{job_id}/recover", status_code=202)
    def recover(job_id: str) -> dict[str, Any]:
        try:
            job = jobs.get(job_id)
        except KeyError as exc:
            raise HTTPException(404, "Task unavailable") from exc
        if (
            job.operation != "studio"
            or not job.target
            or job.stopped
            or job.status in {"queued", "running"}
        ):
            raise HTTPException(409, "Task cannot be resumed")
        if job.recovery_reference is not None:
            reference = job.recovery_reference
        elif job.episode_key:
            reference = ProductionReference(episode_key=job.episode_key)
        else:
            run_id = (job.result or {}).get("run_id")
            if not isinstance(run_id, str) or not run_id.strip():
                raise HTTPException(409, "Task has no retained production identity to resume")
            reference = ProductionReference(run_id=run_id)
        # The worker recomputes this exact production's continuation under the lease.
        return submit(job.target, reference, job.job_id)

    @router.post("/jobs/{job_id}/stop")
    def stop(job_id: str) -> dict[str, Any]:
        try:
            return jobs.stop(job_id).model_dump()
        except KeyError as exc:
            raise HTTPException(404, "Task unavailable") from exc
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
