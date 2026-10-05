"""Donor-adapted MockTransport coverage; never calls NVIDIA or needs a real key."""

import json
from contextlib import closing

import httpx
import pytest
from pydantic import BaseModel

from tovitunes.config import CreativeLLMConfig
from tovitunes.creative.nvidia import NvidiaNIMClient
from tovitunes.creative.provider import (
    DurableStructuredGenerator,
    GenerationContext,
    ProviderError,
    StructuredOutputError,
)
from tovitunes.domain.episode import Episode
from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import InvalidRequestTransition, RequestLedger


class Answer(BaseModel):
    value: int


@pytest.fixture
def request_owner(tmp_path, catalog, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-test-secret")
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("network call"))
    database = Database(tmp_path / "state.db")
    database.migrate()
    episode = Episode.create(catalog, "red", "red-test")
    database.create_episode(catalog, episode)
    return database, GenerationContext("episode_spec", "contract-v1", episode_id=episode.episode_id)


def generate(request_owner, handler):
    database, context = request_owner
    client = NvidiaNIMClient(
        CreativeLLMConfig(base_url="https://example.test/v1"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return DurableStructuredGenerator(database, client), context


def rows(database):
    with closing(database.connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM generation_requests ORDER BY rowid")]


def response(content='{"value": 7}', **kwargs):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, **kwargs)


def test_explicit_model_bearer_schema_timeout_and_json_fallback(request_owner):
    calls = []

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer offline-test-secret"
        assert request.headers["Accept"] == "text/event-stream"
        assert all(t == 1800 for t in request.extensions["timeout"].values())
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["model"] == "moonshotai/kimi-k3"
        assert payload["stream"] is True
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 16384
        assert "JSON Schema" in payload["messages"][0]["content"]
        assert "no Markdown" in payload["messages"][0]["content"]
        return response(headers={"x-request-id": "nim-header-id"})

    provider, context = generate(request_owner, handler)
    draft = provider.generate(Answer, [{"role": "user", "content": "answer"}], context=context)
    assert draft.output.value == 7
    assert draft.request_id == "nim-header-id"
    assert draft.local_request_id == rows(request_owner[0])[0]["request_id"]
    assert len(calls) == 1


def test_sse_ignores_reasoning_and_persists_body_identity(request_owner):
    def handler(request):
        events = [
            {"id": "nim-body-id", "choices": [{"delta": {"reasoning_content": "not JSON"}}]},
            {"choices": [{"delta": {"content": '{"value":'}}]},
            {"choices": [{"delta": {"content": "7}"}, "finish_reason": "stop"}]},
        ]
        body = ": heartbeat\n\n" + "".join(f"data: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=body + "data: [DONE]\n\n"
        )

    provider, context = generate(request_owner, handler)
    result = provider.generate(Answer, [], context=context)
    assert result.output.value == 7 and result.request_id == "nim-body-id"
    assert rows(request_owner[0])[0]["response_content"] == '{"value":7}'


def test_unknown_provider_id_remains_unknown_and_success_reused_without_key(
    request_owner, monkeypatch
):
    calls = []
    provider, context = generate(request_owner, lambda r: calls.append(r) or response())
    first = provider.generate(Answer, [], context=context)
    monkeypatch.delenv("NVIDIA_API_KEY")
    second = provider.generate(Answer, [], context=context)
    assert first == second
    assert first.request_id is None
    assert rows(request_owner[0])[0]["provider_request_id"] is None
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", ["not-json", '{"value":"no"}', '{"value":-1}'])
def test_exactly_one_visible_repair_with_domain_callback(request_owner, invalid):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return response(invalid if len(calls) == 1 else '{"value":7}')

    def validate(answer):
        if answer.value < 0:
            raise ValueError("negative values are forbidden")

    provider, context = generate(request_owner, handler)
    first = provider.generate(Answer, [], context=context, validate=validate)
    again = provider.generate(Answer, [], context=context, validate=validate)
    assert first == again and len(calls) == 2
    saved = rows(request_owner[0])
    assert saved[0]["status"] == "succeeded_response_invalid"
    assert saved[1]["kind"] == "episode_spec_repair"
    assert saved[1]["parent_request_id"] == saved[0]["request_id"]
    assert saved[1]["attempt"] == 2 and saved[1]["status"] == "succeeded"
    assert saved[0]["remote_started_at"] and saved[0]["response_sha256"]
    assert "previous response was invalid" in calls[1]["messages"][-1]["content"].lower()
    assert json.loads(saved[1]["messages_json"]) == calls[1]["messages"]


def test_invalid_repair_fails_closed_no_third_post_even_on_reinvocation(request_owner):
    calls = []
    provider, context = generate(request_owner, lambda r: calls.append(r) or response("broken"))
    for _ in range(2):
        with pytest.raises(StructuredOutputError, match="one repair"):
            provider.generate(Answer, [], context=context)
    assert len(calls) == 2
    assert all(r["status"] == "succeeded_response_invalid" for r in rows(request_owner[0]))


def test_missing_key_does_not_create_or_start_request(request_owner, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY")
    provider, context = generate(request_owner, lambda r: pytest.fail("POST without key"))
    with pytest.raises(ProviderError, match="NVIDIA_API_KEY"):
        provider.generate(Answer, [], context=context)
    assert rows(request_owner[0]) == []


@pytest.mark.parametrize(
    "body",
    [
        "data: not-json\n\ndata: [DONE]\n\n",
        "data: []\n\ndata: [DONE]\n\n",
        'data: {"choices": [null]}\n\ndata: [DONE]\n\n',
        'data: {"choices": [{}]}\n\ndata: [DONE]\n\n',
        'data: {"choices": [{"delta": {"content": 3}}]}\n\ndata: [DONE]\n\n',
        'data: {"choices": [{"delta": {"reasoning_content": "only thinking"}}]}\n\n'
        "data: [DONE]\n\n",
    ],
)
def test_malformed_or_empty_stream_fails_explicitly_without_repair(request_owner, body):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider, context = generate(request_owner, handler)
    for _ in range(2):
        with pytest.raises(ProviderError):
            provider.generate(Answer, [], context=context)
    assert len(calls) == 1
    assert rows(request_owner[0])[0]["status"] == "failed"


@pytest.mark.parametrize("status", [401, 404, 429, 500, 503])
def test_http_failure_has_no_retry_or_fallback(request_owner, status):
    calls = []
    provider, context = generate(request_owner, lambda r: calls.append(r) or httpx.Response(status))
    for _ in range(2):
        with pytest.raises(ProviderError):
            provider.generate(Answer, [], context=context)
    assert len(calls) == 1
    assert rows(request_owner[0])[0]["status"] == ("ambiguous" if status >= 500 else "failed")


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ReadError, httpx.ConnectError])
def test_remote_connection_failure_is_ambiguous_and_never_resent(request_owner, error):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("remote outcome unknown", request=request)

    provider, context = generate(request_owner, handler)
    for _ in range(2):
        with pytest.raises(ProviderError, match="resend|recovery"):
            provider.generate(Answer, [], context=context)
    assert len(calls) == 1
    assert rows(request_owner[0])[0]["status"] == "ambiguous"


def test_truncated_stream_preserves_known_identity_and_blocks_even_changed_input(request_owner):
    calls = []
    body = 'data: {"id":"known","choices":[{"delta":{"content":"{\\"value\\":7}"}}]}\n\n'
    provider, context = generate(
        request_owner,
        lambda r: (
            calls.append(r)
            or httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        ),
    )
    with pytest.raises(ProviderError, match="without completion"):
        provider.generate(Answer, [], context=context)
    assert rows(request_owner[0])[0]["provider_request_id"] == "known"
    with pytest.raises(InvalidRequestTransition, match="unresolved"):
        provider.generate(Answer, [{"role": "user", "content": "new input"}], context=context)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [[], {}, {"error": {"message": "unavailable"}}, {"choices": [{"message": {"content": ""}}]}],
)
def test_bad_json_fallback_and_provider_error_do_not_repair(request_owner, body):
    calls = []
    provider, context = generate(
        request_owner, lambda r: calls.append(r) or httpx.Response(200, json=body)
    )
    with pytest.raises(ProviderError):
        provider.generate(Answer, [], context=context)
    assert len(calls) == 1


def test_saved_receipt_resumes_validation_without_post(request_owner):
    provider, context = generate(request_owner, lambda r: response())
    result = provider.generate(Answer, [], context=context)
    with request_owner[0].connect() as db:
        db.execute(
            "UPDATE generation_requests SET status='remote_started' WHERE request_id=?",
            (result.local_request_id,),
        )
    assert provider.generate(Answer, [], context=context).output.value == 7


def test_start_without_receipt_is_marked_ambiguous_on_resume(request_owner):
    provider, context = generate(request_owner, lambda r: response())
    result = provider.generate(Answer, [], context=context)
    with request_owner[0].connect() as db:
        db.execute(
            "UPDATE generation_requests SET status='remote_started',response_content=NULL,"
            "response_sha256=NULL WHERE request_id=?",
            (result.local_request_id,),
        )
    with pytest.raises(ProviderError, match="do not resend"):
        provider.generate(Answer, [], context=context)
    assert rows(request_owner[0])[0]["status"] == "ambiguous"


def test_response_tampering_fails_closed(request_owner):
    provider, context = generate(request_owner, lambda r: response())
    result = provider.generate(Answer, [], context=context)
    with request_owner[0].connect() as db:
        db.execute(
            "UPDATE generation_requests SET response_content='{}' WHERE request_id=?",
            (result.local_request_id,),
        )
    with pytest.raises(ProviderError, match="hash differs"):
        provider.generate(Answer, [], context=context)


def test_forward_migration_preserves_existing_generation_requests_and_leases(
    tmp_path, catalog, monkeypatch
):
    from importlib import resources

    original = resources.files("tovitunes.persistence.migrations")
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for item in original.iterdir():
        if item.name.endswith(".sql") and item.name < "0014":
            (old_migrations / item.name).write_bytes(item.read_bytes())
    with monkeypatch.context() as scoped:
        scoped.setattr("tovitunes.persistence.db.resources.files", lambda name: old_migrations)
        database = Database(tmp_path / "existing.db")
        database.migrate()
        episode = Episode.create(catalog, "red", "existing-request")
        database.create_episode(catalog, episode)
        request = RequestLedger(database).prepare(
            episode.episode_id,
            "scene_image",
            "main",
            "existing-provider",
            "existing-model",
            "a" * 64,
        )
        with database.connect() as db:
            before = dict(db.execute("SELECT * FROM generation_requests").fetchone())
            db.execute("INSERT INTO execution_leases VALUES ('existing', 'owner', 1, 2)")
    database.migrate()
    with database.connect() as db:
        after = dict(db.execute("SELECT * FROM generation_requests").fetchone())
        assert {key: after[key] for key in before} == before
        assert tuple(db.execute("SELECT * FROM execution_leases").fetchone()) == (
            "existing",
            "owner",
            1,
            2,
        )
    assert (
        RequestLedger(database).transition(request.request_id, "remote_started").status
        == "remote_started"
    )


def test_ambiguous_repair_blocks_new_input_for_the_whole_stage(request_owner):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return response("not-json")
        raise httpx.ReadTimeout("repair outcome unknown", request=request)

    provider, context = generate(request_owner, handler)
    with pytest.raises(ProviderError):
        provider.generate(Answer, [], context=context)
    with pytest.raises(InvalidRequestTransition, match="unresolved"):
        provider.generate(Answer, [{"role": "user", "content": "changed input"}], context=context)
    assert len(calls) == 2
    assert rows(request_owner[0])[1]["status"] == "ambiguous"
