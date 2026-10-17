"""PR43's retired-model behavior, adapted to immutable ToviTunes requests."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml
from test_creative_fallback import build, records, reply, run
from test_creative_fallback import owner as owner

from tovitunes.config import CreativeLLMConfig
from tovitunes.creative.failures import FailureScope, classify_creative_failure
from tovitunes.creative.provider import (
    ProviderError,
    http_failure,
    stored_failure,
)
from tovitunes.errors import FailureCategory
from tovitunes.persistence.requests import CreativeRequestLedger

LEGACY_CHAIN = (
    "moonshotai/kimi-k3",
    "z-ai/glm-5.3",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "deepseek-ai/deepseek-v4.1-flash",
)


def legacy_config(**updates):
    return CreativeLLMConfig(model=LEGACY_CHAIN[0], fallback_models=LEGACY_CHAIN[1:], **updates)


def retain_old_failure(
    database, context, *, status="failed", category="configuration", reason=None, provider="nvidia"
):
    # Create the old diagnostic before terminalization; never rewrite terminal evidence.
    transport = build(
        database, lambda r: pytest.fail("seed sent request"), config=legacy_config()
    ).transports[0]
    from test_creative_fallback import Answer

    from tovitunes.creative.provider import canonical, fingerprint, structured_json_instruction

    messages = [structured_json_instruction(Answer.model_json_schema())]
    digest = fingerprint(
        dict(
            provider=provider,
            model=transport.model_name,
            settings=transport.settings,
            prompt_version=context.prompt_version,
            messages=messages,
        )
    )
    ledger = CreativeRequestLedger(database)
    row = ledger.prepare_creative(
        episode_id=context.episode_id,
        run_id=context.run_id,
        kind=context.kind,
        provider=provider,
        model=transport.model_name,
        prompt_version=context.prompt_version,
        input_fingerprint=digest,
        messages_json=canonical(messages),
    )
    ledger.start(row["request_id"])
    ledger.finish(
        row["request_id"],
        status,
        error_kind=category,
        error_reason=reason or f"NVIDIA NIM HTTP 410 for {LEGACY_CHAIN[0]}",
    )
    return records(database)[0]


def test_http_410_model_scope_without_retry():
    category = http_failure(410, nvidia_transient=True)
    assert category == FailureCategory.MODEL_UNAVAILABLE
    decision = classify_creative_failure(ProviderError("retired", category=category))
    assert decision.scope == FailureScope.MODEL and decision.retry_delays == ()
    assert http_failure(410) == FailureCategory.CONFIGURATION


def test_donor_410_immediate_fallback_and_sticky_reuse(owner):
    database, context = owner
    calls, sleeps = [], []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        return httpx.Response(410) if model == LEGACY_CHAIN[0] else reply()

    generator = build(database, handler, config=legacy_config())
    generator.sleep = sleeps.append
    first = run(generator, context)
    retained = records(database)
    assert first.model == LEGACY_CHAIN[1]
    assert calls == list(LEGACY_CHAIN[:2]) and sleeps == []
    assert retained[0]["error_kind"] == "model_unavailable"
    assert retained[1]["fallback_reason"] == "model_unavailable"
    assert (
        run(build(database, lambda r: pytest.fail("resend"), config=legacy_config()), context)
        == first
    )
    assert records(database) == retained


@pytest.mark.parametrize("planning", [False, True])
def test_exact_historical_terminal_410_preserves_entire_row(owner, planning, catalog):
    database, context = owner
    if planning:
        context = replace(
            context,
            episode_id=None,
            run_id="59f71b9b-cee2-47d8-994b-e37552a33942",
            kind="subject_pool",
        )
    if planning:
        with database.connect() as db:
            db.execute(
                "INSERT INTO creative_runs (run_id,brand_revision_id,curriculum_revision_id,"
                "provider,model,prompt_version,input_fingerprint,input_json,status,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,'planning','old','old')",
                (
                    context.run_id,
                    catalog.version.revision_id,
                    catalog.curriculum_revision.revision_id,
                    "nvidia",
                    LEGACY_CHAIN[0],
                    context.prompt_version,
                    "planning-fingerprint",
                    "{}",
                ),
            )
    before = retain_old_failure(database, context)
    calls = []
    draft = run(
        build(
            database,
            lambda r: calls.append(json.loads(r.content)["model"]) or reply(),
            config=legacy_config(),
        ),
        context,
    )
    after = records(database)
    assert calls == [LEGACY_CHAIN[1]] and draft.model == LEGACY_CHAIN[1]
    assert after[0] == before  # includes identity, status, model, hash, messages and error evidence
    assert after[1]["previous_attempt_id"] == before["request_id"]
    assert after[1]["requested_model"] == LEGACY_CHAIN[0]
    assert after[1]["fallback_index"] == 1
    assert after[1]["fallback_reason"] == "model_unavailable"
    assert after[1]["input_fingerprint"] != before["input_fingerprint"]


@pytest.mark.parametrize("change", ["temperature", "missing_model"])
def test_historical_410_still_requires_original_contract(owner, change):
    database, context = owner
    before = retain_old_failure(database, context)
    config = legacy_config(temperature=0.2) if change == "temperature" else legacy_config()
    if change == "missing_model":
        config = CreativeLLMConfig()
    generator = build(database, lambda r: pytest.fail("contract changed"), config=config)
    with pytest.raises(ProviderError, match="settings differ|absent from configured chain"):
        run(generator, context)
    assert records(database) == [before]


@pytest.mark.parametrize(
    "updates,expected",
    [
        ({"status": "ambiguous"}, FailureCategory.AMBIGUOUS),
        ({"category": "authentication"}, FailureCategory.AUTHENTICATION),
        ({"reason": "NVIDIA NIM HTTP 410 for another/model"}, FailureCategory.CONFIGURATION),
        (
            {"reason": "NVIDIA NIM HTTP 410 for moonshotai/kimi-k3 extra"},
            FailureCategory.CONFIGURATION,
        ),
        ({"reason": "NVIDIA NIM HTTP 400 for moonshotai/kimi-k3"}, FailureCategory.CONFIGURATION),
        ({"provider": "other"}, FailureCategory.CONFIGURATION),
    ],
)
def test_historical_interpretation_is_exact_and_keeps_safety_policies(owner, updates, expected):
    database, context = owner
    before = retain_old_failure(database, context, **updates)
    assert stored_failure(before) == expected
    assert records(database) == [before]


def test_future_defaults_match_example_and_have_no_duplicates():
    config = CreativeLLMConfig()
    example = yaml.safe_load(Path("config.example.yaml").read_text())["creative_llm"]
    expected = (
        "z-ai/glm-5.3",
        "z-ai/glm-5.3-flash",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "deepseek-ai/deepseek-v4.1-flash",
    )
    assert (config.model, *config.fallback_models) == expected
    assert (example["model"], *example["fallback_models"]) == expected
    assert len(set(m.casefold() for m in expected)) == len(expected)
