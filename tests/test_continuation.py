import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.catalog import BrandCatalog
from tovitunes.cli import main as cli_main
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision
from tovitunes.persistence.db import Database
from tovitunes.persistence.leases import LeaseHeld, LeaseLost, LeaseStore
from tovitunes.persistence.requests import InvalidRequestTransition, RequestLedger
from tovitunes.pipeline.planner import (
    CandidateFact,
    PlanSnapshot,
    SlotFact,
    load_snapshot,
    plan,
    plan_episode,
    requirements,
)


def _setup(tmp_path: Path, catalog: BrandCatalog) -> tuple[AssetStore, Database, Episode]:
    db = Database(tmp_path / "state.db")
    db.migrate()
    episode = Episode.create(catalog, "red", "colors-red")
    db.create_episode(catalog, episode)
    store = AssetStore(tmp_path / "assets", db)
    store.record_approval(
        ApprovalDecision(
            target_id=episode.episode_id,
            target_kind="episode",
            status="approved",
            actor="reviewer",
            policy_version="1",
            decided_at=datetime.now(UTC),
        )
    )
    return store, db, episode


def _put(
    store: AssetStore,
    tmp_path: Path,
    episode: Episode,
    kind: str,
    slot: str,
    dependencies: tuple[str, ...] = (),
    payload: dict[str, object] | None = None,
) -> str:
    source = tmp_path / "payload.json"
    source.write_text(json.dumps(payload or {"kind": kind, "slot": slot}), encoding="utf-8")
    record = store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind=kind,
        slot_key=slot,
        provenance=Provenance.manual("producer", "local://fixture"),
        dependencies=[InputDependency(value, "upstream") for value in dependencies],
    )
    store.record_approval(
        ApprovalDecision(
            target_id=record.identity.artifact_id,
            target_kind="artifact",
            status="approved",
            actor="reviewer",
            policy_version="1",
            decided_at=datetime.now(UTC),
        )
    )
    store.select(record.identity.artifact_id)
    return record.identity.artifact_id


def _through_storyboard(store: AssetStore, tmp_path: Path, episode: Episode) -> dict[str, str]:
    ids: dict[str, str] = {}
    ids["episode_spec"] = _put(store, tmp_path, episode, "episode_spec", "main")
    ids["lyrics"] = _put(store, tmp_path, episode, "lyrics", "main", (ids["episode_spec"],))
    ids["music_spec"] = _put(store, tmp_path, episode, "music_spec", "main", (ids["lyrics"],))
    ids["audio_master"] = _put(
        store, tmp_path, episode, "audio_master", "main", (ids["music_spec"],)
    )
    ids["audio_alignment"] = _put(
        store, tmp_path, episode, "audio_alignment", "main", (ids["audio_master"],)
    )
    ids["beat_analysis"] = _put(
        store, tmp_path, episode, "beat_analysis", "main", (ids["audio_master"],)
    )
    ids["timed_storyboard"] = _put(
        store,
        tmp_path,
        episode,
        "timed_storyboard",
        "main",
        (ids["audio_master"], ids["audio_alignment"], ids["beat_analysis"]),
        {
            "schema_version": 1,
            "audio_master_artifact_id": ids["audio_master"],
            "scene_ids": ["scene_1", "scene_2", "scene_3"],
        },
    )
    return ids


def test_planner_preserves_song_and_finished_scenes(
    tmp_path: Path, draft_catalog: BrandCatalog
) -> None:
    store, _, episode = _setup(tmp_path, draft_catalog)
    assert plan_episode(store, episode.episode_id, "render").requirement == "episode_spec"
    ids = _through_storyboard(store, tmp_path, episode)
    for scene in ("scene_1", "scene_2"):
        _put(store, tmp_path, episode, "scene_image", scene, (ids["timed_storyboard"],))
        _put(store, tmp_path, episode, "character_animation", scene, (ids["timed_storyboard"],))
    hold = plan_episode(store, episode.episode_id, "render")
    assert hold.action == "review"
    assert hold.requirement == "character_pack"
    result = plan(
        replace(load_snapshot(store, episode.episode_id), character_pack_readiness=("approved",)),
        "render",
    )
    assert result.action == "produce"
    assert result.requirement == "scene_image:scene_3"
    assert plan_episode(store, episode.episode_id, "audio").action == "complete"

    replacement = _put(store, tmp_path, episode, "audio_master", "main", (ids["music_spec"],))
    assert replacement != ids["audio_master"]
    stale = plan_episode(store, episode.episode_id, "render")
    assert stale.action == "stale"
    assert stale.requirement == "audio_alignment"


def test_approved_pack_advances_render_and_release_to_next_requirement(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    assert catalog.pack_revisions[0].readiness == "approved"
    store, _, episode = _setup(tmp_path, catalog)
    _through_storyboard(store, tmp_path, episode)
    for goal in ("render", "release"):
        result = plan_episode(store, episode.episode_id, goal)
        assert result.action == "produce"
        assert result.requirement == "scene_image:scene_1"


def test_ambiguous_generation_blocks_repetition(tmp_path: Path, catalog: BrandCatalog) -> None:
    store, db, episode = _setup(tmp_path, catalog)
    spec = _put(store, tmp_path, episode, "episode_spec", "main")
    lyrics = _put(store, tmp_path, episode, "lyrics", "main", (spec,))
    _put(store, tmp_path, episode, "music_spec", "main", (lyrics,))
    ledger = RequestLedger(db)
    request = ledger.prepare(episode.episode_id, "audio_master", "main", "music", "v1", "a" * 64)
    ledger.transition(request.request_id, "remote_started", provider_request_id="remote-1")
    ledger.transition(request.request_id, "ambiguous")
    result = plan_episode(store, episode.episode_id, "audio")
    assert result.action == "reconcile"
    assert result.requirement == "audio_master"
    with pytest.raises(InvalidRequestTransition):
        ledger.prepare(episode.episode_id, "audio_master", "main", "music", "v1", "a" * 64)
    ledger.transition(request.request_id, "failed", definitive_remote_failure=True)
    assert (
        ledger.prepare(episode.episode_id, "audio_master", "main", "music", "v1", "a" * 64).status
        == "prepared"
    )


def test_lease_expiry_fences_old_owner(tmp_path: Path, catalog: BrandCatalog) -> None:
    _, db, episode = _setup(tmp_path, catalog)
    now = [100.0]
    leases = LeaseStore(db, clock=lambda: now[0])
    old = leases.acquire(f"episode:{episode.episode_id}", duration_seconds=10)
    with pytest.raises(LeaseHeld):
        leases.acquire(old.resource_key, duration_seconds=10)
    now[0] = 111.0
    fresh = leases.acquire(old.resource_key, duration_seconds=10)
    with pytest.raises(LeaseLost):
        leases.assert_owner(old)
    with pytest.raises(LeaseLost):
        leases.renew(old, duration_seconds=10)
    leases.assert_owner(fresh)
    renewed = leases.renew(fresh, duration_seconds=10)
    leases.release(renewed)
    with pytest.raises(LeaseLost):
        leases.assert_owner(renewed)


def test_pure_plan_requires_transitive_rights() -> None:
    scene_ids = ("scene_1",)
    selected: dict[str, str] = {}
    slots: dict[tuple[str, str], SlotFact] = {}
    for requirement in requirements("release", scene_ids):
        artifact_id = f"artifact-{requirement.key}"
        prerequisites = frozenset(selected[key] for key in requirement.prerequisites)
        slots[(requirement.kind, requirement.slot_key)] = SlotFact(
            artifact_id,
            (
                CandidateFact(
                    artifact_id, True, None, "approved", "commercial_use_confirmed", prerequisites
                ),
            ),
        )
        selected[requirement.key] = artifact_id
    snapshot = PlanSnapshot(
        "episode-1",
        slots,
        {},
        scene_ids,
        None,
        ("brand-reference-uncleared",),
        "approved",
        ("approved",),
    )
    result = plan(snapshot, "release")
    assert result.action == "rights"
    assert result.artifact_id == "brand-reference-uncleared"


def test_cli_plan_and_status_leave_asset_root_untouched(
    tmp_path: Path, catalog: BrandCatalog, capsys: pytest.CaptureFixture[str]
) -> None:
    store, db, episode = _setup(tmp_path, catalog)
    (store.root / ".staging").rmdir()
    (store.root / "quarantine").rmdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        json.dumps(
            {
                "database_path": str(db.path),
                "data_root": str(store.root),
                "brand_root": str(tmp_path / "brand"),
            }
        ),
        encoding="utf-8",
    )
    base = ["--config", str(config)]
    assert cli_main([*base, "plan", episode.episode_id, "--goal", "render"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["action"] == "produce"
    assert planned["requirement"] == "episode_spec"
    assert cli_main([*base, "status", episode.episode_id, "--goal", "render"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["next_action"] == planned
    assert status["requirements"][0]["candidate_count"] == 0
    assert not (store.root / ".staging").exists()
    assert not (store.root / "quarantine").exists()

