"""Durable identity for expensive generation requests and ambiguous outcomes."""

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
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
                    "INSERT INTO generation_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

