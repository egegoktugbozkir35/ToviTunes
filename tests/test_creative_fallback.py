"""Offline transport, restart and immutable-history regression tests for creative fallback."""

import json
import socket
from contextlib import closing
from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from tovitunes.cli import main
from tovitunes.config import CreativeLLMConfig, RuntimeConfig
from tovitunes.creative.factory import creative_generator
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.nvidia import NvidiaNIMClient
from tovitunes.creative.ollama import OllamaClient
from tovitunes.creative.provider import (
    DurableStructuredGenerator,
    GenerationContext,
    ProviderError,
    StructuredOutputError,
    canonical,
    fingerprint,
    structured_json_instruction,
)
from tovitunes.creative.resilience import ResilientStructuredGenerator, generation_audit
from tovitunes.creative.workflow import CreativeWorkflow, call_report, call_snapshot
from tovitunes.domain.episode import Episode
from tovitunes.persistence.creative_reconciliation import CreativeReconciliations
from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import InvalidRequestTransition

CHAIN = (
    "moonshotai/kimi-k3",
    "z-ai/glm-5.3",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "deepseek-ai/deepseek-v4.1-flash",
)


class Answer(BaseModel):
    value: int


@pytest.fixture
def owner(tmp_path, catalog, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network"))
    database = Database(tmp_path / "state.db")
    database.migrate()
    episode = Episode.create(catalog, "red", "test-red")
    database.create_episode(catalog, episode)
    return database, GenerationContext("episode_spec", "test-v1", episode_id=episode.episode_id)


def records(database):
    with closing(database.connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM generation_requests ORDER BY rowid")]


def reply(content='{"value":7}', identity="id"):
    return httpx.Response(
        200, json={"id": identity, "choices": [{"message": {"content": content}}]}
    )


def build(database, handler, *, emergency=False, config=None):
    config = config or CreativeLLMConfig(base_url="https://example.test/v1")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    transports = [
        NvidiaNIMClient(config.model_copy(update={"model": model}), client=client)
        for model in (config.model, *config.fallback_models)
    ]
    ollama = OllamaClient(config.ollama, client=client) if emergency else None
    return ResilientStructuredGenerator(database, transports, emergency=ollama)


def run(generator, context):
    return generator.generate(Answer, [], context=context)


def test_primary_success_audit_and_exact_reuse(owner):
    database, context = owner
    calls = []
    generator = build(database, lambda r: calls.append(json.loads(r.content)["model"]) or reply())
    draft = run(generator, context)
    before = records(database)
    assert draft.model == CHAIN[0]
    assert run(build(database, lambda r: pytest.fail("resend")), context) == draft
    assert calls == [CHAIN[0]] and records(database) == before
    assert before[0]["requested_model"] == before[0]["model"] == CHAIN[0]
    assert before[0]["fallback_index"] == 0


@pytest.mark.parametrize("failed_count", [1, 2, 3])
def test_ordered_empty_completed_fallback_and_immutable_receipts(owner, failed_count):
    database, context = owner
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if CHAIN.index(model) < failed_count:
            event = {
                "id": model,
                "choices": [{"delta": {"reasoning_content": "HIDDEN"}, "finish_reason": "stop"}],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(event)}\n\n",
            )
        return reply(identity=model)

    before_calls = call_snapshot(database)
    draft = run(build(database, handler), context)
    saved = records(database)
    assert calls == list(CHAIN[: failed_count + 1])
    assert draft.model == CHAIN[failed_count]
    assert [r["status"] for r in saved] == ["failed"] * failed_count + ["succeeded"]
    for index, row in enumerate(saved):
        assert row["model"] == CHAIN[index] and row["provider_request_id"] == CHAIN[index]
        assert row["requested_model"] == CHAIN[0] and row["fallback_index"] == index
        assert row["remote_started_at"] and row["created_at"] and row["updated_at"]
        if index:
            assert row["previous_attempt_id"] == saved[index - 1]["request_id"]
            assert row["fallback_reason"] == "empty_answer"
        else:
            assert row["error_kind"] == "empty_answer"
    assert "HIDDEN" not in json.dumps(saved) and "offline-secret" not in json.dumps(saved)
    assert call_report(database, before_calls)["episode_spec"] == failed_count + 1
    assert run(build(database, lambda r: pytest.fail("resend")), context) == draft
    assert records(database) == saved


def test_invalid_output_one_repair_per_model_then_fallback(owner):
    database, context = owner
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return reply(
            "broken" if payload["model"] == CHAIN[0] else '{"value":7}', identity=f"id-{len(calls)}"
        )

    draft = run(build(database, handler), context)
    saved = records(database)
    assert [c["model"] for c in calls] == [CHAIN[0], CHAIN[0], CHAIN[1]]
    assert draft.model == CHAIN[1]
    assert [r["attempt"] for r in saved] == [1, 2, 1]
    assert saved[1]["parent_request_id"] == saved[0]["request_id"]
    assert saved[2]["previous_attempt_id"] == saved[1]["request_id"]
    assert saved[1]["status"] == "succeeded_response_invalid"
    assert run(build(database, lambda r: pytest.fail("third Kimi")), context) == draft
    assert records(database) == saved


@pytest.mark.parametrize("status", [200, 400, 404, 422])
def test_explicit_machine_readable_model_unavailable(owner, status):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        if len(calls) == 1:
            return httpx.Response(
                status, json={"error": {"code": "model_unavailable", "message": "offline-secret"}}
            )
        return reply()

    assert run(build(database, handler), context).model == CHAIN[1]
    assert calls == list(CHAIN[:2])
    assert records(database)[0]["error_kind"] == "model_unavailable"
    assert "offline-secret" not in json.dumps(records(database))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 409, 422, 425, 429, 500, 503, 504])
def test_auth_bad_configuration_and_unclassified_http_fail_closed(owner, status):
    database, context = owner
    calls = []
    generator = build(database, lambda r: calls.append(r) or httpx.Response(status), emergency=True)
    for _ in range(2):
        with pytest.raises(ProviderError):
            run(generator, context)
    assert len(calls) == 1
    saved = records(database)
    assert len(saved) == 1
    assert saved[0]["status"] == (
        "ambiguous" if status >= 500 or status in {408, 409, 425} else "failed"
    )


@pytest.mark.parametrize("restart", [False, True])
def test_empty_ambiguous_abandoned_rejected_then_success(owner, monkeypatch, restart):
    database, context = owner
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == CHAIN[0]:
            return reply("")
        if model == CHAIN[1]:
            return httpx.Response(504, text="Bearer offline-secret signed-url?token=private")
        if model == CHAIN[2]:
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "provider_rejected",
                        "message": "offline-secret raw-provider-body",
                    }
                },
            )
        return reply(identity=f"deepseek-success-{len(calls)}")

    with pytest.raises(ProviderError) as failure:
        run(build(database, handler), context)
    assert failure.value.ambiguous
    originals = records(database)
    assert calls == list(CHAIN[:2])
    with pytest.raises(ProviderError):
        run(build(database, handler), context)
    assert records(database) == originals and calls == list(CHAIN[:2])
    reconciliations = CreativeReconciliations(database)
    decision = dict(
        reconciliations.abandon(
            originals[1]["request_id"],
            actor="human:operator",
            rationale="Remote result inaccessible; continue with next configured model",
        )
    )
    generator = build(database, handler)
    if restart:
        history = generator._history

        def crash_after_rejection(context):
            rows = history(context)
            if rows[-1]["model"] == CHAIN[2] and rows[-1]["status"] == "failed":
                raise SystemExit("process interrupted after durable provider rejection")
            return rows

        monkeypatch.setattr(generator, "_history", crash_after_rejection)
        with pytest.raises(SystemExit):
            run(generator, context)
        assert calls == list(CHAIN[:3])
        retained = records(database)
        assert retained[-1]["status"] == "failed"
        assert retained[-1]["error_kind"] == "provider_rejected"
        generator = build(Database(database.path), handler)
    else:
        retained = originals
    draft = run(generator, context)
    saved = records(database)
    assert draft.model == CHAIN[3] and draft.output.value == 7
    assert calls == list(CHAIN)
    assert [r["fallback_index"] for r in saved] == [0, 1, 2, 3]
    assert [r["previous_attempt_id"] for r in saved] == [None] + [
        r["request_id"] for r in saved[:-1]
    ]
    assert [r["fallback_reason"] for r in saved] == [
        None,
        "empty_answer",
        "operator_abandoned_ambiguous",
        "provider_rejected",
    ]
    assert saved[: len(retained)] == retained
    assert saved[1]["status"] == "ambiguous" and saved[2]["status"] == "failed"
    assert dict(reconciliations.get(originals[1]["request_id"])) == decision
    with pytest.raises(ValueError, match="immutable"):
        reconciliations.abandon(originals[1]["request_id"], actor="human:other", rationale="change")
    assert run(build(database, lambda r: pytest.fail("resend")), context) == draft
    assert records(database) == saved
    assert run(build(database, handler), replace(context, kind="lyrics")).model == CHAIN[3]
    assert calls == list(CHAIN) + [CHAIN[3]]
    assert all(
        secret not in json.dumps(saved)
        for secret in ("offline-secret", "raw-provider-body", "signed-url", "Bearer")
    )


@pytest.mark.parametrize("status", [200, 400, 404, 422])
def test_conclusive_provider_rejection_advances_once_and_exhausts(owner, status):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(
            status, json={"error": {"code": "provider_rejected", "message": "offline-secret"}}
        )

    for _ in range(2):
        with pytest.raises(ProviderError, match="chain exhausted"):
            run(build(database, handler, emergency=True), context)
    assert calls == list(CHAIN)
    assert all(r["status"] == "failed" for r in records(database))


@pytest.mark.parametrize(
    "code,category",
    [
        ("invalid_api_key", "authentication"),
        ("authentication_error", "authentication"),
        ("invalid_request_error", "configuration"),
        ("rate_limit_exceeded", "rate_limited"),
        ("insufficient_quota", "rate_limited"),
        ("server_error", "ambiguous"),
    ],
)
@pytest.mark.parametrize("status", [200, 400, 422])
def test_typed_endpoint_errors_never_advance(owner, code, category, status):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": code, "message": "offline-secret"}})

    for _ in range(2):
        with pytest.raises(ProviderError) as exc:
            run(build(database, handler, emergency=True), context)
        assert exc.value.category.value == category
    assert len(calls) == 1 and len(records(database)) == 1


@pytest.mark.parametrize("status,category", [(400, "configuration"), (429, "rate_limited")])
def test_old_bare_http_rejection_does_not_become_fallback_safe(owner, status, category):
    database, context = owner
    with pytest.raises(ProviderError):
        run(build(database, lambda r: httpx.Response(status)), context)
    with database.connect() as db:
        db.execute("UPDATE generation_requests SET error_kind='provider_rejected'")
    before = records(database)
    with pytest.raises(ProviderError) as exc:
        run(build(database, lambda r: pytest.fail("resend or fallback")), context)
    assert exc.value.category.value == category
    assert records(database) == before


@pytest.mark.parametrize(
    "body,category",
    [
        ({"code": "unrecognized_auth_code", "type": "authentication_error"}, "authentication"),
        ({"code": 401}, "authentication"),
        ({"code": "provider_rejected", "type": "invalid_request_error"}, "configuration"),
        ({"code": 429}, "rate_limited"),
    ],
)
def test_endpoint_error_type_and_numeric_codes_take_priority(owner, body, category):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"error": body | {"message": "offline-secret"}})

    with pytest.raises(ProviderError) as exc:
        run(build(database, handler), context)
    assert exc.value.category.value == category
    assert len(calls) == 1


def test_explicit_model_unavailable_with_generic_request_error_type_advances(owner):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        if len(calls) == 1:
            return httpx.Response(
                404, json={"error": {"code": "model_not_found", "type": "invalid_request_error"}}
            )
        return reply()

    assert run(build(database, handler), context).model == CHAIN[1]
    assert calls == list(CHAIN[:2])


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout,
        httpx.ReadError,
        httpx.WriteError,
        httpx.ConnectError,
        httpx.ConnectTimeout,
    ],
)
def test_uncertain_transport_never_issues_nvidia_or_ollama_fallback(owner, error):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(request)
        raise error("offline-secret", request=request)

    with pytest.raises(ProviderError) as exc:
        run(build(database, handler, emergency=True), context)
    assert exc.value.ambiguous
    before = records(database)
    with pytest.raises(ProviderError, match="explicit recovery"):
        run(build(database, handler, emergency=True), context)
    assert len(calls) == 1 and len(before) == 1 and before[0]["status"] == "ambiguous"
    assert records(database) == before
    assert "offline-secret" not in json.dumps(before) and "offline-secret" not in str(exc.value)


@pytest.mark.parametrize(
    "body",
    [
        'data: {"id":"started","choices":[{"delta":{"content":"{}"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"{}"}}]}\n\ndata: [DONE]\n\n',
        "data: malformed\n\n",
    ],
)
def test_inconclusive_stream_restart_remains_ambiguous(owner, body):
    database, context = owner
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    for _ in range(2):
        with pytest.raises(ProviderError) as exc:
            run(build(database, handler, emergency=True), context)
        assert exc.value.ambiguous
    assert len(calls) == 1 and records(database)[0]["status"] == "ambiguous"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("cause", [socket.gaierror, ConnectionRefusedError])
def test_only_proven_preinteraction_endpoint_error_can_use_ollama(owner, enabled, cause):
    database, context = owner
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["model"])
        if request.url.host == "example.test":
            try:
                raise cause("offline-secret")
            except OSError as original:
                raise httpx.ConnectError("unreachable", request=request) from original
        assert request.url.path == "/api/chat" and payload["think"] is False
        assert payload["stream"] is False and payload["format"] == "json"
        assert "JSON Schema" in payload["messages"][0]["content"]
        return httpx.Response(200, json={"done": True, "message": {"content": '{"value":7}'}})

    generator = build(database, handler, emergency=enabled)
    if enabled:
        draft = run(generator, context)
        assert draft.provider == "ollama" and draft.model == "qwen3.8:27b-q4_K_M"
        assert calls == [CHAIN[0], "qwen3.8:27b-q4_K_M"]
        saved = records(database)
        assert saved[0]["status"] == "failed" and saved[0]["error_kind"] == "endpoint_unreachable"
        assert saved[1]["fallback_reason"] == "endpoint_unreachable"
        assert saved[1]["previous_attempt_id"] == saved[0]["request_id"]
        assert (
            run(build(database, lambda r: pytest.fail("resend"), emergency=True), context) == draft
        )
        assert records(database) == saved
    else:
        with pytest.raises(ProviderError, match="disabled"):
            run(generator, context)
        saved = records(database)
        with pytest.raises(ProviderError, match="disabled"):
            run(build(database, handler), context)
        assert calls == [CHAIN[0]] and records(database) == saved


def test_sticky_fallback_survives_new_process_but_stage_receipt_has_priority(owner):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return reply("" if len(calls) == 2 else '{"value":7}', identity=f"id-{len(calls)}")

    primary = run(build(database, handler), context)
    stage2 = replace(context, kind="lyrics")
    fallback = run(build(database, handler), stage2)
    assert fallback.model == CHAIN[1]
    # Existing Kimi success must be reused even after a later stage switched to GLM.
    assert run(build(database, handler), context) == primary
    assert run(build(database, handler), replace(context, kind="music_spec")).model == CHAIN[1]
    assert calls == [CHAIN[0], CHAIN[0], CHAIN[1], CHAIN[1]]


@pytest.mark.parametrize("repair", [False, True])
def test_legacy_failed_kimi_restart_preserves_all_evidence(owner, repair):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(request)
        return reply("broken" if repair and len(calls) == 1 else "", identity=f"old-{len(calls)}")

    config = CreativeLLMConfig(base_url="https://example.test/v1")
    old = DurableStructuredGenerator(
        database,
        NvidiaNIMClient(config, client=httpx.Client(transport=httpx.MockTransport(handler))),
    )
    with pytest.raises(ProviderError):
        run(old, context)
    with database.connect() as db:
        db.execute(
            "UPDATE generation_requests SET error_kind='ProviderError',"
            "error_reason='NVIDIA NIM returned empty answer content',"
            "requested_provider=NULL,requested_model=NULL,fallback_reason=NULL,"
            "fallback_index=NULL,previous_attempt_id=NULL WHERE status='failed'"
        )
    before = records(database)
    new_calls = []
    draft = run(
        build(
            database,
            lambda r: new_calls.append(json.loads(r.content)["model"]) or reply(identity="new-glm"),
        ),
        context,
    )
    assert draft.model == CHAIN[1] and new_calls == [CHAIN[1]]
    assert records(database)[: len(before)] == before
    assert records(database)[-1]["previous_attempt_id"] == before[-1]["request_id"]


@pytest.mark.parametrize("reason", ["unknown", "NVIDIA NIM returned empty answer content"])
def test_ambiguous_legacy_evidence_never_promoted_to_safe_failure(owner, reason):
    database, context = owner
    calls = []
    with pytest.raises(ProviderError):
        run(build(database, lambda r: calls.append(r) or httpx.Response(503)), context)
    with database.connect() as db:
        db.execute(
            "UPDATE generation_requests SET error_kind='ProviderError',error_reason=?", (reason,)
        )
    before = records(database)
    with pytest.raises(ProviderError, match="reconcile provider evidence"):
        run(build(database, lambda r: pytest.fail("fallback"), emergency=True), context)
    assert records(database) == before


def test_missing_key_local_lease_and_corruption_never_fallback(owner, monkeypatch):
    database, context = owner
    generator = build(database, lambda r: pytest.fail("local failure made a POST"), emergency=True)
    monkeypatch.delenv("NVIDIA_API_KEY")
    with pytest.raises(ProviderError):
        run(generator, context)
    assert records(database) == []
    with pytest.raises(PermissionError):
        run(
            generator,
            replace(context, assert_owner=lambda: (_ for _ in ()).throw(PermissionError())),
        )
    assert records(database) == []
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    run(build(database, lambda r: reply()), context)
    with database.connect() as db:
        db.execute("UPDATE generation_requests SET response_content='tampered'")
    with pytest.raises(ProviderError, match="hash differs"):
        run(generator, context)
    assert len(records(database)) == 1


def test_exhaustion_does_not_revisit_any_model_or_use_emergency(owner):
    database, context = owner
    calls = []

    def handler(r):
        calls.append(json.loads(r.content)["model"])
        return reply("", identity=str(len(calls)))

    for _ in range(2):
        with pytest.raises(ProviderError, match="exhausted"):
            run(build(database, handler, emergency=True), context)
    assert calls == list(CHAIN) and len(records(database)) == 4


def test_ambiguous_repair_blocks_changed_input_and_fallback(owner):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return reply("broken")
        raise httpx.ReadTimeout("uncertain", request=request)

    generator = build(database, handler, emergency=True)
    with pytest.raises(ProviderError):
        run(generator, context)
    with pytest.raises(InvalidRequestTransition, match="unresolved"):
        generator.generate(Answer, [{"role": "user", "content": "changed"}], context=context)
    assert len(calls) == 2 and records(database)[1]["status"] == "ambiguous"


@pytest.mark.parametrize(
    "values",
    [
        {"model": ""},
        {"fallback_models": [CHAIN[0]]},
        {"fallback_models": [" "]},
        {"fallback_models": ["x/y"] * 9},
        {"ollama": {"base_url": "https://secret@localhost"}},
        {"ollama": {"base_url": "https://remote.test"}},
        {"ollama": {"temperature": float("nan")}},
    ],
)
def test_invalid_chain_and_local_emergency_config_rejected(values):
    with pytest.raises(ValidationError):
        CreativeLLMConfig.model_validate(values)


def test_partial_real_workflow_restart_keeps_episode_artifacts_and_historical_red(
    tmp_path,
    catalog,
    brand_root,
    monkeypatch,
):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network"))
    config = RuntimeConfig(
        database_path=tmp_path / "db",
        data_root=tmp_path / "data",
        brand_root=brand_root,
        creative_llm=CreativeLLMConfig(base_url="https://example.test/v1"),
    )
    database = Database(config.database_path)
    database.migrate()
    fake = FakeNIMTransport()
    # Reserve the published concept without touching its durable state.
    red = Episode.create(catalog, "red", "colors-red-001")
    database.create_episode(catalog, red)
    before_red = database.get_episode(red.episode_id).model_dump_json()
    calls = []
    fail_lyrics = True

    def handler(request):
        payload = json.loads(request.content)
        output = fake.chat(payload["messages"], record_identity=lambda _: None)
        calls.append((payload["model"], fake.calls[-1]))
        if fail_lyrics and fake.calls[-1] == "LyricsSpec":
            return reply("", identity=f"failed-{len(calls)}")
        return reply(output.content, identity=f"id-{len(calls)}")

    # Simulate the old single-model process failing after subject/spec durable progress.
    old_transport = NvidiaNIMClient(
        config.creative_llm, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    old_flow = CreativeWorkflow(
        config, DurableStructuredGenerator(database, old_transport), catalog=catalog
    )
    with pytest.raises(ProviderError):
        old_flow.generate_next()
    before = records(database)
    with database.connect() as db:
        original_run = dict(db.execute("SELECT * FROM creative_runs").fetchone())
        artifacts = [dict(r) for r in db.execute("SELECT * FROM artifact_versions")]
    fail_lyrics = False
    # Normal no-ID discovery in a fresh instance resumes the very same incomplete run.
    flow = CreativeWorkflow(config, build(database, handler), catalog=catalog)
    result = flow.generate_next()
    assert result["run_id"] == original_run["run_id"]
    assert result["episode_id"] == original_run["episode_id"]
    assert records(database)[: len(before)] == before
    assert calls[-2:] == [(CHAIN[1], "LyricsSpec"), (CHAIN[1], "MusicSpec")]
    for kind in ("lyrics", "music_spec"):
        record = flow.store.get(result[kind + "_artifact_id"])
        assert record.provenance.model == CHAIN[1]
    with database.connect() as db:
        now_artifacts = [dict(r) for r in db.execute("SELECT * FROM artifact_versions")]
    assert now_artifacts[: len(artifacts)] == artifacts
    assert database.get_episode(red.episode_id).model_dump_json() == before_red
    assert result["provider_calls"] == dict(
        subject=0, episode_spec=0, lyrics=1, music_spec=1, metadata=0, repair=0
    )
    audit = generation_audit(database, result["episode_id"])
    assert audit[-1]["actual_model"] == CHAIN[1] and audit[-1]["requested_model"] == CHAIN[0]


def test_all_models_invalid_have_exactly_one_repair_and_no_emergency(owner):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return reply("broken", identity=f"id-{len(calls)}")

    for _ in range(2):
        with pytest.raises(ProviderError, match="exhausted"):
            run(build(database, handler, emergency=True), context)
    assert calls == [model for model in CHAIN for _ in range(2)]
    saved = records(database)
    assert len(saved) == 8 and all(r["status"] == "succeeded_response_invalid" for r in saved)
    assert call_report(database, set())["repair"] == 4


def test_unexpected_structured_exception_with_ambiguous_receipt_never_falls_back(
    owner, monkeypatch
):
    database, context = owner
    calls = []

    def broken(transport, *args, **kwargs):
        calls.append(transport.model_name)
        raise StructuredOutputError("unexpected local error")

    monkeypatch.setattr(NvidiaNIMClient, "chat", broken)
    with pytest.raises(ProviderError, match="does not authorize fallback"):
        run(build(database, lambda r: pytest.fail("network"), emergency=True), context)
    assert len(calls) == 1 and records(database)[0]["status"] == "ambiguous"


def test_forward_migration_preserves_actual_pre_fallback_failed_row(
    tmp_path,
    catalog,
    monkeypatch,
):
    from importlib import resources

    original = resources.files("tovitunes.persistence.migrations")
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for item in original.iterdir():
        if item.name.endswith(".sql") and item.name < "0018":
            (old_migrations / item.name).write_bytes(item.read_bytes())
    messages = [structured_json_instruction(Answer.model_json_schema())]
    # This is the exact pre-PR settings serialization, not the new implementation's helper.
    settings = dict(
        provider="nvidia",
        model=CHAIN[0],
        base_url="https://example.test/v1",
        timeout_seconds=1800,
        temperature=0.7,
        max_tokens=16384,
    )
    digest = fingerprint(
        dict(
            provider="nvidia",
            model=CHAIN[0],
            settings=settings,
            prompt_version="test-v1",
            messages=messages,
        )
    )
    with monkeypatch.context() as scoped:
        scoped.setattr("tovitunes.persistence.db.resources.files", lambda name: old_migrations)
        database = Database(tmp_path / "existing.db")
        database.migrate()
        episode = Episode.create(catalog, "red", "old-red")
        database.create_episode(catalog, episode)
        with database.connect() as db:
            db.execute(
                "INSERT INTO generation_requests (request_id,episode_id,kind,slot_key,provider,"
                "model,provider_request_id,input_fingerprint,status,created_at,updated_at,"
                "prompt_version,remote_started_at,error_kind,error_reason,messages_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "old-id",
                    episode.episode_id,
                    "episode_spec",
                    "main",
                    "nvidia",
                    CHAIN[0],
                    "old-remote-id",
                    digest,
                    "failed",
                    "old-time",
                    "old-time",
                    "test-v1",
                    "old-time",
                    "ProviderError",
                    "NVIDIA NIM returned empty answer content",
                    canonical(messages),
                ),
            )
            before = dict(db.execute("SELECT * FROM generation_requests").fetchone())
    database.migrate()
    after = records(database)[0]
    assert {key: after[key] for key in before} == before
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network"))
    context = GenerationContext("episode_spec", "test-v1", episode_id=episode.episode_id)
    calls = []
    draft = run(
        build(database, lambda r: calls.append(json.loads(r.content)["model"]) or reply()), context
    )
    assert draft.model == CHAIN[1] and calls == [CHAIN[1]]
    assert records(database)[0] == after


def test_factory_disabled_emergency_not_instantiated(owner, monkeypatch):
    database, context = owner
    monkeypatch.setattr(
        "tovitunes.creative.factory.OllamaClient", lambda *a, **k: pytest.fail("Ollama required")
    )
    primary = NvidiaNIMClient(
        CreativeLLMConfig(), client=httpx.Client(transport=httpx.MockTransport(lambda r: reply()))
    )
    with creative_generator(database, CreativeLLMConfig(), primary) as generator:
        assert generator.emergency is None
        assert run(generator, context).model == CHAIN[0]


def test_cli_chain_subject_stickiness_and_doctor_without_provider_calls(
    tmp_path,
    brand_root,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network"))
    fake = FakeNIMTransport()
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["model"])
        if len(calls) == 1:
            return reply("", identity="kimi-failed")
        output = fake.chat(payload["messages"], record_identity=lambda _: None)
        return reply(output.content, identity=f"id-{len(calls)}")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    def factory(config):
        return NvidiaNIMClient(config, client=client)

    monkeypatch.setattr("tovitunes.cli.NvidiaNIMClient", factory)
    monkeypatch.setattr("tovitunes.creative.factory.NvidiaNIMClient", factory)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"database_path: {tmp_path.as_posix()}/db\ndata_root: {tmp_path.as_posix()}/data\n"
        f"brand_root: {brand_root.as_posix()}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(config_file), "creative", "doctor"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["primary_model"] == CHAIN[0] and doctor["fallback_models"] == list(CHAIN[1:])
    assert doctor["ollama_endpoint_fallback_enabled"] is False
    assert doctor["key_configured"] is True and not calls
    assert main(["--config", str(config_file), "creative", "generate-next", "--live"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [CHAIN[0], CHAIN[1], CHAIN[1], CHAIN[1], CHAIN[1]]
    assert result["provider_calls"]["subject"] == 2
    assert result["generation_attempts"][-1]["actual_model"] == CHAIN[1]
    assert result["generation_attempts"][-1]["fallback_reason"] == "empty_answer"
    assert "offline-secret" not in json.dumps(result)


def test_restart_after_invalid_repair_does_not_send_third_primary_request(owner):
    database, context = owner
    config = CreativeLLMConfig(base_url="https://example.test/v1")
    calls = []
    transport = NvidiaNIMClient(
        config,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: calls.append(r) or reply("broken", identity=f"id-{len(calls)}")
            )
        ),
    )
    with pytest.raises(StructuredOutputError):
        run(DurableStructuredGenerator(database, transport), context)
    before = records(database)
    new_calls = []
    draft = run(
        build(
            database,
            lambda r: new_calls.append(json.loads(r.content)["model"]) or reply(identity="glm"),
        ),
        context,
    )
    assert draft.model == CHAIN[1] and new_calls == [CHAIN[1]] and len(calls) == 2
    assert records(database)[:2] == before


def test_crash_after_prepared_fallback_resumes_same_new_request(owner, monkeypatch):
    from tovitunes.persistence.requests import CreativeRequestLedger

    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return reply("" if calls[-1] == CHAIN[0] else '{"value":7}', identity=f"id-{len(calls)}")

    original = CreativeRequestLedger.start

    def crash(ledger, request_id):
        if ledger.get(request_id)["model"] == CHAIN[1]:
            raise KeyboardInterrupt()
        return original(ledger, request_id)

    with monkeypatch.context() as scoped:
        scoped.setattr(CreativeRequestLedger, "start", crash)
        with pytest.raises(KeyboardInterrupt):
            run(build(database, handler), context)
    before = records(database)
    assert before[1]["status"] == "prepared"
    draft = run(build(database, handler), context)
    assert calls == list(CHAIN[:2])
    assert draft.local_request_id == before[1]["request_id"]
    assert records(database)[0] == before[0]


@pytest.mark.parametrize("completed", [False, True])
def test_connection_error_after_response_begins_cannot_be_endpoint_fallback(owner, completed):
    database, context = owner
    calls = []

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            event = {
                "choices": [
                    {
                        "delta": {"content": '{"value":7}'},
                        "finish_reason": "stop" if completed else None,
                    }
                ]
            }
            yield f"data: {json.dumps(event)}\n\n".encode()
            try:
                raise ConnectionRefusedError("late connection loss")
            except ConnectionRefusedError as cause:
                raise httpx.ConnectError("lost after response") from cause

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Interrupted()
        )

    generator = build(database, handler, emergency=True)
    if completed:
        assert run(generator, context).output.value == 7
        assert records(database)[0]["status"] == "succeeded"
    else:
        with pytest.raises(ProviderError) as exc:
            run(generator, context)
        assert exc.value.ambiguous
        assert records(database)[0]["status"] == "ambiguous"
    assert len(calls) == 1


@pytest.mark.parametrize("finish", ["unknown", 42, {}])
def test_unknown_stream_finish_is_ambiguous(owner, finish):
    database, context = owner
    calls = []
    body = {"choices": [{"delta": {}, "finish_reason": finish}]}

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(body)}\n\n",
        )

    with pytest.raises(ProviderError) as exc:
        run(build(database, handler, emergency=True), context)
    assert exc.value.ambiguous and len(calls) == 1
    assert records(database)[0]["status"] == "ambiguous"


@pytest.mark.parametrize("content,finish", [(None, "stop"), ("", "stop"), ("{}", "length")])
def test_completed_json_terminal_answer_failures_advance(owner, content, finish):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        if len(calls) == 1:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]}
            )
        return reply()

    assert run(build(database, handler), context).model == CHAIN[1]
    assert calls == list(CHAIN[:2])
    assert records(database)[0]["error_kind"] == (
        "incomplete_answer" if finish == "length" else "empty_answer"
    )


def test_malformed_local_creative_configuration_does_not_echo_secret_or_start_request(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network"))
    config_file = tmp_path / "config.yaml"
    config_file.write_text("creative_llm:\n  api_key: forbidden-test-secret\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        main(["--config", str(config_file), "creative", "generate-next", "--live"])
    assert "forbidden-test-secret" not in str(exc.value)
    with pytest.raises(ValidationError) as nested:
        CreativeLLMConfig.model_validate({"api_key": "forbidden-test-secret"})
    assert "forbidden-test-secret" not in str(nested.value)


def test_emergency_invalid_structured_output_has_one_repair_and_no_further_fallback(owner):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        if request.url.host == "example.test":
            try:
                raise socket.gaierror("DNS unavailable")
            except OSError as cause:
                raise httpx.ConnectError("unreachable", request=request) from cause
        return httpx.Response(200, json={"done": True, "message": {"content": "invalid"}})

    for _ in range(2):
        with pytest.raises(ProviderError, match="exhausted"):
            run(build(database, handler, emergency=True), context)
    assert calls == [CHAIN[0], "qwen3.8:27b-q4_K_M", "qwen3.8:27b-q4_K_M"]
    assert [r["status"] for r in records(database)] == [
        "failed",
        "succeeded_response_invalid",
        "succeeded_response_invalid",
    ]


def test_factory_creates_no_http_clients_until_a_request_is_sent(owner, monkeypatch):
    database, _ = owner
    monkeypatch.setattr("httpx.Client", lambda *a, **k: pytest.fail("eager HTTP client"))
    config = CreativeLLMConfig(fallback_to_ollama_on_endpoint_failure=True)
    primary = NvidiaNIMClient(config)
    with creative_generator(database, config, primary) as generator:
        assert len(generator.transports) == 4
        assert generator.emergency is not None
    primary.close()


@pytest.mark.parametrize("repair", [False, True])
def test_tampered_stored_prompt_fails_closed_without_any_new_request(owner, repair):
    database, context = owner
    config = CreativeLLMConfig(base_url="https://example.test/v1")
    calls = []
    transport = NvidiaNIMClient(
        config,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: (
                    calls.append(r)
                    or reply("broken" if repair else '{"value":7}', identity=f"id-{len(calls)}")
                )
            )
        ),
    )
    if repair:
        with pytest.raises(StructuredOutputError):
            run(DurableStructuredGenerator(database, transport), context)
    else:
        run(DurableStructuredGenerator(database, transport), context)
    with database.connect() as db:
        db.execute(
            "UPDATE generation_requests SET messages_json='[]' WHERE attempt=?",
            (2 if repair else 1,),
        )
    before = records(database)
    with pytest.raises(ProviderError, match="contract differs"):
        run(build(database, lambda r: pytest.fail("resend")), context)
    assert records(database) == before
