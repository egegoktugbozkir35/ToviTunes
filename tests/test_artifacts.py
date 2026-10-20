import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tovitunes.artifacts.media import InvalidMedia
from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.catalog import BrandCatalog
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.persistence.db import Database


def _setup(tmp_path: Path, catalog: BrandCatalog) -> tuple[AssetStore, Database, Episode]:
    db = Database(tmp_path / "state.db")
    db.migrate()
    episode = Episode.create(catalog, "red", "colors-red")
    db.create_episode(catalog, episode)
    return AssetStore(tmp_path / "assets", db), db, episode


def _approve(store: AssetStore, artifact_id: str) -> None:
    store.record_approval(
        ApprovalDecision(
            target_id=artifact_id,
            target_kind="artifact",
            status="approved",
            actor="reviewer",
            policy_version="1",
            decided_at=datetime.now(UTC),
        )
    )


def _json_source(tmp_path: Path, name: str, content: str) -> Path:
    source = tmp_path / name
    source.write_text(content, encoding="utf-8")
    return source


def test_ingest_review_select_and_reopen(tmp_path: Path, catalog: BrandCatalog) -> None:
    store, db, episode = _setup(tmp_path, catalog)
    source = _json_source(tmp_path, "song.json", '{"candidate": "red"}')
    record = store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind="music",
        slot_key="candidate_1",
        provenance=Provenance.manual("producer", "local://song.json"),
        expected_media_type="application/json",
    )
    assert store.inspect(record.identity.artifact_id).valid
    assert record.sha256 == store.get(record.identity.artifact_id).sha256
    with db.connect() as connection:
        rights = connection.execute(
            "SELECT status FROM rights_decisions WHERE artifact_id = ?",
            (record.identity.artifact_id,),
        ).fetchone()
        approval = connection.execute(
            "SELECT status FROM approval_decisions WHERE artifact_id = ?",
            (record.identity.artifact_id,),
        ).fetchone()
        assert rights["status"] == "unknown"
        assert approval["status"] == "pending"
    with pytest.raises(ValueError, match="approval"):
        store.select(record.identity.artifact_id)
    store.record_rights(
        RightsDecision(
            artifact_id=record.identity.artifact_id,
            status="unknown",
            actor="producer",
            policy_version="1",
            decided_at=datetime.now(UTC),
        )
    )
    _approve(store, record.identity.artifact_id)
    assert store.select(record.identity.artifact_id) == record
    reopened = AssetStore(tmp_path / "assets", Database(db.path))
    assert reopened.selected("episode", episode.episode_id, "music", "candidate_1") == record
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_validation").fetchone()[0] == 1


def test_supersession_invalidates_dependent_and_file_tamper_fails(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    store, _, episode = _setup(tmp_path, catalog)
    source = _json_source(tmp_path, "original.json", '{"value": 1}')
    base = store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind="image",
        slot_key="scene_1",
        provenance=Provenance.manual("producer", "local://base"),
    )
    _approve(store, base.identity.artifact_id)
    store.select(base.identity.artifact_id)
    derived = store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind="storyboard",
        slot_key="main",
        provenance=Provenance.manual("producer", "local://derived"),
        dependencies=[InputDependency(base.identity.artifact_id, "scene image")],
    )
    _approve(store, derived.identity.artifact_id)
    store.select(derived.identity.artifact_id)
    source.write_text('{"value": 2}', encoding="utf-8")
    replacement = store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind="image",
        slot_key="scene_1",
        provenance=Provenance.manual("producer", "local://replacement"),
    )
    _approve(store, replacement.identity.artifact_id)
    store.select(replacement.identity.artifact_id)
    with pytest.raises(ValueError, match="no longer selected"):
        store.selected("episode", episode.episode_id, "storyboard", "main")
    stored_path = store.root / replacement.relative_path
    stored_path.write_text('{"value": 3}', encoding="utf-8")
    assert store.inspect(replacement.identity.artifact_id).reasons == ("hash_changed",)
    with pytest.raises(ValueError, match="invalid"):
        store.selected("episode", episode.episode_id, "image", "scene_1")
    assert (store.root / base.relative_path).exists()


def test_invalid_media_and_failed_registration_recover_as_orphan(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    store, db, _ = _setup(tmp_path, catalog)
    source = _json_source(tmp_path, "bad.json", "not json")
    with pytest.raises(InvalidMedia):
        store.ingest(
            source,
            owner_scope="episode",
            owner_id="missing",
            kind="music",
            slot_key="main",
            provenance=Provenance.manual("producer", "local://bad"),
        )
    source.write_text('{"ok": true}', encoding="utf-8")
    with pytest.raises(sqlite3.IntegrityError):
        store.ingest(
            source,
            owner_scope="episode",
            owner_id="missing",
            kind="music",
            slot_key="main",
            provenance=Provenance.manual("producer", "local://orphan"),
        )
    quarantined = store.quarantine_orphans()
    assert len(quarantined) == 1
    assert quarantined[0].startswith("episodes/missing/music/main/")
    assert len(list((store.root / "quarantine").iterdir())) == 1
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0] == 0


def test_generated_sources_require_an_explicit_trusted_root(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    store, db, episode = _setup(tmp_path, catalog)
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    source = _json_source(generated_root, "provider.json", '{"asset": "red"}')
    with pytest.raises(ValidationError):
        Provenance(source_kind="provider", provider="example", acquired_at=datetime.now(UTC))
    provenance = Provenance(
        source_kind="provider",
        provider="example",
        model="v1",
        request_id="request-1",
        acquired_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="trusted roots"):
        store.ingest(
            source,
            owner_scope="episode",
            owner_id=episode.episode_id,
            kind="image",
            slot_key="scene_1",
            provenance=provenance,
        )
    trusted_store = AssetStore(tmp_path / "assets", db, generated_source_roots=[generated_root])
    record = trusted_store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind="image",
        slot_key="scene_1",
        provenance=provenance,
    )
    assert record.provenance.request_id == "request-1"


def test_immutable_db_rows_and_blocked_rights(tmp_path: Path, catalog: BrandCatalog) -> None:
    store, db, episode = _setup(tmp_path, catalog)
    source = _json_source(tmp_path, "asset.json", '{"ok": true}')
    record = store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind="image",
        slot_key="scene_1",
        provenance=Provenance.manual("producer", "local://asset"),
    )
    with db.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE artifact_versions SET sha256 = ? WHERE artifact_id = ?",
                ("0" * 64, record.identity.artifact_id),
            )
    _approve(store, record.identity.artifact_id)
    store.select(record.identity.artifact_id)
    store.record_rights(
        RightsDecision(
            artifact_id=record.identity.artifact_id,
            status="blocked",
            actor="rights-reviewer",
            policy_version="1",
            decided_at=datetime.now(UTC),
        )
    )
    with pytest.raises(ValueError, match="blocked"):
        store.selected("episode", episode.episode_id, "image", "scene_1")



def test_truncated_image_with_valid_signature_is_never_registered(tmp_path, catalog):
    from PIL import Image

    store, db, episode = _setup(tmp_path, catalog)
    source = tmp_path / 'truncated.png'
    Image.new('RGB', (128, 128), '#eeeeee').save(source)
    original = source.read_bytes()[:48]
    source.write_bytes(original)
    with pytest.raises(InvalidMedia, match='fully decoded'):
        store.ingest(
            source, owner_scope='episode', owner_id=episode.episode_id,
            kind='visual_asset_source', slot_key='cup',
            provenance=Provenance.manual('test', 'test://truncated'),
        )
    assert source.read_bytes() == original
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM artifact_versions').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM artifact_selections').fetchone()[0] == 0


def test_animated_image_cannot_be_admitted_as_static_art(tmp_path):
    from PIL import Image

    from tovitunes.artifacts.media import validate_media

    path = tmp_path / 'animated.png'
    frames = [Image.new('RGB', (16, 16), color) for color in ('red', 'blue')]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(InvalidMedia, match='animated image is unsupported'):
        validate_media(path)


def test_invalid_provider_artwork_remains_raw_quarantine_evidence(tmp_path, catalog):
    from PIL import Image

    store, db, episode = _setup(tmp_path, catalog)
    store = AssetStore(store.root, db, generated_source_roots=[tmp_path])
    source = tmp_path / 'broken.png'
    Image.new('RGB', (64, 64), 'white').save(source)
    raw = source.read_bytes()[:48]
    source.write_bytes(raw)
    with pytest.raises(InvalidMedia, match='raw provider bytes retained at quarantine/'):
        store.ingest(
            source, owner_scope='episode', owner_id=episode.episode_id,
            kind='visual_asset_source', slot_key='cup',
            provenance=Provenance(
                source_kind='provider', provider='offline-fixture', model='fixture',
                request_id='fixture-request', acquired_at=datetime.now(UTC),
            ),
        )
    retained = list((store.root / 'quarantine').iterdir())
    assert len(retained) == 1 and retained[0].read_bytes() == raw
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM artifact_versions').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM artifact_selections').fetchone()[0] == 0
