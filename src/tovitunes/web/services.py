"""Read-only episode and system projections from authoritative persistence."""

from __future__ import annotations

import os
import shutil
from contextlib import closing
from typing import Any

from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.continuation import plan_continuation
from tovitunes.persistence.db import Database
from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.service import PublicationService


def _selected(db: Any, owner_scope: str, owner_id: str, kind: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT v.artifact_id,v.sha256,v.mime_type,v.slot_key,v.kind "
            "FROM artifact_selections s JOIN artifact_versions v ON v.artifact_id=s.artifact_id "
            "WHERE s.owner_scope=? AND s.owner_id=? AND s.kind=? ORDER BY v.slot_key",
            (owner_scope, owner_id, kind),
        )
    ]


def episode_detail(config: RuntimeConfig, episode_key: str) -> dict[str, Any]:
    database = Database(config.database_path)
    with closing(database.connect()) as db:
        row = db.execute("SELECT * FROM episodes WHERE external_key=?", (episode_key,)).fetchone()
        if row is None:
            raise KeyError(episode_key)
        episode = dict(row)
        eid = str(row["episode_id"])
        brand_id = str(row["brand_revision_id"])
        kinds = (
            "episode_spec",
            "lyrics",
            "music_spec",
            "audio_master",
            "timed_storyboard",
            "final_render",
            "render_manifest",
            "media_qa",
            "publication_metadata",
            "visual_story_plan",
            "production_handoff",
            "episode_visual_plan",
            "episode_environment",
            "visual_asset",
            "scene_image",
        )
        selected = {kind: _selected(db, "episode", eid, kind) for kind in kinds}
        brand = {
            kind: _selected(db, "brand", brand_id, kind)
            for kind in (
                "environment_set",
                "lesson_object_manifest",
                "lesson_object",
                "character_sprite",
            )
        }
    preflight = evaluate_release(config, episode_key)
    publication = PublicationService(config).latest(eid)
    media = None
    if preflight.render_artifact_id:
        store = AssetStore(config.data_root, database, initialize=False)
        try:
            if store.inspect(preflight.render_artifact_id).valid:
                media = f"/api/media/{preflight.render_artifact_id}"
        except (KeyError, ValueError, OSError):
            pass
    manifest: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    if selected["publication_metadata"]:
        metadata_id = selected["publication_metadata"][0]["artifact_id"]
        try:
            value = AssetStore(config.data_root, database, initialize=False).read_json(metadata_id)
            if isinstance(value, dict):
                metadata = value
        except (KeyError, ValueError, OSError):
            pass
    full_manifest: dict[str, Any] | None = None
    if selected["render_manifest"]:
        try:
            value = AssetStore(config.data_root, database, initialize=False).read_json(
                selected["render_manifest"][0]["artifact_id"]
            )
            if isinstance(value, dict):
                full_manifest = value
                manifest = {
                    key: value.get(key)
                    for key in (
                        "renderer_version",
                        "duration_seconds",
                        "canvas",
                        "environment_set_artifact_id",
                        "audio_master_artifact_id",
                    )
                }
        except (KeyError, ValueError, OSError):
            pass
    continuation = plan_continuation(config, episode_key)
    return {
        "episode": episode,
        "production": continuation,
        "stages": continuation["stages"],
        "selected": selected,
        "brand_assets": brand,
        "preflight": preflight.as_dict(),
        "publication": publication,
        "video_url": media,
        "manifest_summary": manifest,
        "render_manifest": full_manifest,
        "publication_metadata": metadata,
    }


def episodes(config: RuntimeConfig) -> list[dict[str, Any]]:
    if not config.database_path.is_file():
        return []
    with closing(Database(config.database_path).connect()) as db:
        rows = db.execute("SELECT external_key FROM episodes ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        detail = episode_detail(config, str(row[0]))
        result.append(
            {
                "external_key": detail["episode"]["external_key"],
                "concept_id": detail["episode"]["concept_id"],
                "lifecycle": detail["episode"]["lifecycle"],
                "stages": {s["name"]: s["status"] for s in detail["stages"]},
                "publication": detail["publication"],
            }
        )
    return result


def system_status(config: RuntimeConfig, active_job: dict[str, Any] | None) -> dict[str, Any]:
    catalog = load_brand(config.brand_root)
    database = Database(config.database_path)
    selected: dict[str, list[dict[str, Any]]] = {}
    if config.database_path.is_file():
        with closing(database.connect()) as db:
            for kind in ("environment_set", "lesson_object_manifest", "lesson_object"):
                selected[kind] = _selected(db, "brand", catalog.version.revision_id, kind)
    yt = config.publication.youtube
    return {
        "runtime": "local",
        "database_path": str(config.database_path),
        "database_present": config.database_path.is_file(),
        "brand_revision": catalog.version.revision_id,
        "curriculum_revision": catalog.curriculum_revision.revision_id,
        "creative_provider_configured": bool(config.creative_llm.model),
        "nvidia_key_configured": bool(os.environ.get(config.creative_llm.api_key_env)),
        "ffmpeg_available": bool(shutil.which("ffmpeg")),
        "ffprobe_available": bool(shutil.which("ffprobe")),
        "character_pack": [
            {"revision_id": p.revision_id, "readiness": p.readiness} for p in catalog.pack_revisions
        ],
        "selected_visual_assets": selected,
        "youtube_enabled": yt.enabled,
        "youtube_credentials_present": yt.credentials_file.is_file(),
        "youtube_token_present": yt.token_file.is_file(),
        "youtube_contains_synthetic_media": yt.contains_synthetic_media,
        "expected_channel_id": config.expected_youtube_channel_id,
        "creative_planning": "open_editorial",
        "active_job": active_job,
    }
