"""Explicit recovery of legacy WAV rejection; never resubmit a retained song task."""

import io
import json
import sqlite3
import wave
from contextlib import closing
from hashlib import sha256

import httpx
import pytest
import test_short_production
import yaml
from test_ace_step import _envelope, _result, _spec, _wav
from test_audio_wav import float_wav

from tovitunes.cli import main
from tovitunes.errors import ExecutionOwnershipLostError
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.music.ace_step import AceStepLocalProvider
from tovitunes.music.benchmark import MusicBenchmark, plan
from tovitunes.persistence.db import Database
from tovitunes.pipeline.targets import ProductionTarget

case = test_short_production.case


def legacy_inspection(data, mime_type):
    try:
        with wave.open(io.BytesIO(data), "rb"):
            pytest.fail("fixture must reproduce IEEE-float rejection on Python 3.11")
    except wave.Error as exc:
        raise ValueError("invalid WAV") from exc


@pytest.fixture
def rejected(tmp_path, monkeypatch):
    state = {"audio": float_wav()}
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        if request.url.path == "/release_task":
            return _envelope({"task_id": "retained-float-task"})
        if request.url.path == "/query_result":
            assert json.loads(request.content) == {"task_id_list": ["retained-float-task"]}
            return _envelope([_result("retained-float-task")])
        assert request.url.path == "/v1/audio"
        return httpx.Response(200, content=state["audio"])

    provider = AceStepLocalProvider(transport=httpx.MockTransport(handle))
    database = Database(tmp_path / "state.db")
    database.migrate()
    benchmark = MusicBenchmark(database, tmp_path / "audio")
    item = plan(_spec().brief, _spec().lyrics, [provider], attempt=1)[0]
    with monkeypatch.context() as scoped:
        scoped.setattr("tovitunes.music.ace_step.inspect_audio", legacy_inspection)
        result = benchmark.run(item, provider)
    assert result["status"] == "terminal_failure"
    return benchmark, provider, state, calls, benchmark.request(result["request_id"]), item


def recover(benchmark, provider, row):
    return benchmark.reconcile_ace_step_float_wav(
        row["request_id"], provider, actor="fixture-operator", rationale="IEEE-float compatibility"
    )


def test_recovery_retains_failure_evidence_and_original_audio_and_is_idempotent(rejected):
    benchmark, provider, state, calls, before, item = rejected
    boundary = len(calls)
    assert benchmark.run(item, provider)["action"] == "new_attempt_required"
    assert (
        benchmark.provider_resume(before["request_id"], provider)["action"]
        == "new_attempt_required"
    )
    assert len(calls) == boundary
    result = recover(benchmark, provider, before)
    assert result["status"] == "succeeded" and result["request_id"] == before["request_id"]
    assert calls[boundary:] == ["/query_result", "/v1/audio"]
    assert calls.count("/release_task") == 1
    with closing(benchmark.database.connect()) as db:
        audit = dict(db.execute("SELECT * FROM music_wav_reconciliations").fetchone())
        receipt = dict(db.execute("SELECT * FROM music_receipts").fetchone())
        output = dict(db.execute("SELECT * FROM music_outputs").fetchone())
        assert json.loads(audit["prior_request_json"]) == before
        assert audit["reconciliation_id"] == result["reconciliation_id"]
        assert audit["actor"] == "fixture-operator"
        assert audit["audio_sha256"] == receipt["sha256"] == sha256(state["audio"]).hexdigest()
        assert (
            audit["provider_request_id"] == receipt["provider_request_id"] == "retained-float-task"
        )
        assert receipt["codec"] == "pcm_f32le" and receipt["byte_count"] == len(state["audio"])
        metadata = json.loads(receipt["response_metadata_json"])
        assert metadata["audio_sample_format"] == "float32"
        assert metadata["audio_channel_count"] == 1 and metadata["audio_frame_count"] == 8000
        assert db.execute("SELECT count(*) FROM music_requests").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE music_wav_reconciliations SET actor='changed'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM music_wav_reconciliations")
    after = benchmark.request(before["request_id"])
    assert after["status"] == "succeeded"
    assert all(
        after[key] == value for key, value in before.items() if key not in {"status", "updated_at"}
    )
    assert (benchmark.audio_root / output["relative_path"]).read_bytes() == state["audio"]
    boundary = len(calls)
    assert recover(benchmark, provider, before)["blind_id"] == result["blind_id"]
    assert len(calls) == boundary


@pytest.mark.parametrize(
    "changes",
    [
        {"failure_reason": "ACE-Step task failed"},
        {"failure_reason": "ACE-Step returned altered lyrics"},
        {"failure_category": "unexpected"},
        {"provider": "google"},
        {"model": "other-model"},
        {"provider_request_id": None},
        {"remote_started_at": None},
        {"status": "ambiguous"},
    ],
)
def test_unrelated_or_unidentified_failure_stays_blocked(rejected, changes):
    benchmark, provider, _, calls, before, _ = rejected
    with closing(benchmark.database.connect()) as db, db:
        for key, value in changes.items():
            db.execute(f"UPDATE music_requests SET {key}=?", (value,))
    before = benchmark.request(before["request_id"])
    boundary = len(calls)
    with pytest.raises(ValueError):
        recover(benchmark, provider, before)
    assert len(calls) == boundary and benchmark.request(before["request_id"]) == before
    with closing(benchmark.database.connect()) as db:
        assert db.execute("SELECT count(*) FROM music_receipts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM music_wav_reconciliations").fetchone()[0] == 0


@pytest.mark.parametrize(
    "bad_audio", [b"not WAV", float_wav()[:-1], _wav()], ids=["malformed", "truncated", "integer"]
)
def test_retrieved_malformed_truncated_or_integer_audio_cannot_reconcile(rejected, bad_audio):
    benchmark, provider, state, _, before, _ = rejected
    state["audio"] = bad_audio
    if bad_audio == _wav():
        with pytest.raises(ValueError, match="IEEE-float"):
            recover(benchmark, provider, before)
    else:
        assert recover(benchmark, provider, before)["action"] == "reconciliation_not_complete"
    assert benchmark.request(before["request_id"]) == before
    with closing(benchmark.database.connect()) as db:
        assert db.execute("SELECT count(*) FROM music_receipts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM music_wav_reconciliations").fetchone()[0] == 0


def test_receipt_and_failure_audit_are_atomic_and_staged_bytes_can_be_reused(rejected):
    benchmark, provider, state, calls, before, _ = rejected
    with closing(benchmark.database.connect()) as db, db:
        db.execute(
            "CREATE TRIGGER reject_recovery BEFORE INSERT ON music_wav_reconciliations "
            "BEGIN SELECT RAISE(ABORT, 'fixture audit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit failure"):
        recover(benchmark, provider, before)
    with closing(benchmark.database.connect()) as db, db:
        assert db.execute("SELECT count(*) FROM music_receipts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM music_wav_reconciliations").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_recovery")
    assert benchmark.request(before["request_id"]) == before
    assert benchmark._staged_path(before["request_id"]).read_bytes() == state["audio"]
    assert recover(benchmark, provider, before)["status"] == "succeeded"
    assert calls.count("/release_task") == 1


def test_lost_owner_keeps_verified_receipt_and_audit_for_provider_free_finalization(rejected):
    benchmark, provider, _, calls, before, _ = rejected
    checks = 0

    def owner():
        nonlocal checks
        checks += 1
        if checks > 1:
            raise ExecutionOwnershipLostError("fixture lease loss after receipt retention")

    benchmark.assert_owner = owner
    with pytest.raises(ExecutionOwnershipLostError):
        recover(benchmark, provider, before)
    boundary = len(calls)
    benchmark.assert_owner = lambda: None
    assert recover(benchmark, provider, before)["status"] == "succeeded"
    assert len(calls) == boundary and calls.count("/release_task") == 1


def test_changed_canonical_fingerprint_stays_blocked_without_query(rejected):
    benchmark, provider, _, calls, before, _ = rejected
    with closing(benchmark.database.connect()) as db, db:
        db.execute("UPDATE music_requests SET input_fingerprint=?", ("changed",))
    before = benchmark.request(before["request_id"])
    boundary = len(calls)
    with pytest.raises(ValueError, match="inputs or provider configuration changed"):
        recover(benchmark, provider, before)
    assert len(calls) == boundary and benchmark.request(before["request_id"]) == before


def test_cli_reconciles_same_episode_task_then_render_reuses_float_receipt(
    case, tmp_path, monkeypatch, capsys
):
    episode, flow, database = case["episode"], case["flow"], case["store"].database
    audio = float_wav(frames=441000, rate=44100, channels=2)
    # Use the real ACE-Step adapter with only its transport replaced by offline responses.
    events = case["music_events"]

    def handle(request):
        events.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        if request.url.path == "/release_task":
            return _envelope({"task_id": "original-episode-float-task"})
        if request.url.path == "/query_result":
            assert json.loads(request.content) == {"task_id_list": ["original-episode-float-task"]}
            result = _result("original-episode-float-task")
            output = json.loads(result["result"])[0]
            output.pop("lyrics")  # Production fixture uses this episode's canonical lyrics.
            result["result"] = json.dumps([output])
            return _envelope([result])
        assert request.url.path == "/v1/audio"
        return httpx.Response(200, content=audio)

    provider = case["music"]
    monkeypatch.setattr(provider, "_transport", httpx.MockTransport(handle))
    with monkeypatch.context() as scoped:
        scoped.setattr("tovitunes.music.ace_step.inspect_audio", legacy_inspection)
        failed = flow.resume(episode.external_key, ProductionTarget.RENDER)
    assert failed["status"] == "FAILED" and failed["current_stage"] == "MUSIC"
    with closing(database.connect()) as db:
        binding = dict(db.execute("SELECT * FROM episode_music_bindings").fetchone())
        before = dict(db.execute("SELECT * FROM music_requests").fetchone())
        assert db.execute("SELECT count(*) FROM music_receipts").fetchone()[0] == 0
    assert (
        before["status"] == "terminal_failure"
        and before["provider_request_id"] == "original-episode-float-task"
    )
    assert flow.resume(episode.external_key, ProductionTarget.RENDER)["status"] == "FAILED"
    assert events.count("/release_task") == 1
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(case["config"].model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setattr("tovitunes.cli.AceStepLocalProvider", lambda config: provider)
    command = [
        "--config",
        str(config_path),
        "music-benchmark",
        "reconcile-ace-step-float-wav",
        "--request-id",
        before["request_id"],
        "--actor",
        "fixture-operator",
        "--rationale",
        "Verified legacy IEEE-float WAV compatibility rejection",
    ]
    boundary = len(events)
    wrong_endpoint = AceStepLocalProvider(
        provider.config.model_copy(update={"base_url": "http://127.0.0.1:19999"}),
        transport=httpx.MockTransport(handle),
    )
    benchmark = MusicBenchmark(database, case["config"].data_root / "music-benchmark")
    with pytest.raises(ValueError, match="endpoint differs"):
        recover(benchmark, wrong_endpoint, before)
    with ProductionExecutionOwnership(database, operation="fixture-other-production"):
        with pytest.raises(SystemExit) as busy:
            main(command)
        assert busy.value.code == 2
    assert len(events) == boundary and benchmark.request(before["request_id"]) == before
    assert main(command) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["status"] == "succeeded" and recovered["request_id"] == before["request_id"]
    assert events[boundary:] == ["/query_result", "/v1/audio"]
    with closing(database.connect()) as db:
        receipt = dict(db.execute("SELECT * FROM music_receipts").fetchone())
        assert receipt["sha256"] == sha256(audio).hexdigest() and receipt["codec"] == "pcm_f32le"
        assert (
            json.loads(
                db.execute("SELECT prior_request_json FROM music_wav_reconciliations").fetchone()[0]
            )
            == before
        )
    boundary = len(events)
    rendered = flow.resume(episode.external_key, ProductionTarget.RENDER)
    assert rendered["status"] == "COMPLETE", rendered
    assert rendered["episode_key"] == episode.external_key
    assert len(events) == boundary  # Music came exclusively from the retained receipt.
    assert case["analysis_calls"] and case["analysis_calls"][0].read_bytes() == audio
    assert case["store"].path_for(rendered["final_render_id"]).read_bytes()[4:8] == b"ftyp"
    with closing(database.connect()) as db:
        assert db.execute("SELECT count(*) FROM music_requests").fetchone()[0] == 1
        assert dict(db.execute("SELECT * FROM episode_music_bindings").fetchone()) == binding
        assert dict(db.execute("SELECT * FROM music_receipts").fetchone()) == receipt
        report = json.loads(
            db.execute("SELECT analysis_json FROM music_audio_analysis").fetchone()[0]
        )
        assert report["audio_sha256"] == receipt["sha256"] and report["channel_count"] == 2
        assert report["technical_metrics"]["decoded_frames"] == 441000
        assert db.execute("SELECT count(*) FROM publication_attempts").fetchone()[0] == 0
    assert events.count("/release_task") == 1
