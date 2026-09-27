"""Durable identity for expensive generation requests and ambiguous outcomes."""

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from sqlite3 import Row
from typing import Literal, cast
from uuid import uuid4

from tovitunes.persistence.db import Database

RequestStatus = Literal["prepared", "remote_started", "succeeded", "failed", "ambiguous"]


@dataclass(frozen=True)
class GenerationRequest:
    request_id: str
    episode_id: str
    kind: str
    slot_key: str
    provider: str
    model: str
    input_fingerprint: str
    status: RequestStatus
    provider_request_id: str | None = None


class InvalidRequestTransition(ValueError):
    pass


class RequestLedger:
    def __init__(self, database: Database) -> None:
        self.database = database

    def prepare(
        self,
        episode_id: str,
        kind: str,
        slot_key: str,
        provider: str,
        model: str,
        input_fingerprint: str,
    ) -> GenerationRequest:
        if len(input_fingerprint) != 64 or any(
            c not in "0123456789abcdef" for c in input_fingerprint
        ):
            raise ValueError("input fingerprint must be lowercase SHA-256")
        request = GenerationRequest(
            str(uuid4()), episode_id, kind, slot_key, provider, model, input_fingerprint, "prepared"
        )
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                unresolved = connection.execute(
                    "SELECT request_id FROM generation_requests WHERE episode_id = ? "
                    "AND kind = ? AND slot_key = ? AND status IN "
                    "('prepared', 'remote_started', 'ambiguous', 'succeeded') LIMIT 1",
                    (episode_id, kind, slot_key),
                ).fetchone()
                if unresolved is not None:
                    raise InvalidRequestTransition("existing request must be resolved or ingested")
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    "INSERT INTO generation_requests "
                    "(request_id, episode_id, kind, slot_key, provider, model, "
                    "provider_request_id, input_fingerprint, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.request_id,
                        episode_id,
                        kind,
                        slot_key,
                        provider,
                        model,
                        None,
                        input_fingerprint,
                        "prepared",
                        now,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return request

    def transition(
        self,
        request_id: str,
        status: RequestStatus,
        *,
        provider_request_id: str | None = None,
        definitive_remote_failure: bool = False,
    ) -> GenerationRequest:
        allowed: dict[RequestStatus, set[RequestStatus]] = {
            "prepared": {"remote_started", "failed"},
            "remote_started": {"succeeded", "ambiguous"},
            "ambiguous": {"succeeded"},
            "succeeded": set(),
            "failed": set(),
        }
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM generation_requests WHERE request_id = ?", (request_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(request_id)
                prior: RequestStatus = row["status"]
                valid = status in allowed[prior]
                if prior == "remote_started" and status == "failed" and definitive_remote_failure:
                    valid = True
                if prior == "ambiguous" and status == "failed" and definitive_remote_failure:
                    valid = True
                if not valid:
                    raise InvalidRequestTransition(f"{prior} -> {status} is not safe")
                remote_id = provider_request_id or row["provider_request_id"]
                connection.execute(
                    "UPDATE generation_requests SET status = ?, provider_request_id = ?, "
                    "updated_at = ? WHERE request_id = ?",
                    (status, remote_id, datetime.now(UTC).isoformat(), request_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return GenerationRequest(
            request_id=row["request_id"],
            episode_id=row["episode_id"],
            kind=row["kind"],
            slot_key=row["slot_key"],
            provider=row["provider"],
            model=row["model"],
            input_fingerprint=row["input_fingerprint"],
            status=status,
            provider_request_id=remote_id,
        )

    def unresolved(
        self, episode_id: str, kind: str, slot_key: str
    ) -> tuple[GenerationRequest, ...]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM generation_requests WHERE episode_id = ? AND kind = ? "
                "AND slot_key = ? AND status IN ('remote_started', 'ambiguous') "
                "ORDER BY created_at",
                (episode_id, kind, slot_key),
            ).fetchall()
        return tuple(
            GenerationRequest(
                request_id=row["request_id"],
                episode_id=row["episode_id"],
                kind=row["kind"],
                slot_key=row["slot_key"],
                provider=row["provider"],
                model=row["model"],
                input_fingerprint=row["input_fingerprint"],
                status=row["status"],
                provider_request_id=row["provider_request_id"],
            )
            for row in rows
        )


class CreativeRequestLedger:
    """Narrow extension of generation_requests for reusable structured responses."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, request_id: str) -> Row:
        with closing(self.database.connect()) as db:
            row = db.execute(
                "SELECT * FROM generation_requests WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return cast(Row, row)

    def find(
        self, episode_id: str | None, run_id: str | None, kind: str, fingerprint: str
    ) -> Row | None:
        with closing(self.database.connect()) as db:
            return cast(
                Row | None,
                db.execute(
                    "SELECT * FROM generation_requests WHERE episode_id IS ? AND run_id IS ? "
                    "AND kind=? AND input_fingerprint=? AND prompt_version IS NOT NULL",
                    (episode_id, run_id, kind, fingerprint),
                ).fetchone(),
            )

    def prepare_creative(
        self,
        *,
        episode_id: str | None,
        run_id: str | None,
        kind: str,
        provider: str,
        model: str,
        prompt_version: str,
        input_fingerprint: str,
        messages_json: str,
        attempt: int = 1,
        parent_request_id: str | None = None,
    ) -> Row:
        request_id, now = str(uuid4()), datetime.now(UTC).isoformat()
        family = kind.removesuffix("_repair")
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                blocked = db.execute(
                    "SELECT request_id FROM generation_requests WHERE episode_id IS ? "
                    "AND run_id IS ? AND kind IN (?,?) AND slot_key='main' "
                    "AND status IN ('prepared','remote_started','ambiguous') LIMIT 1",
                    (episode_id, run_id, family, family + "_repair"),
                ).fetchone()
                if blocked:
                    raise InvalidRequestTransition(
                        f"unresolved creative request {blocked[0]}; explicit recovery required"
                    )
                db.execute(
                    "INSERT INTO generation_requests (request_id,episode_id,run_id,kind,slot_key,"
                    "provider,model,prompt_version,input_fingerprint,status,attempt,parent_request_id,"
                    "messages_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        episode_id,
                        run_id,
                        kind,
                        "main",
                        provider,
                        model,
                        prompt_version,
                        input_fingerprint,
                        "prepared",
                        attempt,
                        parent_request_id,
                        messages_json,
                        now,
                        now,
                    ),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get(request_id)

    def start(self, request_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self.database.connect()) as db:
            cursor = db.execute(
                "UPDATE generation_requests SET status='remote_started',remote_started_at=?,"
                "updated_at=? WHERE request_id=? AND status='prepared'",
                (now, now, request_id),
            )
            if cursor.rowcount != 1:
                raise InvalidRequestTransition("creative request already started")
            db.commit()

    def identity(self, request_id: str, provider_request_id: str) -> None:
        with closing(self.database.connect()) as db:
            db.execute(
                "UPDATE generation_requests SET provider_request_id=?,updated_at=? "
                "WHERE request_id=? AND status='remote_started'",
                (provider_request_id, datetime.now(UTC).isoformat(), request_id),
            )
            db.commit()

    def receipt(self, request_id: str, content: str, provider_request_id: str | None) -> None:
        with closing(self.database.connect()) as db:
            cursor = db.execute(
                "UPDATE generation_requests SET response_content=?,response_sha256=?,"
                "provider_request_id=coalesce(?,provider_request_id),updated_at=? "
                "WHERE request_id=? AND status='remote_started' AND response_content IS NULL",
                (
                    content,
                    sha256(content.encode("utf-8")).hexdigest(),
                    provider_request_id,
                    datetime.now(UTC).isoformat(),
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise InvalidRequestTransition(
                    "creative receipt already exists or request not started"
                )
            db.commit()

    def finish(
        self,
        request_id: str,
        status: Literal["succeeded", "succeeded_response_invalid", "ambiguous", "failed"],
        *,
        error_kind: str | None = None,
        error_reason: str | None = None,
    ) -> None:
        with closing(self.database.connect()) as db:
            prior = db.execute(
                "SELECT status,response_content FROM generation_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if prior is None:
                raise KeyError(request_id)
            if prior["status"] == status:
                return
            if prior["status"] != "remote_started":
                raise InvalidRequestTransition(f"{prior['status']} -> {status} is not safe")
            if status in {"succeeded", "succeeded_response_invalid"} and prior[1] is None:
                raise InvalidRequestTransition("structured success requires a durable response")
            db.execute(
                "UPDATE generation_requests SET status=?,error_kind=?,error_reason=?,updated_at=? "
                "WHERE request_id=?",
                (
                    status,
                    error_kind,
                    error_reason[:2000] if error_reason else None,
                    datetime.now(UTC).isoformat(),
                    request_id,
                ),
            )
            db.commit()
