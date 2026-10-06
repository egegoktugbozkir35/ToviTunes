"""Small transactional SQLite store for pinned episode identity."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import resources
from pathlib import Path
from uuid import uuid4

from tovitunes.catalog import BrandCatalog
from tovitunes.domain.episode import Episode, PinnedCharacterPack
from tovitunes.errors import (
    ExecutionOwnershipConflictError,
    ExecutionOwnershipError,
    ExecutionOwnershipLostError,
)
from tovitunes.execution import ProductionExecutionLease


def _sql_statements(sql: str) -> Iterator[str]:
    fragment = ""
    for line in sql.splitlines(keepends=True):
        fragment += line
        if sqlite3.complete_statement(fragment):
            if fragment.strip():
                yield fragment
            fragment = ""
    if fragment.strip():
        raise ValueError("incomplete migration SQL")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        migration_root = resources.files("tovitunes.persistence.migrations")
        files = sorted(
            (item for item in migration_root.iterdir() if item.name.endswith(".sql")),
            key=lambda item: item.name,
        )
        with closing(self.connect()) as connection:
            for item in files:
                sql = item.read_text(encoding="utf-8")
                checksum = sha256(sql.encode("utf-8")).hexdigest()
                rebuild = item.name == "0020_editorial_memory.sql"
                if rebuild:
                    connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS schema_migrations "
                        "(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
                        "applied_at TEXT NOT NULL)"
                    )
                    row = connection.execute(
                        "SELECT checksum FROM schema_migrations WHERE version = ?", (item.name,)
                    ).fetchone()
                    if row is not None:
                        if row["checksum"] != checksum:
                            raise ValueError(f"migration checksum changed: {item.name}")
                    else:
                        for statement in _sql_statements(sql):
                            connection.execute(statement)
                        connection.execute(
                            "INSERT INTO schema_migrations(version, checksum, applied_at) "
                            "VALUES (?, ?, ?)",
                            (item.name, checksum, datetime.now(UTC).isoformat()),
                        )
                    if rebuild and connection.execute("PRAGMA foreign_key_check").fetchall():
                        raise ValueError("editorial migration would break foreign key references")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    if rebuild:
                        connection.execute("PRAGMA foreign_keys = ON")

    def register_catalog(self, catalog: BrandCatalog) -> None:
        """Register the pinned brand and pack revisions before brand-only intake."""
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_catalog(connection, catalog)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_catalog(connection: sqlite3.Connection, catalog: BrandCatalog) -> None:
        brand = catalog.version
        connection.execute(
            "INSERT OR IGNORE INTO brand_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                brand.revision_id,
                brand.brand_id,
                brand.version,
                brand.definition_sha256,
                brand.creative_sha256,
                brand.safety_sha256,
                brand.source_revision,
            ),
        )
        curriculum = catalog.curriculum_revision
        connection.execute(
            "INSERT OR IGNORE INTO curriculum_revisions VALUES (?, ?, ?, ?)",
            (
                curriculum.revision_id,
                curriculum.curriculum_id,
                curriculum.version,
                curriculum.sha256,
            ),
        )
        for pack in catalog.pack_revisions:
            connection.execute(
                "INSERT OR IGNORE INTO character_pack_revisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    pack.revision_id,
                    pack.pack_id,
                    pack.character_id,
                    pack.version,
                    pack.manifest_sha256,
                    pack.readiness,
                ),
            )

    def create_episode(self, catalog: BrandCatalog, episode: Episode) -> None:
        if episode.learning_source == "generated_learning_brief":
            from tovitunes.creative.director import validate_pins

            validate_pins(episode, catalog, self)
        if episode.brand_revision_id != catalog.version.revision_id:
            raise ValueError("episode brand revision differs from catalog")
        if (
            episode.learning_source == "legacy_curriculum"
            and episode.curriculum_revision_id != catalog.curriculum_revision.revision_id
        ):
            raise ValueError("episode curriculum revision differs from catalog")
        if episode.learning_source == "legacy_curriculum":
            concept = catalog.curriculum.get(episode.concept_id)
            if (
                episode.objective_id != concept.objective_id
                or episode.objective != concept.objective
                or episode.target_vocabulary != concept.target_vocabulary
            ):
                raise ValueError("episode learning objective differs from curriculum")
        expected_packs = {(p.character_id, p.revision_id) for p in catalog.pack_revisions}
        actual_packs = {(p.character_id, p.revision_id) for p in episode.character_packs}
        if expected_packs != actual_packs:
            raise ValueError("episode character pack revisions differ from catalog")
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_catalog(connection, catalog)
                columns = (
                    "episode_id,external_key,brand_revision_id,curriculum_revision_id,"
                    "concept_id,objective_id,objective,target_vocabulary_json,language,"
                    "target_duration_seconds,lifecycle,created_at"
                )
                values: tuple[object, ...] = (
                    episode.episode_id,
                    episode.external_key,
                    episode.brand_revision_id,
                    episode.curriculum_revision_id,
                    episode.concept_id,
                    episode.objective_id,
                    episode.objective,
                    json.dumps(episode.target_vocabulary),
                    episode.language,
                    episode.target_duration_seconds,
                    episode.lifecycle,
                    episode.created_at.isoformat(),
                )
                if episode.learning_source == "generated_learning_brief":
                    columns += (
                        ",learning_source,learning_brief_id,learning_policy_revision_id,subject"
                    )
                    values += (
                        episode.learning_source,
                        episode.learning_brief_id,
                        episode.learning_policy_revision_id,
                        episode.subject,
                    )
                connection.execute(
                    f"INSERT INTO episodes ({columns}) VALUES ({','.join('?' for _ in values)})",
                    values,
                )
                for pinned_pack in episode.character_packs:
                    connection.execute(
                        "INSERT INTO episode_character_packs VALUES (?, ?, ?)",
                        (episode.episode_id, pinned_pack.character_id, pinned_pack.revision_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def get_episode(self, episode_id: str) -> Episode:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            if row is None:
                raise KeyError(episode_id)
            packs = connection.execute(
                "SELECT character_id, revision_id FROM episode_character_packs "
                "WHERE episode_id = ? ORDER BY character_id",
                (episode_id,),
            ).fetchall()
            return Episode(
                episode_id=row["episode_id"],
                external_key=row["external_key"],
                brand_revision_id=row["brand_revision_id"],
                curriculum_revision_id=row["curriculum_revision_id"],
                learning_source=row["learning_source"],
                learning_brief_id=row["learning_brief_id"],
                learning_policy_revision_id=row["learning_policy_revision_id"],
                subject=row["subject"],
                concept_id=row["concept_id"],
                objective_id=row["objective_id"],
                objective=row["objective"],
                target_vocabulary=tuple(json.loads(row["target_vocabulary_json"])),
                language=row["language"],
                target_duration_seconds=row["target_duration_seconds"],
                character_packs=tuple(
                    PinnedCharacterPack(
                        character_id=p["character_id"], revision_id=p["revision_id"]
                    )
                    for p in packs
                ),
                lifecycle=row["lifecycle"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self.connect()) as connection:
            yield connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _execution_now(self) -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _row_to_execution_lease(row: sqlite3.Row) -> ProductionExecutionLease:
        try:
            acquired_at = datetime.fromisoformat(row["acquired_at"])
            heartbeat_at = datetime.fromisoformat(row["heartbeat_at"])
            expires_at = datetime.fromisoformat(row["expires_at"])
            if any(
                value.tzinfo is None or value.utcoffset() is None
                for value in (acquired_at, heartbeat_at, expires_at)
            ):
                raise ValueError("lease timestamps must include a UTC offset")
        except (TypeError, ValueError) as exc:
            raise ExecutionOwnershipError(
                "the persisted production execution lease is invalid; refusing to continue"
            ) from exc
        return ProductionExecutionLease(
            owner_token=row["owner_token"],
            operation=row["operation"],
            item_id=row["item_id"],
            acquired_at=acquired_at,
            heartbeat_at=heartbeat_at,
            expires_at=expires_at,
        )

    @staticmethod
    def _validate_execution_ttl(ttl: timedelta) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("production execution lease TTL must be positive")

    def get_production_execution_lease(self) -> ProductionExecutionLease | None:
        """Return the persisted lease row, including an expired row, for diagnostics."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM production_execution_lease WHERE singleton = 1"
            ).fetchone()
        return self._row_to_execution_lease(row) if row is not None else None

    def acquire_production_execution(
        self,
        *,
        operation: str,
        item_id: str | None = None,
        ttl: timedelta,
    ) -> ProductionExecutionLease:
        """Atomically claim the singleton lease, replacing it only after expiration."""

        operation = operation.strip()
        if not operation:
            raise ValueError("production execution operation cannot be empty")
        normalized_item_id = item_id.strip() if item_id is not None else None
        if normalized_item_id == "":
            normalized_item_id = None
        self._validate_execution_ttl(ttl)
        owner_token = str(uuid4())
        with self._transaction() as connection:
            now = self._execution_now()
            lease = ProductionExecutionLease(
                owner_token=owner_token,
                operation=operation,
                item_id=normalized_item_id,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + ttl,
            )
            row = connection.execute(
                "SELECT * FROM production_execution_lease WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO production_execution_lease (
                        singleton, owner_token, operation, item_id,
                        acquired_at, heartbeat_at, expires_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease.owner_token,
                        lease.operation,
                        lease.item_id,
                        lease.acquired_at.isoformat(),
                        lease.heartbeat_at.isoformat(),
                        lease.expires_at.isoformat(),
                    ),
                )
            else:
                current = self._row_to_execution_lease(row)
                if current.expires_at > now:
                    raise ExecutionOwnershipConflictError(
                        "another production execution is currently active"
                    )
                connection.execute(
                    """
                    UPDATE production_execution_lease
                    SET owner_token = ?, operation = ?, item_id = ?,
                        acquired_at = ?, heartbeat_at = ?, expires_at = ?
                    WHERE singleton = 1
                    """,
                    (
                        lease.owner_token,
                        lease.operation,
                        lease.item_id,
                        lease.acquired_at.isoformat(),
                        lease.heartbeat_at.isoformat(),
                        lease.expires_at.isoformat(),
                    ),
                )
        return lease

    def renew_production_execution(
        self,
        owner_token: str,
        *,
        ttl: timedelta,
    ) -> ProductionExecutionLease:
        """Extend a live lease only when the caller still owns its exact token."""

        self._validate_execution_ttl(ttl)
        with self._transaction() as connection:
            now = self._execution_now()
            expires_at = now + ttl
            row = connection.execute(
                "SELECT * FROM production_execution_lease WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise ExecutionOwnershipLostError("production execution ownership has been lost")
            current = self._row_to_execution_lease(row)
            if current.owner_token != owner_token or current.expires_at <= now:
                raise ExecutionOwnershipLostError(
                    "production execution ownership has been lost or expired"
                )
            cursor = connection.execute(
                """
                UPDATE production_execution_lease
                SET heartbeat_at = ?, expires_at = ?
                WHERE singleton = 1 AND owner_token = ?
                """,
                (now.isoformat(), expires_at.isoformat(), owner_token),
            )
            if cursor.rowcount != 1:
                raise ExecutionOwnershipLostError("production execution ownership has been lost")
        return ProductionExecutionLease(
            owner_token=current.owner_token,
            operation=current.operation,
            item_id=current.item_id,
            acquired_at=current.acquired_at,
            heartbeat_at=now,
            expires_at=expires_at,
        )

    def assert_production_execution_owner(self, owner_token: str) -> ProductionExecutionLease:
        """Verify that a token is still the live singleton owner without mutating it."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM production_execution_lease WHERE singleton = 1"
            ).fetchone()
        now = self._execution_now()
        if row is None:
            raise ExecutionOwnershipLostError("production execution ownership has been lost")
        lease = self._row_to_execution_lease(row)
        if lease.owner_token != owner_token or lease.expires_at <= now:
            raise ExecutionOwnershipLostError(
                "production execution ownership has been lost or expired"
            )
        return lease

    def release_production_execution(self, owner_token: str) -> None:
        """Delete the singleton row only when the exact current owner releases it."""

        with self._transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM production_execution_lease
                WHERE singleton = 1 AND owner_token = ?
                """,
                (owner_token,),
            )
            if cursor.rowcount != 1:
                raise ExecutionOwnershipLostError("production execution ownership has been lost")
