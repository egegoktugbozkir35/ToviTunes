"""Explicit, provider-free admission of a returned environment source after local repair."""

import json
import sqlite3
import tempfile
from contextlib import closing, nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from tovitunes.artifacts.store import ArtifactRecord, AssetStore, InputDependency
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
    with closing(database.connect()) as db:
        dependency = db.execute(
            "SELECT 1 FROM artifact_dependencies WHERE consumer_artifact_id=? "
            "AND input_artifact_id=? AND input_sha256=?",
            (normalized.identity.artifact_id, source.identity.artifact_id, source.sha256),
        ).fetchone()
    if (
        row["status"] not in {"terminal_failure", "remote_started", "ambiguous"}
        or row["response_sha256"] and source.sha256 != row["response_sha256"]
        or row["response_byte_count"] and source.byte_count != row["response_byte_count"]
        or source.provenance.request_id != row["provider_request_id"]
        or normalized.provenance.request_id != row["provider_request_id"]
        or source.provenance.local_request_id != row["request_id"]
        or normalized.provenance.local_request_id != row["request_id"]
        or source.identity.owner_id != row["episode_id"]
        or normalized.identity.owner_id != row["episode_id"]
        or source.identity.slot_key != row["role"]
        or normalized.identity.slot_key != row["role"]
        or source.identity.kind != "environment_source_plate"
        or normalized.identity.kind != "environment_plate"
        or not dependency
        or not store.inspect(source.identity.artifact_id).valid
        or not store.inspect(normalized.identity.artifact_id).valid
    ):
        raise ValueError("reconciled environment source or plate evidence differs")
    result.update(
        status="succeeded", artifact_id=reconciliation["artifact_id"],
        response_metadata_json=reconciliation["response_metadata_json"],
        response_sha256=source.sha256, response_byte_count=source.byte_count,
        response_mime=source.mime_type,
    )
    return result


def returned_environment_source(store: AssetStore, row: sqlite3.Row) -> ArtifactRecord | None:
    with closing(store.database.connect()) as db:
        retained = db.execute(
            "SELECT artifact_id FROM artifact_versions WHERE episode_id=? "
            "AND kind='environment_source_plate' AND slot_key=? "
            "AND json_extract(provenance_json,'$.local_request_id')=? ORDER BY rowid LIMIT 1",
            (row["episode_id"], row["role"], row["request_id"]),
        ).fetchone()
    if retained is None:
        return None
    source = store.get(retained[0])
    if (
        source.provenance.request_id != row["provider_request_id"]
        or source.provenance.provider != row["provider"]
        or source.provenance.model != row["model"]
        or not store.inspect(source.identity.artifact_id).valid
    ):
        raise ValueError("retained environment source identity or bytes differ")
    return source


def reconcile_environment_source(
    config: RuntimeConfig, request_id: str, source_path: Path, *, actor: str, reason: str,
    ownership: ProductionExecutionOwnership | None = None,
) -> dict[str, Any]:
    from tovitunes.render.environment_sets import _validate_image

    if not actor.strip() or not reason.strip():
        raise ValueError("reconciliation requires an operator and evidence")
    database = Database(config.database_path)
    database.migrate()
    scope = nullcontext(ownership) if ownership else ProductionExecutionOwnership(
        database, operation="reconcile-environment-source",
    )
    with scope as owner:
        assert owner is not None
        owner.assert_owned()
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
            row is None or row["status"] not in {"terminal_failure", "remote_started", "ambiguous"}
            or row["provider"] != "qwen_comfyui" or not row["episode_id"]
            or not row["provider_request_id"]
        ):
            raise ValueError("only a conclusively returned Qwen environment may be reconciled")
        with closing(database.connect()) as db:
            retained = db.execute(
                "SELECT artifact_id FROM artifact_versions WHERE episode_id=? "
                "AND kind='environment_source_plate' AND slot_key=? "
                "AND json_extract(provenance_json,'$.local_request_id')=? "
                "ORDER BY rowid LIMIT 1",
                (row["episode_id"], row["role"], request_id),
            ).fetchone()
        retained_source = store.get(retained[0]) if retained else None
        if row["status"] != "terminal_failure" and (
            retained_source is None
            or retained_source.provenance.request_id != row["provider_request_id"]
            or not store.inspect(retained_source.identity.artifact_id).valid
        ):
            raise ValueError("interrupted environment requires a hash-validated retained source")
        expected_hash = row["response_sha256"] or (
            retained_source.sha256 if retained_source else None
        )
        expected_size = row["response_byte_count"] or (
            retained_source.byte_count if retained_source else None
        )
        data = source_path.read_bytes()
        if (
            sha256(data).hexdigest() != expected_hash
            or len(data) != expected_size
        ):
            raise ValueError("returned environment source hash or size differs from receipt")
        metadata = json.loads(row["response_metadata_json"] or "{}")
        dimensions = tuple(metadata.get("source_dimensions", (
            config.environment_generation.width, config.environment_generation.height,
        )))
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
                prompt_version=json.loads(row["spec_json"])["prompt_version"],
            )
            original = stage / "source.png"
            original.write_bytes(data)
            source = retained_source or store.ingest(
                original, owner_scope="episode", owner_id=row["episode_id"],
                kind="environment_source_plate", slot_key=row["role"],
                provenance=provenance, expected_media_type=row["response_mime"] or "image/png",
            )
            normalized_path = stage / "plate.png"
            image.save(normalized_path)
            normalized = _normalized_plate(
                store, row, normalized_path, source, provenance,
            )
            metadata.update(
                source_artifact_id=source.identity.artifact_id,
                response_sha256=source.sha256, source_dimensions=list(dimensions),
                normalized_dimensions=list(image.size),
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


def _normalized_plate(
    store: AssetStore, row: sqlite3.Row, path: Path, source: ArtifactRecord,
    provenance: Provenance,
) -> ArtifactRecord:
    with closing(store.database.connect()) as db:
        prior = db.execute(
            "SELECT artifact_id FROM artifact_versions WHERE episode_id=? "
            "AND kind='environment_plate' AND slot_key=? AND sha256=? "
            "AND json_extract(provenance_json,'$.local_request_id')=? ORDER BY rowid LIMIT 1",
            (row["episode_id"], row["role"], sha256(path.read_bytes()).hexdigest(),
             row["request_id"]),
        ).fetchone()
    if prior:
        record = store.get(prior[0])
        with closing(store.database.connect()) as db:
            dependency = db.execute(
                "SELECT 1 FROM artifact_dependencies WHERE consumer_artifact_id=? "
                "AND input_artifact_id=? AND input_sha256=?",
                (prior[0], source.identity.artifact_id, source.sha256),
            ).fetchone()
        if not dependency or not store.inspect(prior[0]).valid:
            raise ValueError("retained environment normalization differs from its source")
        return record
    return store.ingest(
        path, owner_scope="episode", owner_id=row["episode_id"],
        kind="environment_plate", slot_key=row["role"], provenance=provenance,
        dependencies=[InputDependency(source.identity.artifact_id, "provider_source")],
        expected_media_type="image/png",
    )


def resume_environment_sources(
    config: RuntimeConfig, episode_id: str, owner: ProductionExecutionOwnership,
) -> None:
    """Resume only proven local ingestion. Missing/uncertain remote results stay blocked."""
    database = Database(config.database_path)
    with closing(database.connect()) as db:
        rows = db.execute(
            "SELECT r.*,v.artifact_id AS source_id FROM environment_requests r "
            "JOIN artifact_versions v ON v.episode_id=r.episode_id "
            "AND v.kind='environment_source_plate' AND v.slot_key=r.role "
            "AND json_extract(v.provenance_json,'$.local_request_id')=r.request_id "
            "WHERE r.episode_id=? AND r.status IN ('remote_started','ambiguous') "
            "AND r.request_id NOT IN (SELECT request_id FROM environment_source_reconciliations)",
            (episode_id,),
        ).fetchall()
    store = AssetStore(config.data_root, database, initialize=False)
    for row in rows:
        owner.assert_owned()
        reconcile_environment_source(
            config, row["request_id"], store.path_for(row["source_id"]),
            actor="production-resume", reason="Interrupted ingestion of retained provider source",
            ownership=owner,
        )
