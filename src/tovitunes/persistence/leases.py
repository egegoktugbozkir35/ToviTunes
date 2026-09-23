"""Per-resource SQLite leases with owner-token fencing."""

import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from uuid import uuid4

from tovitunes.persistence.db import Database


@dataclass(frozen=True)
class Lease:
    resource_key: str
    owner_token: str
    expires_at: float


class LeaseHeld(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class LeaseStore:
    def __init__(self, database: Database, *, clock: Callable[[], float] = time.time) -> None:
        self.database = database
        self.clock = clock

    def acquire(self, resource_key: str, *, duration_seconds: float) -> Lease:
        if duration_seconds <= 0 or not resource_key:
            raise ValueError("lease requires a resource key and positive duration")
        now = self.clock()
        token = str(uuid4())
        expiry = now + duration_seconds
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT expires_at FROM execution_leases WHERE resource_key = ?",
                    (resource_key,),
                ).fetchone()
                if row is not None and row["expires_at"] > now:
                    raise LeaseHeld(resource_key)
                connection.execute(
                    "INSERT INTO execution_leases VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(resource_key) DO UPDATE SET "
                    "owner_token = excluded.owner_token, acquired_at = excluded.acquired_at, "
                    "expires_at = excluded.expires_at",
                    (resource_key, token, now, expiry),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return Lease(resource_key, token, expiry)

    def assert_owner(self, lease: Lease) -> None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT owner_token, expires_at FROM execution_leases WHERE resource_key = ?",
                (lease.resource_key,),
            ).fetchone()
        if (
            row is None
            or row["owner_token"] != lease.owner_token
            or row["expires_at"] <= self.clock()
        ):
            raise LeaseLost(lease.resource_key)

    def renew(self, lease: Lease, *, duration_seconds: float) -> Lease:
        if duration_seconds <= 0:
            raise ValueError("lease duration must be positive")
        now = self.clock()
        expiry = now + duration_seconds
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "UPDATE execution_leases SET expires_at = ? WHERE resource_key = ? "
                    "AND owner_token = ? AND expires_at > ?",
                    (expiry, lease.resource_key, lease.owner_token, now),
                )
                if cursor.rowcount != 1:
                    raise LeaseLost(lease.resource_key)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return Lease(lease.resource_key, lease.owner_token, expiry)

    def release(self, lease: Lease) -> None:
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "DELETE FROM execution_leases WHERE resource_key = ? AND owner_token = ?",
                    (lease.resource_key, lease.owner_token),
                )
                if cursor.rowcount != 1:
                    raise LeaseLost(lease.resource_key)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

