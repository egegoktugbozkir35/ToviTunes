"""Offline checks for the v1 component replacements and append-only intake."""

import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageChops, ImageDraw
from pydantic import ValidationError

from tovitunes.artifacts.character_intake import ingest_prepared, prepare_assets
from tovitunes.artifacts.character_png import (
    InvalidCharacterSprite,
    inspect_mouth_component,
    validate_mouth_states,
    visible_art_bbox,
)
from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import load_brand
from tovitunes.domain.review import RightsDecision
from tovitunes.persistence.db import Database

MOUTH_SHAPES = {
    "closed": ((30, 30, 98, 90), (44, 66, 84, 68)),
    "small_open": ((30, 25, 98, 100), (48, 60, 80, 76)),
    "wide_a": ((20, 18, 108, 111), (30, 56, 98, 100)),
    "e_smile": ((25, 25, 103, 105), (38, 60, 90, 87)),
    "o_round": ((29, 28, 99, 106), (47, 55, 81, 91)),
}
TERMS = "https://openai.com/policies/row-terms-of-use/"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _mouth(path: Path, state: str) -> None:
    body, cavity = MOUTH_SHAPES[state]
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(body, fill=(255, 184, 24, 255))
    draw.ellipse(cavity, fill=(90, 12, 10, 255))
    image.save(path)


def test_mouth_components_have_padding_no_face_blue_and_distinct_openings(
    tmp_path: Path,
) -> None:
    facts = {}
    for state in MOUTH_SHAPES:
        path = tmp_path / f"{state}.png"
        _mouth(path, state)
        with Image.open(path) as image:
            facts[state] = inspect_mouth_component(image)
    validate_mouth_states(facts)
    assert len({_sha(path) for path in tmp_path.glob("*.png")}) == 5
    with Image.open(tmp_path / "e_smile.png") as image:
        duplicate = inspect_mouth_component(image)
    with pytest.raises(InvalidCharacterSprite, match="smile and round"):
        validate_mouth_states({**facts, "o_round": duplicate})

    blue = Image.open(tmp_path / "closed.png").convert("RGBA")
    ImageDraw.Draw(blue).rectangle((20, 20, 55, 55), fill=(15, 90, 240, 255))
    with pytest.raises(InvalidCharacterSprite, match="non-beak blue"):
        inspect_mouth_component(blue)
    edge = Image.open(tmp_path / "closed.png").convert("RGBA")
    ImageDraw.Draw(edge).rectangle((0, 40, 20, 60), fill=(255, 150, 20, 255))
    with pytest.raises(InvalidCharacterSprite, match="padding"):
        inspect_mouth_component(edge)


def _old_intake(tmp_path: Path) -> tuple[Path, Path, Path]:
    sources = tmp_path / "sources"
    sources.mkdir()
    for name in ("original-profile.png", "original-banner.png"):
        Image.new("RGB", (24, 24), "skyblue").save(sources / name)
    sheet = Image.new("RGBA", (200, 40), (0, 0, 0, 0))
    for index in range(5):
        ImageDraw.Draw(sheet).ellipse(
            (index * 40 + 4, 5, index * 40 + 36, 35), fill=(255, 165, 25, 255)
        )
    sheet.save(sources / "mouth-sprites.png")
    thinking = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
    ImageDraw.Draw(thinking).ellipse((4, 4, 44, 44), fill=(70, 170, 245, 255))
    thinking.save(sources / "expressions.png")
    files = {
        "original_profile": "original-profile.png",
        "original_banner": "original-banner.png",
        "mouth_sprites": "mouth-sprites.png",
        "expressions": "expressions.png",
    }
    recipe = {
        "schema_version": 1,
        "sources": {
            key: {
                "filename": filename,
                "sha256": _sha(sources / filename),
                "source_uri": (
                    f"generation://{key}"
                    if key not in {"original_profile", "original_banner"}
                    else f"user-supplied://{key}"
                ),
                "uses_originals": key not in {"original_profile", "original_banner"},
            }
            for key, filename in files.items()
        },
        "assets": [
            {
                "role": f"mouth/{state}",
                "source": "mouth_sprites",
                "rect": [index * 40, 0, index * 40 + 40, 40],
                "method": "crop",
                "canvas": [48, 40],
                "art_review": "needs_review",
                "quality_note": "old bust candidate",
            }
            for index, state in enumerate(MOUTH_SHAPES)
        ]
        + [
            {
                "role": "sprite/expression_thinking",
                "source": "expressions",
                "rect": [0, 0, 48, 48],
                "method": "crop",
                "art_review": "needs_review",
                "quality_note": "old clipped candidate",
            }
        ],
    }
    recipe_path = tmp_path / "intake.yaml"
    recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8")
    return recipe_path, sources, tmp_path / "prepared-old"


def test_replacement_retains_old_versions_and_records_separate_rights(
    tmp_path: Path, brand_root: Path
) -> None:
    recipe_path, sources, old_prepared = _old_intake(tmp_path)
    prepare_assets(recipe_path, sources, old_prepared)
    brand = tmp_path / "tovitunes"
    shutil.copytree(brand_root, brand)
    manifest = brand / "characters/tovi/packs/v1/pack.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("readiness: approved", "readiness: draft"),
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db")
    database.migrate()
    store = AssetStore(tmp_path / "assets", database, generated_source_roots=[old_prepared])
    old = ingest_prepared(recipe_path, sources, old_prepared, store, load_brand(brand), manifest)
    old_ids = old["role_artifact_ids"]
    old_hashes = {role: store.get(artifact_id).sha256 for role, artifact_id in old_ids.items()}
    with database.connect() as connection:
        old_approval_counts = {
            role: connection.execute(
                "SELECT COUNT(*) FROM approval_decisions WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()[0]
            for role, artifact_id in old_ids.items()
        }

    recipe = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    recipe["superseded_assets"] = recipe["assets"]
    recipe["rights_evidence"] = {"uri": TERMS, "policy_version": "openai-terms-of-use-2026-01-01"}
    recipe["mouth_normalization"] = {
        "source_canvas": [128, 128],
        "output_canvas": [224, 256],
        "anchor": [112, 128],
        "alpha_threshold": 8,
    }
    for key in ("mouth_sprites", "expressions"):
        recipe["sources"][key]["rights_basis"] = "openai_chatgpt_output"
    assets = []
    for state in MOUTH_SHAPES:
        filename = f"tovi-mouth-{state.replace('_', '-')}.png"
        _mouth(sources / filename, state)
        source = f"mouth_{state}_replacement"
        recipe["sources"][source] = {
            "filename": filename,
            "sha256": _sha(sources / filename),
            "source_uri": f"generation://replacement-{state}",
            "rights_basis": "openai_chatgpt_output",
        }
        assets.append(
            {
                "role": f"mouth/{state}",
                "source": source,
                "rect": [0, 0, 128, 128],
                "method": "mouth_component",
                "art_review": "approved",
            }
        )
    new_thinking = sources / "tovi-thinking.png"
    thinking = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(thinking).ellipse((24, 20, 104, 110), fill=(70, 170, 245, 255))
    thinking.save(new_thinking)
    recipe["sources"]["thinking_replacement"] = {
        "filename": new_thinking.name,
        "sha256": _sha(new_thinking),
        "source_uri": "generation://replacement-thinking",
        "rights_basis": "openai_chatgpt_output",
    }
    assets.append(
        {
            "role": "sprite/expression_thinking",
            "source": "thinking_replacement",
            "rect": [0, 0, 128, 128],
            "method": "pad",
            "padding": 32,
            "art_review": "approved",
        }
    )
    recipe["assets"] = assets
    recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8")
    prepared = tmp_path / "prepared-new"
    report = prepare_assets(recipe_path, sources, prepared)
    alignments = [
        item["mouth_alignment"] for item in report["assets"] if item["role"].startswith("mouth/")
    ]
    assert all(item["common_anchor"] == (112, 128) for item in alignments)
    assert all(item["visible_bbox"][1] == 128 for item in alignments)
    assert {item["paste_offset"] for item in alignments} != {(0, 0)}
    for state in MOUTH_SHAPES:
        with Image.open(prepared / f"mouth__{state}.png") as image:
            assert image.size == (224, 256)
            assert visible_art_bbox(image)[1] == 128
            alignment = next(
                item["mouth_alignment"]
                for item in report["assets"]
                if item["role"] == f"mouth/{state}"
            )
            x, y = alignment["paste_offset"]
            with Image.open(sources / f"tovi-mouth-{state.replace('_', '-')}.png") as source:
                restored = image.crop((x, y, x + source.width, y + source.height))
                assert ImageChops.difference(restored, source).getbbox() is None
    store = AssetStore(tmp_path / "assets", database, generated_source_roots=[prepared])
    new = ingest_prepared(recipe_path, sources, prepared, store, load_brand(brand), manifest)
    new_ids = new["role_artifact_ids"]
    assert len(new_ids) == 6
    assert all(new_ids[role] != old_ids[role] for role in old_ids)
    assert all(
        store.get(artifact_id).sha256 == old_hashes[role] for role, artifact_id in old_ids.items()
    )
    for role, new_id in new_ids.items():
        record = store.get(new_id)
        assert (
            store.selected(
                "brand", record.identity.owner_id, record.identity.kind, record.identity.slot_key
            ).identity.artifact_id
            == new_id
        )
    with database.connect() as connection:
        for role, old_id in old_ids.items():
            old_approval = connection.execute(
                "SELECT status FROM approval_decisions WHERE artifact_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (old_id,),
            ).fetchone()
            assert old_approval["status"] == "needs_review", role
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM approval_decisions WHERE artifact_id = ?", (old_id,)
                ).fetchone()[0]
                == old_approval_counts[role]
            )
        for new_id in new_ids.values():
            approval = connection.execute(
                "SELECT status, reason FROM approval_decisions WHERE artifact_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (new_id,),
            ).fetchone()
            rights = connection.execute(
                "SELECT status, actor, evidence_uri, policy_version, decided_at, rationale "
                "FROM rights_decisions WHERE artifact_id = ? ORDER BY rowid DESC LIMIT 1",
                (new_id,),
            ).fetchone()
            assert approval["status"] == "approved"
            assert "Replaces held PR #10 candidate" in approval["reason"]
            assert rights["status"] == "commercial_use_confirmed"
            assert rights["actor"] == "project-owner"
            assert rights["evidence_uri"] == TERMS
            assert rights["policy_version"] == "openai-terms-of-use-2026-01-01"
            assert datetime.fromisoformat(rights["decided_at"]).tzinfo is not None
            assert "input references remain separate" in rights["rationale"]
        for source in ("original_profile", "original_banner"):
            source_id = new["source_artifact_ids"][source]
            rights = connection.execute(
                "SELECT status FROM rights_decisions WHERE artifact_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (source_id,),
            ).fetchone()
            assert rights["status"] == "unknown"
        for old_id in old_ids.values():
            rights = connection.execute(
                "SELECT status FROM rights_decisions WHERE artifact_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (old_id,),
            ).fetchone()
            assert rights["status"] == "commercial_use_confirmed"
    assert new["blocking_issues"] == ("pack manifest remains draft",)
    repeated = ingest_prepared(recipe_path, sources, prepared, store, load_brand(brand), manifest)
    assert repeated["role_artifact_ids"] == new_ids
    assert repeated["rights_decision_artifact_ids"] == ()


def test_rights_confirmation_still_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="requires evidence"):
        RightsDecision(
            artifact_id="asset-id",
            status="commercial_use_confirmed",
            actor="owner",
            policy_version="terms-2026",
            decided_at=datetime.now(UTC),
        )
