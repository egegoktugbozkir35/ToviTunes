"""Explicit, provider-free admission of a returned environment source after local repair."""

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.config import RuntimeConfig
from tovitunes.domain.artifact import Provenance
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.persistence.db import Database


def reconciled_row(database: Database, store: AssetStore, row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    with closing(database.connect()) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='environment_source_reconciliations'"
        ).fetchone()
        reconciliation = db.execute(
            "SELECT * FROM environment_source_reconciliations WHERE request_id=?",
            (row["request_id"],),
        ).fetchone() if exists else None
    if reconciliation is None:
        return result
    source = store.get(reconciliation["source_artifact_id"])
    normalized = store.get(reconciliation["artifact_id"])
    if (
        row["status"] != "terminal_failure"
        or source.sha256 != row["response_sha256"]
        or source.byte_count != row["response_byte_count"]
        or source.provenance.local_request_id != row["request_id"]
        or normalized.provenance.local_request_id != row["request_id"]
        or source.identity.owner_id != row["episode_id"]
        or normalized.identity.owner_id != row["episode_id"]
        or source.identity.slot_key != row["role"]
        or normalized.identity.slot_key != row["role"]
        or not store.inspect(source.identity.artifact_id).valid
        or not store.inspect(normalized.identity.artifact_id).valid
    ):
        raise ValueError("reconciled environment source or plate evidence differs")
    result.update(
        status="succeeded", artifact_id=reconciliation["artifact_id"],
        response_metadata_json=reconciliation["response_metadata_json"],
    )
    return result


def reconcile_environment_source(
    config: RuntimeConfig, request_id: str, source_path: Path, *, actor: str, reason: str
) -> dict[str, Any]:
    from tovitunes.render.environment_sets import PROMPT_VERSION, _validate_image

    if not actor.strip() or not reason.strip():
        raise ValueError("reconciliation requires an operator and evidence")
    database = Database(config.database_path)
    database.migrate()
    with ProductionExecutionOwnership(database, operation="reconcile-environment-source") as owner:
        with closing(database.connect()) as db:
            row = db.execute(
                "SELECT * FROM environment_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            prior = db.execute(
                "SELECT artifact_id FROM environment_source_reconciliations WHERE request_id=?",
                (request_id,),
            ).fetchone()
        store = AssetStore(config.data_root, database, initialize=False)
        if prior:
            assert row is not None
            reconciled_row(database, store, row)
            return {"request_id": request_id, "artifact_id": prior[0], "provider_effects": 0}
        if (
            row is None or row["status"] != "terminal_failure"
            or row["provider"] != "qwen_comfyui" or not row["episode_id"]
            or not row["provider_request_id"] or not row["response_sha256"]
        ):
            raise ValueError("only a conclusively returned Qwen environment may be reconciled")
        data = source_path.read_bytes()
        if (
            sha256(data).hexdigest() != row["response_sha256"]
            or len(data) != row["response_byte_count"]
        ):
            raise ValueError("returned environment source hash or size differs from receipt")
        metadata = json.loads(row["response_metadata_json"])
        dimensions = tuple(metadata["source_dimensions"])
        if len(dimensions) != 2:
            raise ValueError("environment receipt lacks dimensions")
        image = _validate_image(data, (dimensions[0], dimensions[1]), allow_near_opaque_alpha=True)
        owner.assert_owned()
        working = config.data_root / ".environment-working"
        working.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=working) as dirname:
            stage = Path(dirname)
            store = AssetStore(config.data_root, database, generated_source_roots=[stage])
            provenance = Provenance(
                source_kind="provider", acquired_at=datetime.now(UTC),
                provider=row["provider"], model=row["model"],
                request_id=row["provider_request_id"], local_request_id=request_id,
                prompt_version=PROMPT_VERSION,
            )
            original = stage / "source.png"
            original.write_bytes(data)
            source = store.ingest(
                original, owner_scope="episode", owner_id=row["episode_id"],
                kind="environment_source_plate", slot_key=row["role"],
                provenance=provenance, expected_media_type=row["response_mime"],
            )
            normalized_path = stage / "plate.png"
            image.save(normalized_path)
            normalized = store.ingest(
                normalized_path, owner_scope="episode", owner_id=row["episode_id"],
                kind="environment_plate", slot_key=row["role"], provenance=provenance,
                dependencies=[InputDependency(source.identity.artifact_id, "provider_source")],
                expected_media_type="image/png",
            )
            metadata.update(
                source_artifact_id=source.identity.artifact_id,
                response_sha256=source.sha256, normalized_dimensions=list(image.size),
                technical_validation="passed", alpha_normalization="qwen_near_opaque_v1",
            )
            owner.assert_owned()
            with closing(database.connect()) as db, db:
                db.execute(
                    "INSERT INTO environment_source_reconciliations VALUES (?,?,?,?,?,?,?)",
                    (request_id, source.identity.artifact_id, normalized.identity.artifact_id,
                     json.dumps(metadata, sort_keys=True), actor.strip(), reason.strip(),
                     datetime.now(UTC).isoformat()),
                )
        return {"request_id": request_id, "artifact_id": normalized.identity.artifact_id,
                "provider_effects": 0}
