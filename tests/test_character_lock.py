"""Offline round-trip and fail-closed checks for the canonical pack snapshot."""

import copy
import shutil
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from PIL import Image, ImageDraw

from tovitunes.artifacts.character_intake import ingest_prepared, load_recipe, prepare_assets
from tovitunes.artifacts.character_lock import (
    CharacterPackLock,
    export_pack_lock,
    load_lock,
    rehydrate_pack,
    validate_pack_lock,
)
from tovitunes.artifacts.character_pack import approve_pack_manifest, assess_pack_assets
from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import load_brand
from tovitunes.cli import main
from tovitunes.domain.artifact import ArtifactIdentity
from tovitunes.persistence.db import Database


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _synthetic_sources(root: Path) -> dict[str, str]:
    root.mkdir()
    Image.new("RGB", (24, 24), "skyblue").save(root / "original-profile.png")
    Image.new("RGB", (24, 24), "gold").save(root / "original-banner.png")
    turnaround = Image.new("RGB", (90, 30), "white")
    for index, color in enumerate(("skyblue", "orange", "purple")):
        turnaround.paste(color, (index * 30 + 3, 3, index * 30 + 27, 27))
    turnaround.save(root / "turnaround.png")
    mouths = Image.new("RGBA", (100, 24), (0, 0, 0, 0))
    for index in range(5):
        ImageDraw.Draw(mouths).ellipse(
            (index * 20 + 4, 4, index * 20 + 16, 19), fill=(255, 120 + index * 20, 0, 255)
        )
    mouths.save(root / "mouth-sprites.png")
    old_mouth = Image.new("RGBA", (20, 24), (0, 0, 0, 0))
    ImageDraw.Draw(old_mouth).rectangle((4, 4, 15, 19), fill=(255, 190, 0, 255))
    old_mouth.save(root / "old-mouth.png")
    poses = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    ImageDraw.Draw(poses).ellipse((4, 4, 24, 27), fill=(40, 130, 245, 255))
    ImageDraw.Draw(poses).rectangle((31, 3, 35, 7), fill=(255, 0, 0, 255))
    poses.save(root / "poses.png")
    return {
        "original_profile": "original-profile.png",
        "original_banner": "original-banner.png",
        "turnaround": "turnaround.png",
        "mouth_sprites": "mouth-sprites.png",
        "old_mouth": "old-mouth.png",
        "poses": "poses.png",
    }


def _synthetic_recipe(
    source_root: Path, filenames: dict[str, str]
) -> tuple[dict[str, object], dict[str, object]]:
    old = {
        "role": "mouth/closed",
        "source": "old_mouth",
        "rect": [0, 0, 20, 24],
        "method": "crop",
        "art_review": "needs_review",
        "quality_note": "held synthetic predecessor",
    }
    views = [
        {
            "role": f"view/{role}",
            "source": "turnaround",
            "rect": [index * 30, 0, index * 30 + 30, 30],
            "method": "crop",
            "art_review": "approved",
        }
        for index, role in enumerate(("front", "three_quarter", "profile"))
    ]
    mouths = [
        {
            "role": f"mouth/{role}",
            "source": "mouth_sprites",
            "rect": [index * 20, 0, index * 20 + 20, 24],
            "method": "crop",
            "art_review": "approved",
        }
        for index, role in enumerate(("closed", "small_open", "wide_a", "e_smile", "o_round"))
    ]
    singing = {
        "role": "sprite/singing",
        "source": "poses",
        "rect": [0, 0, 40, 30],
        "method": "component",
        "art_review": "approved",
    }
    sources: dict[str, dict[str, object]] = {}
    for key, filename in filenames.items():
        generated = key not in {"original_profile", "original_banner"}
        sources[key] = {
            "filename": filename,
            "sha256": _sha(source_root / filename),
            "source_uri": (f"generation://{key}" if generated else f"user-supplied://{key}"),
            "uses_originals": generated,
            **({"rights_basis": "openai_chatgpt_output"} if generated else {}),
        }
    base: dict[str, object] = {
        "schema_version": 1,
        "rights_evidence": {
            "uri": "https://openai.com/policies/row-terms-of-use/",
            "policy_version": "openai-terms-of-use-2026-01-01",
        },
        "sources": sources,
    }
    old_recipe = {**base, "assets": views + [old] + mouths[1:] + [singing]}
    new_recipe = {
        **base,
        "assets": views + mouths + [singing],
        "superseded_assets": [old],
    }
    return old_recipe, new_recipe


@pytest.fixture(scope="module")
def synthetic_pack(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("canonical-pack")
    brand = root / "tovitunes"
    brand_root = Path(__file__).resolve().parents[1] / "brands" / "tovitunes"
    shutil.copytree(brand_root, brand)
    manifest = brand / "characters/tovi/packs/v1/pack.yaml"
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw.update(
        readiness="draft",
        canonical_views=["front", "three_quarter", "profile"],
        reusable_sprites=["singing"],
        expressions=["neutral"],
        wing_poses=["folded"],
        asset_artifact_ids={},
    )
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    source_root = root / "sources"
    filenames = _synthetic_sources(source_root)
    old_recipe, new_recipe = _synthetic_recipe(source_root, filenames)
    old_path = root / "old-intake.yaml"
    new_path = manifest.with_name("intake.yaml")
    old_path.write_text(yaml.safe_dump(old_recipe, sort_keys=False), encoding="utf-8")
    new_path.write_text(yaml.safe_dump(new_recipe, sort_keys=False), encoding="utf-8")
    database = Database(root / "original.db")
    database.migrate()
    old_prepared = root / "prepared-old"
    new_prepared = root / "prepared"
    prepare_assets(old_path, source_root, old_prepared)
    new_prepared.mkdir()
    store = AssetStore(
        root / "original-assets", database, generated_source_roots=[old_prepared, new_prepared]
    )
    old_report = ingest_prepared(
        old_path, source_root, old_prepared, store, load_brand(brand), manifest
    )
    with pytest.raises(ValueError, match="readiness checks"):
        approve_pack_manifest(manifest, load_brand(brand), store)
    assert load_brand(brand).packs[0].readiness == "draft"
    prepare_assets(new_path, source_root, new_prepared)
    new_report = ingest_prepared(
        new_path, source_root, new_prepared, store, load_brand(brand), manifest
    )
    approved = approve_pack_manifest(manifest, load_brand(brand), store)
    assert approved.ready
    catalog = load_brand(brand)
    lock_path = manifest.with_name("artifact-lock.yaml")
    lock = export_pack_lock(new_path, store, catalog, lock_path)
    assert (
        lock.superseded_role_artifact_ids["mouth/closed"]
        == old_report["role_artifact_ids"]["mouth/closed"]
    )
    assert lock.active_role_artifact_ids == new_report["role_artifact_ids"]
    return {
        "root": root,
        "brand": brand,
        "manifest": manifest,
        "recipe": new_path,
        "sources": source_root,
        "prepared": new_prepared,
        "database": database,
        "store": store,
        "catalog": catalog,
        "lock_path": lock_path,
        "lock": lock,
    }


def _rows(database: Database) -> dict[str, list[tuple[object, ...]]]:
    queries = {
        "artifact_versions": "SELECT * FROM artifact_versions ORDER BY artifact_id",
        "artifact_dependencies": (
            "SELECT * FROM artifact_dependencies ORDER BY consumer_artifact_id, input_artifact_id"
        ),
        "rights_decisions": "SELECT * FROM rights_decisions ORDER BY artifact_id, rowid",
        "approval_decisions": "SELECT * FROM approval_decisions ORDER BY artifact_id, rowid",
        "artifact_selections": "SELECT * FROM artifact_selections ORDER BY kind, slot_key",
    }
    with database.connect() as connection:
        return {
            name: [tuple(row) for row in connection.execute(query)]
            for name, query in queries.items()
        }


def test_fresh_store_round_trip_preserves_full_canonical_state(
    synthetic_pack: dict[str, object], tmp_path: Path
) -> None:
    source = synthetic_pack
    brand = source["brand"]
    manifest = source["manifest"]
    recipe = source["recipe"]
    sources = source["sources"]
    prepared = source["prepared"]
    lock_path = source["lock_path"]
    lock = source["lock"]
    database = source["database"]
    assert isinstance(brand, Path)
    assert isinstance(manifest, Path)
    assert isinstance(recipe, Path)
    assert isinstance(sources, Path)
    assert isinstance(prepared, Path)
    assert isinstance(lock_path, Path)
    assert isinstance(lock, CharacterPackLock)
    assert isinstance(database, Database)
    before = _rows(database)
    manifest_bytes = manifest.read_bytes()
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "database_path": str(tmp_path / "fresh.db"),
                "data_root": str(tmp_path / "fresh-assets"),
                "brand_root": str(brand),
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--config",
                str(config),
                "character-pack",
                "rehydrate",
                "--recipe",
                str(recipe),
                "--lock",
                str(lock_path),
                "--source-dir",
                str(sources),
                "--prepared-dir",
                str(prepared),
            ]
        )
        == 0
    )
    fresh_db = Database(tmp_path / "fresh.db")
    fresh_store = AssetStore(
        tmp_path / "fresh-assets", fresh_db, generated_source_roots=[prepared], initialize=False
    )
    assert _rows(fresh_db) == before
    assert manifest.read_bytes() == manifest_bytes
    assert assess_pack_assets(load_brand(brand).packs[0], fresh_store, lock.brand_revision_id).ready
    assert rehydrate_pack(recipe, lock_path, sources, prepared, fresh_store, load_brand(brand))[
        "ready"
    ]
    assert _rows(fresh_db) == before
    assert len(lock.artifacts) == 16
    assert lock.superseded_role_artifact_ids["mouth/closed"] not in {
        row[4] for row in before["artifact_selections"]
    }


def test_checked_in_approved_manifest_and_lock_validate_without_media(brand_root: Path) -> None:
    catalog = load_brand(brand_root)
    assert catalog.packs[0].readiness == "approved"
    recipe_path = brand_root / "characters/tovi/packs/v1/intake.yaml"
    lock_path = recipe_path.with_name("artifact-lock.yaml")
    lock = load_lock(lock_path)
    validate_pack_lock(lock, catalog, load_recipe(recipe_path), recipe_path)
    assert len(lock.artifacts) == 48
    assert len(lock.source_artifact_ids) == 16
    assert len(lock.active_role_artifact_ids) == 26
    assert len(lock.superseded_role_artifact_ids) == 6
    assert load_brand(brand_root).version.revision_id == lock.brand_revision_id


def test_lock_file_cannot_change_brand_or_pack_revision(brand_root: Path, tmp_path: Path) -> None:
    copied = tmp_path / "brand"
    shutil.copytree(brand_root, copied)
    before = load_brand(copied)
    lock_path = copied / "characters/tovi/packs/v1/artifact-lock.yaml"
    lock_path.write_text(
        lock_path.read_text(encoding="utf-8") + "# revision probe\n", encoding="utf-8"
    )
    after = load_brand(copied)
    assert after.version.revision_id == before.version.revision_id
    assert after.pack_revisions[0].revision_id == before.pack_revisions[0].revision_id


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_id",
        "source_sha",
        "missing_dependency",
        "wrong_dependency_hash",
        "unknown_dependency",
        "role_mapping",
        "pack_version",
        "brand_revision",
        "missing_approval",
        "missing_rights_evidence",
        "superseded_selected",
    ],
)
def test_tampered_lock_is_rejected(synthetic_pack: dict[str, object], case: str) -> None:
    lock = synthetic_pack["lock"]
    catalog = synthetic_pack["catalog"]
    recipe_path = synthetic_pack["recipe"]
    assert isinstance(lock, CharacterPackLock)
    assert isinstance(recipe_path, Path)
    raw = copy.deepcopy(lock.model_dump(mode="json"))
    role_id = lock.active_role_artifact_ids["mouth/closed"]
    source_id = lock.source_artifact_ids["mouth_sprites"]
    role = next(item for item in raw["artifacts"] if item["artifact_id"] == role_id)
    source = next(item for item in raw["artifacts"] if item["artifact_id"] == source_id)
    if case == "duplicate_id":
        raw["artifacts"].append(copy.deepcopy(role))
    elif case == "source_sha":
        source["sha256"] = "0" * 64
    elif case == "missing_dependency":
        role["dependencies"] = []
    elif case == "wrong_dependency_hash":
        role["dependencies"][0]["input_sha256"] = "0" * 64
    elif case == "unknown_dependency":
        role["dependencies"][0]["input_artifact_id"] = str(uuid4())
    elif case == "role_mapping":
        raw["active_role_artifact_ids"]["mouth/closed"] = lock.superseded_role_artifact_ids[
            "mouth/closed"
        ]
    elif case == "pack_version":
        raw["version"] = "wrong-version"
    elif case == "brand_revision":
        raw["brand_revision_id"] = "wrong-brand-revision"
    elif case == "missing_approval":
        role["approval_decisions"] = []
    elif case == "missing_rights_evidence":
        role["rights_decisions"][-1]["evidence_uri"] = None
    elif case == "superseded_selected":
        raw["selections"][0]["artifact_id"] = lock.superseded_role_artifact_ids["mouth/closed"]
    with pytest.raises(ValueError):
        tampered = CharacterPackLock.model_validate(raw)
        validate_pack_lock(tampered, catalog, load_recipe(recipe_path), recipe_path)


def test_explicit_identity_conflicts_and_draft_safety(
    synthetic_pack: dict[str, object], tmp_path: Path
) -> None:
    source = synthetic_pack
    store = source["store"]
    lock = source["lock"]
    brand = source["brand"]
    manifest = source["manifest"]
    recipe = source["recipe"]
    sources = source["sources"]
    prepared = source["prepared"]
    assert isinstance(store, AssetStore)
    assert isinstance(lock, CharacterPackLock)
    assert isinstance(brand, Path)
    assert isinstance(manifest, Path)
    assert isinstance(recipe, Path)
    assert isinstance(sources, Path)
    assert isinstance(prepared, Path)
    with pytest.raises(ValueError, match="draft"):
        ingest_prepared(recipe, sources, prepared, store, load_brand(brand), manifest)
    original_id = lock.source_artifact_ids["original_profile"]
    item = next(artifact for artifact in lock.artifacts if artifact.artifact_id == original_id)
    path = sources / "original-profile.png"
    identity = ArtifactIdentity(
        artifact_id=item.artifact_id,
        owner_scope="brand",
        owner_id=item.owner_id,
        kind=item.kind,
        slot_key=item.slot_key,
    )
    common = {
        "expected_byte_count": item.byte_count,
        "expected_mime_type": item.mime_type,
        "expected_relative_path": item.relative_path,
        "provenance": item.provenance,
        "created_at": item.created_at.isoformat(),
    }
    with pytest.raises(ValueError, match="bytes differ"):
        store.rehydrate_artifact(path, identity=identity, expected_sha256="0" * 64, **common)
    altered = tmp_path / "altered-profile.png"
    Image.new("RGB", (24, 24), "red").save(altered)
    with pytest.raises(ValueError, match="immutable metadata"):
        store.rehydrate_artifact(
            altered,
            identity=identity,
            expected_sha256=_sha(altered),
            **{**common, "expected_byte_count": altered.stat().st_size},
        )
    wrong_slot = identity.model_copy(update={"slot_key": "wrong_slot"})
    with pytest.raises(ValueError, match="immutable metadata"):
        store.rehydrate_artifact(
            path,
            identity=wrong_slot,
            expected_sha256=item.sha256,
            **{
                **common,
                "expected_relative_path": item.relative_path.replace(item.slot_key, "wrong_slot"),
            },
        )
    other_id = str(uuid4())
    conflict = identity.model_copy(update={"artifact_id": other_id})
    with pytest.raises(ValueError, match="another identity"):
        store.rehydrate_artifact(
            path,
            identity=conflict,
            expected_sha256=item.sha256,
            **{
                **common,
                "expected_relative_path": item.relative_path.replace(original_id, other_id),
            },
        )
    draft_brand = tmp_path / "unrelated-brand"
    shutil.copytree(brand, draft_brand)
    draft_manifest = draft_brand / "characters/tovi/packs/v1/pack.yaml"
    draft_manifest.write_text(
        draft_manifest.read_text(encoding="utf-8").replace(
            "readiness: approved", "readiness: draft"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="approved pack"):
        rehydrate_pack(
            draft_manifest.with_name("intake.yaml"),
            source["lock_path"],
            sources,
            prepared,
            store,
            load_brand(draft_brand),
        )
    other_brand = tmp_path / "different-version"
    shutil.copytree(brand, other_brand)
    other_manifest = other_brand / "characters/tovi/packs/v1/pack.yaml"
    other_raw = yaml.safe_load(other_manifest.read_text(encoding="utf-8"))
    other_raw["version"] = "different-pack-version"
    other_manifest.write_text(yaml.safe_dump(other_raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="pack identity or version"):
        rehydrate_pack(
            other_manifest.with_name("intake.yaml"),
            source["lock_path"],
            sources,
            prepared,
            store,
            load_brand(other_brand),
        )


@pytest.mark.parametrize("changed", ["source", "prepared"])
def test_media_tampering_fails_before_import(
    synthetic_pack: dict[str, object], tmp_path: Path, changed: str
) -> None:
    source = synthetic_pack
    brand = source["brand"]
    recipe = source["recipe"]
    lock_path = source["lock_path"]
    sources = source["sources"]
    prepared = source["prepared"]
    assert isinstance(brand, Path)
    assert isinstance(recipe, Path)
    assert isinstance(lock_path, Path)
    assert isinstance(sources, Path)
    assert isinstance(prepared, Path)
    copied_sources = tmp_path / "sources"
    copied_prepared = tmp_path / "prepared"
    shutil.copytree(sources, copied_sources)
    shutil.copytree(prepared, copied_prepared)
    target = (
        copied_sources / "turnaround.png"
        if changed == "source"
        else copied_prepared / "mouth__closed.png"
    )
    target.write_bytes(target.read_bytes() + b"tampered")
    database = Database(tmp_path / "fresh.db")
    database.migrate()
    store = AssetStore(tmp_path / "assets", database, generated_source_roots=[copied_prepared])
    with pytest.raises(ValueError):
        rehydrate_pack(recipe, lock_path, copied_sources, copied_prepared, store, load_brand(brand))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0] == 0


def test_explicit_identity_requires_registered_dependency(
    synthetic_pack: dict[str, object], tmp_path: Path
) -> None:
    source = synthetic_pack
    brand = source["brand"]
    prepared = source["prepared"]
    lock = source["lock"]
    assert isinstance(brand, Path)
    assert isinstance(prepared, Path)
    assert isinstance(lock, CharacterPackLock)
    database = Database(tmp_path / "fresh.db")
    database.migrate()
    database.register_catalog(load_brand(brand))
    store = AssetStore(tmp_path / "assets", database, generated_source_roots=[prepared])
    item = next(
        artifact
        for artifact in lock.artifacts
        if artifact.artifact_id == lock.active_role_artifact_ids["mouth/closed"]
    )
    with pytest.raises(ValueError, match="dependency is missing"):
        store.rehydrate_artifact(
            prepared / "mouth__closed.png",
            identity=ArtifactIdentity(
                artifact_id=item.artifact_id,
                owner_scope=item.owner_scope,
                owner_id=item.owner_id,
                kind=item.kind,
                slot_key=item.slot_key,
            ),
            expected_sha256=item.sha256,
            expected_byte_count=item.byte_count,
            expected_mime_type=item.mime_type,
            expected_relative_path=item.relative_path,
            provenance=item.provenance,
            created_at=item.created_at.isoformat(),
            dependencies=item.dependencies,
        )
