"""Offline ACE-Step REST and durable recovery contract tests."""

import io
import json
import wave
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from tovitunes.config import MusicGenerationConfig, load_config
from tovitunes.music.ace_step import AceStepLocalProvider
from tovitunes.music.audio import inspect_audio
from tovitunes.music.benchmark import MusicBenchmark, plan
from tovitunes.music.models import CanonicalMusicSpec, load_brief, load_lyrics
from tovitunes.music.providers import MusicFailure, MusicTaskPending
from tovitunes.persistence.db import Database


def _spec() -> CanonicalMusicSpec:
    brief = load_brief(Path("benchmarks/music/colors_red_v1.yaml"))
    lyrics = load_lyrics(Path("benchmarks/music/colors_red_lyrics_v1.yaml"))
    return CanonicalMusicSpec(brief=brief, lyrics=lyrics, attempt=1)


def _wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


def _envelope(data: object) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": data, "error": None})


def _result(task_id: str, *, file: str = "/v1/audio?path=%2Ftmp%2Ftest.wav") -> dict:
    return {
        "task_id": task_id,
        "status": 1,
        "result": json.dumps(
            [
                {
                    "file": file,
                    "status": 1,
                    "lyrics": _spec().lyrics.text(),
                    "dit_model": "acestep-v15-turbo",
                    "lm_model": "acestep-5Hz-lm-0.6B",
                    "seed_value": "123",
                    "metas": {"bpm": 120, "duration": 30},
                }
            ]
        ),
    }


def test_config_and_exact_mapping_without_service(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "database_path: data/x.db\ndata_root: data\nbrand_root: brands/tovitunes\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.music_generation.provider == "ace_step_local"
    provider = AceStepLocalProvider(config.music_generation)
    spec = _spec()
    request = provider.translate(spec)
    assert request["lyrics"] == spec.lyrics.text()
    assert request["prompt"].find(spec.brief.style) >= 0
    assert request["prompt"].find(spec.brief.arrangement) >= 0
    assert request["prompt"].find(spec.brief.vocal_direction) >= 0
    assert request["use_format"] is False
    assert request["sample_mode"] is False
    assert request["thinking"] is True
    assert request["task_type"] == "text2music"
    assert request["bpm"] == spec.brief.target_bpm
    assert request["audio_duration"] == sum(spec.brief.preferred_duration_seconds) // 2
    assert request["batch_size"] == 1
    assert request["use_random_seed"] is False
    assert request == provider.translate(spec)
    assert provider.translate(spec.model_copy(update={"attempt": 2}))["seed"] != request["seed"]
    with pytest.raises(ValidationError):
        MusicGenerationConfig(base_url="https://example.com")
    with pytest.raises(ValidationError):
        MusicGenerationConfig(batch_size=2)


def test_health_and_absent_service_fail_before_submission() -> None:
    calls: list[str] = []

    def absent(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("offline")

    provider = AceStepLocalProvider(transport=httpx.MockTransport(absent))
    assert provider.health()["status"] == "unavailable"
    with pytest.raises(MusicFailure) as failure:
        provider.generate(_spec(), provider.translate(_spec()), lambda: calls.append("start"))
    assert failure.value.outcome == "retryable_failure"
    assert calls == ["/health", "/health"]
    invalid = AceStepLocalProvider(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    )
    assert invalid.health()["status"] == "misconfigured"


def test_release_identity_is_persisted_before_query_and_audio_is_checked() -> None:
    events: list[str] = []
    wav = _wav()

    def handle(request: httpx.Request) -> httpx.Response:
        events.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        if request.url.path == "/release_task":
            body = json.loads(request.content)
            assert body["lyrics"] == _spec().lyrics.text()
            return _envelope({"task_id": "task-1", "status": "queued"})
        if request.url.path == "/query_result":
            assert events[-2] == "identity"
            assert json.loads(request.content) == {"task_id_list": ["task-1"]}
            return _envelope([_result("task-1")])
        assert request.url.path == "/v1/audio"
        return httpx.Response(200, content=wav)

    provider = AceStepLocalProvider(transport=httpx.MockTransport(handle))
    spec = _spec()
    result = provider.generate_with_identity(
        spec,
        provider.translate(spec),
        lambda: events.append("start"),
        lambda task_id: events.append("identity"),
    )
    assert events == ["/health", "start", "/release_task", "identity", "/query_result", "/v1/audio"]
    assert result.provider_request_id == "task-1"
    assert result.audio_bytes == wav
    assert result.rights_evidence["commercial_clearance"] == "review_required"
    assert result.response_metadata["audio_sha256"]
    assert inspect_audio(result.audio_bytes, "audio/wav").container == "wav"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(0, "retryable_failure"), (2, "terminal_failure")],
)
def test_retrieve_running_and_failed_never_submit(status: int, expected: str) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _envelope([{"task_id": "known", "status": status}])

    provider = AceStepLocalProvider(transport=httpx.MockTransport(handle))
    with pytest.raises(MusicFailure) as failure:
        provider.retrieve("known", provider.translate(_spec()))
    assert failure.value.outcome == expected
    if status == 0:
        assert isinstance(failure.value, MusicTaskPending)
    assert calls == ["/query_result"]


@pytest.mark.parametrize(
    "task",
    [
        {"task_id": "known", "status": 1, "result": "not-json"},
        {"task_id": "known", "status": 1, "result": "[]"},
        _result("known", file="https://evil.example/v1/audio?path=x"),
        _result("known", file="/other?path=x"),
    ],
)
def test_malformed_result_and_external_audio_are_rejected(task: dict) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _envelope([task])

    provider = AceStepLocalProvider(transport=httpx.MockTransport(handle))
    with pytest.raises(MusicFailure) as failure:
        provider.retrieve("known", provider.translate(_spec()))
    assert failure.value.outcome == "terminal_failure"
    assert calls == ["/query_result"]


def test_timeout_and_transport_failure_keep_known_identity() -> None:
    calls: list[str] = []
    ticks = iter([0.0, 2.0])

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        if request.url.path == "/release_task":
            return _envelope({"task_id": "known"})
        raise httpx.ReadTimeout("lost")

    provider = AceStepLocalProvider(
        MusicGenerationConfig(timeout_seconds=1),
        transport=httpx.MockTransport(handle),
        clock=lambda: next(ticks),
    )
    with pytest.raises(MusicFailure) as failure:
        provider.generate_with_identity(
            _spec(), provider.translate(_spec()), lambda: None, lambda _id: None
        )
    assert failure.value.outcome == "ambiguous"
    assert failure.value.provider_request_id == "known"
    assert calls.count("/release_task") == 1


def test_connection_failure_at_release_has_no_task_id() -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        raise httpx.ConnectError("refused")

    provider = AceStepLocalProvider(transport=httpx.MockTransport(handle))
    events: list[str] = []
    with pytest.raises(MusicFailure) as failure:
        provider.generate_with_identity(
            _spec(),
            provider.translate(_spec()),
            lambda: events.append("start"),
            lambda _id: events.append("identity"),
        )
    assert failure.value.outcome == "retryable_failure"
    assert failure.value.provider_request_id is None
    assert events == ["start"]
    assert calls == ["/health", "/release_task"]


def test_benchmark_resume_uses_existing_task_only(tmp_path: Path) -> None:
    calls: list[str] = []
    queries = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal queries
        calls.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        if request.url.path == "/release_task":
            return _envelope({"task_id": "known"})
        if request.url.path == "/query_result":
            queries += 1
            if queries <= 2:
                return _envelope([{"task_id": "known", "status": 0}])
            return _envelope([_result("known")])
        assert request.url.path == "/v1/audio"
        return httpx.Response(200, content=_wav())

    provider = AceStepLocalProvider(
        MusicGenerationConfig(timeout_seconds=1, poll_interval_seconds=1),
        transport=httpx.MockTransport(handle),
        clock=iter([0.0, 2.0]).__next__,
    )
    db = Database(tmp_path / "music.db")
    db.migrate()
    benchmark = MusicBenchmark(db, tmp_path / "audio")
    spec = _spec()
    planned = plan(spec.brief, spec.lyrics, [provider], attempt=1)[0]
    initial = benchmark.run(planned, provider)
    assert initial["status"] == "ambiguous"
    row = benchmark.request(initial["request_id"])
    assert row["provider_request_id"] == "known"
    durable_status = row["status"]
    for _ in range(2):
        calls_before_resume = len(calls)
        resumed = benchmark.provider_resume(initial["request_id"], provider)
        assert resumed == {
            "request_id": initial["request_id"],
            "status": durable_status,
            "action": "existing_interaction_pending",
        }
        stored = benchmark.request(initial["request_id"])
        assert stored["status"] == durable_status
        assert stored["provider_request_id"] == "known"
        assert calls[calls_before_resume:] == ["/query_result"]
    completed = benchmark.provider_resume(initial["request_id"], provider)
    assert completed["status"] == "succeeded"
    assert completed["request_id"] == initial["request_id"]
    assert benchmark.request(initial["request_id"])["provider_request_id"] == "known"
    assert calls.count("/release_task") == 1


def test_genuine_retryable_resume_failure_remains_local_preflight(tmp_path: Path) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        if request.url.path == "/release_task":
            return _envelope({"task_id": "known"})
        raise httpx.ReadTimeout("query unavailable")

    provider = AceStepLocalProvider(transport=httpx.MockTransport(handle))
    db = Database(tmp_path / "music.db")
    db.migrate()
    benchmark = MusicBenchmark(db, tmp_path / "audio")
    spec = _spec()
    planned = plan(spec.brief, spec.lyrics, [provider], attempt=1)[0]
    initial = benchmark.run(planned, provider)
    assert initial["status"] == "ambiguous"

    class RetryableLocalProvider(AceStepLocalProvider):
        def retrieve(self, task_id, translated_request):  # type: ignore[no-untyped-def]
            raise MusicFailure("local client preflight unavailable", "retryable_failure", task_id)

    local_failure = RetryableLocalProvider(transport=httpx.MockTransport(handle))
    resumed = benchmark.provider_resume(initial["request_id"], local_failure)
    assert resumed["action"] == "local_preflight_failure"
    assert benchmark.request(initial["request_id"])["provider_request_id"] == "known"
    assert calls.count("/release_task") == 1


def test_benchmark_resume_finishes_known_task_without_release(tmp_path: Path) -> None:
    calls: list[str] = []
    queries = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal queries
        calls.append(request.url.path)
        if request.url.path == "/health":
            return _envelope({"status": "ok"})
        if request.url.path == "/release_task":
            return _envelope({"task_id": "known"})
        if request.url.path == "/query_result":
            queries += 1
            if queries == 1:
                raise httpx.ReadTimeout("query lost")
            return _envelope([_result("known")])
        assert request.url.path == "/v1/audio"
        return httpx.Response(200, content=_wav())

    provider = AceStepLocalProvider(transport=httpx.MockTransport(handle))
    db = Database(tmp_path / "music.db")
    db.migrate()
    benchmark = MusicBenchmark(db, tmp_path / "audio")
    spec = _spec()
    planned = plan(spec.brief, spec.lyrics, [provider], attempt=1)[0]
    initial = benchmark.run(planned, provider)
    assert initial["status"] == "ambiguous"
    assert benchmark.request(initial["request_id"])["provider_request_id"] == "known"
    completed = benchmark.provider_resume(initial["request_id"], provider)
    assert completed["status"] == "succeeded"
    assert calls.count("/release_task") == 1
    row = next(row for row in benchmark.status() if row["request_id"] == initial["request_id"])
    assert row["rights_status"] == "unknown"
    assert row["approval_status"] == "pending"
