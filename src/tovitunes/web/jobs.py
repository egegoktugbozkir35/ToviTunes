"""One in-process heavy worker; durable results live in production services."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from tovitunes.youtube.client import YouTubeError


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
    result: dict[str, Any] | None = None


class JobBusy(RuntimeError):
    pass


def safe_error(exc: Exception) -> str:
    if isinstance(exc, YouTubeError):
        return str(exc)
    if isinstance(exc, JobBusy):
        return "Another production job is active"
    return f"Operation failed ({type(exc).__name__}); inspect local server logs"


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tovitunes-job")
        self._jobs: dict[str, Job] = {}
        self._active: str | None = None

    def submit(
        self, operation: str, episode_key: str | None, runner: Callable[[], dict[str, Any]]
    ) -> Job:
        with self._lock:
            if self._active is not None:
                raise JobBusy("production worker busy")
            job = Job(
                job_id=str(uuid4()),
                operation=operation,
                episode_key=episode_key,
                submitted_at=_now(),
            )
            self._jobs[job.job_id] = job
            self._active = job.job_id
        self._executor.submit(self._run, job.job_id, runner)
        return job.model_copy(deep=True)

    def _run(self, job_id: str, runner: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            stage = {
                "render": "Rendering selected production artifacts",
                "youtube_connect": "Authorizing and checking channel identity",
                "youtube_private_test": "Checking release evidence and uploading private test",
            }.get(job.operation, "Running")
            job.status, job.progress, job.started_at = "running", stage, _now()
        try:
            result = runner()
        except Exception as exc:
            with self._lock:
                job.status, job.progress = "failed", "Failed"
                job.error = safe_error(exc)
        else:
            with self._lock:
                job.status, job.progress, job.result = "succeeded", "Complete", result
                if job.operation == "short_production":
                    job.status = str(result.get("status", "COMPLETE")).lower()
                    job.progress = str(result.get("current_stage", "Complete"))
                    job.episode_key = result.get("episode_key")
        finally:
            with self._lock:
                job.finished_at = _now()
                self._active = None

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id].model_copy(deep=True)

    def update_progress(self, stage: str, status: str) -> None:
        with self._lock:
            if self._active is not None:
                self._jobs[self._active].progress = f"{stage}: {status}"

    def list(self) -> list[Job]:
        with self._lock:
            return [job.model_copy(deep=True) for job in reversed(list(self._jobs.values()))]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
