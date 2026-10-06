"""Historical PR40 evidence stays immutable; normal production advances automatically."""

import json
import sqlite3

import httpx
import pytest
from test_creative_fallback import CHAIN, build, records, reply, run
from test_creative_fallback import owner as owner

from tovitunes.config import CreativeLLMConfig
from tovitunes.creative.provider import DurableStructuredGenerator
from tovitunes.domain.episode import Episode
from tovitunes.errors import CreativeAmbiguity, ProviderError
from tovitunes.persistence.creative_reconciliation import CreativeReconciliations
from tovitunes.persistence.db import Database


def seed_ambiguity(database, context):
    transport = build(database, lambda r: httpx.Response(504)).transports[0]
    with pytest.raises(CreativeAmbiguity):
        run(DurableStructuredGenerator(database, transport), context)
    return records(database)[-1]


def record_historical_decision(database, request_id):
    with database.connect() as db:
        db.execute(
            "INSERT INTO creative_request_reconciliations VALUES "
            "('historical-decision',?,'abandon_remote_result','human:fixture',"
            "'Historical audit fixture',NULL,'old')",
            (request_id,),
        )


@pytest.mark.parametrize("position", range(4))
@pytest.mark.parametrize("repair", [False, True])
def test_ambiguity_at_each_position_and_repair_advances_once(owner, position, repair):
    database, context = owner
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        index = CHAIN.index(model)
        if index < position:
            return reply("", identity=f"request-{len(calls)}")
        if index == position:
            if repair and calls.count(model) == 1:
                return reply("invalid JSON", identity=f"request-{len(calls)}")
            return httpx.Response(504, text="private-response-body")
        return reply(identity=f"request-{len(calls)}")

    if position == 3:
        with pytest.raises(ProviderError, match="exhausted"):
            run(build(database, handler), context)
    else:
        draft = run(build(database, handler), context)
        assert draft.model == CHAIN[position + 1]
    retained = records(database)
    assert calls == list(CHAIN[:position]) + [CHAIN[position]] * (2 if repair else 1) + list(
        CHAIN[position + 1 : position + 2]
    )
    ambiguous = [r for r in retained if r["status"] == "ambiguous"]
    assert len(ambiguous) == 1
    assert "private-response-body" not in json.dumps(retained)
    with database.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM creative_request_reconciliations").fetchone()[0] == 0
        )
    if position == 3:
        with pytest.raises(ProviderError, match="exhausted"):
            run(build(Database(database.path), lambda r: pytest.fail("resend")), context)
    else:
        assert (
            run(build(Database(database.path), lambda r: pytest.fail("resend")), context) == draft
        )
    assert records(database) == retained


def test_historical_decision_is_immutable_and_not_required_for_advance(owner):
    database, context = owner
    target = seed_ambiguity(database, context)
    record_historical_decision(database, target["request_id"])
    decision = dict(CreativeReconciliations(database).get(target["request_id"]))
    with database.connect() as db:
        for sql in (
            "UPDATE creative_request_reconciliations SET rationale='changed'",
            "DELETE FROM creative_request_reconciliations",
            "INSERT OR REPLACE INTO creative_request_reconciliations "
            "SELECT * FROM creative_request_reconciliations",
            "UPDATE generation_requests SET status='failed'",
            "DELETE FROM generation_requests",
            "INSERT OR REPLACE INTO generation_requests SELECT * FROM generation_requests",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    draft = run(build(database, lambda r: reply(identity="glm-success")), context)
    assert draft.model == CHAIN[1]
    assert records(database)[0] == target
    assert dict(CreativeReconciliations(database).get(target["request_id"])) == decision


def test_ambiguous_chain_rejects_changed_settings_and_missing_model(owner):
    database, context = owner
    target = seed_ambiguity(database, context)
    for config, message in (
        (CreativeLLMConfig(base_url="https://example.test/v1", temperature=0.2), "settings differ"),
        (
            CreativeLLMConfig(
                model=CHAIN[1], fallback_models=CHAIN[2:], base_url="https://example.test/v1"
            ),
            "absent from configured chain",
        ),
    ):
        with pytest.raises(ProviderError, match=message):
            run(build(database, lambda r: pytest.fail("changed request"), config=config), context)
    assert records(database) == [target]


def test_unexpected_programming_exception_fails_closed(owner):
    database, context = owner
    calls = []

    def handler(request):
        calls.append(request)
        raise RuntimeError("offline programming error")

    with pytest.raises(RuntimeError, match="offline programming error"):
        run(build(database, handler), context)
    assert len(calls) == 1 and records(database)[0]["status"] == "ambiguous"
    assert "offline programming error" not in json.dumps(records(database))


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
    record_historical_decision(database, "historical-ambiguous")
    assert records(database)[0]["status"] == "ambiguous"
