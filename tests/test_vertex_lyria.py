"""Offline Vertex Lyria contract and durable recovery tests."""

import base64
import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest

from tovitunes.music.audio import inspect_audio
from tovitunes.music.benchmark import MusicBenchmark, plan
from tovitunes.music.models import load_brief, load_lyrics
from tovitunes.music.providers import FakeMusicProvider, MusicResult
from tovitunes.music.vertex_lyria import VertexLyriaProvider
from tovitunes.persistence.db import Database

ROOT = Path(__file__).resolve().parents[1]
TONE = ROOT / "tests/fixtures/lyria_tone.mp3"
TOKEN = "offline-oauth-secret-unique-9348"


class FakeCredentials:
    def __init__(self, *, valid: bool = True, refresh_fails: bool = False) -> None:
        self.valid = valid
        self.token = TOKEN if valid else None
        self.refresh_fails = refresh_fails
        self.refreshes = 0

    def refresh(self, request: object) -> None:
        self.refreshes += 1
        if self.refresh_fails:
            raise RuntimeError("offline refresh failure")
        self.valid = True
        self.token = TOKEN


def inputs() -> tuple[Any, Any]:
    folder = ROOT / "benchmarks/music"
    return load_brief(folder / "colors_red_v1.yaml"), load_lyrics(
        folder / "colors_red_lyrics_v1.yaml"
    )


def interaction(
    *,
    status: str = "completed",
    audio: bytes | None = None,
    audio_count: int = 1,
    model: str = "lyria-3-pro-preview",
    interaction_id: str = "vertex-interaction-123",
) -> dict[str, Any]:
    payload = TONE.read_bytes() if audio is None else audio
    return {
        "id": interaction_id,
        "status": status,
        "model": model,
        "outputs": [
            {"type": "text", "text": "Red, red, look ahead! [provider evidence]"},
            {"type": "text", "text": "A bright preschool pop track."},
            *[
                {
                    "type": "audio",
                    "mime_type": "audio/mpeg",
                    "data": base64.b64encode(payload).decode("ascii"),
                }
                for _ in range(audio_count)
            ],
            {},
        ],
        "usage": {"total_tokens": 12},
    }


def setup(
    tmp_path: Path,
    handler: Any,
    *,
    credentials: FakeCredentials | None = None,
) -> tuple[MusicBenchmark, VertexLyriaProvider, Any]:
    database = Database(tmp_path / "music.sqlite")
    database.migrate()
    store = MusicBenchmark(database, tmp_path / "audio")
    credentials = credentials or FakeCredentials()
    provider = VertexLyriaProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        credentials_loader=lambda: credentials,  # type: ignore[arg-type]
    )
    brief, lyrics = inputs()
    return store, provider, plan(brief, lyrics, [provider])[0]


@pytest.fixture(autouse=True)
def vertex_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tovitunes")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_dry_run_has_three_deterministic_plans_without_adc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    provider = VertexLyriaProvider(
        credentials_loader=lambda: (_ for _ in ()).throw(AssertionError("ADC called"))
    )
    brief, lyrics = inputs()
    first = plan(brief, lyrics, [provider])
    assert first == plan(brief, lyrics, [provider])
    assert len(first) == len({item.input_fingerprint for item in first}) == 3
    assert all(item.provider == "google" and item.model == "lyria-3-pro-preview" for item in first)
    request = first[0].translated_request
    assert request["backend"] == "vertex_ai_interactions"
    assert request["project"] == "{GOOGLE_CLOUD_PROJECT}"
    assert request["location"] == "global"
    assert request["endpoint"].endswith("/locations/global/interactions")
    assert set(request["body"]) == {"model", "input", "store"}
    assert request["body"]["store"] is True
    assert len(request["body"]["input"]) == 1
    prompt = request["body"]["input"][0]["text"]
    assert request["body"]["input"][0]["type"] == "text"
    assert "approximately 34 seconds" in prompt and "112 BPM" in prompt
    assert "Lyrics:\n" + lyrics.text() in prompt
    assert "0:31–0:34 short ending" in prompt
    assert "named artist imitation" in prompt
    assert first[0].capabilities["word_timestamps"] is False


@pytest.mark.parametrize(
    "reason", ["missing_project", "wrong_location", "missing_adc", "refresh_failure"]
)
def test_preflight_failure_has_zero_generation_posts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reason: str
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=interaction())

    credentials = FakeCredentials(valid=False, refresh_fails=reason == "refresh_failure")
    store, provider, item = setup(tmp_path, handler, credentials=credentials)
    if reason == "missing_project":
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    elif reason == "wrong_location":
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    elif reason == "missing_adc":
        provider._credentials_loader = lambda: (_ for _ in ()).throw(RuntimeError("no ADC"))
    first = store.run(item, provider)
    assert first["status"] == "retryable_failure" and calls == []
    assert store.request(first["request_id"])["remote_started_at"] is None


def test_credentials_repair_reuses_same_request(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=interaction())

    store, provider, item = setup(tmp_path, handler)
    provider._credentials_loader = lambda: (_ for _ in ()).throw(RuntimeError("no ADC"))
    first = store.run(item, provider)
    assert first["status"] == "retryable_failure" and calls == 0
    provider._credentials_loader = lambda: FakeCredentials()  # type: ignore[assignment]
    second = store.run(item, provider)
    assert second["request_id"] == first["request_id"]
    assert second["status"] == "succeeded" and calls == 1


def test_completed_post_boundary_receipt_and_evidence(tmp_path: Path) -> None:
    posts: list[str] = []
    store: MusicBenchmark
    item: Any

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.method)
        row = store.prepare(item)
        assert row["status"] == "remote_started" and row["remote_started_at"]
        assert request.method == "POST"
        assert str(request.url) == (
            "https://aiplatform.googleapis.com/v1beta1/projects/tovitunes/"
            "locations/global/interactions"
        )
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert set(json.loads(request.content)) == {"model", "input", "store"}
        return httpx.Response(200, json=interaction())

    store, provider, item = setup(tmp_path, handler)
    result = store.run(item, provider)
    assert result["status"] == "succeeded" and posts == ["POST"]
    row = store.request(result["request_id"])
    assert row["provider_request_id"] == "vertex-interaction-123"
    assert row["request_id"] != row["provider_request_id"]
    with store.database.connect() as db:
        receipt = dict(db.execute("SELECT * FROM music_receipts").fetchone())
        output = dict(db.execute("SELECT * FROM music_outputs").fetchone())
    assert receipt["sha256"] == sha256(TONE.read_bytes()).hexdigest()
    assert receipt["byte_count"] == len(TONE.read_bytes())
    assert receipt["mime_type"] == "audio/mpeg"
    assert receipt["container"] == receipt["codec"] == "mp3"
    assert receipt["usage_json"] == json.dumps({"total_tokens": 12})
    assert receipt["actual_cost_amount"] is None
    metadata = json.loads(receipt["response_metadata_json"])
    assert metadata["backend"] == "vertex_ai_interactions"
    assert metadata["provider_lyrics_text"].startswith("Red, red")
    assert metadata["provider_description_text"] == "A bright preschool pop track."
    assert metadata["audio_sample_rate_hz"] == 44100
    assert 190000 <= metadata["audio_bitrate_bps"] <= 194000
    assert output["relative_path"] == result["request_id"] + ".mp3"
    assert output["rights_status"] == "unknown"
    assert output["approval_status"] == "pending"
    assert (store.audio_root / output["relative_path"]).read_bytes() == TONE.read_bytes()
    assert TOKEN not in json.dumps(row) + json.dumps(receipt)
    assert store.run(item, provider)["action"] == "reused" and posts == ["POST"]


@pytest.mark.parametrize(
    "code,expected",
    [
        (400, "terminal_failure"),
        (401, "terminal_failure"),
        (422, "terminal_failure"),
        (408, "ambiguous"),
        (429, "ambiguous"),
        (500, "ambiguous"),
        (503, "ambiguous"),
    ],
)
def test_http_post_failure_never_retries(tmp_path: Path, code: int, expected: str) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        posts += 1
        return httpx.Response(code)

    store, provider, item = setup(tmp_path, handler)
    result = store.run(item, provider)
    assert result["status"] == expected and posts == 1
    assert store.run(item, provider)["status"] == expected and posts == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("timeout"), httpx.ConnectError("lost")])
def test_post_transport_loss_is_ambiguous(tmp_path: Path, error: httpx.RequestError) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise error

    store, provider, item = setup(tmp_path, handler)
    result = store.run(item, provider)
    assert result["status"] == "ambiguous" and calls == 1
    assert store.run(item, provider)["action"] == "manual_reconciliation_required"
    assert calls == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"bad JSON"),
        httpx.Response(200, json=interaction(audio_count=0)),
        httpx.Response(200, json=interaction(audio_count=2)),
        httpx.Response(200, json=interaction(audio=b"not an MP3")),
        httpx.Response(200, json=interaction(model="other-model")),
    ],
)
def test_malformed_completed_result_fails_closed(tmp_path: Path, response: httpx.Response) -> None:
    store, provider, item = setup(tmp_path, lambda request: response)
    result = store.run(item, provider)
    assert result["status"] == "ambiguous"
    with store.database.connect() as db:
        assert db.execute("SELECT count(*) FROM music_receipts").fetchone()[0] == 0


def test_invalid_base64_and_mime_fail_closed(tmp_path: Path) -> None:
    for change in ("base64", "mime"):
        payload = interaction()
        audio_part = payload["outputs"][2]
        if change == "base64":
            audio_part["data"] = "%%%"
        else:
            audio_part["mime_type"] = "audio/wav"
        case = tmp_path / change
        store, provider, item = setup(case, lambda request: httpx.Response(200, json=payload))
        assert store.run(item, provider)["status"] == "ambiguous"


def test_step_content_audio_is_accepted(tmp_path: Path) -> None:
    payload = interaction()
    payload["steps"] = [{"type": "model_output", "content": payload.pop("outputs")}]
    store, provider, item = setup(tmp_path, lambda request: httpx.Response(200, json=payload))
    assert store.run(item, provider)["status"] == "succeeded"


def test_missing_interaction_id_cannot_trigger_resume_get(tmp_path: Path) -> None:
    methods: list[str] = []
    payload = interaction()
    del payload["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json=payload)

    store, provider, item = setup(tmp_path, handler)
    first = store.run(item, provider)
    assert first["status"] == "ambiguous"
    assert store.request(first["request_id"])["provider_request_id"] is None
    assert store.provider_resume(first["request_id"], provider)["action"] == (
        "known_provider_identity_required"
    )
    assert methods == ["POST"]


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "in_progress",
        "requires_action",
        "failed",
        "cancelled",
        "incomplete",
        "budget_exceeded",
    ],
)
def test_interaction_lifecycle_statuses(tmp_path: Path, status: str) -> None:
    store, provider, item = setup(
        tmp_path, lambda request: httpx.Response(200, json=interaction(status=status))
    )
    result = store.run(item, provider)
    pending = {"queued", "in_progress", "requires_action"}
    assert result["status"] == ("ambiguous" if status in pending else "terminal_failure")
    assert store.request(result["request_id"])["provider_request_id"] == "vertex-interaction-123"


def test_queued_post_resumes_with_gets_until_completed(tmp_path: Path) -> None:
    methods: list[str] = []
    get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json=interaction(status="queued"))
        assert request.method == "GET"
        assert request.url.path.endswith("/interactions/vertex-interaction-123")
        get_count += 1
        return httpx.Response(
            200, json=interaction(status="queued" if get_count < 3 else "completed")
        )

    store, provider, item = setup(tmp_path, handler)
    initial = store.run(item, provider)
    request_id = initial["request_id"]
    assert initial["status"] == "ambiguous"
    assert store.request(request_id)["provider_request_id"] == "vertex-interaction-123"
    assert store.run(item, provider)["action"] == "manual_reconciliation_required"
    assert methods == ["POST"]

    for expected_methods in (["POST", "GET"], ["POST", "GET", "GET"]):
        pending = store.provider_resume(request_id, provider)
        assert pending["status"] == "ambiguous"
        assert store.request(request_id)["provider_request_id"] == "vertex-interaction-123"
        assert methods == expected_methods
        assert store.run(item, provider)["action"] == "manual_reconciliation_required"
        assert methods == expected_methods

    completed = store.provider_resume(request_id, provider)
    assert completed["status"] == "succeeded"
    assert methods == ["POST", "GET", "GET", "GET"]
    assert methods.count("POST") == 1
    assert (store.audio_root / f"{request_id}.mp3").read_bytes() == TONE.read_bytes()
    assert store.run(item, provider)["action"] == "reused"
    assert methods.count("POST") == 1


def test_budget_exceeded_post_is_terminal_without_retry(tmp_path: Path) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json=interaction(status="budget_exceeded"))

    store, provider, item = setup(tmp_path, handler)
    first = store.run(item, provider)
    request_id = first["request_id"]
    assert first["status"] == "terminal_failure"
    row = store.request(request_id)
    assert row["provider_request_id"] == "vertex-interaction-123"
    assert row["failure_category"] == "provider"
    assert row["failure_reason"] == "Vertex interaction budget_exceeded"
    assert store.run(item, provider)["action"] == "new_attempt_required"
    assert store.provider_resume(request_id, provider)["action"] == "new_attempt_required"
    assert methods == ["POST"]


def test_budget_exceeded_get_transitions_pending_request_to_terminal(tmp_path: Path) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.method in {"POST", "GET"}
        if request.method == "GET":
            assert request.url.path.endswith("/interactions/vertex-interaction-123")
        status = "queued" if request.method == "POST" else "budget_exceeded"
        return httpx.Response(200, json=interaction(status=status))

    store, provider, item = setup(tmp_path, handler)
    pending = store.run(item, provider)
    request_id = pending["request_id"]
    assert pending["status"] == "ambiguous"
    terminal = store.provider_resume(request_id, provider)
    assert terminal["status"] == "terminal_failure"
    row = store.request(request_id)
    assert row["status"] == "terminal_failure"
    assert row["provider_request_id"] == "vertex-interaction-123"
    assert row["failure_category"] == "provider_retrieval"
    assert row["failure_reason"] == "Vertex interaction budget_exceeded"
    assert store.run(item, provider)["action"] == "new_attempt_required"
    assert store.provider_resume(request_id, provider)["action"] == "new_attempt_required"
    assert methods == ["POST", "GET"]


def test_in_progress_get_resume_never_posts_again(tmp_path: Path) -> None:
    methods: list[str] = []
    get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json=interaction(status="in_progress"))
        assert request.url.path.endswith("/interactions/vertex-interaction-123")
        get_count += 1
        status = "in_progress" if get_count == 1 else "completed"
        return httpx.Response(200, json=interaction(status=status))

    store, provider, item = setup(tmp_path, handler)
    initial = store.run(item, provider)
    assert initial["status"] == "ambiguous"
    assert store.provider_resume(initial["request_id"], provider)["status"] == "ambiguous"
    final = store.provider_resume(initial["request_id"], provider)
    assert final["status"] == "succeeded"
    assert methods == ["POST", "GET", "GET"]
    assert store.reconcile(initial["request_id"])["status"] == "succeeded"
    assert store.provider_resume(initial["request_id"], provider)["status"] == "succeeded"
    assert methods == ["POST", "GET", "GET"]


def test_crash_after_identity_can_resume_by_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200,
            json=interaction(status="in_progress" if request.method == "POST" else "completed"),
        )

    store, provider, item = setup(tmp_path, handler)
    original = store._transition

    def crash(request_id: str, status: str, **kwargs: Any) -> None:
        if status == "ambiguous":
            raise RuntimeError("simulated process crash")
        original(request_id, status, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_transition", crash)
        with pytest.raises(RuntimeError):
            store.run(item, provider)
    row = store.prepare(item)
    assert row["status"] == "remote_started"
    assert row["provider_request_id"] == "vertex-interaction-123"
    assert store.provider_resume(row["request_id"], provider)["status"] == "succeeded"
    assert methods == ["POST", "GET"]


def test_receipt_crash_reconciles_locally_without_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json=interaction())

    store, provider, item = setup(tmp_path, handler)
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "_finalize",
            lambda request_id: (_ for _ in ()).throw(RuntimeError("interrupted")),
        )
        first = store.run(item, provider)
    assert first["action"] == "local_reconciliation_required"
    assert store._staged_path(first["request_id"], "mp3").read_bytes() == TONE.read_bytes()
    assert store.reconcile(first["request_id"])["status"] == "succeeded"
    assert store.reconcile(first["request_id"])["status"] == "succeeded"
    assert methods == ["POST"]


def test_staged_without_receipt_needs_provider_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json=interaction())

    store, provider, item = setup(tmp_path, handler)
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "_record_receipt",
            lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted")),
        )
        first = store.run(item, provider)
    assert first["action"] == "local_reconciliation_required"
    assert store.reconcile(first["request_id"])["action"] == "provider_side_reconciliation_required"
    assert methods == ["POST"]
    assert store.provider_resume(first["request_id"], provider)["status"] == "succeeded"
    assert methods == ["POST", "GET"]


def test_mp3_validation_and_mapping_backward_compatibility(tmp_path: Path) -> None:
    data = TONE.read_bytes()
    info = inspect_audio(data, "audio/mpeg")
    assert info.container == "mp3" and 1.5 < info.duration_seconds < 2.5
    for invalid in (b"", b"random bytes", data[: len(data) // 2]):
        with pytest.raises(ValueError):
            inspect_audio(invalid, "audio/mpeg")
    store, _, item = setup(tmp_path, lambda request: httpx.Response(500))
    row = store.prepare(item)
    store._transition(row["request_id"], "remote_started")
    result = MusicResult(
        audio_bytes=data,
        mime_type="audio/mpeg",
        container="mp3",
        codec="mp3",
        provider_request_id="vertex-interaction-123",
    )
    store._record_provider_identity(row["request_id"], "vertex-interaction-123")
    store._write_audio(row["request_id"], data, "mp3")
    store._record_receipt(row["request_id"], result, info.duration_seconds, info.codec)
    with store.database.connect() as db:
        for relative_path, digest in (
            (row["request_id"] + ".wav", sha256(data).hexdigest()),
            (row["request_id"] + ".mp3", "0" * 64),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO music_outputs "
                    "(request_id, blind_id, relative_path, sha256, created_at) "
                    "VALUES (?, ?, ?, ?, 'test')",
                    (row["request_id"], relative_path, relative_path, digest),
                )
    assert store.reconcile(row["request_id"])["status"] == "succeeded"
    brief, lyrics = inputs()
    fake = FakeMusicProvider()
    fake_result = store.run(plan(brief, lyrics, [fake])[0], fake)
    assert fake_result["status"] == "succeeded"
    assert store._audio_path(fake_result["request_id"]).suffix == ".wav"
