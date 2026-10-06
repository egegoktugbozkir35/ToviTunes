"""Crash-released process exclusion around the existing SQLite owner-token lease."""

import os
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from hashlib import sha256

from tovitunes.persistence.db import Database
from tovitunes.persistence.leases import Lease, LeaseHeld, LeaseStore


@contextmanager
def production_execution(
    database: Database,
    resource: str,
    *,
    duration_seconds: float = 14400,
) -> Iterator[tuple[LeaseStore, Lease]]:
    """A dead process must not leave the next invocation waiting for a four-hour TTL.

    All pipeline writers acquire the same OS file lock beside this database first.
    Obtaining it proves that no live writer holds this resource; only then may a
    crashed writer's SQLite lease be reclaimed. OS locks release on hard process exit.
    """
    if not resource.startswith(("short-production:", "creative-planning:")):
        raise ValueError("production lock is restricted to production resources")
    directory = database.path.parent / ".production-locks"
    directory.mkdir(exist_ok=True)
    path = directory / (sha256(resource.encode()).hexdigest() + ".lock")
    with path.open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LeaseHeld(resource) from exc
        try:
            with closing(database.connect()) as db:
                db.execute(
                    "DELETE FROM execution_leases WHERE resource_key=? AND owner_token IN "
                    "(SELECT owner_token FROM workflow_process_leases WHERE resource_key=?)",
                    (resource, resource),
                )
                db.commit()
            leases = LeaseStore(database)
            lease = leases.acquire(resource, duration_seconds=duration_seconds, process_lock=True)
            try:
                yield leases, lease
            finally:
                leases.release(lease)
        finally:
            lock.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
