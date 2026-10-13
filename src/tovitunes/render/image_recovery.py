"""Operator-authorized new image attempts after definitive failures, never ambiguous requests."""

from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tovitunes.config import RuntimeConfig
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.persistence.db import Database


def authorize_image_retry(
    config: RuntimeConfig, request_id: str, *, confirmed: bool, actor: str, reason: str
) -> dict[str, Any]:
    if not confirmed or not actor.strip() or not reason.strip():
        raise ValueError(
            "image retry requires explicit definitive-failure confirmation and evidence"
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
            if old is None or old["kind"] != "visual_asset" or old["status"] != "failed":
                raise ValueError(
                    "only a definitively failed illustration may receive a new attempt"
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
            source = db.execute(
                "SELECT source_artifact_id FROM production_image_receipts WHERE request_id=?",
                (request_id,),
            ).fetchone()
            selected = db.execute(
                "SELECT 1 FROM artifact_selections WHERE owner_id=? "
                "AND kind='visual_asset' AND slot_key=?",
                (old["episode_id"], old["slot_key"]),
            ).fetchone()
            latest = db.execute(
                "SELECT request_id FROM generation_requests WHERE episode_id=? "
                "AND kind='visual_asset' AND slot_key=? ORDER BY rowid DESC LIMIT 1",
                (old["episode_id"], old["slot_key"]),
            ).fetchone()
            if selected or source and source[0] or latest[0] != request_id:
                raise ValueError("image already has retained work or a newer request; recover it")
            new_id, now = str(uuid4()), datetime.now(UTC).isoformat()
            db.execute(
                "INSERT INTO generation_requests (request_id,episode_id,kind,slot_key,"
                "provider,model,input_fingerprint,status,created_at,updated_at,"
                "parent_request_id,attempt) VALUES (?,?,?,?,?,?,?,'prepared',?,?,?,2)",
                (
                    new_id,
                    old["episode_id"],
                    old["kind"],
                    old["slot_key"],
                    old["provider"],
                    old["model"],
                    old["input_fingerprint"],
                    now,
                    now,
                    request_id,
                ),
            )
            db.execute(
                "INSERT INTO image_retry_authorizations VALUES (?,?,?,?,?)",
                (request_id, new_id, actor.strip(), reason.strip(), now),
            )
            database.assert_production_execution_owner(owner.owner_token)
    return {"failed_request_id": request_id, "new_request_id": new_id, "provider_effects": 0}
