from base64 import b64decode
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tovitunes.artifacts.character_pack import assess_pack_assets
from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import BrandCatalog
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.character import CharacterAssetPack
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.persistence.db import Database


def test_pack_stays_draft_without_art_and_rights(tmp_path: Path, catalog: BrandCatalog) -> None:
    db = Database(tmp_path / "state.db")
    db.migrate()
    episode = Episode.create(catalog, "red", "colors-red")
    db.create_episode(catalog, episode)
    store = AssetStore(tmp_path / "assets", db)
    draft = catalog.packs[0]
    report = assess_pack_assets(draft, store, catalog.version.revision_id)
    assert not report.ready
    assert "pack manifest remains draft" in report.issues
    with pytest.raises(ValidationError, match="approved pack needs"):
        CharacterAssetPack.model_validate({**draft.model_dump(), "readiness": "approved"})


def test_pack_requires_selected_approved_rights_cleared_brand_assets(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    db = Database(tmp_path / "state.db")
    db.migrate()
    db.create_episode(catalog, Episode.create(catalog, "red", "colors-red"))
    store = AssetStore(tmp_path / "assets", db)
    png = tmp_path / "art.png"
    png.write_bytes(
        b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WTcYAAAAABJRU5ErkJggg=="
        )
    )
    views = ("front", "three_quarter", "profile")
    mouths = ("closed", "small_open", "wide_a", "e_smile", "o_round")
    roles = [
        *(f"view/{name}" for name in views),
        "sprite/body",
        *(f"mouth/{name}" for name in mouths),
    ]
    refs: dict[str, str] = {}
    for index, role in enumerate(roles):
        kind = "character_reference" if role.startswith("view/") else "character_sprite"
        record = store.ingest(
            png,
            owner_scope="brand",
            owner_id=catalog.version.revision_id,
            kind=kind,
            slot_key=f"tovi_{index}",
            provenance=Provenance.manual("artist", "local://approved-original"),
        )
        artifact_id = record.identity.artifact_id
        refs[role] = artifact_id
        store.record_approval(
            ApprovalDecision(
                target_id=artifact_id,
                target_kind="artifact",
                status="approved",
                actor="art-reviewer",
                policy_version="pack-v1",
                decided_at=datetime.now(UTC),
            )
        )
        store.record_rights(
            RightsDecision(
                artifact_id=artifact_id,
                status="commercial_use_confirmed",
                actor="rights-reviewer",
                evidence_uri="local://rights-evidence",
                policy_version="pack-v1",
                decided_at=datetime.now(UTC),
            )
        )
        store.select(artifact_id)
    approved = CharacterAssetPack.model_validate(
        {
            **catalog.packs[0].model_dump(),
            "readiness": "approved",
            "palette": {"body": "#F4A340"},
            "canonical_views": views,
            "expressions": ("happy",),
            "wing_poses": ("neutral",),
            "mouth_states": mouths,
            "reusable_sprites": ("body",),
            "forbidden_changes": ("keep the same silhouette",),
            "visual_rules": {
                "silhouette": "pinned by reviewed art",
                "proportions": "pinned by reviewed art",
                "eyes": "pinned by reviewed art",
                "beak": "pinned by reviewed art",
                "tuft": "pinned by reviewed art",
            },
            "asset_artifact_ids": refs,
        }
    )
    report = assess_pack_assets(approved, store, catalog.version.revision_id)
    assert report.ready
    assert len(report.checked_artifact_ids) == len(roles)

    store.record_rights(
        RightsDecision(
            artifact_id=refs["view/front"],
            status="blocked",
            actor="rights-reviewer",
            policy_version="pack-v1",
            decided_at=datetime.now(UTC),
        )
    )
    blocked = assess_pack_assets(approved, store, catalog.version.revision_id)
    assert not blocked.ready
    assert any("view/front" in issue for issue in blocked.issues)

