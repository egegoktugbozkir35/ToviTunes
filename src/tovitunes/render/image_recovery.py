"""Explicit new image identities; native crashes require exceptional, retained evidence."""

import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from sqlite3 import Row
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from tovitunes.config import RuntimeConfig
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.persistence.db import Database


def authorize_image_retry(
    config: RuntimeConfig, request_id: str, *, confirmed: bool, actor: str, reason: str,
    native_crash_evidence: Path | None = None,
) -> dict[str, Any]:
    if not confirmed or not actor.strip() or not reason.strip():
        raise ValueError(
            "image retry requires explicit failure or native-crash confirmation and evidence"
        )
    database = Database(config.database_path)
    database.migrate()
    with ProductionExecutionOwnership(database, operation="authorize-image-retry") as owner:
        owner.assert_owned()
        with closing(database.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM generation_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            exceptional = native_crash_evidence is not None
            if (
                old is None or old["kind"] != "visual_asset"
                or old["status"] != ("ambiguous" if exceptional else "failed")
                or exceptional and old["provider"] != "qwen_comfyui"
            ):
                raise ValueError(
                    "only a definitively failed illustration or evidenced local native crash "
                    "may receive a new attempt"
                )
            prior = db.execute(
                "SELECT new_request_id FROM image_retry_authorizations WHERE failed_request_id=?",
                (request_id,),
            ).fetchone()
            if prior:
                return {
                    "failed_request_id": request_id,
                    "new_request_id": prior[0],
                    "provider_effects": 0,
                }
            source = _retained_image_checks(db, old, request_id)
            evidence = None
            if native_crash_evidence is not None:
                if source is None:
                    raise ValueError("native crash requires the original image receipt")
                profile = json.loads(source["provider_profile_json"])
                if (
                    source["provider_request_id"] not in {None, old["provider_request_id"]}
                    or sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
                    != old["input_fingerprint"]
                    or profile["translated"]["endpoint"]
                    != config.lesson_object_generation.base_url.rstrip("/") + "/prompt"
                    or profile["translated"]["method"] != "POST"
                ):
                    raise ValueError("original image receipt identity or fingerprint differs")
                evidence = _native_crash_checks(
                    config, old, json.loads(native_crash_evidence.read_text(encoding="utf-8"))
                )
            new_id, now = str(uuid4()), datetime.now(UTC).isoformat()
            db.execute(
                "INSERT INTO generation_requests (request_id,episode_id,run_id,kind,slot_key,"
                "provider,model,input_fingerprint,status,created_at,updated_at,"
                "parent_request_id,attempt) VALUES (?,?,?,?,?,?,?,?,'prepared',?,?,?,?)",
                (
                    new_id,
                    old["episode_id"],
                    old["run_id"],
                    old["kind"],
                    old["slot_key"],
                    old["provider"],
                    old["model"],
                    old["input_fingerprint"],
                    now,
                    now,
                    request_id,
                    old["attempt"] + 1,
                ),
            )
            if evidence is not None:
                assert source is not None
                db.execute(
                    "INSERT INTO production_image_receipts "
                    "(request_id,spec_json,provider_profile_json) VALUES (?,?,?)",
                    (new_id, source["spec_json"], source["provider_profile_json"]),
                )
            db.execute(
                "INSERT INTO image_retry_authorizations VALUES (?,?,?,?,?)",
                (request_id, new_id, actor.strip(), reason.strip(), now),
            )
            if evidence is not None:
                content = json.dumps(evidence, sort_keys=True)
                db.execute(
                    "INSERT INTO image_native_crash_abandonments VALUES (?,?,?)",
                    (request_id, content, sha256(content.encode()).hexdigest()),
                )
            database.assert_production_execution_owner(owner.owner_token)
    return {"failed_request_id": request_id, "new_request_id": new_id, "provider_effects": 0}


def _retained_image_checks(db: sqlite3.Connection, old: Row, latest_id: str) -> Row | None:
    source: Row | None = db.execute(
        "SELECT * FROM production_image_receipts WHERE request_id=?", (old["request_id"],)
    ).fetchone()
    selected = db.execute(
        "SELECT 1 FROM artifact_selections WHERE owner_id=? AND kind='visual_asset' AND slot_key=?",
        (old["episode_id"], old["slot_key"]),
    ).fetchone()
    latest = db.execute(
        "SELECT request_id FROM generation_requests WHERE episode_id=? "
        "AND kind='visual_asset' AND slot_key=? ORDER BY rowid DESC LIMIT 1",
        (old["episode_id"], old["slot_key"]),
    ).fetchone()
    retained = db.execute(
        "SELECT 1 FROM artifact_versions WHERE "
        "json_extract(provenance_json,'$.local_request_id')=? OR "
        "(? IS NOT NULL AND json_extract(provenance_json,'$.request_id')=?)",
        (old["request_id"], old["provider_request_id"], old["provider_request_id"]),
    ).fetchone()
    if (
        selected or retained or latest is None or latest[0] != latest_id
        or source and any(source[k] for k in (
            "source_artifact_id", "normalized_artifact_id", "response_sha256"
        ))
    ):
        raise ValueError("image already has retained work or a newer request; recover it")
    return source


def recheck_native_image_successor(
    config: RuntimeConfig, database: Database, request_id: str
) -> None:
    """A prepared grant is not permission to use stale provider observations."""
    with closing(database.connect()) as db:
        grant = db.execute(
            "SELECT a.failed_request_id,n.evidence_json,n.evidence_sha256 "
            "FROM image_retry_authorizations a JOIN image_native_crash_abandonments n "
            "ON n.request_id=a.failed_request_id WHERE a.new_request_id=?", (request_id,),
        ).fetchone()
        if grant is None:
            return
        old = db.execute(
            "SELECT * FROM generation_requests WHERE request_id=?", (grant[0],)
        ).fetchone()
        successor = db.execute(
            "SELECT * FROM generation_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if (
            old is None or successor is None or old["status"] != "ambiguous"
            or successor["status"] != "prepared" or successor["provider_request_id"] is not None
            or successor["parent_request_id"] != old["request_id"]
            or any(successor[k] != old[k] for k in (
                "episode_id", "run_id", "kind", "slot_key", "provider", "model", "input_fingerprint"
            ))
            or sha256(grant["evidence_json"].encode()).hexdigest() != grant["evidence_sha256"]
        ):
            raise ValueError("native crash successor identity or authorization evidence differs")
        _retained_image_checks(db, old, request_id)
    evidence = json.loads(grant["evidence_json"])
    _native_crash_checks(config, old, evidence)


def _native_crash_checks(config: RuntimeConfig, old: Row, evidence: Any) -> dict[str, Any]:
    """Fail closed. The operator attests log attribution and exclusive local-service use."""
    began = time.monotonic()
    expected = {
        "request_id": old["request_id"], "episode_id": old["episode_id"],
        "provider_request_id": old["provider_request_id"],
        "input_fingerprint": old["input_fingerprint"],
    }
    if (
        not old["provider_request_id"] or not isinstance(evidence, dict)
        or any(evidence.get(k) != v for k, v in expected.items())
        or evidence.get("confirm_log_attribution_and_exclusive_service") is not True
        or evidence.get("confirm_original_process_exited") is not True
        or evidence.get("confirm_no_output_directory_overrides") is not True
    ):
        raise ValueError("native crash identity and operator attestations are required")
    root = Path(evidence["comfyui_root"]).resolve(strict=True)
    crash = Path(evidence["crash_log_path"]).read_bytes()
    log = crash.decode("utf-8", errors="replace")
    if (
        sha256(crash).hexdigest() != evidence.get("crash_log_sha256")
        or not all(marker in log for marker in (
            "got prompt", "out of memory", "Fatal Python error: Aborted", "attention_split",
            str(root),
        ))
    ):
        raise ValueError("hash-matched local CUDA attention native-abort log required")
    base = config.lesson_object_generation.base_url.rstrip("/")
    url = urlparse(base)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("native crash abandonment is restricted to local ComfyUI")
    try:
        with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:
            observations = {}
            for endpoint in ("/system_stats", "/queue", f"/history/{old['provider_request_id']}"):
                response = client.get(endpoint)
                response.raise_for_status()
                observations[endpoint] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("live read-only ComfyUI checks unavailable; keep request blocked") from exc
    argv = observations["/system_stats"]["system"]["argv"]
    queue = observations["/queue"]
    if (
        not isinstance(argv, list) or not argv
        or not Path(argv[0]).is_absolute() or Path(argv[0]).resolve() != root / "main.py"
        or any(str(arg).split("=", 1)[0] in {
            "--base-directory", "--output-directory", "--temp-directory"
        } for arg in argv)
        or queue != {"queue_running": [], "queue_pending": []}
        or observations[f"/history/{old['provider_request_id']}"] != {}
    ):
        raise ValueError("ComfyUI identity, idle queue or absent task history not verified")
    # Reject even unattributed recent image bytes: a missing receipt is insufficient.
    since = datetime.fromisoformat(old["created_at"]).timestamp()
    roots = (root / "output", root / "temp", config.data_root)
    for directory in roots:
        if not directory.is_dir():
            raise ValueError("all provider output, temp and application roots must be inspectable")
        for item in directory.rglob("*"):
            if (
                item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                and item.stat().st_mtime >= since
            ):
                raise ValueError("possible retained image output exists; inspect and recover it")
    # Catch changes during directory inspection, and never accept a slow stale snapshot.
    try:
        with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:
            for endpoint in ("/queue", f"/history/{old['provider_request_id']}"):
                response = client.get(endpoint)
                response.raise_for_status()
                if response.json() != observations[endpoint]:
                    raise ValueError("ComfyUI queue/history changed during crash verification")
    except httpx.HTTPError as exc:
        raise ValueError("final ComfyUI verification unavailable; keep request blocked") from exc
    if time.monotonic() - began > 30:
        raise ValueError("ComfyUI crash verification exceeded freshness budget")
    return {
        **evidence, "crash_log": log, "observations": observations,
        "scanned_roots": [str(p) for p in roots],
        "checked_at": datetime.now(UTC).isoformat(),
    }
