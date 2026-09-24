import json
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from PIL import Image
from pydantic import ValidationError

from tovitunes.artifacts.character_intake import ingest_prepared, prepare_assets
from tovitunes.artifacts.character_pack import assess_pack_assets
from tovitunes.artifacts.character_png import InvalidCharacterSprite, inspect_sprite_png
from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import load_brand
from tovitunes.domain.character import CharacterAssetPack
from tovitunes.domain.review import RightsDecision
from tovitunes.persistence.db import Database


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    for name, color in (("original-profile", "blue"), ("original-banner", "yellow")):
        Image.new("RGB", (24, 24), color).save(sources / f"{name}.png")
    view_sheet = Image.new("RGB", (90, 30), "white")
    for index, color in enumerate(("blue", "green", "orange")):
        view_sheet.paste(color, (index * 30 + 4, 4, index * 30 + 26, 26))
    view_sheet.save(sources / "turnaround.png")
    mouths = Image.new("RGBA", (100, 20), (0, 0, 0, 0))
    for index in range(5):
        mouths.paste((40 * index, 100, 230, 255), (index * 20 + 4, 3, index * 20 + 16, 17))
    mouths.save(sources / "mouth-sprites.png")
    poses = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    poses.paste((20, 100, 250, 255), (4, 4, 22, 27))
    poses.paste((255, 0, 0, 255), (29, 4, 35, 10))
    poses.save(sources / "poses.png")
    source_specs = {
        "original_profile": "original-profile.png",
        "original_banner": "original-banner.png",
        "turnaround": "turnaround.png",
        "mouth_sprites": "mouth-sprites.png",
        "poses": "poses.png",
    }
    recipe = {
        "schema_version": 1,
        "sources": {
            key: {
                "filename": name,
                "sha256": _sha(sources / name),
                "source_uri": f"user-supplied://{key}",
                "uses_originals": key not in {"original_profile", "original_banner"},
            }
            for key, name in source_specs.items()
        },
        "assets": [
            {
                "role": f"view/{role}",
                "source": "turnaround",
                "rect": [index * 30, 0, index * 30 + 30, 30],
                "method": "crop",
                "art_review": "approved",
            }
            for index, role in enumerate(("front", "three_quarter", "profile"))
        ]
        + [
            {
                "role": f"mouth/{role}",
                "source": "mouth_sprites",
                "rect": [index * 20, 0, index * 20 + 20, 20],
                "method": "crop",
                "canvas": [24, 20],
                "art_review": "needs_review",
                "quality_note": "synthetic review hold",
            }
            for index, role in enumerate(("closed", "small_open", "wide_a", "e_smile", "o_round"))
        ]
        + [
            {
                "role": "sprite/singing",
                "source": "poses",
                "rect": [0, 0, 40, 30],
                "method": "component",
                "canvas": [44, 30],
                "art_review": "approved",
            }
        ],
    }
    recipe_path = tmp_path / "intake.yaml"
    recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8")
    return recipe_path, sources, tmp_path / "prepared"


def test_prepare_creates_distinct_roles_with_fixed_mouth_alignment(tmp_path: Path) -> None:
    recipe, sources, prepared = _fixture(tmp_path)
    report = prepare_assets(recipe, sources, prepared)
    assets = {item["role"]: item for item in report["assets"]}
    assert {f"view/{v}" for v in ("front", "three_quarter", "profile")} <= assets.keys()
    mouth_roles = ("closed", "small_open", "wide_a", "e_smile", "o_round")
    mouth_files = [prepared / f"mouth__{role}.png" for role in mouth_roles]
    assert len({_sha(path) for path in mouth_files}) == 5
    assert {Image.open(path).size for path in mouth_files} == {(24, 20)}
    assert {inspect_sprite_png(path).alpha_bbox for path in mouth_files} == {(6, 3, 18, 17)}
    assert Image.open(prepared / "view__front.png").mode == "RGB"
    singing = Image.open(prepared / "sprite__singing.png").convert("RGBA")
    assert singing.getchannel("A").getpixel((34, 5)) == 0  # detached decoration removed
    assert prepare_assets(recipe, sources, prepared) == report


def test_png_decode_alpha_and_trusted_source_paths(tmp_path: Path) -> None:
    opaque = tmp_path / "opaque.png"
    Image.new("RGBA", (10, 10), (255, 255, 255, 255)).save(opaque)
    with pytest.raises(InvalidCharacterSprite, match="opaque"):
        inspect_sprite_png(opaque)
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"\x89PNG\r\n\x1a\nnot a PNG")
    with pytest.raises(InvalidCharacterSprite, match="decoded"):
        inspect_sprite_png(corrupt)
    recipe, sources, prepared = _fixture(tmp_path / "good")
    with pytest.raises(ValueError, match="separate"):
        prepare_assets(recipe, sources, sources / "nested-output")
    (sources / "turnaround.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        prepare_assets(recipe, sources, prepared)
    raw = yaml.safe_load(recipe.read_text(encoding="utf-8"))
    raw["sources"]["turnaround"]["filename"] = "../outside.png"
    recipe.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError, match="source filename"):
        prepare_assets(recipe, sources, prepared)


def test_ingest_preserves_provenance_and_art_rights_separation(
    tmp_path: Path, brand_root: Path
) -> None:
    recipe, sources, prepared = _fixture(tmp_path)
    prepare_assets(recipe, sources, prepared)
    brand = tmp_path / "tovitunes"
    shutil.copytree(brand_root, brand)
    manifest = brand / "characters/tovi/packs/v1/pack.yaml"
    catalog = load_brand(brand)
    database = Database(tmp_path / "state.db")
    database.migrate()
    store = AssetStore(tmp_path / "assets", database, generated_source_roots=[prepared])
    report = ingest_prepared(recipe, sources, prepared, store, catalog, manifest)
    role_ids = report["role_artifact_ids"]
    assert len(role_ids) == 9
    assert len(set(role_ids.values())) == 9
    source_id = report["source_artifact_ids"]["mouth_sprites"]
    assert store.get(source_id).provenance.source_uri == "user-supplied://mouth_sprites"
    mouth_id = role_ids["mouth/closed"]
    assert store.get(mouth_id).provenance.input_artifact_ids == (source_id,)
    with database.connect() as connection:
        dependency = connection.execute(
            "SELECT input_artifact_id, input_sha256 FROM artifact_dependencies "
            "WHERE consumer_artifact_id = ?", (mouth_id,)
        ).fetchone()
        assert dependency["input_artifact_id"] == source_id
        assert dependency["input_sha256"] == store.get(source_id).sha256
        rights = connection.execute(
            "SELECT status FROM rights_decisions WHERE artifact_id = ? ORDER BY rowid DESC",
            (role_ids["view/front"],),
        ).fetchone()
        approval = connection.execute(
            "SELECT status FROM approval_decisions WHERE artifact_id = ? ORDER BY rowid DESC",
            (role_ids["view/front"],),
        ).fetchone()
        assert rights["status"] == "unknown"
        assert approval["status"] == "approved"
    assert "view/front" in report["selected_roles"]
    assert "mouth/closed" not in report["selected_roles"]
    assert not report["pack_ready"]
    assert "pack manifest remains draft" in report["blocking_issues"]
    assert any(
        "commercial-use rights lack evidence" in issue for issue in report["blocking_issues"]
    )
    assert any(
        "mouth/closed: artifact lacks current approval" in issue
        for issue in report["blocking_issues"]
    )
    loaded_pack = CharacterAssetPack.model_validate(yaml.safe_load(manifest.read_text()))
    assert loaded_pack.readiness == "draft"
    assert json.loads((prepared / "intake-report.json").read_text())["rights_state"] == "unknown"
    repeated = ingest_prepared(recipe, sources, prepared, store, load_brand(brand), manifest)
    assert repeated["role_artifact_ids"] == role_ids
    # A later rights decision still cannot make a draft manifest ready.
    store.record_rights(
        RightsDecision(
            artifact_id=role_ids["view/front"],
            status="commercial_use_confirmed",
            actor="rights-reviewer",
            evidence_uri="test://owner-license",
            policy_version="test",
            decided_at=datetime.now(UTC),
        )
    )
    assessment = assess_pack_assets(load_brand(brand).packs[0], store, catalog.version.revision_id)
    assert not assessment.ready
    assert "pack manifest remains draft" in assessment.issues
