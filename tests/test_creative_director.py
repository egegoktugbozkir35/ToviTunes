"""Offline curriculum planning, structural policy, resume and provenance checks."""

import json
from contextlib import closing
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from tovitunes.cli import main
from tovitunes.config import CreativeLLMConfig, CreativeTopicsConfig, RuntimeConfig
from tovitunes.creative import prompts
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.learning import TopicCandidate, TopicPool
from tovitunes.creative.models import CreativeSubjectCandidate
from tovitunes.creative.nvidia import NvidiaNIMClient
from tovitunes.creative.provider import (
    DurableStructuredGenerator,
    ProviderError,
    StructuredOutputError,
)
from tovitunes.creative.service import CreativeService, eligibility
from tovitunes.creative.validation import (
    treatment,
    validate_episode_spec,
    validate_lyrics,
    validate_music,
    validate_subject,
)
from tovitunes.domain.episode import Episode
from tovitunes.errors import StateError
from tovitunes.persistence.db import Database
from tovitunes.pipeline.creative import CreativeDraftService, FakeDraftGenerator


@pytest.fixture
def workflow(tmp_path, catalog, brand_root, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("network call"))
    config = RuntimeConfig(
        database_path=tmp_path / "state.db",
        data_root=tmp_path / "data",
        brand_root=brand_root,
        creative_topics=CreativeTopicsConfig(candidate_batch_size=3),
    )
    database = Database(config.database_path)
    database.migrate()
    fake = FakeNIMTransport()
    return CreativeService(
        config,
        DurableStructuredGenerator(database, fake),
        catalog=catalog,
        assert_owner=lambda: None,
    ), fake


def candidate(**updates):
    return CreativeSubjectCandidate(
        **{
            "concept_id": "blue",
            "premise": "Tovi finds a blue ball in a basket.",
            "hook": "A ball gently rolls into view.",
            "setting": "a simple playroom",
            "example_objects": ("blue ball",),
            "song_angle": "Name blue with a clear song.",
            "reason": "A familiar concrete example supports the objective.",
            **updates,
        }
    )


def test_end_to_end_generates_pinned_selected_creative_only(workflow):
    flow, fake = workflow
    result = flow.prepare()
    assert result["episode_key"].startswith("big-and-small-")
    assert fake.calls == ["TopicPool", "EpisodeSpec", "LyricsSpec", "MusicSpec"]
    episode = flow.database.get_episode(result["episode_id"])
    assert episode.objective == "Compare a big balloon with a small balloon."
    assert episode.target_vocabulary == ("big", "small") and episode.language == "en"
    assert episode.learning_source == "generated_learning_brief"
    assert episode.target_duration_seconds == 37
    assert episode.character_packs[0].revision_id == flow.catalog.pack_revisions[0].revision_id
    with closing(flow.database.connect()) as db:
        approvals = [dict(r) for r in db.execute("SELECT * FROM approval_decisions")]
        requests = [dict(r) for r in db.execute("SELECT * FROM generation_requests ORDER BY rowid")]
        kinds = {r[0] for r in db.execute("SELECT kind FROM artifact_versions")}
    assert any(
        a["actor"] == "machine:learning_policy"
        and a["policy_version"] == episode.learning_policy_revision_id
        for a in approvals
    )
    assert sum(a["policy_version"] == "creative_structural_v1" for a in approvals) == 3
    assert kinds == {"episode_spec", "lyrics", "music_spec"}
    assert requests[0]["episode_id"] is None and requests[0]["run_id"] == result["run_id"]
    for kind in kinds:
        record = flow.store.selected("episode", episode.episode_id, kind, "main")
        assert record.identity.artifact_id == result[kind + "_artifact_id"]
        assert record.provenance.local_request_id and record.provenance.request_id is None
        assert record.provenance.source_kind == "provider"
    assert result["provider_calls"] == {
        "subject": 1,
        "episode_spec": 1,
        "lyrics": 1,
        "music_spec": 1,
        "metadata": 0,
        "repair": 0,
    }


def test_live_cli_contract_through_mock_nim_creates_nvidia_provenance(
    workflow, monkeypatch, capsys
):
    flow, fake = workflow
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-contract-key")

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "moonshotai/kimi-k3"
        reply = fake.chat(payload["messages"], record_identity=lambda value: None)
        return httpx.Response(
            200,
            json={
                "id": f"nim-{len(fake.calls)}",
                "choices": [
                    {"message": {"content": reply.content}},
                ],
            },
        )

    transport = NvidiaNIMClient(
        flow.config.creative_llm, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr("tovitunes.creative.factory.NvidiaNIMClient", lambda config: transport)
    config_file = flow.config.database_path.parent / "cli-config.yaml"
    config_file.write_text(
        f"database_path: {flow.config.database_path.as_posix()}\n"
        f"data_root: {flow.config.data_root.as_posix()}\n"
        f"brand_root: {flow.config.brand_root.as_posix()}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(config_file), "creative", "generate-next", "--live"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["provider_calls"] == dict(
        subject=1, episode_spec=1, lyrics=1, music_spec=1, metadata=0, repair=0
    )
    record = flow.store.get(result["music_spec_artifact_id"])
    assert record.provenance.provider == "nvidia"
    assert record.provenance.model == "moonshotai/kimi-k3"
    assert record.provenance.request_id == "nim-4" and record.provenance.local_request_id


def test_same_run_and_episode_resume_make_no_more_calls(workflow):
    flow, fake = workflow
    first = flow.prepare()
    second = flow.prepare(run_id=first["run_id"])
    third = flow.prepare(episode_key=first["episode_key"])
    assert len(fake.calls) == 4
    assert second["episode_spec_artifact_id"] == third["episode_spec_artifact_id"]
    assert all(v == 0 for v in second["provider_calls"].values())
    assert all(v == 0 for v in third["provider_calls"].values())


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("LyricsSpec", ["LyricsSpec", "MusicSpec"]),
        ("MusicSpec", ["MusicSpec"]),
    ],
)
def test_interrupted_after_selected_stage_resumes_only_missing_work(
    workflow, monkeypatch, stage, expected
):
    flow, fake = workflow
    # Stop before prepare/start of the missing stage, as a process exit between stages.
    original_generate = flow.provider.generate

    def interrupted(model_type, messages, **kwargs):
        if model_type.__name__ == stage:
            raise KeyboardInterrupt("process killed between stages")
        return original_generate(model_type, messages, **kwargs)

    monkeypatch.setattr(flow.provider, "generate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        flow.prepare()
    before = len(fake.calls)
    monkeypatch.setattr(flow.provider, "generate", original_generate)
    flow.prepare()
    assert fake.calls[before:] == expected


def test_crash_after_remote_receipt_before_artifact_ingest_reuses_it(workflow, monkeypatch):
    flow, fake = workflow
    original = flow.store.ingest

    def interrupt(*args, **kwargs):
        if kwargs["kind"] == "lyrics":
            raise KeyboardInterrupt("crash before ingest")
        return original(*args, **kwargs)

    monkeypatch.setattr(flow.store, "ingest", interrupt)
    with pytest.raises(KeyboardInterrupt):
        flow.prepare()
    assert fake.calls == ["TopicPool", "EpisodeSpec", "LyricsSpec"]
    monkeypatch.setattr(flow.store, "ingest", original)
    flow.prepare()
    assert fake.calls == ["TopicPool", "EpisodeSpec", "LyricsSpec", "MusicSpec"]


def test_crash_after_ingest_before_selection_reuses_same_artifact(workflow, monkeypatch):
    flow, fake = workflow
    original = CreativeDraftService.select_structural
    observed = []

    def interrupt(service, artifact_id):
        observed.append(artifact_id)
        if service.store.get(artifact_id).identity.kind == "lyrics":
            raise KeyboardInterrupt("crash before select")
        original(service, artifact_id)

    monkeypatch.setattr(CreativeDraftService, "select_structural", interrupt)
    with pytest.raises(KeyboardInterrupt):
        flow.prepare()
    lyric_id = observed[-1]
    monkeypatch.setattr(CreativeDraftService, "select_structural", original)
    result = flow.prepare()
    assert result["lyrics_artifact_id"] == lyric_id and len(fake.calls) == 4


def test_crash_after_subject_reservation_recovers_same_episode_without_subject_post(
    workflow, monkeypatch
):
    flow, fake = workflow
    original = flow.database.create_episode
    monkeypatch.setattr(
        flow.database, "create_episode", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        flow.prepare()
    with flow.database.connect() as db:
        reserved = json.loads(
            db.execute("SELECT reserved_episode_json FROM creative_runs").fetchone()[0]
        )
    monkeypatch.setattr(flow.database, "create_episode", original)
    result = flow.prepare()
    assert result["episode_id"] == reserved["episode_id"] and fake.calls.count("TopicPool") == 1


def test_ambiguous_subject_is_never_restarted(workflow):
    flow, fake = workflow
    fake.responses["TopicPool"] = [ProviderError("timeout", ambiguous=True)]
    for _ in range(2):
        with pytest.raises(ProviderError):
            flow.prepare()
    assert fake.calls == ["TopicPool"]
    with flow.database.connect() as db:
        assert db.execute("SELECT count(*) FROM episodes").fetchone()[0] == 0


def test_used_concepts_excluded_across_revisions_and_archival(workflow):
    flow, _ = workflow
    ep = Episode.create(flow.catalog, "red", "existing-red")
    flow.database.create_episode(flow.catalog, ep.model_copy(update={"lifecycle": "archived"}))
    report = eligibility(flow.database, flow.catalog)
    assert report["used_concepts"] == ["red"]
    assert "red" not in {c["concept_id"] for c in report["eligible_concepts"]}


def test_curriculum_exhaustion_does_not_limit_new_editorial_subjects(workflow):
    flow, fake = workflow
    for c in flow.catalog.curriculum.concepts:
        flow.database.create_episode(
            flow.catalog, Episode.create(flow.catalog, c.concept_id, c.concept_id)
        )
    result = flow.prepare()
    assert flow.database.get_episode(result["episode_id"]).subject == "Big and small"
    assert fake.calls[0] == "TopicPool"


@pytest.mark.parametrize(
    "updates",
    [
        {"concept_id": "unknown"},
        {"example_objects": ("quantum field",)},
        {"premise": "Tovi imitates Peppa in the playroom."},
        {"hook": "Tovi's friend becomes a new character."},
        {"premise": "Tovi and a monster play with fire."},
        {"hook": "Ask your parents to buy a toy."},
        {"premise": "Tovi plays with weapons."},
        {"premise": "Tovi sings about drugs."},
    ],
)
def test_candidate_rejects_ineligible_unsafe_abstract_or_extra_character(updates):
    with pytest.raises(ValueError):
        validate_subject(candidate(**updates), {"blue"}, [])


def test_duplicate_premise_and_object_treatment_is_rejected():
    item = candidate()
    with pytest.raises(ValueError, match="duplicate"):
        validate_subject(item, {"blue"}, [treatment(item)])


def test_lexical_paraphrase_does_not_evade_treatment_check():
    item = candidate(
        concept_id="red",
        premise="Tovi discovers an apple that is red.",
        hook="A red apple appears.",
        example_objects=("apple",),
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_subject(item, {"red"}, ["Tovi finds a red apple. A red apple appears. apple"])


def test_next_fresh_run_excludes_previous_selected_concept(workflow):
    flow, fake = workflow
    first = flow.prepare()
    second = flow.prepare()
    assert first["episode_id"] != second["episode_id"]
    assert flow.database.get_episode(second["episode_id"]).subject == "Name a leaf"
    assert fake.calls.count("TopicPool") == 2


def test_real_episode_spec_domain_error_is_repaired_and_recorded(workflow, monkeypatch):
    flow, fake = workflow
    original = fake.chat
    injected = False

    def chat(messages, *, record_identity):
        nonlocal injected
        result = original(messages, record_identity=record_identity)
        if fake.calls[-1] == "EpisodeSpec" and not injected:
            injected = True
            data = json.loads(result.content)
            data["objective_id"] = "wrong-objective"
            return replace(result, content=json.dumps(data))
        return result

    monkeypatch.setattr(fake, "chat", chat)
    result = flow.prepare()
    assert fake.calls.count("EpisodeSpec") == 2 and result["provider_calls"]["repair"] == 1
    with flow.database.connect() as db:
        saved = db.execute(
            "SELECT status,kind FROM generation_requests "
            "WHERE kind LIKE 'episode_spec%' ORDER BY rowid"
        ).fetchall()
    assert [tuple(r) for r in saved] == [
        ("succeeded_response_invalid", "episode_spec"),
        ("succeeded", "episode_spec_repair"),
    ]


def test_invalid_episode_spec_repair_remains_failed_closed(workflow):
    flow, fake = workflow
    fake.responses["EpisodeSpec"] = ["{}", "{}"]
    for _ in range(2):
        with pytest.raises(StructuredOutputError):
            flow.prepare()
    assert fake.calls == ["TopicPool", "EpisodeSpec", "EpisodeSpec"]


def test_concept_revisions_do_not_reset_history_eligibility(workflow):
    flow, _ = workflow
    alternate = replace(
        flow.catalog,
        version=flow.catalog.version.model_copy(
            update={
                "revision_id": "alternate-brand",
                "creative_sha256": "f" * 64,
            }
        ),
    )
    flow.database.create_episode(alternate, Episode.create(alternate, "red", "older-red"))
    assert "red" not in {
        c["concept_id"] for c in eligibility(flow.database, flow.catalog)["eligible_concepts"]
    }


def test_all_bad_candidates_get_bounded_durable_subject_rounds(workflow):
    flow, fake = workflow
    bad = TopicCandidate(
        subject="Quantum theory",
        domain="abstract",
        objective="Understand quantum theory",
        target_vocabulary=("quantum",),
        premise="Tovi thinks about quantum theory",
        hook="What is quantum?",
        setting="a playroom",
        example_objects=("quantum field",),
        song_angle="Sing about quantum",
        working_title="Quantum with Tovi",
        score=10,
        reason="An abstract idea",
    )
    pool = TopicPool(candidates=(bad,) * 3)
    fake.responses["TopicPool"] = [pool.model_dump_json()] * 3
    for _ in range(2):
        with pytest.raises(ValueError, match="TOPIC_POOLS_EXHAUSTED"):
            flow.prepare()
    assert fake.calls == ["TopicPool"] * 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("episode_id", "other"),
        ("concept_id", "blue"),
        ("objective_id", "changed"),
        ("teaching_vocabulary", ("blue",)),
    ],
)
def test_episode_spec_identity_and_vocabulary_immutable(workflow, field, value):
    flow, _ = workflow
    episode = Episode.create(flow.catalog, "red", "spec-test")
    spec = (
        FakeDraftGenerator()
        .episode_spec(episode, variant=1)
        .output.model_copy(update={field: value})
    )
    with pytest.raises(ValueError):
        validate_episode_spec(episode, spec)


def test_episode_spec_cast_and_actual_teaching_beats_restricted(workflow):
    flow, _ = workflow
    episode = Episode.create(flow.catalog, "red", "spec-test")
    spec = FakeDraftGenerator().episode_spec(episode, variant=1).output
    with pytest.raises(ValueError, match="cast"):
        validate_episode_spec(
            episode,
            spec.model_copy(
                update={
                    "concept": spec.concept.model_copy(update={"cast": ("tovi", "bobo")}),
                }
            ),
        )
    with pytest.raises(ValueError, match="teaching beats"):
        validate_episode_spec(
            episode,
            spec.model_copy(
                update={
                    "story_beats": tuple(
                        b.model_copy(update={"teaching_vocabulary": ()}) for b in spec.story_beats
                    )
                }
            ),
        )


def test_lyrics_budget_directions_and_vocabulary_checked(workflow):
    flow, _ = workflow
    episode = Episode.create(flow.catalog, "red", "lyrics-test")
    generator = FakeDraftGenerator()
    spec = generator.episode_spec(episode, variant=1).output
    lyrics = generator.lyrics(episode, spec, "spec-id", variant=1).output
    for changed in (
        lyrics.model_copy(update={"target_vocabulary": ("blue",)}),
        lyrics.model_copy(update={"lines": lyrics.lines * 6}),
        lyrics.model_copy(
            update={"lines": (lyrics.lines[0].model_copy(update={"text": "red " * 30}),)}
        ),
        lyrics.model_copy(
            update={"lines": (lyrics.lines[0].model_copy(update={"text": "[sing red]"}),)}
        ),
    ):
        with pytest.raises(ValueError):
            validate_lyrics(episode, changed, "spec-id")


def test_music_brief_pins_duration_and_moderate_tempo(workflow):
    flow, _ = workflow
    episode = Episode.create(flow.catalog, "red", "music-test")
    generator = FakeDraftGenerator()
    lyrics = generator.lyrics(
        episode, generator.episode_spec(episode, variant=1).output, "s", variant=1
    ).output
    music = generator.music_spec(episode, lyrics, "lyrics-id", variant=1).output
    for changed in (
        music.model_copy(update={"target_duration_seconds": 90}),
        music.model_copy(update={"tempo_bpm": 200}),
    ):
        with pytest.raises(ValueError):
            validate_music(episode, changed, "lyrics-id")


def test_curriculum_machine_approval_does_not_overwrite_human_rejection(workflow):
    flow, _ = workflow
    result = flow.prepare()
    service = CreativeDraftService(flow.store, flow.generated, FakeDraftGenerator())
    service.review_objective(result["episode_id"], "rejected", actor="human", reason="hold")
    with pytest.raises(PermissionError, match="escalation"):
        flow.prepare(run_id=result["run_id"])
    service.review_objective(result["episode_id"], "approved", actor="human")
    service.review_candidate(result["lyrics_artifact_id"], "rejected", actor="human", reason="hold")
    with pytest.raises(PermissionError, match="escalation"):
        flow.prepare(run_id=result["run_id"])


def test_modified_curriculum_cannot_be_machine_approved(workflow):
    flow, fake = workflow
    legacy = Episode.create(flow.catalog, "blue", "colors-blue-001")
    flow.database.create_episode(flow.catalog, legacy)
    flow.catalog = replace(
        flow.catalog,
        curriculum_revision=flow.catalog.curriculum_revision.model_copy(
            update={"sha256": "0" * 64}
        ),
    )
    with pytest.raises(PermissionError, match="committed"):
        flow.prepare(episode_key=legacy.external_key)
    assert fake.calls == []


def test_historical_subject_planning_cannot_start_new_generation(workflow):
    flow, fake = workflow
    row = flow._new_run()
    with closing(flow.database.connect()) as db:
        db.execute(
            "UPDATE creative_runs SET prompt_version=? WHERE run_id=?",
            (prompts.SUBJECT_PROMPT, row["run_id"]),
        )
        db.commit()
        before = [tuple(r) for r in db.execute("SELECT * FROM creative_runs")]
    with pytest.raises(StateError, match="read-only"):
        flow.prepare(run_id=row["run_id"])
    assert fake.calls == []
    with closing(flow.database.connect()) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM creative_runs")] == before


def test_pure_prompts_include_version_pins_safety_and_preschool_rules():
    for builder, version in (
        (prompts.subject_messages, prompts.SUBJECT_PROMPT),
        (prompts.episode_messages, prompts.EPISODE_PROMPT),
        (prompts.lyrics_messages, prompts.LYRICS_PROMPT),
        (prompts.music_messages, prompts.MUSIC_PROMPT),
        (prompts.metadata_messages, prompts.METADATA_PROMPT),
    ):
        output = builder({"objective": "Identify blue", "target_vocabulary": ["blue"]})
        text = " ".join(m["content"] for m in output)
        assert version in text and "ages 3-6" in text and "Identify blue" in text
        assert all(term in text for term in ("sexual", "gambling", "weapons", "political"))
        assert "55-60" not in text and "stock-footage" not in text


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "ollama"},
        {"model": ""},
        {"api_key": "secret"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"base_url": "https://secret@example.test/v1"},
        {"api_key_env": ""},
    ],
)
def test_strict_nvidia_configuration_no_secret_model_or_provider_fallback(values):
    with pytest.raises(ValidationError):
        CreativeLLMConfig.model_validate(values)


def test_eligible_and_doctor_cli_are_provider_free(tmp_path, brand_root, monkeypatch, capsys):
    monkeypatch.setattr(
        "tovitunes.creative.nvidia.NvidiaNIMClient.chat", lambda *a, **k: pytest.fail("POST")
    )
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"database_path: {tmp_path.as_posix()}/state.db\ndata_root: {tmp_path.as_posix()}/data\n"
        f"brand_root: {brand_root.as_posix()}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(config_file), "creative", "eligible"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["eligible_concepts"]) == 10 and report["target_duration_seconds"] == 37
    assert main(["--config", str(config_file), "creative", "doctor"]) == 0
    assert all(v == 0 for v in json.loads(capsys.readouterr().out)["provider_calls"].values())
