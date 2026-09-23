from pathlib import Path

import pytest

from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import BrandCatalog
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.persistence.db import Database
from tovitunes.pipeline.creative import CreativeDraftService, FakeDraftGenerator
from tovitunes.pipeline.planner import plan_episode


def _service(
    tmp_path: Path, catalog: BrandCatalog
) -> tuple[CreativeDraftService, AssetStore, Episode]:
    db = Database(tmp_path / "state.db")
    db.migrate()
    episode = Episode.create(catalog, "red", "colors-red")
    db.create_episode(catalog, episode)
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    store = AssetStore(tmp_path / "assets", db, generated_source_roots=(generated_root,))
    return CreativeDraftService(store, generated_root, FakeDraftGenerator()), store, episode


def test_fake_creative_drafts_require_human_gates_and_preserve_alternatives(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    service, store, episode = _service(tmp_path, catalog)
    assert plan_episode(store, episode.episode_id, "audio").requirement == "learning_objective"
    with pytest.raises(PermissionError):
        service.draft_episode_spec(episode.episode_id)

    service.review_objective(episode.episode_id, "approved", actor="human")
    rejected = service.draft_episode_spec(episode.episode_id, variant=1)
    chosen = service.draft_episode_spec(episode.episode_id, variant=2)
    assert chosen.provenance.provider == "fake-llm"
    assert chosen.provenance.model == "fixture-v1"
    assert chosen.provenance.request_id
    assert chosen.provenance.prompt_version == "episode-concept-v1"
    with pytest.raises(ValueError, match="approval"):
        store.select(chosen.identity.artifact_id)
    assert plan_episode(store, episode.episode_id, "audio").action == "review"
    service.review_candidate(
        rejected.identity.artifact_id, "rejected", actor="human", reason="weak hook"
    )
    service.review_candidate(chosen.identity.artifact_id, "approved", actor="human")
    spec = EpisodeSpec.model_validate(store.read_json(chosen.identity.artifact_id))
    assert spec.objective_id == episode.objective_id
    assert "red" in spec.teaching_vocabulary

    lyrics = service.draft_lyrics(episode.episode_id)
    lyric_data = LyricsSpec.model_validate(store.read_json(lyrics.identity.artifact_id))
    assert lyric_data.episode_spec_artifact_id == chosen.identity.artifact_id
    service.review_candidate(lyrics.identity.artifact_id, "approved", actor="human")

    music = service.draft_music_spec(episode.episode_id)
    music_data = MusicSpec.model_validate(store.read_json(music.identity.artifact_id))
    assert music_data.lyrics_artifact_id == lyrics.identity.artifact_id
    assert music_data.target_duration_seconds == episode.target_duration_seconds
    service.review_candidate(music.identity.artifact_id, "approved", actor="human")
    assert plan_episode(store, episode.episode_id, "audio").requirement == "audio_master"

    service.review_candidate(
        lyrics.identity.artifact_id, "rejected", actor="human", reason="revoked"
    )
    assert store.selected("episode", episode.episode_id, "lyrics", "main") is None
    assert store.eligibility(music.identity.artifact_id)[0] is False
    assert plan_episode(store, episode.episode_id, "audio").requirement == "lyrics"
    assert store.get(rejected.identity.artifact_id).identity.kind == "episode_spec"


def test_objective_rejection_holds_existing_creative_work(
    tmp_path: Path, catalog: BrandCatalog
) -> None:
    service, store, episode = _service(tmp_path, catalog)
    service.review_objective(episode.episode_id, "approved", actor="human")
    candidate = service.draft_episode_spec(episode.episode_id)
    service.review_candidate(candidate.identity.artifact_id, "approved", actor="human")
    service.review_objective(
        episode.episode_id, "rejected", actor="human", reason="objective needs revision"
    )
    assert plan_episode(store, episode.episode_id, "audio").requirement == "learning_objective"
    with pytest.raises(PermissionError):
        service.draft_lyrics(episode.episode_id)

