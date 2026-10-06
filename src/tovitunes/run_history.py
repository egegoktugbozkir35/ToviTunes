"""MPT-derived best-effort diagnostics; never consulted by continuation."""

import logging
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4

from tovitunes.persistence.db import Database

logger = logging.getLogger(__name__)


class RunHistory:
    def __init__(self, database: Database) -> None:
        self.database = database

    def start(self, operation: str, target: str, key: str | None) -> str | None:
        run_id = str(uuid4())
        try:
            with closing(self.database.connect()) as db:
                db.execute(
                    "INSERT INTO production_runs VALUES (?,?,?,?,?,NULL,'running')",
                    (run_id, operation, target, key, datetime.now(UTC).isoformat()),
                )
                db.commit()
            return run_id
        except Exception:
            logger.exception("failed to start diagnostic history")
            return None

    def finish(self, run_id: str | None, outcome: str) -> None:
        if run_id is None:
            return
        try:
            with closing(self.database.connect()) as db:
                db.execute(
                    "UPDATE production_runs SET outcome=?,finished_at=? WHERE run_id=?",
                    (outcome, datetime.now(UTC).isoformat(), run_id),
                )
                db.commit()
        except Exception:
            logger.exception("failed to finalize diagnostic history; production is unchanged")
