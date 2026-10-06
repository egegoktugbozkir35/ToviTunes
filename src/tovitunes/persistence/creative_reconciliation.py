"""Read-only access to immutable historical PR40 operator evidence."""

from contextlib import closing
from sqlite3 import Row
from typing import cast

from tovitunes.persistence.db import Database


class CreativeReconciliations:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, request_id: str) -> Row | None:
        with closing(self.database.connect()) as db:
            return cast(
                Row | None,
                db.execute(
                    "SELECT * FROM creative_request_reconciliations WHERE request_id=?",
                    (request_id,),
                ).fetchone(),
            )
