"""Metadata from retained selected facts, with no rendering or uploading in this suite."""

from contextlib import closing
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_creative_director import workflow as creative_workflow_fixture

from tovitunes.artifacts.store import InputDependency
from tovitunes.creative.metadata import MetadataWriter
from tovitunes.creative.models import EpisodePublicationMetadata
from tovitunes.creative.provider import StructuredOutputError
from tovitunes.creative.validation import validate_metadata
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision
from tovitunes.domain.storyboard import TimedScene, TimedStoryboard
from tovitunes.publication.rights_policy import is_direct_rights_root
from tovitunes.render.models import RenderManifest, SceneRender

workflow = creative_workflow_fixture


def ingest(flow, episode_id, kind, content, deps=()):
    path = flow.generated / (kind + (".mp4" if kind == "final_render" else ".json"))
    path.write_bytes(content if isinstance(content, bytes) else content.model_dump_json().encode())
    record = flow.store.ingest(
        path,
        owner_scope="episode",
        owner_id=episode_id,
        kind=kind,
        slot_key="main",
        provenance=Provenance(
            source_kind="deterministic",
            acquired_at=datetime.now(UTC),
            provider="offline-facts",
            input_artifact_ids=deps,
        ),
        dependencies=[InputDependency(d, "fixture_input") for d in deps],
    )
    flow.store.record_approval(
        ApprovalDecision(
            target_id=record.identity.artifact_id,
            target_kind="artifact",
            status="approved",
            actor="human:fixture",
            policy_version="fixture-v1",
            decided_at=datetime.now(UTC),
        )
    )
    flow.store.select(record.identity.artifact_id)
    return record


@pytest.fixture
def finished(workflow):
    flow, fake = workflow
    legacy = Episode.create(flow.catalog, "red", "colors-red-001")
    flow.database.create_episode(flow.catalog, legacy)
    result = flow.generate_next(episode_key=legacy.external_key)
    episode = flow.database.get_episode(result["episode_id"])
    lyrics = flow.store.read_json(result["lyrics_artifact_id"])
    texts = [line["text"] for line in lyrics["lines"]]
    duration = float(episode.target_duration_seconds)
    scenes = tuple(
        TimedScene(
            scene_id=f"lyric_{i}",
            start=i * duration / len(texts),
            end=(i + 1) * duration / len(texts),
            kind="lyric",
            section="verse",
            lyric_text=text,
            lyric_start=i * duration / len(texts),
            lyric_end=(i + 1) * duration / len(texts),
            lesson_target="red",
            visual_focus="one red swatch",
            required_props=("red_swatch",),
            tovi_action="point",
            beat_index_range=(0, 0),
            downbeat_index_range=(0, 0),
        )
        for i, text in enumerate(texts)
    )
    storyboard = TimedStoryboard(
        episode_id=episode.episode_id,
        audio_master_artifact_id="audio-fixture",
        audio_alignment_artifact_id="alignment-fixture",
        beat_analysis_artifact_id="beats-fixture",
        audio_sha256="a" * 64,
        duration_seconds=duration,
        template_id="red_fixture",
        template_sha256="b" * 64,
        concept_id="red",
        objective_id=episode.objective_id,
        character_pack=episode.character_packs[0],
        scenes=scenes,
    )
    sb = ingest(flow, episode.episode_id, "timed_storyboard", storyboard)
    scene_refs = tuple(
        SceneRender(
            scene_id=s.scene_id,
            start=s.start,
            end=s.end,
            scene_image_artifact_id="image-fixture",
            character_animation_artifact_id="animation-fixture",
        )
        for s in scenes
    )
    manifest = RenderManifest(
        renderer_version="tovitunes_sprite_render_v2",  # Retained pre-motion render facts.
        episode_id=episode.episode_id,
        audio_master_artifact_id="audio-fixture",
        audio_alignment_artifact_id="alignment-fixture",
        beat_analysis_artifact_id="beats-fixture",
        timed_storyboard_artifact_id=sb.identity.artifact_id,
        character_pack_revision=episode.character_packs[0].revision_id,
        dependency_sha256={
            sb.identity.artifact_id: sb.sha256,
            **dict.fromkeys(
                (
                    "audio-fixture",
                    "alignment-fixture",
                    "beats-fixture",
                    "image-fixture",
                    "animation-fixture",
                ),
                "a" * 64,
            ),
        },
        scenes=scene_refs,
        duration_seconds=duration,
        ffmpeg_version="fixture",
        ffprobe_version="fixture",
    )
    mr = ingest(flow, episode.episode_id, "render_manifest", manifest, (sb.identity.artifact_id,))
    final = ingest(
        flow,
        episode.episode_id,
        "final_render",
        b"\0\0\0\x18ftypisomfixture-one",
        (mr.identity.artifact_id,),
    )
    return flow, fake, result, final, mr


def test_metadata_requires_selected_final_render_before_provider(workflow):
    flow, fake = workflow
    result = flow.generate_next()
    with pytest.raises(ValueError, match="selected final_render"):
        MetadataWriter(flow).generate(result["episode_key"])
    assert len(fake.calls) == 4


def test_metadata_pins_all_authoritative_dependencies_and_reuses_same_render(finished):
    flow, fake, creative, final, _ = finished
    writer = MetadataWriter(flow)
    first = writer.generate(creative["episode_key"])
    second = writer.generate(creative["episode_key"])
    assert fake.calls.count("EpisodePublicationMetadata") == 1
    assert first["publication_metadata_artifact_id"] == second["publication_metadata_artifact_id"]
    assert all(v == 0 for v in second["provider_calls"].values())
    metadata = first["metadata"]
    assert metadata["made_for_kids"] is True and metadata["language"] == "en"
    assert metadata["final_render_sha256"] == final.sha256
    record = flow.store.get(first["publication_metadata_artifact_id"])
    assert record.identity.kind == "publication_metadata" and record.identity.slot_key == "main"
    with closing(flow.database.connect()) as db:
        deps = {
            r[0]: r[1]
            for r in db.execute(
                "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                "WHERE consumer_artifact_id=?",
                (record.identity.artifact_id,),
            )
        }
        rights = db.execute(
            "SELECT status FROM rights_decisions WHERE artifact_id=? ORDER BY rowid DESC",
            (record.identity.artifact_id,),
        ).fetchone()[0]
    assert final.identity.artifact_id in deps and deps[final.identity.artifact_id] == final.sha256
    assert creative["episode_spec_artifact_id"] in deps and creative["lyrics_artifact_id"] in deps
    assert len(deps) == 5 and rights == "unknown"
    assert record.provenance.local_request_id and record.provenance.request_id is None
    assert first["provider_calls"]["metadata"] == 1
    # Prompt contains actual scene props and measured final duration, rather than guessed scenes.
    text = " ".join(m["content"] for m in fake.messages[-1])
    assert "red_swatch" in text and str(final.sha256) in text and "duration_seconds" in text


def test_same_render_reuses_metadata_even_if_provider_configuration_changes(finished, monkeypatch):
    flow, _, creative, _, _ = finished
    writer = MetadataWriter(flow)
    first = writer.generate(creative["episode_key"])
    monkeypatch.setattr(
        flow.provider, "generate", lambda *a, **k: pytest.fail("metadata regeneration")
    )
    second = writer.generate(creative["episode_key"])
    assert second["publication_metadata_artifact_id"] == first["publication_metadata_artifact_id"]


def test_operator_metadata_replaces_provider_selection_without_provider_or_render(
    finished, monkeypatch
):
    flow, fake, creative, final, _ = finished
    writer = MetadataWriter(flow)
    old = writer.generate(creative["episode_key"])
    old_id = old["publication_metadata_artifact_id"]
    before_calls = tuple(fake.calls)
    with closing(flow.database.connect()) as db:
        before_render_count = db.execute(
            "SELECT count(*) FROM artifact_versions WHERE kind='final_render'"
        ).fetchone()[0]
    monkeypatch.setattr(flow.provider, "generate", lambda *a, **k: pytest.fail("provider call"))
    replacement = EpisodePublicationMetadata.model_validate(
        {
            **old["metadata"],
            "youtube_title": "Learn Red with Tovi | ToviTunes Preschool Short",
        }
    )
    result = writer.record_operator_approved(
        creative["episode_key"],
        replacement,
        actor="human:operator",
        source_uri="repo://docs/rights/COLORS_RED_OPERATOR_METADATA.md",
    )
    new_id = result["publication_metadata_artifact_id"]
    assert new_id != old_id and tuple(fake.calls) == before_calls
    assert all(value == 0 for value in result["provider_calls"].values())
    assert result["final_render_sha256"] == final.sha256
    assert flow.store.get(old_id).provenance.source_kind == "provider"
    assert (
        flow.store.selected(
            "episode", creative["episode_id"], "publication_metadata", "main"
        ).identity.artifact_id
        == new_id
    )
    record = flow.store.get(new_id)
    assert record.provenance.source_kind == "manual" and not is_direct_rights_root(record)
    assert is_direct_rights_root(flow.store.get(old_id))
    with closing(flow.database.connect()) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM artifact_versions WHERE kind='final_render'"
            ).fetchone()[0]
            == before_render_count
        )
        assert (
            db.execute(
                "SELECT count(*) FROM artifact_versions WHERE artifact_id=?", (old_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT status FROM approval_decisions WHERE artifact_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (new_id,),
            ).fetchone()[0]
            == "approved"
        )
        assert (
            db.execute(
                "SELECT input_sha256 FROM artifact_dependencies "
                "WHERE consumer_artifact_id=? AND input_artifact_id=?",
                (new_id, final.identity.artifact_id),
            ).fetchone()[0]
            == final.sha256
        )


def test_frozen_storyboard_without_creative_pair_supplies_metadata_facts(finished):
    flow, _, creative, final, manifest = finished
    with closing(flow.database.connect()) as db:
        db.execute(
            "DELETE FROM artifact_selections WHERE owner_id=? "
            "AND kind IN ('episode_spec','lyrics')",
            (creative["episode_id"],),
        )
        db.commit()
    facts, dependencies = MetadataWriter(flow)._facts(creative["episode_id"])
    assert facts["episode_spec"]["basis"] == "selected_timed_storyboard"
    assert facts["lyrics"]["lines"]
    assert facts["final_render"]["sha256"] == final.sha256
    assert manifest.identity.artifact_id in dependencies
    assert creative["episode_spec_artifact_id"] not in dependencies


def test_human_objective_hold_blocks_post_render_metadata(finished):
    flow, fake, creative, _, _ = finished
    flow.store.record_approval(
        ApprovalDecision(
            target_id=creative["episode_id"],
            target_kind="episode",
            status="rejected",
            actor="human",
            reason="hold",
            policy_version="human-v1",
            decided_at=datetime.now(UTC),
        )
    )
    with pytest.raises(PermissionError, match="objective approval"):
        MetadataWriter(flow).generate(creative["episode_key"])
    assert "EpisodePublicationMetadata" not in fake.calls


def test_changed_render_requires_new_metadata_and_old_metadata_becomes_stale(finished):
    flow, fake, creative, final, manifest = finished
    writer = MetadataWriter(flow)
    first = writer.generate(creative["episode_key"])
    changed = ingest(
        flow,
        creative["episode_id"],
        "final_render",
        b"\0\0\0\x18ftypisomfixture-two",
        (manifest.identity.artifact_id,),
    )
    assert changed.sha256 != final.sha256
    assert not flow.store.eligibility(first["publication_metadata_artifact_id"])[0]
    second = writer.generate(creative["episode_key"])
    assert fake.calls.count("EpisodePublicationMetadata") == 2
    assert first["publication_metadata_artifact_id"] != second["publication_metadata_artifact_id"]
    assert second["metadata"]["final_render_sha256"] == changed.sha256


def valid_metadata():
    return EpisodePublicationMetadata(
        youtube_title="Learn Red with Tovi | Color Song for Kids #Shorts",
        youtube_description="Learn red with ToviTunes in this preschool song.",
        tags=("red", "kids song", "shorts"),
        episode_id="episode",
        concept_id="red",
        final_render_artifact_id="final",
        final_render_sha256="a" * 64,
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"youtube_title": "x" * 101},
        {"youtube_description": "x" * 1001},
        {"tags": tuple("x" * 79 + str(i) for i in range(7))},
        {"made_for_kids": False},
        {"language": "de"},
    ],
)
def test_metadata_bounds_and_deterministic_child_language_pins(updates):
    with pytest.raises(ValidationError):
        EpisodePublicationMetadata.model_validate({**valid_metadata().model_dump(), **updates})


def test_tags_deduplicated_case_insensitively_and_language_kids_default_to_pins():
    data = valid_metadata().model_dump()
    data.pop("language")
    data.pop("made_for_kids")
    data["tags"] = [" red ", "RED", "kids song", "  ", "kids   song"]
    result = EpisodePublicationMetadata.model_validate(data)
    assert result.tags == ("red", "kids song") and result.language == "en" and result.made_for_kids


@pytest.mark.parametrize(
    "updates",
    [
        {"concept_id": "blue"},
        {"final_render_sha256": "b" * 64},
        {"youtube_title": "Learn Blue with Tovi"},
        {"youtube_description": "Learn red and blue with ToviTunes."},
        {"youtube_description": "ToviTunes teaches red with a red apple."},
        {"youtube_description": "ToviTunes guarantees a genius child who learns red."},
    ],
)
def test_metadata_rejects_other_concept_render_and_unsupported_content(workflow, updates):
    flow, _ = workflow
    episode = Episode.create(flow.catalog, "red", "metadata-test").model_copy(
        update={"episode_id": "episode"}
    )
    metadata = valid_metadata().model_copy(update=updates)
    with pytest.raises(ValueError):
        validate_metadata(episode, metadata, "final", "a" * 64, ("red", "blue"), "red swatch song")


def test_invalid_metadata_repairs_once_then_fails_without_upload(finished):
    flow, fake, creative, _, _ = finished
    fake.responses["EpisodePublicationMetadata"] = ["invalid", "invalid"]
    for _ in range(2):
        with pytest.raises(StructuredOutputError):
            MetadataWriter(flow).generate(creative["episode_key"])
    assert fake.calls.count("EpisodePublicationMetadata") == 2
    with flow.database.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM artifact_versions WHERE kind='publication_metadata'"
            ).fetchone()[0]
            == 0
        )


def test_metadata_rejects_mismatching_final_manifest_before_provider(finished):
    flow, fake, creative, _, _ = finished
    ingest(flow, creative["episode_id"], "final_render", b"\0\0\0\x18ftypisomunlinked")
    with pytest.raises(ValueError, match="facts differ"):
        MetadataWriter(flow).generate(creative["episode_key"])
    assert "EpisodePublicationMetadata" not in fake.calls


def test_metadata_command_never_invokes_renderer_or_music(finished, monkeypatch):
    flow, _, creative, _, _ = finished
    for target in (
        "tovitunes.render.production.ProductionRenderer.render",
        "tovitunes.music.vertex_lyria.VertexLyriaProvider.generate",
        "tovitunes.music.benchmark.MusicBenchmark.run",
    ):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail("out-of-scope generation"))
    MetadataWriter(flow).generate(creative["episode_key"])
