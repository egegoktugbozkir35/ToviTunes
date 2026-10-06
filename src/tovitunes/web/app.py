"""FastAPI localhost control surface over ToviTunes services."""

from __future__ import annotations

import re
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig
from tovitunes.persistence.db import Database
from tovitunes.pipeline.short_production import ShortProductionWorkflow
from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.service import PublicationService
from tovitunes.render.production import ProductionRenderer
from tovitunes.web.jobs import JobBusy, JobManager, safe_error
from tovitunes.web.services import (
    episode_detail,
    episodes,
    generate_publication_metadata,
    system_status,
)
from tovitunes.youtube.client import YouTubeClient, YouTubeError

_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_ARTIFACT = re.compile(r"^[0-9a-fA-F-]{36}$")
_MEDIA_MIME = {"video/mp4", "image/png", "image/jpeg", "image/webp", "audio/mpeg"}


def create_app(config: RuntimeConfig) -> FastAPI:
    app = FastAPI(title="ToviTunes Operator", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    database = Database(config.database_path)
    database.migrate()
    jobs = JobManager()
    app.state.jobs = jobs
    app.state.config = config
    static = resources.files("tovitunes.web").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.middleware("http")
    async def local_write_guard(request: Request, call_next: Any) -> Response:
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                host = request.url.hostname
                if (
                    parsed.scheme != "http"
                    or parsed.hostname != host
                    or parsed.port != request.url.port
                ):
                    return JSONResponse(
                        {"detail": "Cross-origin operator action denied"}, status_code=403
                    )
        response: Response = await call_next(request)
        return response

    @app.exception_handler(Exception)
    async def safe_exception(_: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return JSONResponse({"detail": safe_error(exc)}, status_code=500)

    def checked_key(value: str) -> str:
        if not _KEY.fullmatch(value):
            raise HTTPException(404, "Episode unavailable")
        return value

    def submit(operation: str, key: str | None, runner: Any) -> dict[str, Any]:
        try:
            return jobs.submit(operation, key, runner).model_dump()
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return static.joinpath("index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "database": config.database_path.is_file()}

    @app.get("/api/system")
    def system() -> dict[str, Any]:
        active = next(
            (j.model_dump() for j in jobs.list() if j.status in {"queued", "running"}), None
        )
        result = system_status(config, active)
        if (
            config.publication.youtube.enabled
            and config.publication.youtube.token_file.is_file()
            and config.expected_youtube_channel_id
        ):
            try:
                result["connected_channel"] = YouTubeClient(config.publication.youtube).channel(
                    config.expected_youtube_channel_id
                )
            except YouTubeError as exc:
                result["youtube_connection_error"] = str(exc)
        return result

    @app.get("/api/episodes")
    def episode_list() -> list[dict[str, Any]]:
        return episodes(config)

    @app.get("/api/episodes/{episode_key}")
    def episode(episode_key: str) -> dict[str, Any]:
        try:
            return episode_detail(config, checked_key(episode_key))
        except KeyError as exc:
            raise HTTPException(404, "Episode unavailable") from exc

    @app.get("/api/jobs")
    def job_list() -> list[dict[str, Any]]:
        return [job.model_dump() for job in jobs.list()]

    def production_workflow() -> ShortProductionWorkflow:
        return ShortProductionWorkflow(config, progress=jobs.update_progress)

    @app.get("/api/production/plan")
    def production_plan(episode_key: str | None = None) -> dict[str, Any]:
        return production_workflow().plan(checked_key(episode_key) if episode_key else None)

    @app.post("/api/production/generate-next-short")
    def generate_next_short(confirm_provider_generation: bool = False) -> dict[str, Any]:
        if not confirm_provider_generation:
            return production_workflow().plan()
        return submit(
            "short_production", None, lambda: production_workflow().produce_next(confirmed=True)
        )

    @app.post("/api/episodes/{episode_key}/produce")
    def produce_short(
        episode_key: str, confirm_provider_generation: bool = False
    ) -> dict[str, Any]:
        key = checked_key(episode_key)
        if not confirm_provider_generation:
            return production_workflow().plan(key)
        return submit(
            "short_production", key, lambda: production_workflow().produce(key, confirmed=True)
        )

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        try:
            return jobs.get(job_id).model_dump()
        except KeyError as exc:
            raise HTTPException(404, "Job unavailable") from exc

    @app.post("/api/episodes/{episode_key}/render", status_code=202)
    def render(episode_key: str) -> dict[str, Any]:
        key = checked_key(episode_key)
        try:
            episode_detail(config, key)
        except KeyError as exc:
            raise HTTPException(404, "Episode unavailable") from exc
        return submit(
            "render", key, lambda: ProductionRenderer(config).render(key, visual_story=True)
        )

    @app.get("/api/episodes/{episode_key}/release-preflight")
    def release_preflight(episode_key: str) -> dict[str, Any]:
        try:
            return evaluate_release(config, checked_key(episode_key)).as_dict()
        except KeyError as exc:
            raise HTTPException(404, "Episode unavailable") from exc

    @app.post("/api/episodes/{episode_key}/publication-metadata", status_code=202)
    def publication_metadata(episode_key: str) -> dict[str, Any]:
        key = checked_key(episode_key)
        try:
            episode_detail(config, key)
        except KeyError as exc:
            raise HTTPException(404, "Episode unavailable") from exc
        return submit(
            "publication_metadata",
            key,
            lambda: generate_publication_metadata(config, key),
        )

    @app.get("/api/youtube/status")
    def youtube_status(refresh: bool = False) -> dict[str, Any]:
        yt = config.publication.youtube
        result: dict[str, Any] = {
            "enabled": yt.enabled,
            "credentials_present": yt.credentials_file.is_file(),
            "token_present": yt.token_file.is_file(),
            "expected_channel_id": config.expected_youtube_channel_id,
            "connected_channel": None,
        }
        if (
            refresh
            and yt.enabled
            and yt.token_file.is_file()
            and config.expected_youtube_channel_id
        ):
            try:
                result["connected_channel"] = YouTubeClient(yt).channel(
                    config.expected_youtube_channel_id
                )
            except YouTubeError as exc:
                result["connection_error"] = str(exc)
        return result

    @app.post("/api/youtube/connect", status_code=202)
    def youtube_connect() -> dict[str, Any]:
        if not config.publication.youtube.enabled or not config.expected_youtube_channel_id:
            raise HTTPException(409, "Configure and enable YouTube first")
        return submit(
            "youtube_connect",
            None,
            lambda: YouTubeClient(config.publication.youtube).channel(
                config.expected_youtube_channel_id or "", interactive=True
            ),
        )

    @app.post("/api/episodes/{episode_key}/youtube/upload-private", status_code=202)
    def upload_private(episode_key: str) -> dict[str, Any]:
        key = checked_key(episode_key)
        try:
            ready = evaluate_release(config, key)
        except KeyError as exc:
            raise HTTPException(404, "Episode unavailable") from exc
        if not ready.private_test_upload_allowed:
            raise HTTPException(409, "Private test upload blocked; inspect preflight")
        return submit(
            "youtube_private_test", key, lambda: PublicationService(config).upload_private(key)
        )

    @app.get("/api/episodes/{episode_key}/youtube/video-status")
    def video_status(episode_key: str) -> dict[str, Any]:
        key = checked_key(episode_key)
        detail = episode_detail(config, key)
        publication = detail["publication"]
        if not publication or publication["outcome"] != "succeeded":
            raise HTTPException(404, "No successful YouTube upload")
        if not config.expected_youtube_channel_id:
            raise HTTPException(409, "Expected YouTube channel ID missing")
        client = YouTubeClient(config.publication.youtube)
        client.assert_channel(config.expected_youtube_channel_id)
        return client.video_status(str(publication["youtube_video_id"]))

    @app.post("/api/episodes/{episode_key}/youtube/publish")
    def publish_public(episode_key: str) -> dict[str, Any]:
        key = checked_key(episode_key)
        try:
            return PublicationService(config).publish_public(key)
        except KeyError as exc:
            raise HTTPException(404, "Episode unavailable") from exc
        except (ValueError, YouTubeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/media/{artifact_id}")
    def media(artifact_id: str) -> FileResponse:
        if not _ARTIFACT.fullmatch(artifact_id):
            raise HTTPException(404, "Artifact unavailable")
        try:
            store = AssetStore(config.data_root, database, initialize=False)
            record = store.get(artifact_id)
            if record.mime_type not in _MEDIA_MIME or not store.inspect(artifact_id).valid:
                raise ValueError("Artifact is not trusted media")
            return FileResponse(store.path_for(artifact_id), media_type=record.mime_type)
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(404, "Artifact unavailable") from exc

    return app
