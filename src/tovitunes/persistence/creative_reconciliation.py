"""Immutable operator acknowledgement, separate from remote request outcomes."""

from contextlib import closing
from datetime import UTC, datetime
from sqlite3 import IntegrityError, Row
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

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

    def abandon(
        self,
        request_id: str,
        *,
        actor: str,
        rationale: str,
        evidence_uri: str | None = None,
    ) -> Row:
        # Operator prose is audit evidence, never a provider body. Do not echo invalid input.
        for value, limit in ((actor, 200), (rationale, 2000)):
            if not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
                raise ValueError("operator actor and rationale must be bounded nonempty text")
        if evidence_uri is not None:
            uri = urlsplit(evidence_uri)
            if (
                not evidence_uri.strip()
                or len(evidence_uri) > 2000
                or uri.username is not None
                or uri.password is not None
                or uri.query
                or uri.fragment
                or any(ord(c) < 32 for c in evidence_uri)
            ):
                raise ValueError("evidence URI must not contain credentials or signed parameters")
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if (
                    db.execute(
                        "SELECT 1 FROM generation_requests WHERE request_id=?", (request_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError("creative request does not exist")
                prior = db.execute(
                    "SELECT * FROM creative_request_reconciliations WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if prior is not None:
                    if (prior["actor"], prior["rationale"], prior["evidence_uri"]) != (
                        actor,
                        rationale,
                        evidence_uri,
                    ):
                        raise ValueError("request already has an immutable reconciliation")
                    db.commit()
                    return cast(Row, prior)
                identity = str(uuid4())
                db.execute(
                    "INSERT INTO creative_request_reconciliations "
                    "(reconciliation_id,request_id,action,actor,rationale,evidence_uri,created_at) "
                    "VALUES (?,?,'abandon_remote_result',?,?,?,?)",
                    (
                        identity,
                        request_id,
                        actor,
                        rationale,
                        evidence_uri,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                saved = db.execute(
                    "SELECT * FROM creative_request_reconciliations WHERE reconciliation_id=?",
                    (identity,),
                ).fetchone()
                assert saved is not None
                db.commit()
                return cast(Row, saved)
            except IntegrityError:
                db.rollback()
                raise ValueError("request is not eligible for creative abandonment") from None
            except Exception:
                db.rollback()
                raise
