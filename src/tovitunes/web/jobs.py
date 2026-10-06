"""One in-process heavy worker; durable results live in production services."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from tovitunes.errors import ChannelMismatch
from tovitunes.persistence.db import Database
from tovitunes.pipeline.targets import ProductionTarget, steps_for
from tovitunes.progress import PipelineProgress


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    operation: str
    episode_key: str | None
    status: str = "queued"
    progress: str = "Queued"
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    error_category: str | None = None
    result: dict[str, Any] | None = None
    execution: int = 1
    target: ProductionTarget | None = None
    current_stage: str | None = None
    current_substage: str | None = None
    completed_steps: int = 0
    total_steps: int = 0
    progress_percent: int = 0
    steps: list[str] = []
    blocker: dict[str, Any] | None = None
    recovery_action: str | None = None
    stopped: bool = False


class JobBusy(RuntimeError):
    pass


def safe_error(exc: Exception) -> str:
    if isinstance(exc, ChannelMismatch):
        return "The connected YouTube channel does not match the configured channel."
    if isinstance(exc, JobBusy):
        return "Another production job is active"
    return f"Operation failed ({type(exc).__name__}); inspect local server logs"


class JobManager:
    def __init__(self, database: Database | None = None) -> None:
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tovitunes-job")
        self._jobs: dict[str, Job] = {}
        self._active: str | None = None
        self.database = database
        if database:
            try:
                with closing(database.connect()) as db:
                    rows = db.execute(
                        "SELECT payload_json FROM studio_jobs ORDER BY rowid"
                    ).fetchall()
                for row in rows:
                    try:
                        job = Job.model_validate_json(row[0])
                    except ValueError:
                        logging.getLogger(__name__).exception("ignored invalid diagnostic job")
                        continue
                    if job.status in {"queued", "running"}:
                        job.status = "interrupted"
                        job.error = "Studio stopped during this task. Resume its durable work."
                        job.recovery_action = "resume"
                    self._jobs[job.job_id] = job
            except Exception:
                logging.getLogger(__name__).exception("failed to load diagnostic job history")
            for job in self._jobs.values():
                self._save(job)

    def _save(self, job: Job) -> None:
        try:
            if self.database:
                with closing(self.database.connect()) as db:
                    db.execute(
                        "INSERT INTO studio_jobs VALUES (?,?) ON CONFLICT(job_id) DO "
                        "UPDATE SET payload_json=excluded.payload_json",
                        (job.job_id, job.model_dump_json()),
                    )
                    db.commit()
        except Exception:
            logging.getLogger(__name__).exception("diagnostic job persistence failed")

    def submit(
        self,
        operation: str,
        episode_key: str | None,
        runner: Callable[[], dict[str, Any]],
        *,
        target: ProductionTarget | None = None,
        resume_job_id: str | None = None,
    ) -> Job:
        with self._lock:
            if self._active is not None:
                raise JobBusy("production worker busy")
            previous = self._jobs.get(resume_job_id or "")
            job = Job(
                job_id=resume_job_id or str(uuid4()),
                execution=previous.execution + 1 if previous else 1,
                operation=operation,
                episode_key=episode_key,
                submitted_at=_now(),
                target=target,
                steps=list(steps_for(target)) if target else [],
                total_steps=len(steps_for(target)) if target else 0,
            )
            self._jobs[job.job_id] = job
            self._active = job.job_id
            self._save(job)
        self._executor.submit(self._run, job.job_id, runner)
        return self.get(job.job_id)

    def _run(self, job_id: str, runner: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            stage = {
                "render": "Rendering selected production artifacts",
                "youtube_connect": "Authorizing and checking channel identity",
                "youtube_private_test": "Checking release evidence and uploading private test",
            }.get(job.operation, "Running")
            job.status, job.progress, job.started_at = "running", stage, _now()
            self._save(job)
        try:
            result = runner()
        except Exception as exc:
            with self._lock:
                job.status, job.progress = "failed", "Failed"
                job.error = safe_error(exc)
                job.error_category = type(exc).__name__
                job.recovery_action = "resume" if job.target else None
        else:
            with self._lock:
                job.status, job.progress, job.result = "succeeded", "Complete", result
                if job.operation in {"short_production", "studio"}:
                    job.status = str(result.get("status", "COMPLETE")).lower()
                    job.progress = str(result.get("current_stage", "Complete"))
                    job.episode_key = result.get("episode_key") or job.episode_key
                    job.current_stage = result.get("current_stage")
                    if job.status == "complete":
                        job.completed_steps = job.total_steps
                        job.progress_percent = 100
                    else:
                        evidence = result.get("blocker", {})
                        job.blocker = (
                            evidence if isinstance(evidence, dict) else {"reason": evidence}
                        )
                        job.error = str(
                            job.blocker.get("reason")
                            or "Production paused. Check this stage before continuing."
                        )
                        action = job.blocker.get("recovery_action")
                        job.recovery_action = (
                            str(action)
                            if action in {"resume_music_task"}
                            else None
                            if job.status == "ambiguous"
                            else "resume"
                        )
        finally:
            with self._lock:
                job.finished_at = _now()
                self._active = None
                self._save(job)

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id].model_copy(deep=True)

    def update_progress(self, progress: PipelineProgress) -> None:
        with self._lock:
            if self._active is None:
                return
            job = self._jobs[self._active]
            job.progress = progress.detail
            job.current_stage = progress.stage
            job.current_substage = progress.detail
            job.progress_percent = progress.percent
            job.episode_key = progress.episode_key or job.episode_key
            self._save(job)

    def stop(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            if job.status in {"queued", "running"}:
                raise JobBusy("Wait for the active provider operation to finish before stopping")
            job.stopped = True
            job.recovery_action = None
            self._save(job)
            return job.model_copy(deep=True)

    def list(self) -> list[Job]:
        with self._lock:
            return [job.model_copy(deep=True) for job in reversed(list(self._jobs.values()))]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
