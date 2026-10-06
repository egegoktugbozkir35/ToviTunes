"""Cross-process production execution ownership and heartbeat management."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event, Lock, Thread
from types import TracebackType
from typing import Protocol

from tovitunes.errors import ExecutionOwnershipLostError

logger = logging.getLogger(__name__)

DEFAULT_EXECUTION_LEASE_TTL = timedelta(minutes=2)
DEFAULT_EXECUTION_HEARTBEAT_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class ProductionExecutionLease:
    """Persisted diagnostic view of the singleton production lease."""

    owner_token: str
    operation: str
    item_id: str | None
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime


class ProductionExecutionLeaseStore(Protocol):
    def acquire_production_execution(
        self,
        *,
        operation: str,
        item_id: str | None,
        ttl: timedelta,
    ) -> ProductionExecutionLease: ...

    def renew_production_execution(
        self,
        owner_token: str,
        *,
        ttl: timedelta,
    ) -> ProductionExecutionLease: ...

    def release_production_execution(self, owner_token: str) -> None: ...


class ProductionExecutionOwnership:
    """Own the global production lease and renew it while live work is in flight."""

    def __init__(
        self,
        store: ProductionExecutionLeaseStore,
        *,
        operation: str,
        item_id: str | None = None,
        ttl: timedelta = DEFAULT_EXECUTION_LEASE_TTL,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        ttl_seconds = ttl.total_seconds()
        if ttl_seconds <= 0:
            raise ValueError("production execution lease TTL must be positive")
        interval = heartbeat_interval_seconds
        if interval is None:
            interval = min(
                DEFAULT_EXECUTION_HEARTBEAT_INTERVAL_SECONDS,
                ttl_seconds / 4,
            )
        if interval <= 0 or interval >= ttl_seconds:
            raise ValueError("heartbeat interval must be positive and shorter than the lease TTL")

        self._store = store
        self._operation = operation
        self._item_id = item_id
        self._ttl = ttl
        self._heartbeat_interval_seconds = interval
        self._stop = Event()
        self._lock = Lock()
        self._lease: ProductionExecutionLease | None = None
        self._loss: ExecutionOwnershipLostError | None = None
        self._heartbeat_thread: Thread | None = None

    @property
    def owner_token(self) -> str:
        with self._lock:
            lease = self._lease
        if lease is None:
            raise ExecutionOwnershipLostError("production execution ownership is not active")
        return lease.owner_token

    def __enter__(self) -> ProductionExecutionOwnership:
        lease = self._store.acquire_production_execution(
            operation=self._operation,
            item_id=self._item_id,
            ttl=self._ttl,
        )
        with self._lock:
            self._lease = lease
        heartbeat = Thread(
            target=self._heartbeat_loop,
            name="production-execution-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread = heartbeat
        try:
            heartbeat.start()
        except BaseException:
            self._store.release_production_execution(lease.owner_token)
            with self._lock:
                self._lease = None
            raise
        return self

    def _remember_loss(self, error: ExecutionOwnershipLostError) -> None:
        with self._lock:
            if self._loss is None:
                self._loss = error
        self._stop.set()

    def _renew(self) -> None:
        with self._lock:
            lease = self._lease
            loss = self._loss
        if loss is not None:
            raise loss
        if lease is None:
            raise ExecutionOwnershipLostError("production execution ownership is not active")
        try:
            renewed = self._store.renew_production_execution(
                lease.owner_token,
                ttl=self._ttl,
            )
        except ExecutionOwnershipLostError as exc:
            self._remember_loss(exc)
            raise
        except Exception as exc:
            error = ExecutionOwnershipLostError(
                "production execution ownership could not be renewed"
            )
            self._remember_loss(error)
            raise error from exc
        with self._lock:
            self._lease = renewed

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_interval_seconds):
            try:
                self._renew()
            except ExecutionOwnershipLostError:
                logger.exception("production execution heartbeat lost ownership")
                return

    def assert_owned(self) -> None:
        """Fail closed and extend the lease immediately before a live boundary."""

        self._renew()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc, traceback
        self._stop.set()
        heartbeat = self._heartbeat_thread
        if heartbeat is not None:
            heartbeat.join()

        with self._lock:
            lease = self._lease
            loss = self._loss
        release_error: ExecutionOwnershipLostError | None = None
        if lease is not None:
            try:
                self._store.release_production_execution(lease.owner_token)
            except ExecutionOwnershipLostError as error:
                release_error = error
            except Exception as error:
                release_error = ExecutionOwnershipLostError(
                    "production execution ownership could not be released safely"
                )
                release_error.__cause__ = error
        with self._lock:
            self._lease = None
        if exc_type is None:
            if loss is not None:
                raise loss
            if release_error is not None:
                raise release_error
