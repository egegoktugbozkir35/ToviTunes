"""Offline persistence/renderer contracts; no generation or analysis models are required."""

import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import BrandCatalog
from tovitunes.cli import main
from tovitunes.config import RuntimeConfig
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision
from tovitunes.domain.storyboard import (
    AudioAlignment,
    BeatAnalysis,
    Interval,
    StoryboardTemplate,
    TimedStoryboard,
    build_storyboard,
)
from tovitunes.music.analysis_models import AudioAnalysis
from tovitunes.music.audio import inspect_audio
from tovitunes.music.models import CanonicalMusicSpec, load_brief, load_lyrics
from tovitunes.persistence.db import Database
from tovitunes.pipeline import production
from tovitunes.pipeline.planner import load_snapshot, plan
from tovitunes.pipeline.production import (
    ProductionHandoff,
    canonical_bytes,
    extract_alignment,
    extract_beats,
    plan_handoff,
)

ROOT = Path(__file__).resolve().parents[1]
BLIND = "mb_3f657849e3d04060a0107940b098fb60"
REQUEST = "152f47fa-54f6-4bb3-ad67-8dc5467380d5"
ARGS = ("red", "colors-red-001", BLIND, 3)


def snapshot(database: Path, prefix: str = "") -> dict[str, list[str]]:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {
            name: sorted(
                sha256(canonical_bytes(dict(r))).hexdigest()
                for r in db.execute(f'SELECT * FROM "{name}"')
            )
            for name in names
            if name.startswith(prefix)
        }


@pytest.fixture(autouse=True)
def no_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("provider/network/analysis call forbidden during production handoff")

    for target in (
        "socket.socket.connect",
        "socket.create_connection",
        "tovitunes.music.benchmark.MusicBenchmark.run",
        "tovitunes.music.benchmark.MusicBenchmark.provider_resume",
        "tovitunes.music.benchmark.MusicBenchmark.analyze_audio",
        "tovitunes.music.benchmark.MusicBenchmark.evaluate_analysis_qa",
        "tovitunes.music.benchmark.MusicBenchmark.evaluate_timing",
        "tovitunes.music.vertex_lyria.VertexLyriaProvider.generate",
        "tovitunes.benchmark.runner.BenchmarkRunner.run",
        "tovitunes.pipeline.creative.FakeDraftGenerator.episode_spec",
    ):
        monkeypatch.setattr(target, forbidden)


@pytest.fixture
def factory(tmp_path: Path, brand_root: Path, monkeypatch: pytest.MonkeyPatch):
    def make(*, missing: str | None = None, blind: str = BLIND) -> RuntimeConfig:
        root = tmp_path / str(uuid4())
        root.mkdir()
        database = Database(root / "state.db")
        database.migrate()
        audio_root = root / "data/music-benchmark"
        audio_root.mkdir(parents=True)
        tone = ROOT / "tests/fixtures/lyria_tone.mp3"
        path = audio_root / f"{REQUEST}.mp3"
        shutil.copyfile(tone, path)
        digest = sha256(path.read_bytes()).hexdigest()
        # Offline tests substitute only the expected fixture-byte pin; the SHA checks
        # against original bytes, receipt, analysis, timing and output all remain active.
        monkeypatch.setattr(production, "PILOT_AUDIO_SHA256", digest)
        duration = inspect_audio(path.read_bytes(), "audio/mpeg").duration_seconds
        spec = CanonicalMusicSpec(
            brief=load_brief(ROOT / "benchmarks/music/colors_red_v1.yaml"),
            lyrics=load_lyrics(ROOT / "benchmarks/music/colors_red_lyrics_v1.yaml"),
            attempt=2,
        )
        words, lines, sections = [], [], []
        cursor = 0.05
        for line in spec.lyrics.lines:
            start = cursor
            for text in line.text.split():
                words.append({"text": text, "start": cursor, "end": cursor + 0.015})
                cursor += 0.02
            end = words[-1]["end"]
            lines.append({"text": line.text, "start": start, "end": end})
            if sections and sections[-1]["text"] == line.section:
                sections[-1]["end"] = end
            else:
                sections.append({"text": line.section, "start": start, "end": end})
            cursor += 0.01
        points = [0.0, 0.1, 0.3, 0.5, 0.7, duration]
        timing = {
            "version": 3,
            "audio_sha256": digest,
            "duration_seconds": duration,
            "estimated_bpm": 112,
            "beat_seconds": points,
            "downbeat_seconds": points[::2],
            "words": words,
            "lyric_lines": lines,
            "sections": sections,
            "intro": {"start": 0, "end": lines[0]["start"]},
            "outro": {"start": lines[-1]["end"], "end": duration},
        }
        provenance = {
            "name": "Beat This",
            "version": "fixture-v1",
            "model_name": "fixture",
            "model_revision": "sha256:" + "a" * 64,
            "device": "cpu",
            "timestamp": "2026-01-01T00:00:00Z",
            "source_audio_sha256": digest,
        }
        recognized = [{**w, "score": 0.9} for w in words]
        report = AudioAnalysis.model_validate(
            {
                "version": 3,
                "blind_id": blind,
                "request_id": REQUEST,
                "audio_sha256": digest,
                "analyzer_config_sha256": sha256(canonical_bytes({})).hexdigest(),
                "analyzer_configuration": {},
                "duration_seconds": duration,
                "source_format": "mp3",
                "sample_rate_hz": 44100,
                "channel_count": 1,
                "technical_metrics": {
                    "decode_integrity": True,
                    "invalid_pcm_samples": 0,
                    "sample_rate_hz": 44100,
                    "channel_count": 1,
                    "decoded_frames": 44100,
                    "duration_seconds": duration,
                    "peak_amplitude": 0.5,
                    "rms_amplitude": 0.2,
                    "dc_offset": 0,
                    "clipping_ratio": 0,
                    "near_silence_ratio": 0,
                    "longest_near_silent_span_seconds": 0,
                    "beginning_silence_seconds": 0,
                    "ending_silence_seconds": 0,
                    "near_silence_threshold_dbfs": -40,
                    "clipping_threshold_amplitude": 0.99,
                    "window_seconds": 0.01,
                },
                "transcription": {
                    "status": "complete",
                    "recognized_text": spec.lyrics.text(),
                    "words": recognized,
                    "mean_word_score": 0.9,
                },
                "lyric_comparison": {
                    "status": "complete",
                    "expected_transcript": spec.lyrics.text(),
                    "recognized_transcript": spec.lyrics.text(),
                    "expected_word_count": len(words),
                    "recognized_word_count": len(words),
                    "required_phrase_presence": {},
                },
                "rhythm": {
                    "status": "complete",
                    "beat_seconds": points,
                    "downbeat_seconds": points[::2],
                    "estimated_bpm": 112,
                    "beat_count": len(points),
                    "provenance": provenance,
                    "target_bpm": 112,
                    "allowed_bpm_range": [100, 124],
                },
                "alignment": {
                    "status": "complete",
                    "canonical_words": recognized,
                    "lyric_lines": lines,
                    "aligned_word_count": len(words),
                    "aligned_line_count": len(lines),
                },
                "timing": timing,
                "analyzer_provenance": {"beat": provenance},
            }
        )
        analysis_json = report.model_dump_json()
        with closing(database.connect()) as db:
            db.execute(
                "INSERT INTO music_requests (request_id, brief_id, lyric_id, provider, model, "
                "attempt, input_fingerprint, status, canonical_spec_json, translated_request_json, "
                "capabilities_json, provider_request_id, remote_started_at, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, 'google', 'lyria-3-pro-preview', 2, ?, 'remote_started', "
                "?, ?, '{}', 'fixture-provider-id', 'fixture', 'fixture', 'fixture')",
                (
                    REQUEST,
                    spec.brief.id,
                    spec.lyrics.id,
                    digest,
                    spec.model_dump_json(),
                    json.dumps({"prompt_contract": "lyria_exact_lyrics_v2"}),
                ),
            )
            db.execute(
                "INSERT INTO music_receipts (request_id, provider_request_id, sha256, byte_count, "
                "duration_seconds, mime_type, container, codec, "
                "response_metadata_json, received_at) "
                "VALUES (?, 'fixture-provider-id', ?, ?, ?, 'audio/mpeg', "
                "'mp3', 'mp3', '{}', 'fixture')",
                (REQUEST, digest, path.stat().st_size, duration),
            )
            db.execute(
                "INSERT INTO music_outputs VALUES (?, ?, ?, ?, 'unknown', 'pending', 'fixture')",
                (REQUEST, blind, path.name, digest),
            )
            db.execute(
                "UPDATE music_requests SET status='succeeded' WHERE request_id=?", (REQUEST,)
            )
            for version in (1, 2, 3):
                version_report = report.model_copy(
                    update={
                        "version": version,
                        "timing": report.timing.model_copy(update={"version": version}),
                    }
                )
                if not (missing == "analysis" and version == 3):
                    db.execute(
                        "INSERT INTO music_audio_analysis VALUES (?, ?, ?, ?, ?, ?, 'fixture')",
                        (
                            blind,
                            version,
                            REQUEST,
                            digest,
                            report.analyzer_config_sha256,
                            version_report.model_dump_json(),
                        ),
                    )
                if not (missing == "timing" and version == 3):
                    db.execute(
                        "INSERT INTO music_timing VALUES (?, ?, ?, 'fixture')",
                        (blind, version, version_report.timing.model_dump_json()),
                    )
            for subject, subject_id, policy, version, evidence in (
                (
                    "audio",
                    blind,
                    "music_qa",
                    2,
                    {
                        "analysis_version": 3,
                        "request_id": REQUEST,
                        "analysis_sha256": sha256(analysis_json.encode()).hexdigest(),
                        "timing_admission": {"admitted": True},
                    },
                ),
                (
                    "timing",
                    blind + ":3",
                    "music_timing",
                    1,
                    {
                        "analysis_sha256": sha256(
                            canonical_bytes(report.timing.model_dump(mode="json"))
                        ).hexdigest()
                    },
                ),
            ):
                if missing == policy:
                    continue
                db.execute(
                    "INSERT INTO music_policy_evaluations VALUES (?, ?, ?, ?, ?, ?, "
                    "'fixture', 'machine', 'pass', ?, '{}', 'fixture')",
                    (
                        str(uuid4()),
                        subject,
                        subject_id,
                        digest,
                        policy,
                        version,
                        json.dumps(evidence),
                    ),
                )
            db.commit()
        return RuntimeConfig(
            database_path=database.path, data_root=root / "data", brand_root=brand_root
        )

    return make


def test_handoff_reuses_all_artifacts_and_preserves_history(factory) -> None:
    config = factory()
    before = snapshot(config.database_path, "music_")
    original = (config.data_root / "music-benchmark" / f"{REQUEST}.mp3").read_bytes()
    preview_before = snapshot(config.database_path)
    dry = plan_handoff(config, *ARGS)
    assert len(dry.preview.scenes) == 9
    assert snapshot(config.database_path) == preview_before
    result = ProductionHandoff(config).prepare(*ARGS)
    ids = result["artifact_ids"]
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    assert store.path_for(ids["audio_master"]).read_bytes() == original
    alignment = AudioAlignment.model_validate(store.read_json(ids["audio_alignment"]))
    beats = BeatAnalysis.model_validate(store.read_json(ids["beat_analysis"]))
    assert alignment == extract_alignment(dry.source, ids["audio_master"])
    assert beats == extract_beats(dry.source, ids["audio_master"])
    assert "score" not in json.dumps(alignment.model_dump())
    board = TimedStoryboard.model_validate(store.read_json(ids["timed_storyboard"]))
    assert [
        (s.lyric_text, s.lyric_start, s.lyric_end) for s in board.scenes if s.kind == "lyric"
    ] == [(x.text, x.start, x.end) for x in alignment.lyric_lines]
    assert board.scenes[0].start == 0
    assert board.scenes[-1].end == alignment.duration_seconds
    assert all(a.end == b.start for a, b in zip(board.scenes, board.scenes[1:]))
    assert board.scenes[3].required_props == ("red_apple",)
    assert board.scenes[4].required_props == ("red_ball",)
    assert sum(s.beat_index_range[1] - s.beat_index_range[0] for s in board.scenes) == len(
        beats.beat_seconds
    )
    assert sum(s.downbeat_index_range[1] - s.downbeat_index_range[0] for s in board.scenes) == len(
        beats.downbeat_seconds
    )
    snapshot_after = snapshot(config.database_path)
    rerun = ProductionHandoff(config).prepare(*ARGS)
    assert rerun["artifact_ids"] == ids
    assert set(rerun["artifact_actions"].values()) == {"reuse"}
    assert snapshot(config.database_path) == snapshot_after
    assert snapshot(config.database_path, "music_") == before
    assert (config.data_root / "music-benchmark" / f"{REQUEST}.mp3").read_bytes() == original
    snapshot_state = load_snapshot(store, result["episode_id"])
    assert snapshot_state.scene_ids == board.scene_ids
    assert snapshot_state.production_audio_handoff
    assert not snapshot_state.storyboard_problem
    assert plan(snapshot_state, "storyboard").action == "complete"
    assert plan(snapshot_state, "render").requirement == "scene_image:intro"
    assert snapshot_state.uncleared_rights
    with closing(store.database.connect()) as db:
        assert {
            r[0]
            for r in db.execute(
                "SELECT status FROM rights_decisions WHERE artifact_id IN "
                "(SELECT artifact_id FROM artifact_versions WHERE episode_id=?)",
                (result["episode_id"],),
            )
        } == {"unknown"}
        decisions = list(
            db.execute(
                "SELECT policy_version, reason, actor FROM approval_decisions "
                "WHERE status='approved'"
            )
        )
        assert all(
            r[0] in {"canonical_curriculum_v1", "technical_production_master_v1"}
            and "publication approval" in r[1]
            and r[2].startswith("machine:")
            for r in decisions
        )
        for kind, expected in {
            "audio_master": (ids["production_handoff"],),
            "audio_alignment": (ids["audio_master"],),
            "beat_analysis": (ids["audio_master"],),
            "timed_storyboard": (ids["audio_master"], ids["audio_alignment"], ids["beat_analysis"]),
        }.items():
            deps = list(
                db.execute(
                    "SELECT input_artifact_id, input_sha256 FROM artifact_dependencies "
                    "WHERE consumer_artifact_id=? ORDER BY rowid",
                    (ids[kind],),
                )
            )
            assert tuple(r[0] for r in deps) == expected
            assert all(store.get(r[0]).sha256 == r[1] for r in deps)
    master = store.get(ids["audio_master"])
    assert master.provenance.source_kind == "provider"
    assert master.provenance.local_request_id == REQUEST
    assert master.provenance.request_id == "fixture-provider-id"
    assert store.read_json(ids["production_handoff"])["source_blind_id"] == BLIND


@pytest.mark.parametrize("missing", ["music_qa", "music_timing", "timing", "analysis"])
def test_missing_evidence_fails_before_mutation(factory, missing) -> None:
    config = factory(missing=missing)
    before = snapshot(config.database_path)
    with pytest.raises(ValueError):
        ProductionHandoff(config).prepare(*ARGS)
    assert snapshot(config.database_path) == before
    assert not (config.data_root / ".production-handoff").exists()


@pytest.mark.parametrize(
    "change", ["wrong_blind", "wrong_sha", "wrong_version", "rights", "approval"]
)
def test_source_failure_is_read_only(factory, change) -> None:
    config = factory()
    args = ARGS
    if change == "wrong_blind":
        args = (*ARGS[:2], "mb_other", 3)
    elif change == "wrong_version":
        args = (*ARGS[:3], 2)
    elif change == "wrong_sha":
        (config.data_root / "music-benchmark" / f"{REQUEST}.mp3").write_bytes(b"wrong audio")
    else:
        with closing(Database(config.database_path).connect()) as db:
            column = "rights_status" if change == "rights" else "approval_status"
            db.execute(f"UPDATE music_outputs SET {column}='approved'")
            db.commit()
    before = snapshot(config.database_path)
    with pytest.raises(ValueError):
        ProductionHandoff(config).prepare(*args)
    assert snapshot(config.database_path) == before


def test_existing_wrong_candidate_identity_fails(factory) -> None:
    config = factory(blind="mb_existing_wrong_candidate")
    before = snapshot(config.database_path)
    with pytest.raises(ValueError, match="authoritative pilot"):
        ProductionHandoff(config).prepare(*ARGS[:2], "mb_existing_wrong_candidate", 3)
    assert snapshot(config.database_path) == before


@pytest.mark.parametrize("field", ["concept", "curriculum", "objective", "pack"])
def test_mismatched_existing_episode_fails(factory, catalog: BrandCatalog, field) -> None:
    config = factory()
    altered = catalog
    concept = "red"
    if field == "concept":
        concept = "blue"
    elif field == "curriculum":
        altered = replace(
            catalog,
            curriculum_revision=catalog.curriculum_revision.model_copy(
                update={
                    "revision_id": "fixture-curriculum",
                    "version": "fixture",
                    "sha256": "b" * 64,
                }
            ),
        )
    elif field == "objective":
        concepts = tuple(
            c.model_copy(update={"objective_id": "colors.red.other"})
            if c.concept_id == "red"
            else c
            for c in catalog.curriculum.concepts
        )
        altered = replace(
            catalog, curriculum=catalog.curriculum.model_copy(update={"concepts": concepts})
        )
    else:
        altered = replace(
            catalog,
            pack_revisions=(
                catalog.pack_revisions[0].model_copy(
                    update={
                        "revision_id": "fixture-pack",
                        "version": "fixture",
                        "manifest_sha256": "b" * 64,
                    }
                ),
            ),
        )
    episode = Episode.create(altered, concept, "colors-red-001")
    Database(config.database_path).create_episode(altered, episode)
    before = snapshot(config.database_path)
    with pytest.raises(ValueError, match="existing episode differs"):
        ProductionHandoff(config).prepare(*ARGS)
    assert snapshot(config.database_path) == before


@pytest.mark.parametrize("failure", ["later_fail", "wrong_analysis_hash", "wrong_analysis_version"])
def test_qa_must_bind_current_exact_version(factory, failure) -> None:
    config = factory()
    with closing(Database(config.database_path).connect()) as db:
        ev = dict(
            db.execute(
                "SELECT * FROM music_policy_evaluations WHERE policy_id='music_qa'"
            ).fetchone()
        )
        ev["evaluation_id"] = str(uuid4())
        if failure == "later_fail":
            ev["status"] = "fail"
        else:
            evidence = json.loads(ev["evidence_json"])
            evidence[
                "analysis_sha256" if failure == "wrong_analysis_hash" else "analysis_version"
            ] = "b" * 64 if failure == "wrong_analysis_hash" else 2
            ev["evidence_json"] = json.dumps(evidence)
        db.execute(
            "INSERT INTO music_policy_evaluations VALUES (" + ",".join("?" for _ in ev) + ")",
            tuple(ev.values()),
        )
        db.commit()
    before = snapshot(config.database_path)
    with pytest.raises(ValueError):
        ProductionHandoff(config).prepare(*ARGS)
    assert snapshot(config.database_path) == before


@pytest.mark.parametrize("pre,post", [(True, True), (False, True), (True, False), (False, False)])
def test_intro_outro_only_when_positive(factory, pre, post) -> None:
    config = factory()
    prepared = plan_handoff(config, *ARGS)
    alignment = extract_alignment(prepared.source, "master")
    data = alignment.model_dump(mode="json")
    if not pre:
        data["words"][0]["start"] = data["lyric_lines"][0]["start"] = data["sections"][0][
            "start"
        ] = 0
        data["pre_lyric"] = None
    if not post:
        data["duration_seconds"] = data["lyric_lines"][-1]["end"]
        data["post_lyric"] = None
    alignment = AudioAlignment.model_validate(data)
    beats_data = extract_beats(prepared.source, "master").model_dump(mode="json")
    beats_data["duration_seconds"] = alignment.duration_seconds
    for key in ("beat_seconds", "downbeat_seconds"):
        beats_data[key] = [x for x in beats_data[key] if x <= alignment.duration_seconds]
    beats = BeatAnalysis.model_validate(beats_data)
    first = build_storyboard(
        prepared.episode,
        alignment,
        beats,
        "alignment",
        "beats",
        prepared.template,
        prepared.template_sha256,
    )
    again = build_storyboard(
        prepared.episode,
        alignment,
        beats,
        "alignment",
        "beats",
        prepared.template,
        prepared.template_sha256,
    )
    assert first == again
    assert len(first.scenes) == len(alignment.lyric_lines) + int(pre) + int(post)
    assert first.scenes[0].start == 0 and first.scenes[-1].end == alignment.duration_seconds


@pytest.mark.parametrize(
    "defect",
    [
        "gap",
        "overlap",
        "duplicate_id",
        "unsafe_id",
        "vocal_shift",
        "apple",
        "ball",
        "color",
        "extra_character",
        "wrong_pack",
        "nan",
    ],
)
def test_storyboard_rejects_invalid_scenes(factory, defect) -> None:
    board = plan_handoff(factory(), *ARGS).preview.model_dump(mode="json")
    scenes = board["scenes"]
    if defect in {"gap", "overlap"}:
        scenes[1]["end"] += 0.001 if defect == "gap" else -0.001
    elif defect == "duplicate_id":
        scenes[1]["scene_id"] = scenes[0]["scene_id"]
    elif defect == "unsafe_id":
        scenes[0]["scene_id"] = "../unsafe"
    elif defect == "vocal_shift":
        scenes[1]["lyric_start"] += 0.001
    elif defect in {"apple", "ball"}:
        scenes[3 if defect == "apple" else 4]["required_props"] = []
    elif defect == "color":
        scenes[1]["lesson_target"] = "blue"
    elif defect == "extra_character":
        scenes[1]["character_id"] = "other"
    elif defect == "wrong_pack":
        board["character_pack"]["character_id"] = "other"
    else:
        scenes[1]["end"] = float("nan")
    with pytest.raises(ValidationError):
        TimedStoryboard.model_validate(board)


def test_template_requires_exact_line_mapping_and_red_objects(factory) -> None:
    prepared = plan_handoff(factory(), *ARGS)
    template_data = prepared.template.model_dump(mode="json")
    template_data["lyrics"][2]["required_props"] = []
    with pytest.raises(ValidationError, match="red_apple"):
        StoryboardTemplate.model_validate(template_data)
    template_data = prepared.template.model_dump(mode="json")
    template_data["lyrics"][3]["required_props"] = []
    with pytest.raises(ValidationError, match="red_ball"):
        StoryboardTemplate.model_validate(template_data)
    bad = prepared.template.model_copy(update={"lyrics": prepared.template.lyrics[:-1]})
    with pytest.raises(ValueError, match="inputs differ"):
        build_storyboard(
            prepared.episode,
            extract_alignment(prepared.source, "master"),
            extract_beats(prepared.source, "master"),
            "alignment",
            "beats",
            bad,
            prepared.template_sha256,
        )


def test_cli_dry_run_and_real_idempotent_command(factory, tmp_path: Path, capsys) -> None:
    config = factory()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    args = [
        "--config",
        str(config_path),
        "production",
        "prepare-storyboard",
        "--concept",
        "red",
        "--episode-key",
        "colors-red-001",
        "--music-blind-id",
        BLIND,
        "--analysis-version",
        "3",
    ]
    before = snapshot(config.database_path)
    assert main([*args, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["episode_action"] == "create"
    assert snapshot(config.database_path) == before
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["artifact_ids"] == second["artifact_ids"]
    assert set(second["artifact_actions"].values()) == {"reuse"}


def test_rejected_objective_is_not_overridden(factory) -> None:
    config = factory()
    prepared = plan_handoff(config, *ARGS)
    database = Database(config.database_path)
    database.create_episode(prepared.catalog, prepared.episode)
    store = AssetStore(config.data_root, database)
    store.record_approval(
        ApprovalDecision(
            target_id=prepared.episode.episode_id,
            target_kind="episode",
            status="rejected",
            actor="reviewer",
            reason="hold",
            policy_version="fixture",
            decided_at=datetime.now(UTC),
        )
    )
    before = snapshot(config.database_path)
    with pytest.raises(ValueError, match="objective decision blocks"):
        ProductionHandoff(config).prepare(*ARGS)
    assert snapshot(config.database_path) == before


def test_invalid_alignment_and_beats_fail(factory) -> None:
    prepared = plan_handoff(factory(), *ARGS)
    alignment = extract_alignment(prepared.source, "master").model_dump(mode="json")
    alignment["pre_lyric"] = Interval(start=0, end=0.1).model_dump()
    with pytest.raises(ValidationError, match="edges differ"):
        AudioAlignment.model_validate(alignment)
    beat_data = extract_beats(prepared.source, "master").model_dump(mode="json")
    beat_data["downbeat_seconds"] = [0.01234]
    with pytest.raises(ValidationError, match="belong"):
        BeatAnalysis.model_validate(beat_data)


@pytest.mark.parametrize("change", ["rejected", "corrupt"])
def test_rerun_respects_later_artifact_decisions_and_invalid_files(factory, change) -> None:
    config = factory()
    result = ProductionHandoff(config).prepare(*ARGS)
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    master_id = result["artifact_ids"]["audio_master"]
    if change == "rejected":
        store.record_approval(
            ApprovalDecision(
                target_id=master_id,
                target_kind="artifact",
                status="rejected",
                actor="reviewer",
                reason="hold",
                policy_version="fixture",
                decided_at=datetime.now(UTC),
            )
        )
    else:
        store.path_for(master_id).write_bytes(b"changed production copy")
    before = snapshot(config.database_path)
    with pytest.raises(ValueError, match="selected production artifact"):
        ProductionHandoff(config).prepare(*ARGS)
    assert snapshot(config.database_path) == before
