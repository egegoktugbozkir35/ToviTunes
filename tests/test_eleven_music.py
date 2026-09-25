"""Offline contract checks: no ElevenLabs transport is used."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest

from tovitunes.music.audio import inspect_audio
from tovitunes.music.benchmark import MusicBenchmark, plan
from tovitunes.music.models import load_brief, load_lyrics
from tovitunes.music.providers import ElevenMusicProvider
from tovitunes.persistence.db import Database

ROOT = Path(__file__).resolve().parents[1] / "benchmarks/music"


def inputs() -> tuple[Any, Any]:
    return load_brief(ROOT / "colors_red_v1.yaml"), load_lyrics(ROOT / "colors_red_lyrics_v1.yaml")


def mp3_bytes() -> bytes:
    # Two complete 48 kHz, 192 kbps MPEG-1 Layer III frames.
    return (bytes.fromhex("ff fB b4 00") + bytes(572)) * 2


def multipart(audio: bytes | None = None, *, duplicate: bool = False) -> bytes:
    metadata = {
        "composition_plan": {"chunks": [{"text": "[Intro]"}]},
        "song_metadata": {"title": "Red"},
        "words_timestamps": [{"text": "Red", "start": 0.1, "end": 0.3}],
    }
    header = b"--music\r\nContent-Type: application/json\r\n\r\n"
    part = b"--music\r\nContent-Type: audio/mpeg\r\n\r\n" + (audio or mp3_bytes()) + b"\r\n"
    return (
        header
        + json.dumps(metadata).encode()
        + b"\r\n"
        + part
        + (part if duplicate else b"")
        + b"--music--\r\n"
    )


def setup(
    tmp_path: Path, transport: httpx.BaseTransport
) -> tuple[MusicBenchmark, ElevenMusicProvider, Any]:
    database = Database(tmp_path / "music.sqlite")
    database.migrate()
    store = MusicBenchmark(database, tmp_path / "audio")
    provider = ElevenMusicProvider(httpx.Client(transport=transport))
    brief, lyrics = inputs()
    return store, provider, plan(brief, lyrics, [provider])[0]


def test_dry_run_plan_is_deterministic_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    brief, lyrics = inputs()
    provider = ElevenMusicProvider()
    first = plan(brief, lyrics, [provider])
    assert first == plan(brief, lyrics, [provider])
    assert len(first) == len({item.input_fingerprint for item in first}) == 3
    assert [item.attempt for item in first] == [1, 2, 3]
    assert [item.translated_request["body"]["seed"] for item in first] == [1001, 1002, 1003]
    body = first[0].translated_request["body"]
    assert body["model_id"] == "music_v2_5"
    assert "prompt" not in body and "music_length_ms" not in body
    assert body["with_timestamps"] is True
    chunks = body["composition_plan"]["chunks"]
    assert [chunk["duration_ms"] for chunk in chunks] == [3000, 9000, 10000, 9000, 3000]
    assert sum(chunk["duration_ms"] for chunk in chunks) == 34000
    assert (
        "\n".join(line for chunk in chunks for line in chunk["text"].splitlines()[1:])
        == lyrics.text()
    )
    assert first[0].translated_request["query"] == {"output_format": "mp3_48000_192"}
    assert first[0].capabilities["word_timestamps"] is True


def test_missing_key_repairs_same_attempt_without_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "multipart/mixed; boundary=music", "song-id": "song-1"},
            content=multipart(),
        )

    store, provider, item = setup(tmp_path, httpx.MockTransport(handler))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    first = store.run(item, provider)
    assert first["status"] == "retryable_failure" and calls == 0
    assert store.request(first["request_id"])["remote_started_at"] is None
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret-never-persist")
    second = store.run(item, provider)
    assert second["request_id"] == first["request_id"]
    assert second["status"] == "succeeded" and calls == 1


def test_remote_boundary_multipart_receipt_and_reconciliation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret-never-persist")
    calls = 0
    states: list[str] = []
    store: MusicBenchmark
    item: Any

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        row = store.prepare(item)
        states.append(row["status"])
        assert row["remote_started_at"] is not None
        assert request.method == "POST"
        assert str(request.url).startswith(ElevenMusicProvider.endpoint)
        assert json.loads(request.content)["with_timestamps"] is True
        return httpx.Response(
            200,
            headers={"content-type": "multipart/mixed; boundary=music", "song-id": "song-123"},
            content=multipart(),
        )

    store, provider, item = setup(tmp_path, httpx.MockTransport(handler))
    original_transition = store._transition
    remote_starts = 0

    def transition(request_id: str, status: str, **kwargs: Any) -> None:
        nonlocal remote_starts
        if status == "remote_started":
            remote_starts += 1
        original_transition(request_id, status, **kwargs)

    monkeypatch.setattr(store, "_transition", transition)
    result = store.run(item, provider)
    assert result["status"] == "succeeded" and states == ["remote_started"] and calls == 1
    assert remote_starts == 1
    row = store.request(result["request_id"])
    assert row["provider_request_id"] == "song-123"
    assert "test-secret-never-persist" not in json.dumps(row)
    with store.database.connect() as db:
        receipt = dict(db.execute("SELECT * FROM music_receipts").fetchone())
        output = dict(db.execute("SELECT * FROM music_outputs").fetchone())
    assert receipt["provider_request_id"] == "song-123"
    assert receipt["mime_type"] == "audio/mpeg"
    assert receipt["container"] == receipt["codec"] == "mp3"
    assert receipt["sha256"] == sha256(mp3_bytes()).hexdigest()
    assert receipt["byte_count"] == len(mp3_bytes())
    assert json.loads(receipt["response_metadata_json"])["words_timestamps"][0]["text"] == "Red"
    assert "test-secret-never-persist" not in json.dumps(receipt)
    assert output["relative_path"].endswith(".mp3")
    assert output["rights_status"] == "unknown" and output["approval_status"] == "pending"
    original = store.audio_root / output["relative_path"]
    assert original.read_bytes() == mp3_bytes()
    assert store.reconcile(result["request_id"])["status"] == "succeeded"
    assert store.run(item, provider)["action"] == "reused" and calls == 1
    assert original.read_bytes() == mp3_bytes()


@pytest.mark.parametrize(
    "code,status",
    [
        (401, "terminal_failure"),
        (422, "terminal_failure"),
        (429, "terminal_failure"),
        (408, "ambiguous"),
        (500, "ambiguous"),
        (503, "ambiguous"),
    ],
)
def test_http_failure_has_one_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int, status: str
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(code)

    store, provider, item = setup(tmp_path, httpx.MockTransport(handler))
    result = store.run(item, provider)
    assert result["status"] == status and calls == 1
    assert store.run(item, provider)["action"] == (
        "new_attempt_required" if status == "terminal_failure" else "manual_reconciliation_required"
    )
    assert calls == 1


def test_timeout_is_ambiguous_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("simulated timeout")

    store, provider, item = setup(tmp_path, httpx.MockTransport(handler))
    result = store.run(item, provider)
    assert result["status"] == "ambiguous" and calls == 1
    assert store.run(item, provider)["action"] == "manual_reconciliation_required"
    assert calls == 1


@pytest.mark.parametrize(
    "content", [b"not multipart", multipart(duplicate=True), multipart(b"bad")]
)
def test_malformed_success_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: bytes
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "multipart/mixed; boundary=music"}, content=content
        )
    )
    store, provider, item = setup(tmp_path, transport)
    result = store.run(item, provider)
    assert result["status"] == "ambiguous"
    with store.database.connect() as db:
        assert db.execute("SELECT count(*) FROM music_receipts").fetchone()[0] == 0


def test_mp3_validation_rejects_truncation() -> None:
    info = inspect_audio(mp3_bytes(), "audio/mpeg")
    assert info.container == "mp3" and info.duration_seconds == 2 * 1152 / 48000
    with pytest.raises(ValueError):
        inspect_audio(mp3_bytes()[:-1], "audio/mpeg")
    with pytest.raises(ValueError):
        inspect_audio(mp3_bytes(), "audio/wav")


def test_mp3_receipt_recovery_is_provider_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "multipart/mixed; boundary=music", "song-id": "song-2"},
            content=multipart(),
        )

    store, provider, item = setup(tmp_path, httpx.MockTransport(handler))
    finalize = store._finalize
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "_finalize",
            lambda request_id: (_ for _ in ()).throw(RuntimeError("interrupted")),
        )
        first = store.run(item, provider)
    assert first["action"] == "local_reconciliation_required" and calls == 1
    with store.database.connect() as db:
        assert db.execute("SELECT count(*) FROM music_receipts").fetchone()[0] == 1
    assert store._staged_path(first["request_id"], "mp3").read_bytes() == mp3_bytes()
    assert store._finalize.__func__ is finalize.__func__
    assert store.reconcile(first["request_id"])["status"] == "succeeded"
    assert store.reconcile(first["request_id"])["status"] == "succeeded"
    assert calls == 1


def test_mp3_staged_without_receipt_does_not_regenerate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "multipart/mixed; boundary=music"},
            content=multipart(),
        )

    store, provider, item = setup(tmp_path, httpx.MockTransport(handler))
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "_record_receipt",
            lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted")),
        )
        first = store.run(item, provider)
    assert first["action"] == "local_reconciliation_required" and calls == 1
    assert store._staged_path(first["request_id"], "mp3").read_bytes() == mp3_bytes()
    assert store.reconcile(first["request_id"])["action"] == "provider_side_reconciliation_required"
    assert store.run(item, provider)["action"] == "manual_reconciliation_required"
    assert calls == 1
