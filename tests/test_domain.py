import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tovitunes.catalog import BrandCatalog, load_brand
from tovitunes.domain.artifact import ArtifactIdentity, Provenance
from tovitunes.domain.episode import Episode, EpisodeSpec
from tovitunes.domain.review import ApprovalDecision, RightsDecision


def test_catalog_and_pack_revision_are_independently_pinned(
    tmp_path: Path, brand_root: Path, catalog: BrandCatalog
) -> None:
    assert catalog.definition.brand_id == "tovitunes"
    assert catalog.curriculum.get("red").objective_id == "colors.red.identify"
    assert catalog.pack_revisions[0].readiness == "approved"
    episode = Episode.create(catalog, "red", "colors-red")
    assert episode.brand_revision_id == catalog.version.revision_id
    assert episode.character_packs[0].revision_id == catalog.pack_revisions[0].revision_id

    changed_root = tmp_path / "brand"
    shutil.copytree(brand_root, changed_root)
    pack_path = changed_root / "characters" / "tovi" / "packs" / "v1" / "pack.yaml"
    pack_path.write_text(
        pack_path.read_text(encoding="utf-8").replace("readiness: approved", "readiness: draft"),
        encoding="utf-8",
    )
    changed = load_brand(changed_root)
    assert changed.pack_revisions[0].readiness == "draft"
    assert changed.version.revision_id == catalog.version.revision_id
    assert changed.pack_revisions[0].revision_id != catalog.pack_revisions[0].revision_id
    assert episode.character_packs[0].revision_id != changed.pack_revisions[0].revision_id


def test_catalog_rejects_path_escape(tmp_path: Path, brand_root: Path) -> None:
    changed_root = tmp_path / "brand"
    shutil.copytree(brand_root, changed_root)
    character_path = changed_root / "characters" / "tovi" / "character.yaml"
    character_path.write_text(
        character_path.read_text(encoding="utf-8").replace(
            "characters/tovi/packs/v1/pack.yaml", "../outside.yaml"
        ),
        encoding="utf-8",
    )
    (tmp_path / "outside.yaml").write_text("{}", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        load_brand(changed_root)


def test_artifact_and_review_contracts(catalog: BrandCatalog) -> None:
    episode = Episode.create(catalog, "red", "colors-red")
    identity = ArtifactIdentity.create("episode", episode.episode_id, "music", "candidate_1")
    assert identity.artifact_id
    with pytest.raises(ValidationError):
        ArtifactIdentity.create("episode", episode.episode_id, "music", "../escape")
    provenance = Provenance.manual("producer", "local://approved-song")
    assert provenance.source_kind == "manual"
    with pytest.raises(ValidationError):
        RightsDecision(
            artifact_id=identity.artifact_id,
            status="commercial_use_confirmed",
            actor="producer",
            policy_version="1",
            decided_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        ApprovalDecision(
            target_id=identity.artifact_id,
            target_kind="artifact",
            status="rejected",
            actor="producer",
            policy_version="1",
            decided_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        EpisodeSpec.model_validate(
            {
                "episode_id": episode.episode_id,
                "objective_id": episode.objective_id,
                "premise": "Find a red ball",
                "cast": ["tovi"],
                "setting": "playground",
                "teaching_vocabulary": ["red"],
                "story_beats": ["find", "name"],
                "lyrics": "Red ball",
                "desired_structure": "verse/chorus",
                "start_time": 0,
            }
        )

