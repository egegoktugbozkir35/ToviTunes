"""Pinned donor behaviors adapted to ToviTunes' real durable ledger, entirely offline."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from threading import Barrier

import httpx
import pytest
from pydantic import ValidationError

from tovitunes.cli import main
from tovitunes.config import (
    CreativeLLMConfig,
    CreativeTopicsConfig,
    RuntimeConfig,
    TopicEmbeddingConfig,
)
from tovitunes.creative.director import validate_pins
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.learning import LEARNING_POLICY, TopicCandidate, TopicPool
from tovitunes.creative.nvidia import NvidiaNIMClient
from tovitunes.creative.prompts import topic_messages
from tovitunes.creative.provider import DurableStructuredGenerator, canonical
from tovitunes.creative.resilience import ResilientStructuredGenerator
from tovitunes.creative.service import CreativeService
from tovitunes.creative.similarity import normalize_topic
from tovitunes.creative.topic_memory import TopicMemory, duplicate_reason, validate_candidate
from tovitunes.creative.topics import TopicPlanner
from tovitunes.creative.validation import validate_episode_spec, validate_lyrics, validate_music
from tovitunes.domain.episode import Episode
from tovitunes.persistence.db import Database
from tovitunes.pipeline.creative import FakeDraftGenerator, GeneratedDraft
from tovitunes.pipeline.targets import ProductionTarget


def idea(kind="size", **updates):
    values = {
        "size": (
            "Big and small",
            "opposites",
            "Compare big and small balloons.",
            ("big", "small"),
            "Tovi compares two balloons beside a basket.",
            "Which balloon is big?",
            ("balloon",),
        ),
        "leaf": (
            "Name a leaf",
            "nature",
            "Identify a leaf on a tree.",
            ("leaf",),
            "Tovi points at a leaf underneath a tree.",
            "A leaf floats down!",
            ("leaf", "tree"),
        ),
        "texture": (
            "Smooth and rough",
            "textures",
            "Compare smooth and rough stones.",
            ("smooth", "rough"),
            "Tovi shows two stones on a tray.",
            "See the bumpy surface!",
            ("stone",),
        ),
        "red": (
            "Learn the color red",
            "colors",
            "Identify red objects.",
            ("red",),
            "Tovi finds a red apple inside a basket.",
            "Can you see red?",
            ("apple",),
        ),
    }[kind]
    subject, domain, objective, words, premise, hook, objects = values
    return TopicCandidate(
        **{
            "subject": subject,
            "domain": domain,
            "objective": objective,
            "target_vocabulary": words,
            "premise": premise,
            "hook": hook,
            "setting": "a simple garden",
            "example_objects": objects,
            "song_angle": "Repeat " + " and ".join(words) + " in a gentle musical phrase.",
            "working_title": "Tovi sings " + " and ".join(words),
            "score": 8,
            "reason": "A visible example and one clear objective fit a short musical lesson.",
            **updates,
        }
    )


@pytest.fixture
def editorial(tmp_path, catalog, brand_root, monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live provider call"))
    config = RuntimeConfig(
        database_path=tmp_path / "state.db",
        data_root=tmp_path / "data",
        brand_root=brand_root,
        creative_topics=CreativeTopicsConfig(candidate_batch_size=2),
    )
    database = Database(config.database_path)
    database.migrate()
    fake = FakeNIMTransport()
    workflow = CreativeService(
        config,
        DurableStructuredGenerator(database, fake),
        catalog=catalog,
        assert_owner=lambda: None,
    )
    return workflow, fake


def plan(flow, fake, pools, count=1, *, embedding=None):
    fake.responses["TopicPool"] = [TopicPool(candidates=tuple(p)).model_dump_json() for p in pools]
    row = flow._new_run()
    planner = TopicPlanner(
        flow.provider,
        TopicMemory(flow.database, flow.catalog),
        flow.config.creative_topics,
        embedding=embedding,
    )
    return planner.select(row["run_id"], json.loads(row["input_json"]), count)


def test_score_ranking_is_stable_and_skips_unsafe_high_scores(editorial):
    flow, fake = editorial
    selected = plan(flow, fake, [[idea("leaf", score=3), idea(score=9)]])
    assert selected[0].subject == "Big and small"
    flow2, fake2 = editorial
    selected = plan(
        flow2, fake2, [[idea("leaf", score=3), idea(subject="Play with fire", score=10)]]
    )
    assert selected[0].subject == "Name a leaf"


@pytest.mark.parametrize("subject", ["BIG—and SMALL!!!", "Ｂｉｇ and small", "  big\tAND small  "])
def test_exact_unicode_normalized_duplicates_are_rejected(editorial, subject):
    flow, fake = editorial
    plan(flow, fake, [[idea(), idea("leaf")]])
    selected = plan(flow, fake, [[idea(subject=subject, score=10), idea("leaf")]])
    assert selected[0].subject == "Name a leaf"


@pytest.mark.parametrize(
    "candidate",
    [
        idea("red", subject="Can Tovi find red things?"),
        idea(
            "red",
            subject="A bright apple lesson",
            objective="Name red in a ball and a crayon.",
            premise="Tovi paints a picture",
            hook="A crayon draws a line!",
            example_objects=("crayon",),
        ),
        idea(
            subject="Learn opposites: big and small using balls",
            objective="Identify big and small balls.",
            example_objects=("ball",),
        ),
    ],
)
def test_subject_and_objective_vocabulary_paraphrases_are_duplicate(candidate):
    prior = idea("red" if candidate.target_vocabulary == ("red",) else "size").model_dump()
    assert duplicate_reason(candidate, [prior], CreativeTopicsConfig()) is not None


def test_generic_shared_words_do_not_exclude_distinct_ideas():
    assert duplicate_reason(idea("leaf"), [idea().model_dump()], CreativeTopicsConfig()) is None


def test_creative_treatment_has_its_own_configured_threshold():
    prior = idea().model_dump()
    candidate = idea(
        "leaf",
        premise=prior["premise"],
        hook=prior["hook"],
        example_objects=prior["example_objects"],
    )
    assert (
        duplicate_reason(candidate, [prior], CreativeTopicsConfig()) == "lexical creative treatment"
    )


def test_same_pool_duplicate_and_second_round_exclusion(editorial):
    flow, fake = editorial
    selected = plan(
        flow,
        fake,
        [
            [idea(score=10), idea(subject="Big vs small with balloons")],
            [idea(score=10), idea("leaf")],
        ],
        count=2,
    )
    assert [b.subject for b in selected] == ["Big and small", "Name a leaf"]
    facts = json.loads(fake.messages[1][-1]["content"].split("Authoritative pinned facts:\n")[1])
    assert facts["same_run_exclusions"] == ["Big and small"]
    assert facts["history"][0]["subject"] == "Big and small"
    assert "embedding" not in facts["history"][0] and "brief_id" not in facts["history"][0]


def test_max_rounds_is_bounded_and_restart_does_not_reset_budget(editorial):
    flow, fake = editorial
    row = flow._new_run()
    fake.responses["TopicPool"] = [
        TopicPool(candidates=(idea(subject="Play with fire"),) * 2).model_dump_json()
    ] * 3
    for _ in range(2):
        planner = TopicPlanner(
            flow.provider, TopicMemory(flow.database, flow.catalog), flow.config.creative_topics
        )
        with pytest.raises(ValueError, match="after 3 bounded"):
            planner.select(row["run_id"], json.loads(row["input_json"]))
    assert fake.calls == ["TopicPool"] * 3


def test_restart_after_persisted_brief_before_episode_reservation_reuses_selection(
    editorial, monkeypatch
):
    flow, fake = editorial
    monkeypatch.setattr(
        flow, "_reserve_brief", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        flow.prepare()
    memory = TopicMemory(Database(flow.config.database_path), flow.catalog)
    assert memory.history()[0]["subject"] == "Big and small"
    assert fake.calls == ["TopicPool"]
    restarted = CreativeService(
        flow.config, flow.provider, catalog=flow.catalog, assert_owner=lambda: None
    )
    result = restarted.prepare()
    assert result["episode_key"].startswith("big-and-small-")
    assert fake.calls == ["TopicPool", "EpisodeSpec", "LyricsSpec", "MusicSpec"]


def test_memory_precedes_creative_artifacts_and_expensive_media(editorial, monkeypatch):
    flow, fake = editorial
    original = flow.provider.generate

    def generate(model, messages, **kwargs):
        if model.__name__ != "TopicPool":
            assert TopicMemory(flow.database, flow.catalog).history()[0]["episode_id"]
        return original(model, messages, **kwargs)

    monkeypatch.setattr(flow.provider, "generate", generate)
    flow.prepare()
    assert fake.calls == ["TopicPool", "EpisodeSpec", "LyricsSpec", "MusicSpec"]


def test_novel_domain_outside_policy_examples_is_allowed(editorial):
    flow, fake = editorial
    fake.responses["TopicPool"] = [
        TopicPool(candidates=(idea("texture", score=10), idea())).model_dump_json()
    ]
    result = flow.prepare()
    ep = flow.database.get_episode(result["episode_id"])
    assert ep.subject == "Smooth and rough" and "textures" not in LEARNING_POLICY.example_domains
    assert ep.curriculum_revision_id is None
    assert ep.concept_id not in {c.concept_id for c in flow.catalog.curriculum.concepts}


def test_database_concurrent_selection_and_independent_unique_constraint(editorial):
    flow, _ = editorial
    run_ids = [flow._new_run()["run_id"] for _ in range(2)]
    barrier = Barrier(2)

    def reserve(run_id):
        barrier.wait()
        memory = TopicMemory(Database(flow.database.path), flow.catalog)
        draft = GeneratedDraft(idea(), "fake", "model", None, "test", datetime.now(UTC))
        try:
            memory.reserve(idea(), run_id, 1, draft, flow.config.creative_topics)
            return True
        except (ValueError, sqlite3.IntegrityError):
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, run_ids)) == [False, True]
    with closing(flow.database.connect()) as db:
        row = list(db.execute("SELECT * FROM learning_briefs").fetchone())
        row[0], row[3], row[6] = "other", "different-fingerprint", run_ids[1]
        row[7] = 2
        facts = json.loads(row[4])
        facts.update(brief_id=row[0], idea_fingerprint=row[3])
        row[4] = json.dumps(facts)
        with pytest.raises(sqlite3.IntegrityError, match="normalized_subject"):
            db.execute(
                "INSERT INTO learning_briefs VALUES (" + ",".join("?" for _ in row) + ")", row
            )


class Embeddings:
    identity = "offline:embedding-v1"

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("Authorization: Bearer never-persist-this-secret")
        return [[1.0, 0.0] if "leaf" not in text.lower() else [0.0, 1.0] for text in texts]


@pytest.mark.parametrize("failure", [False, True])
def test_optional_embedding_backfill_and_failure_degrades_to_lexical(editorial, failure):
    flow, fake = editorial
    plan(flow, fake, [[idea(), idea("leaf")]])
    embedding = Embeddings(fail=failure)
    flow.config = flow.config.model_copy(
        update={
            "creative_topics": flow.config.creative_topics.model_copy(
                update={
                    "embedding": TopicEmbeddingConfig(enabled=True, model="explicit-local-model")
                }
            )
        }
    )
    selected = plan(flow, fake, [[idea("texture", score=10), idea("leaf")]], embedding=embedding)
    assert selected[0].subject == ("Smooth and rough" if failure else "Name a leaf")
    history = TopicMemory(flow.database, flow.catalog).history(
        embedding_identity=embedding.identity
    )
    if not failure:
        assert next(i for i in history if i["subject"] == "Big and small")["embedding"] == [
            1.0,
            0.0,
        ]
    else:
        assert all(i["embedding"] is None for i in history)
    with closing(flow.database.connect()) as db:
        assert "never-persist-this-secret" not in str(
            db.execute("SELECT * FROM topic_rounds").fetchall()
        )


def test_disabled_embeddings_never_call_interface(editorial):
    flow, fake = editorial
    embedding = Embeddings()
    plan(flow, fake, [[idea(), idea("leaf")]], embedding=embedding)
    assert embedding.calls == []


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
        sqlite3.OperationalError("database unavailable"),
        ValueError("local persisted identity corrupt"),
    ],
)
def test_local_reservation_errors_do_not_authorize_another_pool(editorial, monkeypatch, error):
    flow, fake = editorial

    def reserve(*args, **kwargs):
        raise error

    monkeypatch.setattr(TopicMemory, "reserve", reserve)
    with pytest.raises(type(error), match=str(error)):
        plan(flow, fake, [[idea(), idea("leaf")]])
    assert fake.calls == ["TopicPool"]


def test_equal_scores_preserve_pool_order(editorial):
    flow, fake = editorial
    selected = plan(flow, fake, [[idea("leaf", score=8), idea(score=8)]])
    assert selected[0].subject == "Name a leaf"


def test_prompt_window_does_not_truncate_deterministic_duplicate_memory(editorial):
    flow, fake = editorial
    plan(flow, fake, [[idea(score=10), idea("leaf")]])
    plan(flow, fake, [[idea("leaf", score=10), idea("texture")]])
    flow.config = flow.config.model_copy(
        update={
            "creative_topics": flow.config.creative_topics.model_copy(
                update={"recent_history_count": 1}
            )
        }
    )
    selected = plan(flow, fake, [[idea(score=10), idea("texture")]])
    assert selected[0].subject == "Smooth and rough"


def test_crash_in_multi_selection_reuses_pool_and_same_run_memory(editorial, monkeypatch):
    flow, fake = editorial
    row = flow._new_run()
    fake.responses["TopicPool"] = [
        TopicPool(candidates=(idea(score=10), idea("leaf"))).model_dump_json()
    ]
    original = TopicMemory.reserve

    def reserve(memory, *args, **kwargs):
        if memory.selected(row["run_id"]):
            raise KeyboardInterrupt("crash before second reservation")
        return original(memory, *args, **kwargs)

    monkeypatch.setattr(TopicMemory, "reserve", reserve)
    planner = TopicPlanner(
        flow.provider, TopicMemory(flow.database, flow.catalog), flow.config.creative_topics
    )
    with pytest.raises(KeyboardInterrupt):
        planner.select(row["run_id"], json.loads(row["input_json"]), 2)
    monkeypatch.setattr(TopicMemory, "reserve", original)
    selected = planner.select(row["run_id"], json.loads(row["input_json"]), 2)
    assert [b.subject for b in selected] == ["Big and small", "Name a leaf"]
    assert fake.calls == ["TopicPool"]


@pytest.mark.parametrize(
    "updates",
    [
        {"subject": "Play with fire"},
        {"objective": "Understand existential philosophy"},
        {"example_objects": ("quantum field",)},
        {"song_angle": "Sing in the style of Mozart"},
        {"hook": "Ask your parents to buy a toy"},
        {"objective": "Compare big and small; also count three"},
    ],
)
def test_preschool_policy_rejects_unsafe_abstract_or_complex_ideas(updates):
    with pytest.raises(ValueError):
        validate_candidate(idea(**updates), CreativeTopicsConfig())


@pytest.mark.parametrize(
    "words",
    [
        (),
        ("one", "two", "three", "four", "five"),
        ("big", "big"),
        ("existential transcendental philosophy idea",),
    ],
)
def test_vocabulary_bounds_are_strict(words):
    with pytest.raises(ValidationError):
        idea(target_vocabulary=words)


def test_learning_facts_remain_exact_across_episode_and_downstream_specs(editorial):
    flow, _ = editorial
    result = flow.prepare()
    episode = flow.database.get_episode(result["episode_id"])
    brief = TopicMemory(flow.database, flow.catalog).get(episode.learning_brief_id)
    assert (
        episode.objective == brief.objective
        and episode.target_vocabulary == brief.target_vocabulary
    )
    assert brief.working_title == "Tovi sings big and small"
    validate_pins(episode, flow.catalog, flow.database)
    with pytest.raises(ValueError):
        validate_pins(
            episode.model_copy(update={"objective": "Changed"}), flow.catalog, flow.database
        )
    generator = FakeDraftGenerator()
    spec = generator.episode_spec(episode, variant=1).output
    lyrics = generator.lyrics(episode, spec, "spec", variant=1).output
    music = generator.music_spec(episode, lyrics, "lyrics", variant=1).output
    for validate, changed, args in (
        (validate_episode_spec, spec.model_copy(update={"objective_id": "other"}), ()),
        (validate_episode_spec, spec.model_copy(update={"teaching_vocabulary": ("leaf",)}), ()),
        (validate_lyrics, lyrics.model_copy(update={"target_vocabulary": ("leaf",)}), ("spec",)),
        (validate_music, music.model_copy(update={"objective_id": "other"}), ("lyrics",)),
    ):
        with pytest.raises(ValueError):
            validate(episode, changed, *args)
    with closing(flow.database.connect()) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE learning_briefs SET brief_json='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "UPDATE episodes SET objective='changed' WHERE episode_id=?", (episode.episode_id,)
            )
        assert not db.execute(
            "SELECT 1 FROM artifact_versions WHERE kind='publication_metadata'"
        ).fetchone()


def test_kimi_failure_glm_topic_success_persists_actual_model_provenance(editorial, monkeypatch):
    flow, fake = editorial
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    config = CreativeLLMConfig()
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == config.model:
            return httpx.Response(
                200, json={"id": "empty", "choices": [{"message": {"content": ""}}]}
            )
        content = fake.chat(body["messages"], record_identity=lambda _: None).content
        return httpx.Response(
            200, json={"id": f"glm-{len(calls)}", "choices": [{"message": {"content": content}}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    chain = [
        NvidiaNIMClient(config.model_copy(update={"model": m}), client=client)
        for m in (config.model, *config.fallback_models)
    ]
    flow.provider = ResilientStructuredGenerator(flow.database, chain)
    flow.prepare()
    with closing(flow.database.connect()) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM generation_requests ORDER BY rowid")]
        brief = dict(db.execute("SELECT * FROM learning_briefs").fetchone())
    assert rows[0]["model"] == config.model and rows[0]["status"] == "failed"
    assert rows[1]["kind"] == "subject_pool" and rows[1]["model"] == config.fallback_models[0]
    assert brief["model"] == config.fallback_models[0] and brief["provider"] == "nvidia"
    assert brief["generation_request_id"] == rows[1]["request_id"]
    assert "offline-secret" not in canonical(rows)


def test_doctor_history_and_config_make_no_provider_calls(editorial, tmp_path, capsys):
    flow, _ = editorial
    flow.prepare()
    path = tmp_path / "config.yaml"
    path.write_text(
        f"database_path: {flow.database.path.as_posix()}\n"
        f"brand_root: {flow.config.brand_root.as_posix()}\n"
        f"data_root: {flow.config.data_root.as_posix()}\n",
        encoding="utf-8",
    )
    for command in ("doctor", "history"):
        assert main(["--config", str(path), "creative", command]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["topic_mode"] == "open"
        if command == "doctor":
            assert data["remembered_selected_ideas"] == 1
            assert data["editorial_memory"]["candidate_batch_size"] == 15
        else:
            assert data["history"][0]["subject"] == "Big and small"
    assert "Never invent a lesson" not in topic_messages({"learning_policy": {}})[0]["content"]
    assert normalize_topic("Ｒｅｄ—  Things!") == "red things"


@pytest.mark.parametrize(
    "values",
    [
        {"candidate_batch_size": 0},
        {"candidate_batch_size": 31},
        {"recent_history_count": 0},
        {"max_generation_rounds": 0},
        {"max_generation_rounds": 11},
        {"lexical_similarity_threshold": 0},
        {"treatment_similarity_threshold": 1.1},
        {"semantic_similarity_threshold": float("nan")},
        {"embedding": {"enabled": True}},
        {"embedding": {"base_url": "http://secret@localhost:11434"}},
        {"api_key": "secret"},
    ],
)
def test_editorial_configuration_is_bounded_and_secret_free(values):
    with pytest.raises(ValidationError):
        CreativeTopicsConfig.model_validate(values)


def test_legacy_blue_resume_reuses_exact_selections(editorial):
    flow, fake = editorial
    episode = Episode.create(flow.catalog, "blue", "colors-blue-001")
    flow.database.create_episode(flow.catalog, episode)
    first = flow.prepare(episode_key="colors-blue-001")
    before = list(fake.calls)
    fresh = CreativeService(
        flow.config, flow.provider, catalog=flow.catalog, assert_owner=lambda: None
    )
    second = fresh.prepare(episode_key="colors-blue-001")
    assert fake.calls == before == ["EpisodeSpec", "LyricsSpec", "MusicSpec"]
    assert first["episode_spec_artifact_id"] == second["episode_spec_artifact_id"]
    assert (
        flow.database.get_episode(episode.episode_id).model_dump_json() == episode.model_dump_json()
    )
    assert "learning_source" not in json.loads(episode.model_dump_json())


def test_real_pre_editorial_migration_preserves_red_publication_and_blue_creative_bytes(
    tmp_path,
    monkeypatch,
):
    from importlib import resources

    from test_public_release_v1 import record_upload
    from test_web_youtube_v1 import artifact, context, ready

    from tovitunes.catalog import load_brand
    from tovitunes.orchestrator import build_orchestrator

    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live call"))
    migration_dir = tmp_path / "pre-editorial-migrations"
    migration_dir.mkdir()
    for file in resources.files("tovitunes.persistence.migrations").iterdir():
        if file.name.endswith(".sql") and file.name < "0020":
            (migration_dir / file.name).write_text(
                file.read_text(encoding="utf-8"), encoding="utf-8"
            )
    with monkeypatch.context() as scoped:
        scoped.setattr("tovitunes.persistence.db.resources.files", lambda _: migration_dir)
        historical = ready.__wrapped__(context.__wrapped__(tmp_path))
        config, database, red, store = historical[0]
        record_upload(historical)
        catalog = flow_catalog = load_brand(config.brand_root)
        blue = Episode.create(catalog, "blue", "colors-blue-001")
        database.create_episode(catalog, blue)
        blue_context = (config, database, blue, store)
        generator = FakeDraftGenerator()
        spec = generator.episode_spec(blue, variant=1).output
        spec_record = artifact(blue_context, "episode_spec", spec.model_dump(mode="json"))
        lyrics = generator.lyrics(blue, spec, spec_record.identity.artifact_id, variant=1).output
        lyric_record = artifact(
            blue_context, "lyrics", lyrics.model_dump(mode="json"), (spec_record,)
        )
        music = generator.music_spec(
            blue, lyrics, lyric_record.identity.artifact_id, variant=1
        ).output
        artifact(blue_context, "music_spec", music.model_dump(mode="json"), (lyric_record,))
        # Capture the exact columns/rows present at PR #38, including requests and publication.
        with closing(database.connect()) as db:
            tables = [
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if r[0] != "schema_migrations"
            ]
            columns = {
                name: [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')] for name in tables
            }
            before = {
                name: [tuple(r) for r in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                for name in tables
            }
        bytes_before = {p: p.read_bytes() for p in config.data_root.rglob("*") if p.is_file()}
    database.migrate()
    database.migrate()
    with closing(database.connect()) as db:
        for name in tables:
            projection = ",".join('"' + c + '"' for c in columns[name])
            assert [
                tuple(r) for r in db.execute(f'SELECT {projection} FROM "{name}" ORDER BY rowid')
            ] == before[name]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert all(p.read_bytes() == value for p, value in bytes_before.items())
    assert database.get_episode(blue.episode_id).model_dump_json() == blue.model_dump_json()
    assert database.get_episode(red.episode_id).model_dump_json() == red.model_dump_json()
    fake = FakeNIMTransport()
    flow = CreativeService(
        config,
        DurableStructuredGenerator(database, fake),
        catalog=flow_catalog,
        assert_owner=lambda: None,
    )
    result = flow.prepare(episode_key="colors-blue-001")
    assert result["episode_spec_artifact_id"] == spec_record.identity.artifact_id
    assert fake.calls == []
    immutable = {p: p.read_bytes() for p in bytes_before}
    production = build_orchestrator(config).resume(
        red.external_key, target=ProductionTarget.PUBLISH
    )
    assert production["historical"] and production["status"] == "COMPLETE"
    assert all(p.read_bytes() == value for p, value in immutable.items())
    protected = build_orchestrator(config).resume(blue.external_key, target=ProductionTarget.RENDER)
    assert protected["historical"] and protected["status"] == "BLOCKED"
    assert all(p.read_bytes() == value for p, value in immutable.items())
