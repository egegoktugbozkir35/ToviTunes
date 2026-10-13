"""Read-only production prerequisite inspection, without migration or generation."""

import importlib.util
import os
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from tovitunes.artifacts.character_intake import load_recipe
from tovitunes.artifacts.character_lock import load_lock, validate_pack_lock
from tovitunes.artifacts.media import validate_media
from tovitunes.benchmark.providers import QwenComfyUIImageProvider
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.render.ffmpeg import doctor


def production_preflight(config: RuntimeConfig) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for name, generation in (
        ("illustrations", config.lesson_object_generation),
        ("environments", config.environment_generation),
    ):
        if generation.provider != "qwen_comfyui":
            checks[name] = {"status": "misconfigured", "reason": "production_requires_qwen"}
        else:
            checks[name] = QwenComfyUIImageProvider(
                generation.workflow_path,
                base_url=generation.base_url,
                width=generation.width,
                height=generation.height,
                steps=generation.steps,
                cfg=generation.cfg,
                sampler=generation.sampler,
                scheduler=generation.scheduler,
            ).preflight()
    try:
        checks["ffmpeg"] = {"status": "verified", **doctor()}
    except (ValueError, OSError, RuntimeError, TimeoutError):
        checks["ffmpeg"] = {"status": "missing"}
    checks["moviepy"] = {"status": "verified" if importlib.util.find_spec("moviepy") else "missing"}
    try:
        catalog = load_brand(config.brand_root)
        checks["character_catalog"] = {
            "status": "verified"
            if all(p.readiness == "approved" for p in catalog.packs)
            else "misconfigured",
            "pack_count": len(catalog.packs),
        }
    except (ValueError, OSError, KeyError):
        checks["character_catalog"] = {"status": "missing"}
    try:
        pack_root = config.brand_root / "characters/tovi/packs/v1"
        recipe_path = pack_root / "intake.yaml"
        lock = load_lock(pack_root / "artifact-lock.yaml")
        validate_pack_lock(
            lock, load_brand(config.brand_root), load_recipe(recipe_path), recipe_path
        )
        invalid = []
        for artifact in lock.artifacts:
            path = (config.data_root / artifact.relative_path).resolve()
            if not path.is_relative_to(config.data_root.resolve()) or not path.is_file():
                invalid.append(artifact.artifact_id)
                continue
            validate_media(path)
            if (
                path.stat().st_size != artifact.byte_count
                or sha256(path.read_bytes()).hexdigest() != artifact.sha256
            ):
                invalid.append(artifact.artifact_id)
        checks["character_media"] = {
            "status": "verified" if not invalid else "misconfigured",
            "checked_assets": len(lock.artifacts),
            "invalid_artifact_ids": invalid,
        }
    except (ValueError, OSError, KeyError):
        checks["character_media"] = {"status": "missing"}
    checks["filesystem"] = {
        "status": "verified"
        if config.data_root.is_dir() and os.access(config.data_root, os.R_OK | os.W_OK)
        else "missing"
    }
    try:
        with sqlite3.connect(config.database_path.as_uri() + "?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            applied = dict(db.execute("SELECT version,checksum FROM schema_migrations"))
        migrations = {
            p.name: sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
            for p in (Path(__file__).parent / "persistence/migrations").glob("*.sql")
        }
        mismatched = [
            name
            for name, digest in applied.items()
            if name not in migrations or migrations[name] != digest
        ]
        checks["database"] = {
            "status": "verified" if integrity == "ok" and not mismatched else "misconfigured",
            "pending_migrations": sorted(migrations.keys() - applied.keys()),
            "checksum_mismatches": mismatched,
        }
    except (sqlite3.Error, OSError):
        checks["database"] = {"status": "missing"}
    try:
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(config.music_generation.base_url + "/openapi.json")
            response.raise_for_status()
            paths = response.json().get("paths", {})
            missing = [p for p in ("/release_task", "/query_result", "/v1/audio") if p not in paths]
            models_response = client.get(config.music_generation.base_url + "/v1/models")
            models_response.raise_for_status()
            models = models_response.json()
        serialized = str(models)
        checks["ace_step"] = {
            "status": "verified"
            if not missing and config.music_generation.model in serialized
            else "not_verified_without_live_call"
            if not missing and models.get("data") == []
            else "misconfigured",
            "missing_routes": missing,
            "configured_model_advertised": config.music_generation.model in serialized,
            "generation": "not_verified_without_live_call",
        }
    except (httpx.HTTPError, ValueError, AttributeError):
        checks["ace_step"] = {"status": "unavailable"}
    checks["creative_provider"] = {
        "status": "not_verified_without_live_call"
        if os.environ.get(config.creative_llm.api_key_env)
        else "missing",
        "credential_present": bool(os.environ.get(config.creative_llm.api_key_env)),
        "model_chain": [config.creative_llm.model, *config.creative_llm.fallback_models],
    }
    return {
        "readiness": "NOT VERIFIED",
        "provider_effects": 0,
        "prerequisites_passed": all(
            c["status"] in {"verified", "not_verified_without_live_call"} for c in checks.values()
        ),
        "checks": checks,
    }
