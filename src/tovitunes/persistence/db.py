"""Small transactional SQLite store for pinned episode identity."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from importlib import resources
from pathlib import Path

from tovitunes.catalog import BrandCatalog
from tovitunes.domain.episode import Episode, PinnedCharacterPack


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
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

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
        if episode.brand_revision_id != catalog.version.revision_id:
            raise ValueError("episode brand revision differs from catalog")
        if episode.curriculum_revision_id != catalog.curriculum_revision.revision_id:
            raise ValueError("episode curriculum revision differs from catalog")
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
                connection.execute(
                    "INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
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
                    ),
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
