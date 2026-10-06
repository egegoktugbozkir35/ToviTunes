"""Offline operator recovery at every configured chain position, including repair/restarts."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from test_creative_fallback import CHAIN, build, records, reply, run

from tovitunes.cli import main
from tovitunes.config import CreativeLLMConfig, RuntimeConfig
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.provider import CreativeAmbiguity, GenerationContext, ProviderError
from tovitunes.creative.recovery import request_status
from tovitunes.creative.workflow import CreativeWorkflow
from tovitunes.domain.episode import Episode
from tovitunes.persistence.creative_reconciliation import CreativeReconciliations
from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import CreativeRequestLedger


@pytest.fixture
def owner(tmp_path, catalog, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network"))
    database = Database(tmp_path / "state.db")
    database.migrate()
    episode = Episode.create(catalog, "red", "reconciliation-fixture")
    database.create_episode(catalog, episode)
    return database, GenerationContext("episode_spec", "test-v1", episode_id=episode.episode_id)


def abandon(database, request_id):
    return CreativeReconciliations(database).abandon(
        request_id, actor="human:operator", rationale="No recoverable remote result."
    )


def ambiguous(database, context):
    with pytest.raises(CreativeAmbiguity):
        run(build(database, lambda r: httpx.Response(504)), context)
    return records(database)[-1]


@pytest.mark.parametrize("position", range(4))
@pytest.mark.parametrize("repair", [False, True])
def test_every_position_stops_then_advances_once_with_immutable_history(owner, position, repair):
    database, context = owner
    calls = []

    def before_reconciliation(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if CHAIN.index(model) < position:
            return reply("", identity=f"id-{len(calls)}")
        if repair and calls.count(model) == 1:
            return reply("invalid JSON", identity=f"id-{len(calls)}")
        return httpx.Response(504, text="credential-and-signed-response-body")

    for _ in range(2):
        with pytest.raises(CreativeAmbiguity) as exc:
            run(build(database, before_reconciliation, emergency=True), context)
    before = records(database)
    target = before[-1]
    assert calls == list(CHAIN[:position]) + [CHAIN[position]] * (2 if repair else 1)
    assert target["status"] == "ambiguous"
    assert target["kind"] == ("episode_spec_repair" if repair else "episode_spec")
    assert exc.value.evidence["request_id"] == target["request_id"]
    assert "credential-and-signed-response-body" not in json.dumps(before)
    assert "credential-and-signed-response-body" not in str(exc.value)
    decision = abandon(database, target["request_id"])
    assert decision["action"] == "abandon_remote_result"
    assert records(database) == before
    restarted = Database(database.path)
    restarted.migrate()

    def after_reconciliation(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        return reply(identity=f"id-{len(calls)}")

    if position == len(CHAIN) - 1:
        for _ in range(2):
            with pytest.raises(ProviderError, match="exhausted"):
                run(build(restarted, after_reconciliation, emergency=True), context)
        assert records(database) == before
        assert request_status(database, CreativeLLMConfig(), target["request_id"])[
            "chain_exhausted"
        ]
        return
    draft = run(build(restarted, after_reconciliation), context)
    assert draft.model == CHAIN[position + 1]
    saved = records(database)
    assert saved[: len(before)] == before
    assert saved[-1]["previous_attempt_id"] == target["request_id"]
    assert saved[-1]["fallback_reason"] == "operator_abandoned_ambiguous"
    assert saved[-1]["fallback_index"] == position + 1
    assert saved[-1]["requested_model"] == CHAIN[0]
    assert saved[-1]["request_id"] != target["request_id"]
    assert run(build(restarted, lambda r: pytest.fail("duplicate request")), context) == draft
    assert records(database) == saved
    # New stage starts at the successful fallback, including after a fresh process.
    run(build(Database(database.path), after_reconciliation), replace(context, kind="lyrics"))
    assert calls[-2:] == [CHAIN[position + 1], CHAIN[position + 1]]


def test_reconciliation_then_empty_answer_then_invalid_repair_continues_full_chain(owner):
    database, context = owner
    original = ambiguous(database, context)
    abandon(database, original["request_id"])
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == CHAIN[1]:
            return reply("", identity=f"id-{len(calls)}")
        if model == CHAIN[2]:
            return reply("invalid JSON", identity=f"id-{len(calls)}")
        return reply(identity=f"id-{len(calls)}")

    draft = run(build(Database(database.path), handler), context)
    assert draft.model == CHAIN[3]
    assert calls == [CHAIN[1], CHAIN[2], CHAIN[2], CHAIN[3]]
    saved = records(database)
    assert saved[0] == original
    assert [row["fallback_index"] for row in saved] == [0, 1, 2, 2, 3]
    assert saved[1]["fallback_reason"] == "operator_abandoned_ambiguous"
    assert saved[2]["fallback_reason"] == "empty_answer"
    assert saved[4]["fallback_reason"] == "structured_output"
    assert saved[4]["previous_attempt_id"] == saved[3]["request_id"]
    assert len(calls) == 4
    assert run(build(Database(database.path), lambda r: pytest.fail("resend")), context) == draft


def test_second_ambiguity_requires_separate_decision_then_sticky_moves_forward(owner):
    database, context = owner
    first = ambiguous(database, context)
    abandon(database, first["request_id"])
    run(build(database, lambda r: reply(identity="glm-spec")), context)
    next_stage = replace(context, kind="lyrics")
    second = ambiguous(database, next_stage)
    assert second["model"] == CHAIN[1]
    with pytest.raises(CreativeAmbiguity):
        run(build(Database(database.path), lambda r: pytest.fail("unreconciled")), next_stage)
    abandon(database, second["request_id"])
    calls = []
    run(
        build(database, lambda r: calls.append(json.loads(r.content)["model"]) or reply()),
        next_stage,
    )
    run(
        build(
            Database(database.path),
            lambda r: (
                calls.append(json.loads(r.content)["model"]) or reply(identity="nemotron-music")
            ),
        ),
        replace(context, kind="music_spec"),
    )
    assert calls == [CHAIN[2], CHAIN[2]]
    assert records(database)[0] == first
    assert records(database)[2] == second


def test_later_conclusive_failure_moves_sticky_model_without_more_reconciliation(owner):
    database, context = owner
    first = ambiguous(database, context)
    abandon(database, first["request_id"])
    run(build(database, lambda r: reply(identity="glm-spec")), context)
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        return reply("" if model == CHAIN[1] else '{"value":7}', identity=f"id-{len(calls)}")

    run(build(Database(database.path), handler), replace(context, kind="lyrics"))
    run(build(Database(database.path), handler), replace(context, kind="music_spec"))
    assert calls == [CHAIN[1], CHAIN[2], CHAIN[2]]
    assert records(database)[0] == first
    with database.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM creative_request_reconciliations").fetchone()[0] == 1
        )


def test_unknown_episode_owner_is_not_eligible_even_for_orphaned_historical_data(owner):
    database, context = owner
    target = ambiguous(database, context)
    with sqlite3.connect(database.path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("UPDATE generation_requests SET episode_id='unknown-owner'")
    with pytest.raises(ValueError, match="not eligible"):
        abandon(database, target["request_id"])


def test_restart_after_new_fallback_failure_uses_next_position(owner):
    database, context = owner
    first = ambiguous(database, context)
    abandon(database, first["request_id"])
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == CHAIN[2]:
            raise KeyboardInterrupt()
        return reply("", identity="glm-failed")

    with pytest.raises(KeyboardInterrupt):
        run(build(database, handler), context)
    # Nemotron has already started: restart turns it into ambiguity, never resends it.
    with pytest.raises(CreativeAmbiguity):
        run(build(Database(database.path), lambda r: pytest.fail("resend")), context)
    saved = records(database)
    assert saved[0] == first and saved[1]["error_kind"] == "empty_answer"
    assert saved[2]["status"] == "ambiguous"
    assert calls == [CHAIN[1], CHAIN[2]]


def test_restart_reuses_prepared_reconciled_fallback(owner, monkeypatch):
    database, context = owner
    target = ambiguous(database, context)
    abandon(database, target["request_id"])
    original_start = CreativeRequestLedger.start

    def crash_before_start(ledger, request_id):
        if ledger.get(request_id)["model"] == CHAIN[1]:
            raise KeyboardInterrupt()
        return original_start(ledger, request_id)

    with monkeypatch.context() as scoped:
        scoped.setattr(CreativeRequestLedger, "start", crash_before_start)
        with pytest.raises(KeyboardInterrupt):
            run(build(database, lambda r: pytest.fail("not started")), context)
    prepared = records(database)[-1]
    calls = []
    draft = run(build(Database(database.path), lambda r: calls.append(r) or reply()), context)
    assert draft.local_request_id == prepared["request_id"]
    assert len(calls) == 1 and len(records(database)) == 2


def test_config_order_and_arbitrary_models_drive_reconciliation(owner):
    database, context = owner
    config = CreativeLLMConfig(
        model="example/model-one",
        fallback_models=("example/model-three", "example/model-two"),
        base_url="https://example.test/v1",
    )
    with pytest.raises(CreativeAmbiguity):
        run(build(database, lambda r: httpx.Response(504), config=config), context)
    abandon(database, records(database)[-1]["request_id"])
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        return reply("" if model == "example/model-three" else '{"value":7}', identity=model)

    draft = run(build(Database(database.path), handler, config=config), context)
    assert calls == ["example/model-three", "example/model-two"]
    assert draft.model == "example/model-two"


def test_abandonment_does_not_authorize_changed_input_or_missing_configured_model(owner):
    from test_creative_fallback import Answer

    from tovitunes.persistence.requests import InvalidRequestTransition

    database, context = owner
    target = ambiguous(database, context)
    abandon(database, target["request_id"])
    with pytest.raises(InvalidRequestTransition, match="unresolved"):
        build(database, lambda r: pytest.fail("changed input")).generate(
            Answer, [{"role": "user", "content": "different input"}], context=context
        )
    config = CreativeLLMConfig(
        model=CHAIN[1], fallback_models=CHAIN[2:], base_url="https://example.test/v1"
    )
    with pytest.raises(ProviderError, match="absent from configured chain"):
        run(build(database, lambda r: pytest.fail("missing model"), config=config), context)
    assert records(database) == [target]


def test_unknown_exception_after_start_remains_typed_ambiguity_without_secret(owner):
    database, context = owner

    def handler(request):
        raise RuntimeError("credentials-and-signed-URL")

    with pytest.raises(CreativeAmbiguity) as exc:
        run(build(database, handler), context)
    assert exc.value.evidence["request_id"] == records(database)[0]["request_id"]
    assert "credentials-and-signed-URL" not in str(exc.value)
    assert "credentials-and-signed-URL" not in json.dumps(records(database))
    abandon(database, records(database)[0]["request_id"])
    assert run(build(database, lambda r: reply()), context).model == CHAIN[1]


def test_ambiguity_with_saved_receipt_guides_inspection_instead_of_abandonment(owner):
    from hashlib import sha256

    database, context = owner
    target = ambiguous(database, context)
    with database.connect() as db:
        db.execute(
            "UPDATE generation_requests SET response_content='{}',response_sha256=?",
            (sha256(b"{}").hexdigest(),),
        )
    with pytest.raises(CreativeAmbiguity) as exc:
        run(build(database, lambda r: pytest.fail("resend")), context)
    assert exc.value.evidence["recovery_action"] == "inspect_durable_receipt"
    assert "request-status" in exc.value.evidence["recovery_command"]
    with pytest.raises(ValueError, match="not eligible"):
        abandon(database, target["request_id"])


@pytest.mark.parametrize("status", [401, 403, 400])
def test_auth_and_configuration_rejection_after_abandonment_still_stop(owner, status):
    database, context = owner
    target = ambiguous(database, context)
    abandon(database, target["request_id"])
    calls = []
    with pytest.raises(ProviderError) as exc:
        run(build(database, lambda r: calls.append(r) or httpx.Response(status)), context)
    assert not exc.value.ambiguous and len(calls) == 1
    assert records(database)[-1]["model"] == CHAIN[1]


def test_changed_inference_settings_fail_closed_after_reconciliation(owner):
    database, context = owner
    target = ambiguous(database, context)
    abandon(database, target["request_id"])
    config = CreativeLLMConfig(base_url="https://example.test/v1", temperature=0.2)
    with pytest.raises(ProviderError, match="settings differ"):
        run(build(database, lambda r: pytest.fail("changed settings"), config=config), context)
    assert records(database) == [target]


def test_reconciliation_is_idempotent_concurrent_and_immutable_in_sql(owner):
    database, context = owner
    target = ambiguous(database, context)
    with ThreadPoolExecutor(max_workers=3) as workers:
        decisions = list(
            workers.map(lambda _: dict(abandon(database, target["request_id"])), range(3))
        )
    assert decisions[0] == decisions[1] == decisions[2]
    with pytest.raises(ValueError, match="immutable"):
        CreativeReconciliations(database).abandon(
            target["request_id"], actor="other:operator", rationale="Different rationale."
        )
    with database.connect() as db:
        for sql in (
            "UPDATE creative_request_reconciliations SET rationale='changed'",
            "DELETE FROM creative_request_reconciliations",
            "INSERT OR REPLACE INTO creative_request_reconciliations "
            "SELECT reconciliation_id,request_id,action,actor,rationale,evidence_uri,created_at "
            "FROM creative_request_reconciliations",
            "UPDATE generation_requests SET status='failed'",
            "DELETE FROM generation_requests",
            "INSERT OR REPLACE INTO generation_requests SELECT * FROM generation_requests",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    assert records(database) == [target]
    assert dict(CreativeReconciliations(database).get(target["request_id"])) == decisions[0]


def test_additive_upgrade_preserves_all_existing_tables_and_ambiguous_rows(
    tmp_path,
    catalog,
    monkeypatch,
):
    from importlib import resources

    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for item in resources.files("tovitunes.persistence.migrations").iterdir():
        if item.name.endswith(".sql") and item.name < "0021":
            (old_migrations / item.name).write_bytes(item.read_bytes())
    database = Database(tmp_path / "old.db")
    with monkeypatch.context() as scoped:
        scoped.setattr("tovitunes.persistence.db.resources.files", lambda _: old_migrations)
        database.migrate()
    episode = Episode.create(catalog, "red", "historical-red-migration")
    database.create_episode(catalog, episode)
    with database.connect() as db:
        db.execute(
            "INSERT INTO generation_requests "
            "(request_id,episode_id,kind,slot_key,provider,model,input_fingerprint,status,"
            "created_at,updated_at,prompt_version,messages_json,error_kind) "
            "VALUES (?,?, 'episode_spec','main','nvidia',?,?,'ambiguous','old','old',"
            "'test-v1','[]','ambiguous')",
            ("historical-ambiguous", episode.episode_id, CHAIN[0], "f" * 64),
        )
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'"
            )
        ]
        before = {
            name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}"')] for name in tables
        }
    database.migrate()
    with database.connect() as db:
        after = {
            name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}"')] for name in tables
        }
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert after == before
    abandon(database, "historical-ambiguous")
    assert records(database)[0]["status"] == "ambiguous"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", s)
        for s in ("prepared", "remote_started", "failed", "succeeded", "succeeded_response_invalid")
    ]
    + [("kind", k) for k in ("music", "image", "publication", "unknown")]
    + [
        ("prompt_version", None),
        ("prompt_version", ""),
        ("messages_json", None),
        ("response_content", "{}"),
        ("response_sha256", "f" * 64),
    ],
)
def test_invalid_reconciliation_targets_are_rejected_without_mutation(owner, field, value):
    database, context = owner
    target = ambiguous(database, context)
    with database.connect() as db:
        db.execute(f"UPDATE generation_requests SET {field}=?", (value,))
    before = records(database)
    with pytest.raises(ValueError, match="not eligible"):
        abandon(database, target["request_id"])
    assert records(database) == before
    assert CreativeReconciliations(database).get(target["request_id"]) is None


def test_successful_replacement_prevents_late_abandonment(owner):
    database, context = owner
    target = ambiguous(database, context)
    # Model historical replacement evidence without altering the ambiguous original.
    other = replace(context, kind="lyrics")
    run(build(database, lambda r: reply()), other)
    with database.connect() as db:
        db.execute(
            "UPDATE generation_requests SET previous_attempt_id=? WHERE status='succeeded'",
            (target["request_id"],),
        )
    with pytest.raises(ValueError, match="not eligible"):
        abandon(database, target["request_id"])
    assert records(database)[0] == target


@pytest.mark.parametrize(
    "kwargs",
    [
        {"actor": "", "rationale": "reason"},
        {"actor": "human:operator", "rationale": ""},
        {
            "actor": "human:operator",
            "rationale": "reason",
            "evidence_uri": "https://x/a?token=secret",
        },
        {
            "actor": "human:operator",
            "rationale": "reason",
            "evidence_uri": "https://user:secret@x/a",
        },
    ],
)
def test_invalid_operator_metadata_rejected_without_echoing_it(owner, kwargs):
    database, context = owner
    target = ambiguous(database, context)
    with pytest.raises(ValueError) as exc:
        CreativeReconciliations(database).abandon(target["request_id"], **kwargs)
    assert "secret" not in str(exc.value)
    assert CreativeReconciliations(database).get(target["request_id"]) is None


def test_cli_records_only_and_status_never_constructs_provider(
    owner, tmp_path, capsys, monkeypatch
):
    database, context = owner
    target = ambiguous(database, context)
    config = tmp_path / "config.yaml"
    config.write_text(
        f"database_path: {database.path.as_posix()}\n"
        f"data_root: {tmp_path.as_posix()}/data\nbrand_root: {tmp_path.as_posix()}/brand\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("NVIDIA_API_KEY")
    monkeypatch.setattr("tovitunes.cli.NvidiaNIMClient", lambda *a: pytest.fail("provider created"))
    monkeypatch.setattr("httpx.Client", lambda *a, **k: pytest.fail("HTTP client created"))
    prefix = ["--config", str(config), "creative"]
    assert main([*prefix, "request-status", "--request-id", target["request_id"]]) == 0
    before = json.loads(capsys.readouterr().out)
    assert before["reconciliation"] is None and before["next_configured_model"] is None
    assert (
        main(
            [
                *prefix,
                "reconcile",
                "--request-id",
                target["request_id"],
                "--action",
                "abandon-remote-result",
                "--actor",
                "human:operator",
                "--reason",
                "No usable result.",
                "--evidence-uri",
                "https://example.test/incident",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    after = json.loads(output)
    assert after["provider_calls"] == 0
    assert after["status"] == "ambiguous"
    assert after["reconciliation"]["action"] == "abandon_remote_result"
    assert after["next_configured_model"]["model"] == CHAIN[1]
    assert "offline-secret" not in output and "response_content" not in output
    assert records(database) == [target]
    with pytest.raises(SystemExit) as exc:
        main(
            [
                *prefix,
                "reconcile",
                "--request-id",
                "not-a-request",
                "--action",
                "abandon-remote-result",
                "--actor",
                "human:operator",
                "--reason",
                "reason",
            ]
        )
    assert exc.value.code == 2


def test_open_topic_planning_reconciles_and_sticks_across_episode_owner(
    tmp_path,
    brand_root,
    catalog,
    monkeypatch,
):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-secret")
    monkeypatch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network"))
    config = RuntimeConfig(
        database_path=tmp_path / "db",
        data_root=tmp_path / "data",
        brand_root=brand_root,
    )
    database = Database(config.database_path)
    database.migrate()
    # Reserve a historical episode and verify all its durable rows remain identical.
    historical = Episode.create(catalog, "red", "colors-red-history")
    database.create_episode(catalog, historical)
    original_episode = database.get_episode(historical.episode_id).model_dump_json()
    workflow = CreativeWorkflow(
        config, build(database, lambda r: httpx.Response(504)), catalog=catalog
    )
    with pytest.raises(CreativeAmbiguity):
        workflow.generate_next()
    target = records(database)[-1]
    assert target["kind"] == "subject_pool" and target["run_id"] and not target["episode_id"]
    abandon(database, target["request_id"])
    fake = FakeNIMTransport()
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["model"])
        result = fake.chat(payload["messages"], record_identity=lambda _: None)
        return reply(result.content, identity=f"id-{len(calls)}")

    result = CreativeWorkflow(
        config, build(Database(database.path), handler), catalog=catalog
    ).generate_next()
    assert calls == [CHAIN[1]] * 4
    assert result["run_id"] == target["run_id"]
    assert records(database)[0] == target
    assert database.get_episode(historical.episode_id).model_dump_json() == original_episode
    before = records(database)
    CreativeWorkflow(
        config,
        build(Database(database.path), lambda r: pytest.fail("completed episode resent")),
        catalog=catalog,
    ).generate_next(episode_key=result["episode_key"])
    assert records(database) == before
