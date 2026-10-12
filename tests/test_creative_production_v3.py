"""Versioned creative production, real local media and explicit retained-music recovery."""

import json
import math
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_short_production import case as case
from test_short_production import connect_offline, tone, visual_plan

from tovitunes.catalog import load_brand
from tovitunes.continuation import plan_continuation
from tovitunes.creative.metadata import MetadataWriter
from tovitunes.creative.provider import DurableStructuredGenerator, GenerationContext
from tovitunes.creative.service import CreativeService
from tovitunes.creative.visual_recovery import authorize_visual_recovery
from tovitunes.domain.creative_drafts import (
    MetadataDraft,
    parse_episode_spec,
    parse_lyrics,
    parse_music_spec,
)
from tovitunes.domain.episode import Episode
from tovitunes.domain.production_v3 import AudioClock, compile_storyboard_v3
from tovitunes.domain.storyboard import BeatAnalysis, parse_storyboard
from tovitunes.domain.visual_draft import (
    CreativeVisualDraft,
    EpisodeVisualPlanV2,
    compile_visual_draft,
    parse_visual_plan,
)
from tovitunes.domain.visual_plan import EpisodeVisualPlan, validate_visual_plan
from tovitunes.errors import ExecutionOwnershipLostError, ProviderError, StructuredOutputError
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.music.analysis import technical_measurements
from tovitunes.music.analysis_models import AlignmentEvidence, TranscriptionEvidence
from tovitunes.music.benchmark import MusicBenchmark
from tovitunes.orchestrator import ProductionReference
from tovitunes.pipeline.duration_source import admit_duration_source
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.pipeline.production import production_analysis_config
from tovitunes.pipeline.targets import ProductionTarget
from tovitunes.publication.service import PublicationService
from tovitunes.render.models import RenderManifest, parse_manifest
from tovitunes.render.production import load_inputs, validate_manifest
from tovitunes.services.music import MusicService
from tovitunes.services.visual import VisualService
from tovitunes.web import app as web_app
from tovitunes.web.jobs import Job
from tovitunes.web.studio import safe_production_result

pytest_plugins = ("test_web_youtube_v1",)


def proposal(variant=0):
    scenes = [
        {
            "description": "Tovi discovers a large yellow ball beside a tiny one.",
            "tovi_action": "wave hello",
            "objects": ["a large yellow ball"],
            "setting": "a sunny playroom",
        },
        {
            "description": "Tovi gently points at the surprising difference in sizes.",
            "tovi_action": "point",
            "setting": "a cozy garden",
            "camera": "gently move closer",
        },
        {
            "description": "Tovi dances joyfully while remembering the playful discovery.",
            "tovi_action": "dance happily",
            "setting": "an open toy stage",
            "transition": "soft dissolve",
        },
    ]
    if variant == 1:
        for scene in scenes:
            scene.pop("tovi_action")
            scene.pop("objects", None)
    elif variant == 2:
        scenes = [
            {"description": "Tovi playfully compares two familiar sizes.", "tovi_action": "twirl"}
        ]
    return CreativeVisualDraft.model_validate({"scenes": scenes})


def clock(duration, beats=False):
    import miniaudio

    metrics = technical_measurements(miniaudio.wav_read_f32(tone(max(1, math.ceil(duration)))))
    metrics = metrics.model_copy(update={"duration_seconds": duration})
    rhythm = (
        BeatAnalysis(
            audio_master_artifact_id="audio",
            audio_sha256="a" * 64,
            duration_seconds=duration,
            source_analysis_version=1,
            estimated_bpm=120,
            beat_seconds=tuple(i / 2 for i in range(math.ceil(duration * 2))),
            downbeat_seconds=tuple(i * 2 for i in range(math.ceil(duration / 2))),
            detector="offline measured fixture",
            detector_version="1",
            model_identity="fixture",
            model_revision="fixture-revision",
        )
        if beats
        else None
    )
    return AudioClock(
        audio_master_artifact_id="audio",
        audio_sha256="a" * 64,
        source_request_id="retained-request",
        source_provider_request_id="retained-task",
        source_blind_id="retained-blind",
        duration_seconds=duration,
        technical_metrics=metrics,
        measured_beats=rhythm,
    )


@pytest.mark.parametrize("variant", [0, 1, 2])
def test_model_draft_has_no_bookkeeping_lyric_or_palette_requirements(catalog, variant):
    episode = Episode.create(catalog, "yellow", "yellow-flexibility")
    draft = proposal(variant)
    plan = compile_visual_draft(draft, episode, ("spec", "lyrics", "music"), "raw", "b" * 64)
    assert plan == compile_visual_draft(
        draft, episode, ("spec", "lyrics", "music"), "raw", "b" * 64
    )
    assert len(plan.environments) == 4  # Infrastructure, supplied by Python.
    assert len(plan.scenes) == len(draft.scenes)
    assert all(a.kind != "color_swatch" for a in plan.required_assets)
    assert all("lyric" not in s.model_dump() for s in plan.scenes)
    assert parse_visual_plan(plan.model_dump(mode="json")) == plan
    old = plan.model_dump(mode="json")
    old["schema_version"] = 1
    with pytest.raises(ValueError):
        parse_visual_plan(old)
    with pytest.raises(ValueError):
        EpisodeVisualPlan.model_validate(plan.model_dump(mode="json"))
    if variant == 2:
        assert plan.scenes[0].tovi_action == "idle"
        assert any("approved idle" in d for d in plan.diagnostics)


@pytest.mark.parametrize(
    "duration,count,beats", [(0.02, 64, False), (0.7, 64, False), (10.017, 7, False), (10, 5, True)]
)
def test_frame_coverage_merging_and_optional_measured_beats(catalog, duration, count, beats):
    episode = Episode.create(catalog, "yellow", "timing")
    draft = CreativeVisualDraft.model_validate(
        {"scenes": [{"description": f"Tovi explores yellow moment {i}"} for i in range(count)]}
    )
    plan = compile_visual_draft(draft, episode, ("s", "l", "m"), "raw", "b" * 64)
    source = clock(duration, beats)
    board = compile_storyboard_v3(episode, plan, "plan", "c" * 64, source, "clock", {}, "env")
    assert parse_storyboard(board.model_dump(mode="json")) == board
    assert board.scenes[0].start_frame == 0
    assert board.scenes[-1].end_frame == math.ceil(duration * 30 - 1e-9)
    assert all(a.end_frame == b.start_frame for a, b in zip(board.scenes, board.scenes[1:]))
    assert all(s.end_frame > s.start_frame for s in board.scenes)
    assert sum(len(s.source_scene_ids) for s in board.scenes) == count
    assert board.duration_seconds - duration < 1 / 30
    assert board.scheduling_mode == ("measured_beats" if beats else "duration")
    assert source.words == ()
    assert "words" not in source.model_dump()
    assert all("lyric_text" not in s.model_dump() for s in board.scenes)
    bad = board.model_dump(mode="json")
    bad["scenes"][0]["end_frame"] = 0
    with pytest.raises(ValueError):
        parse_storyboard(bad)


@pytest.mark.parametrize(
    "content",
    [
        "Tovi plays with fire",
        "Tovi jumps off a roof",
        "Tovi imitates Mickey",
        "A new permanent character arrives",
    ],
)
def test_flexible_draft_keeps_prohibited_content_gates(content):
    with pytest.raises(ValueError):
        CreativeVisualDraft.model_validate({"scenes": [{"description": content}]})


def test_model_cannot_supply_authoritative_identity_or_character_image():
    for extra in ({"episode_id": "invented"}, {"start": 0}, {"asset_key": "invented"}):
        with pytest.raises(ValueError):
            CreativeVisualDraft.model_validate({"scenes": [{"description": "Tovi waves", **extra}]})
    with pytest.raises(ValueError, match="approved Tovi pack"):
        CreativeVisualDraft.model_validate(
            {"scenes": [{"description": "Tovi waves", "objects": ["an image of Tovi"]}]}
        )
    with pytest.raises(ValueError):
        CreativeVisualDraft.model_validate({"scenes": [{"description": "   "}]})


@pytest.fixture
def new_case(case, monkeypatch):
    config = case["config"]
    config = config.model_copy(
        update={
            "publication_enabled": True,
            "expected_youtube_channel_id": "offline-fixture-channel",
            "publication": config.publication.model_copy(
                update={"youtube": config.publication.youtube.model_copy(update={"enabled": True})}
            ),
            "automation": config.automation.model_copy(
                update={"auto_publish": False, "require_human_review": True}
            ),
        }
    )
    service = CreativeService(
        config,
        DurableStructuredGenerator(case["store"].database, case["fake"]),
        catalog=load_brand(config.brand_root),
        assert_owner=lambda: None,
    )
    result = service.prepare(run_id=service.reserve_next_run())
    episode = service.database.get_episode(result["episode_id"])
    ids = tuple(result[k + "_artifact_id"] for k in ("episode_spec", "lyrics", "music_spec"))
    new = connect_offline(
        config, episode, ids, case["fake"], monkeypatch, case["red"], case["socket_connect"]
    )
    with closing(service.database.connect()) as db, db:
        db.execute(
            "INSERT INTO production_requests VALUES (?,?,?,?)",
            ("retained-production-request", result["run_id"], "render", "2026-01-01"),
        )
        db.execute(
            "INSERT INTO studio_jobs VALUES (?,?)",
            (
                "retained-studio-job",
                Job(
                    job_id="retained-studio-job",
                    operation="studio",
                    episode_key=episode.external_key,
                    recovery_reference=ProductionReference(run_id=result["run_id"]),
                    target=ProductionTarget.RENDER,
                    status="blocked",
                    current_stage="VISUAL_PLAN",
                    submitted_at="2026-01-01",
                    recovery_action="resume",
                ).model_dump_json(),
            ),
        )
    new["fake"].responses["CreativeVisualDraft"] = [proposal().model_dump_json()]
    return new


def retain_music(new):
    with ProductionExecutionOwnership(new["store"].database, operation="offline-fixture") as owner:
        service = MusicService(new["config"], owner, music_provider=new["music"])
        inputs = service._creative_inputs(new["episode"])
        request, blind = service._music(new["episode"], inputs)
        service._analysis(new["episode"], blind)
    return request, inputs


def test_pipeline_repairs_unsupported_adapter_tempo_before_success(new_case):
    new = new_case
    new["fake"].responses["MusicDraft"] = [
        json.dumps(
            {
                "mood": "playful",
                "instrumentation": ["gentle bells"],
                "vocal_direction": "a warm clear voice",
                "tempo_bpm": bpm,
            }
        )
        for bpm in (55, 60)
    ]
    service = CreativeService(
        new["config"],
        DurableStructuredGenerator(new["store"].database, new["fake"]),
        catalog=load_brand(new["config"].brand_root),
        assert_owner=lambda: None,
    )
    result = service.prepare(run_id=service.reserve_next_run())
    episode = service.database.get_episode(result["episode_id"])
    ids = tuple(result[k + "_artifact_id"] for k in ("episode_spec", "lyrics", "music_spec"))
    canonical = creative_music_spec(
        episode,
        parse_episode_spec(service.store.read_json(ids[0])),
        parse_lyrics(service.store.read_json(ids[1])),
        parse_music_spec(service.store.read_json(ids[2])),
        ids,
    )
    assert canonical.brief.target_bpm == 60  # Valid technical range, outside old artistic 80-130.
    with closing(service.database.connect()) as db:
        assert [
            r[0]
            for r in db.execute(
                "SELECT status FROM generation_requests WHERE episode_id=? "
                "AND prompt_version='music_idea_v2' ORDER BY rowid",
                (episode.episode_id,),
            )
        ] == ["succeeded_response_invalid", "succeeded"]


def failed_legacy_contract(new):
    request, inputs = retain_music(new)
    episode = new["episode"]
    old = visual_plan(episode, new["ids"], parse_lyrics(new["store"].read_json(new["ids"][1])))
    # Reproduce a full-schema response with non-palette swatch notation; no bookkeeping omission.
    old = old.model_dump(mode="json")
    old["required_assets"][0]["target_color"] = "#FFD700"
    new["fake"].responses["EpisodeVisualPlan"] = [json.dumps(old)] * 2
    provider = DurableStructuredGenerator(new["store"].database, new["fake"])
    with pytest.raises(StructuredOutputError):
        provider.generate(
            EpisodeVisualPlan,
            [{"role": "user", "content": "offline legacy reproduction"}],
            context=GenerationContext(
                "episode_visual_plan", "episode_visual_plan_v1", episode_id=episode.episode_id
            ),
            validate=lambda plan: validate_visual_plan(
                plan, episode, inputs[0], inputs[1], inputs[3]
            ),
        )
    return request, inputs


@pytest.mark.parametrize("variant", [0, 1, 2])
def test_non_lyric_proposals_render_real_complete_mp4_and_reuse(new_case, variant):
    new = new_case
    new["fake"].responses["CreativeVisualDraft"] = [proposal(variant).model_dump_json()]
    if variant == 0:
        vocabulary = " and ".join(new["episode"].target_vocabulary)
        copy = {
            "youtube_title": f"Learn {vocabulary} with Tovi | Preschool Song",
            "youtube_description": f"Discover {vocabulary} with ToviTunes in this preschool song.",
            "tags": ["preschool learning", "kids song", "Kids Song"],
        }
        new["fake"].responses["MetadataDraft"] = [
            json.dumps(copy | {"tags": ["x" * 81]}),
            json.dumps(copy),
        ]
    result = new["flow"].resume(new["episode"].external_key, ProductionTarget.RENDER)
    assert result["target_complete"], result
    store, eid = new["store"], new["episode"].episode_id
    board_record = store.selected("episode", eid, "timed_storyboard", "main")
    board = parse_storyboard(store.read_json(board_record.identity.artifact_id))
    assert board.schema_version == 3 and board.scheduling_mode == "duration"
    assert len(board.scenes) != len(parse_lyrics(store.read_json(new["ids"][1])).lines)
    assert not new["analysis_calls"]
    manifest_record = store.selected("episode", eid, "render_manifest", "main_v4")
    manifest = parse_manifest(store.read_json(manifest_record.identity.artifact_id))
    assert manifest.schema_version == 2
    assert "audio_alignment_artifact_id" not in manifest.model_dump()
    validate_manifest(store, manifest, board)
    with pytest.raises(ValueError):
        RenderManifest.model_validate(manifest.model_dump(mode="json"))
    qa = store.selected("episode", eid, "media_qa", "main_v4")
    assert store.read_json(qa.identity.artifact_id)["passed"] is True
    assert Path(result["output_path"]).read_bytes()[4:8] == b"ftyp"
    assert "final_render" in Path(result["output_path"]).parts
    if variant == 0:
        with pytest.raises(ValueError):
            MetadataDraft.model_validate(copy | {"episode_id": eid})
        writer = MetadataWriter(
            CreativeService(
                new["config"],
                DurableStructuredGenerator(store.database, new["fake"]),
                assert_owner=lambda: None,
            )
        )
        writer.store.local_preview = True  # Same local admission policy as RenderService._metadata.
        metadata = writer.generate(new["episode"].external_key)
        assert metadata["metadata"]["episode_id"] == eid
        assert metadata["metadata"]["final_render_artifact_id"] == result["final_render_id"]
        final = store.get(result["final_render_id"])
        assert metadata["metadata"]["final_render_sha256"] == final.sha256
        assert metadata["metadata"]["tags"] == ["preschool learning", "kids song"]
        assert new["fake"].calls.count("MetadataDraft") == 2
        with closing(store.database.connect()) as db:
            requests = db.execute(
                "SELECT status,response_content FROM generation_requests WHERE episode_id=? "
                "AND prompt_version='metadata_copy_v2' ORDER BY rowid",
                (eid,),
            ).fetchall()
        assert [r[0] for r in requests] == ["succeeded_response_invalid", "succeeded"]
        assert set(json.loads(requests[1][1])) == set(copy)
        assert not any(writer.generate(new["episode"].external_key)["provider_calls"].values())
    before = (len(new["fake"].calls), len(new["music_events"]), len(new["image_events"]))
    again = new["flow"].resume(new["episode"].external_key, ProductionTarget.RENDER)
    assert again["final_render_id"] == result["final_render_id"]
    assert before == (len(new["fake"].calls), len(new["music_events"]), len(new["image_events"]))
    with closing(store.database.connect()) as db:
        assert db.execute("SELECT count(*) FROM publication_attempts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM music_audio_analysis").fetchone()[0] == 0


@pytest.mark.parametrize("studio_resume", [False, True])
def test_explicit_recovery_preserves_failures_upstream_music_and_episode(
    new_case, monkeypatch, studio_resume
):
    new = new_case
    request, _ = failed_legacy_contract(new)
    store, episode = new["store"], new["episode"]
    config = new["config"]
    assert config.publication_enabled and config.publication.youtube.enabled
    assert not config.automation.auto_publish and config.automation.require_human_review
    monkeypatch.setattr(
        new["flow"], "_publisher_factory", lambda *a: pytest.fail("render invoked publisher")
    )
    for operation in ("publish", "publish_public"):
        monkeypatch.setattr(
            PublicationService,
            operation,
            lambda *a, **k: pytest.fail("recovery invoked publication"),
        )
    exports = new["config"].brand_root.parent.parent / "outputs"
    exports.mkdir(exist_ok=True)
    old_export = (
        exports / f"TOVITUNES_{episode.external_key.upper().replace('-', '_')}_PILOT_V4.mp4"
    )
    old_export.write_bytes(b"retained earlier draft must remain untouched")
    with closing(store.database.connect()) as db:
        old_rows = [
            tuple(r)
            for r in db.execute(
                "SELECT * FROM generation_requests WHERE prompt_version='episode_visual_plan_v1'"
            )
        ]
        count = db.execute("SELECT count(*) FROM episodes").fetchone()[0]
        old_music = [tuple(r) for r in db.execute("SELECT * FROM music_requests")]
        old_fingerprints = {
            r[0]
            for r in db.execute(
                "SELECT input_fingerprint FROM generation_requests "
                "WHERE prompt_version='episode_visual_plan_v1'"
            )
        }
        old_intent = [tuple(r) for r in db.execute("SELECT * FROM production_requests")]
        old_jobs = [tuple(r) for r in db.execute("SELECT * FROM studio_jobs")]
        old_selections = [tuple(r) for r in db.execute("SELECT * FROM artifact_selections")]
        old_decisions = {
            table: [tuple(r) for r in db.execute(f"SELECT * FROM {table}")]
            for table in ("approval_decisions", "rights_decisions")
        }
        receipt = dict(
            db.execute("SELECT * FROM music_receipts WHERE request_id=?", (request,)).fetchone()
        )
    audio = store.selected("episode", episode.episode_id, "audio_master", "main")
    audio_hash = sha256(store.path_for(audio.identity.artifact_id).read_bytes()).hexdigest()
    plan = plan_continuation(new["config"], episode.external_key, target=ProductionTarget.RENDER)
    assert plan["status"] == "BLOCKED"
    assert plan["blocker"]["recovery_action"] == "authorize_visual_v2"
    assert "recover-visual-contract" in safe_production_result(plan)["blocker"]["reason"]

    def provider_events():
        return (
            list(new["fake"].calls),
            list(new["music_events"]),
            list(new["image_events"]),
            list(new["environment_events"]),
        )

    before = provider_events()
    grant = authorize_visual_recovery(
        new["config"], episode.external_key, confirmed=True, actor="offline-test"
    )
    assert grant["provider_calls"] == 0
    authorize_visual_recovery(
        new["config"], episode.external_key, confirmed=True, actor="offline-test"
    )
    assert provider_events() == before
    with closing(store.database.connect()) as db:
        assert old_selections == [tuple(r) for r in db.execute("SELECT * FROM artifact_selections")]
        for table, decisions in old_decisions.items():
            assert decisions == [tuple(r) for r in db.execute(f"SELECT * FROM {table}")]
    if studio_resume:

        def local_event_loop_connect(sock, address):
            # Windows asyncio creates its internal socketpair over loopback.
            if address[0] not in {"127.0.0.1", "::1"}:
                pytest.fail("live network forbidden")
            return new["socket_connect"](sock, address)

        monkeypatch.setattr("socket.socket.connect", local_event_loop_connect)
        monkeypatch.setattr(web_app, "build_orchestrator", lambda *a, **k: new["flow"])
        app = web_app.create_app(config)  # Reconstruct the target/reference from durable records.
        try:
            with TestClient(app, base_url="http://127.0.0.1:8766") as client:
                response = client.post(
                    "/api/studio/jobs/retained-studio-job/recover", json={"target": "publish"}
                )
                assert response.status_code == 202
                app.state.jobs._executor.submit(lambda: None).result(timeout=60)
                job = app.state.jobs.get("retained-studio-job")
                assert job.status == "complete", job
                assert job.target == ProductionTarget.RENDER
                assert "RELEASE" not in job.steps and "YOUTUBE" not in job.steps
                assert (
                    job.recovery_reference
                    == Job.model_validate_json(old_jobs[0][1]).recovery_reference
                )
                assert job.result["target"] == "render"
                result = new["flow"].resume(episode.external_key, ProductionTarget.RENDER)
        finally:
            app.state.jobs.close()
    else:
        result = new["flow"].resume(episode.external_key, ProductionTarget.RENDER)
    assert result["target_complete"], result
    assert result["target"] == "render"
    assert not {"RELEASE", "YOUTUBE"} & {s["name"] for s in result["stages"]}
    with closing(store.database.connect()) as db:
        assert old_rows == [
            tuple(r)
            for r in db.execute(
                "SELECT * FROM generation_requests WHERE prompt_version='episode_visual_plan_v1'"
            )
        ]
        assert old_music == [tuple(r) for r in db.execute("SELECT * FROM music_requests")]
        assert count == db.execute("SELECT count(*) FROM episodes").fetchone()[0]
        assert (
            dict(
                db.execute("SELECT * FROM music_receipts WHERE request_id=?", (request,)).fetchone()
            )
            == receipt
        )
        assert db.execute("SELECT count(*) FROM visual_contract_recoveries").fetchone()[0] == 1
        fresh = db.execute(
            "SELECT * FROM generation_requests WHERE prompt_version='visual_creative_v2'"
        ).fetchone()
        assert fresh["episode_id"] == episode.episode_id
        assert fresh["input_fingerprint"] not in old_fingerprints
        assert old_intent == [tuple(r) for r in db.execute("SELECT * FROM production_requests")]
        if not studio_resume:
            assert old_jobs == [tuple(r) for r in db.execute("SELECT * FROM studio_jobs")]
        assert db.execute("SELECT count(*) FROM publication_attempts").fetchone()[0] == 0
        for kind in ("episode_spec", "lyrics", "music_spec", "audio_master"):
            assert [r for r in old_selections if r[2] == kind] == [
                tuple(r)
                for r in db.execute("SELECT * FROM artifact_selections WHERE kind=?", (kind,))
            ]
        for sql in (
            "DELETE FROM visual_contract_recoveries",
            "UPDATE visual_contract_recoveries SET actor='changed'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    assert sha256(store.path_for(audio.identity.artifact_id).read_bytes()).hexdigest() == audio_hash
    assert new["music_events"].count("/release_task") == 1
    assert old_export.read_bytes() == b"retained earlier draft must remain untouched"
    publish_plan = plan_continuation(config, episode.external_key, target=ProductionTarget.PUBLISH)
    checks = publish_plan["blocker"]["release"]["checks"]
    assert any(c["name"] == "production_human_review" and not c["passed"] for c in checks)
    assert any(c["name"] == "commercial_rights_direct" and not c["passed"] for c in checks)


def test_v3_media_pack_and_dependency_failures_still_block_admission(new_case, monkeypatch):
    from tovitunes.render import production

    new = new_case
    result = new["flow"].resume(new["episode"].external_key, ProductionTarget.RENDER)
    assert result["target_complete"], result
    store, episode = new["store"], new["episode"]
    key, eid = episode.external_key, episode.episode_id
    loaded = load_inputs(new["config"], key, local_preview=True)
    aid = next(iter(loaded.storyboard.asset_artifact_ids.values()))
    path = store.path_for(aid)
    original = path.read_bytes()
    for bad in (None, b"corrupt image"):
        if bad is None:
            path.unlink()
        else:
            path.write_bytes(bad)
        with pytest.raises((ValueError, OSError)):
            load_inputs(new["config"], key, local_preview=True)
        path.write_bytes(original)
    catalog = production.load_brand(new["config"].brand_root)
    for pack in (
        catalog.packs[0].model_copy(update={"readiness": "placeholder"}),
        catalog.packs[0].model_copy(update={"asset_artifact_ids": {}}),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(production, "load_brand", lambda _: replace(catalog, packs=(pack,)))
            with pytest.raises(ValueError, match="unavailable|missing approved"):
                load_inputs(new["config"], key, local_preview=True)
    manifest_record = store.selected("episode", eid, "render_manifest", "main_v4")
    manifest = parse_manifest(store.read_json(manifest_record.identity.artifact_id))
    mismatched = manifest.model_copy(update={"duration_seconds": manifest.duration_seconds + 1})
    with pytest.raises(ValueError, match="differs"):
        validate_manifest(store, mismatched, loaded.storyboard)
    qa = store.selected("episode", eid, "media_qa", "main_v4")
    qa_path, qa_bytes = (
        store.path_for(qa.identity.artifact_id),
        store.path_for(qa.identity.artifact_id).read_bytes(),
    )
    qa_path.write_text('{"passed":true,"render_sha256":"wrong"}', encoding="utf-8")
    assert (
        plan_continuation(new["config"], key, target=ProductionTarget.RENDER)["status"] == "BLOCKED"
    )
    qa_path.write_bytes(qa_bytes)
    with closing(store.database.connect()) as db, db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "UPDATE artifact_dependencies SET input_sha256=? WHERE consumer_artifact_id=?",
                ("0" * 64, loaded.storyboard_record.identity.artifact_id),
            )


@pytest.mark.parametrize("failure", ["missing", "corrupt", "sha", "receipt", "episode"])
def test_duration_source_fails_closed(new_case, failure):
    new = new_case
    request, inputs = retain_music(new)
    store, episode = new["store"], new["episode"]
    audio = store.selected("episode", episode.episode_id, "audio_master", "main")
    with closing(store.database.connect()) as db, db:
        output = db.execute("SELECT * FROM music_outputs WHERE request_id=?", (request,)).fetchone()
        path = new["config"].data_root / "music-benchmark" / output["relative_path"]
        if failure == "missing":
            path.unlink()
        elif failure == "corrupt":
            path.write_bytes(b"corrupt audio")
        elif failure == "sha":
            store.path_for(audio.identity.artifact_id).write_bytes(tone(9))
        elif failure == "receipt":
            with pytest.raises(sqlite3.IntegrityError, match="matching receipt"):
                db.execute(
                    "UPDATE music_requests SET provider_request_id='wrong' WHERE request_id=?",
                    (request,),
                )
            return
        else:
            episode = episode.model_copy(update={"external_key": "another-episode"})
    with pytest.raises((ValueError, OSError)):
        admit_duration_source(
            new["config"],
            episode,
            request,
            creative_music_spec(new["episode"], *inputs),
            audio.identity.artifact_id,
        )


def test_failed_retained_asr_stays_failed_while_optional_rhythm_is_reused(new_case, monkeypatch):
    new = new_case
    request, inputs = retain_music(new)

    def unavailable(*args):
        return (
            TranscriptionEvidence(status="unavailable", failure_reason="offline ASR unavailable"),
            AlignmentEvidence(
                status="unavailable",
                aligned_word_count=0,
                aligned_line_count=0,
                failure_reason="offline alignment unavailable",
            ),
            "fixture",
            "cpu",
        )

    monkeypatch.setattr("tovitunes.music.analysis.transcribe_and_align", unavailable)
    with closing(new["store"].database.connect()) as db:
        blind = db.execute(
            "SELECT blind_id FROM music_outputs WHERE request_id=?", (request,)
        ).fetchone()[0]
    benchmark = MusicBenchmark(new["store"].database, new["config"].data_root / "music-benchmark")
    report, _ = benchmark.analyze_audio(blind, 1, production_analysis_config(new["config"]))
    assert report.transcription.status == "unavailable"
    assert report.lyric_comparison.wer is None
    with closing(new["store"].database.connect()) as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM music_audio_analysis")]
        policies = [tuple(r) for r in db.execute("SELECT * FROM music_policy_evaluations")]
    master = new["store"].selected("episode", new["episode"].episode_id, "audio_master", "main")
    admitted = admit_duration_source(
        new["config"],
        new["episode"],
        request,
        creative_music_spec(new["episode"], *inputs),
        master.identity.artifact_id,
    )
    assert admitted.measured_beats is not None
    assert admitted.measured_beats.beat_seconds == report.rhythm.beat_seconds
    assert admitted.words == ()
    with closing(new["store"].database.connect()) as db:
        assert before == [tuple(r) for r in db.execute("SELECT * FROM music_audio_analysis")]
        assert policies == [tuple(r) for r in db.execute("SELECT * FROM music_policy_evaluations")]


def test_recovery_rejects_auto_publication_and_unresolved_receipts(new_case):
    new = new_case
    failed_legacy_contract(new)
    unsafe = new["config"].model_copy(
        update={"automation": new["config"].automation.model_copy(update={"auto_publish": True})}
    )
    with pytest.raises(PermissionError, match="automatic publication"):
        authorize_visual_recovery(
            unsafe, new["episode"].external_key, confirmed=True, actor="operator"
        )
    with closing(new["store"].database.connect()) as db, db:
        # Isolated fixture corruption to simulate a retained uncertain attempt.
        db.execute(
            "UPDATE generation_requests SET status='ambiguous' WHERE "
            "prompt_version='episode_visual_plan_v1' AND attempt=2"
        )
    with pytest.raises(ValueError, match="unresolved"):
        authorize_visual_recovery(
            new["config"], new["episode"].external_key, confirmed=True, actor="operator"
        )
    with closing(new["store"].database.connect()) as db:
        assert db.execute("SELECT count(*) FROM visual_contract_recoveries").fetchone()[0] == 0


@pytest.mark.parametrize(
    "key,confirmed,actor,error",
    [
        ("unknown-episode", True, "operator", KeyError),
        ("", True, "operator", KeyError),
        (None, False, "operator", PermissionError),
        (None, True, "  ", PermissionError),
    ],
)
def test_recovery_requires_existing_identity_and_explicit_operator(
    new_case, key, confirmed, actor, error
):
    new = new_case
    with pytest.raises(error):
        authorize_visual_recovery(
            new["config"],
            new["episode"].external_key if key is None else key,
            confirmed=confirmed,
            actor=actor,
        )
    assert not new["music_events"] and not new["image_events"]
    with closing(new["store"].database.connect()) as db:
        assert db.execute("SELECT count(*) FROM visual_contract_recoveries").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["prepared", "remote_started", "ambiguous", "succeeded"])
def test_recovery_rejects_unresolved_or_successful_original_visual_attempt(new_case, status):
    new = new_case
    failed_legacy_contract(new)
    with closing(new["store"].database.connect()) as db, db:
        db.execute(
            "UPDATE generation_requests SET status=? WHERE "
            "prompt_version='episode_visual_plan_v1' AND attempt=2",
            (status,),
        )
    with pytest.raises(ValueError, match="successful or unresolved"):
        authorize_visual_recovery(
            new["config"], new["episode"].external_key, confirmed=True, actor="operator"
        )
    with closing(new["store"].database.connect()) as db:
        assert db.execute("SELECT count(*) FROM visual_contract_recoveries").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["audio", "music_attempt", "upstream"])
def test_recovery_rejects_invalid_retained_sources_without_provider_calls(new_case, failure):
    new = new_case
    failed_legacy_contract(new)
    store, episode = new["store"], new["episode"]
    if failure == "audio":
        audio = store.selected("episode", episode.episode_id, "audio_master", "main")
        store.path_for(audio.identity.artifact_id).write_bytes(b"corrupt retained audio")
    else:
        with closing(store.database.connect()) as db, db:
            if failure == "music_attempt":
                with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                    db.execute("UPDATE music_requests SET status='ambiguous'")
                # Only corrupt this isolated fixture to exercise recovery's second
                # line of defense against a bound unresolved music source.
                db.execute("DROP TRIGGER music_succeeded_request_immutable")
                db.execute("UPDATE music_requests SET status='ambiguous'")
            else:
                db.execute("DELETE FROM artifact_selections WHERE kind='lyrics'")
    before = (list(new["fake"].calls), list(new["music_events"]), list(new["image_events"]))
    with pytest.raises(ValueError, match="upstream|identity|ineligible|artifact file invalid"):
        authorize_visual_recovery(
            config=new["config"], episode_key=episode.external_key, confirmed=True, actor="operator"
        )
    assert before == (new["fake"].calls, new["music_events"], new["image_events"])
    with closing(store.database.connect()) as db:
        assert db.execute("SELECT count(*) FROM visual_contract_recoveries").fetchone()[0] == 0


def test_restart_after_visual_response_persistence_does_not_resend(new_case, monkeypatch):
    new = new_case
    retain_music(new)
    with ProductionExecutionOwnership(new["store"].database, operation="offline-test") as owner:
        service = VisualService(
            new["config"],
            owner,
            creative_provider=DurableStructuredGenerator(new["store"].database, new["fake"]),
        )
        inputs = service._creative_inputs(new["episode"])
        original = service._json

        def crash(episode, kind, *args, **kwargs):
            if kind == "creative_visual_draft":
                raise KeyboardInterrupt("process exit after response before ingest")
            return original(episode, kind, *args, **kwargs)

        monkeypatch.setattr(service, "_json", crash)
        with pytest.raises(KeyboardInterrupt):
            service._visual(new["episode"], inputs)
        count = len(new["fake"].calls)
        monkeypatch.setattr(service, "_json", original)
        plan, _ = service._visual(new["episode"], inputs)
        assert isinstance(plan, EpisodeVisualPlanV2) and len(new["fake"].calls) == count


def test_new_visual_ambiguity_and_ownership_never_duplicate_call(new_case):
    new = new_case
    retain_music(new)
    new["fake"].responses["CreativeVisualDraft"] = [ProviderError("uncertain", ambiguous=True)]
    with ProductionExecutionOwnership(new["store"].database, operation="offline-test") as owner:
        service = VisualService(
            new["config"],
            owner,
            creative_provider=DurableStructuredGenerator(new["store"].database, new["fake"]),
        )
        inputs = service._creative_inputs(new["episode"])
        before = len(new["fake"].calls)
        for _ in range(2):
            with pytest.raises(ProviderError):
                service._visual(new["episode"], inputs)
        assert len(new["fake"].calls) == before + 1
    provider = DurableStructuredGenerator(new["store"].database, new["fake"])

    def lost():
        raise ExecutionOwnershipLostError("old owner")

    with pytest.raises(ExecutionOwnershipLostError):
        provider.generate(
            CreativeVisualDraft,
            [{"role": "user", "content": "offline"}],
            context=GenerationContext(
                "creative_visual_draft",
                "visual_creative_v2",
                episode_id=new["episode"].episode_id,
                assert_owner=lost,
            ),
        )
    assert len(new["fake"].calls) == before + 1


def test_ownership_loss_after_response_preserves_receipt_and_blocks_resend(new_case, monkeypatch):
    new = new_case
    returned = False
    original = new["fake"].chat

    def chat(*args, **kwargs):
        nonlocal returned
        response = original(*args, **kwargs)
        returned = True
        return response

    def owner():
        if returned:
            raise ExecutionOwnershipLostError("lease lost after remote response")

    monkeypatch.setattr(new["fake"], "chat", chat)
    provider = DurableStructuredGenerator(new["store"].database, new["fake"])
    messages = [{"role": "user", "content": "offline ownership boundary"}]
    context = GenerationContext(
        "creative_visual_draft",
        "visual_creative_v2",
        episode_id=new["episode"].episode_id,
        assert_owner=owner,
    )
    before = len(new["fake"].calls)
    with pytest.raises(ExecutionOwnershipLostError):
        provider.generate(CreativeVisualDraft, messages, context=context)
    with closing(new["store"].database.connect()) as db:
        saved = dict(
            db.execute(
                "SELECT * FROM generation_requests WHERE prompt_version='visual_creative_v2'"
            ).fetchone()
        )
    assert saved["status"] == "ambiguous" and saved["response_content"]
    assert sha256(saved["response_content"].encode()).hexdigest() == saved["response_sha256"]
    with pytest.raises(ProviderError):
        provider.generate(
            CreativeVisualDraft,
            messages,
            context=GenerationContext(
                "creative_visual_draft", "visual_creative_v2", episode_id=new["episode"].episode_id
            ),
        )
    assert len(new["fake"].calls) == before + 1


def test_v3_uncertain_image_survives_restart_without_a_second_prompt(new_case):
    new = new_case
    new["image_behavior"]["crash"] = 1
    with pytest.raises(SystemExit):
        new["flow"].resume(new["episode"].external_key, ProductionTarget.RENDER)
    new["image_behavior"]["crash"] = None
    result = new["flow"].resume(new["episode"].external_key, ProductionTarget.RENDER)
    assert result["status"] == "AMBIGUOUS" and result["current_stage"] == "VISUAL_ASSETS"
    assert new["image_events"].count("/prompt") == 1
    assert new["music_events"].count("/release_task") == 1


def test_v3_readiness_rejects_required_image_and_response_corruption(new_case):
    new = new_case
    result = new["flow"].resume(new["episode"].external_key, ProductionTarget.RENDER)
    assert result["target_complete"], result
    store, eid = new["store"], new["episode"].episode_id
    raw = store.selected("episode", eid, "creative_visual_draft", "main")
    with closing(store.database.connect()) as db, db:
        db.execute(
            "UPDATE generation_requests SET response_content='{}' WHERE request_id=?",
            (raw.provenance.local_request_id,),
        )
    with pytest.raises(ValueError, match="response evidence"):
        load_inputs(new["config"], new["episode"].external_key, local_preview=True)
    # The read-only planner must reject the retained corrupt response too.
    assert (
        plan_continuation(
            new["config"], new["episode"].external_key, target=ProductionTarget.RENDER
        )["status"]
        == "BLOCKED"
    )


def test_new_contract_survives_real_process_restart(new_case):
    new = new_case
    config_file = new["config"].database_path.parent / "v3-restart.json"
    config_file.write_text(new["config"].model_dump_json(), encoding="utf-8")
    runner = Path(__file__).with_name("offline_resume_process.py")
    outputs = [config_file.with_name(f"v3-process-{i}.json") for i in range(2)]
    for stage, path, code in zip(("STORYBOARD", "none"), outputs, (75, 0), strict=True):
        process = subprocess.run(
            [
                sys.executable,
                str(runner),
                str(config_file),
                new["episode"].external_key,
                stage,
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=150,
        )
        assert process.returncode == code, process.stderr
    metrics = [json.loads(p.read_text(encoding="utf-8")) for p in outputs]
    assert metrics[1]["result"]["target_complete"]
    assert sum(m["music_events"].count("/release_task") for m in metrics) == 1
    assert sum(m["analysis_calls"] for m in metrics) == 0
