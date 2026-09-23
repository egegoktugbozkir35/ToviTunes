import sqlite3
from pathlib import Path

import pytest

from tovitunes.catalog import BrandCatalog
from tovitunes.domain.episode import Episode
from tovitunes.persistence.db import Database


def test_episode_survives_reopen_and_migration_is_idempotent(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    db_path = tmp_path / "state.db"
    db = Database(db_path)
    db.migrate()
    episode = Episode.create(catalog, "red", "colors-red")
    db.create_episode(catalog, episode)
    reopened = Database(db_path)
    reopened.migrate()
    assert reopened.get_episode(episode.episode_id) == episode
    with reopened.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM episode_character_packs").fetchone()[0] == 1
    second = Episode.create(catalog, "blue", "colors-red")
    with pytest.raises(sqlite3.IntegrityError):
        reopened.create_episode(catalog, second)


def test_curriculum_mismatch_and_orphan_dependency_are_blocked(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    db = Database(tmp_path / "state.db")
    db.migrate()
    episode = Episode.create(catalog, "red", "red-1")
    with pytest.raises(ValueError, match="learning objective"):
        db.create_episode(catalog, episode.model_copy(update={"objective": "Identify blue"}))
    db.create_episode(catalog, episode)
    with db.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO artifact_dependencies VALUES (?, ?, ?, ?)",
                ("missing-a", "missing-b", "0" * 64, "source"),
            )


def test_migration_checksum_drift_fails_closed(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    db.migrate()
    with db.connect() as connection:
        connection.execute("UPDATE schema_migrations SET checksum = 'tampered'")
    with pytest.raises(ValueError, match="checksum changed"):
        db.migrate()

