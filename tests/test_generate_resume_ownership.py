"""Generate is fresh; explicit resume alone owns continuation of retained production."""

import json
import socket
import sqlite3
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient
from test_creative_fallback import CHAIN, build, reply
from test_studio_v1 import wait

from tovitunes.config import RuntimeConfig
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.orchestrator import ProductionReference, build_orchestrator
from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import CreativeRequestLedger
from tovitunes.pipeline.targets import ProductionTarget
from tovitunes.web import app as web_app


@pytest.fixture
def production(tmp_path, brand_root, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "offline-ownership-secret")
    connect = socket.socket.connect

    def offline_connect(sock, address):
        # Windows asyncio uses a loopback socket pair even for in-process TestClient.
        if address[0] != "127.0.0.1":
            pytest.fail("live network")
        return connect(sock, address)

    monkeypatch.setattr("socket.socket.connect", offline_connect)
    config = RuntimeConfig(
        database_path=tmp_path / "state.db", data_root=tmp_path / "assets", brand_root=brand_root
    )
    database = Database(config.database_path)
    database.migrate()
    return config, database


def snapshot(database):
    with closing(database.connect()) as db:
        return {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("creative_runs", "production_requests", "generation_requests")
        }


def successful_handler(calls):
    fake = FakeNIMTransport()

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["model"])
        result = fake.chat(payload["messages"], record_identity=lambda _: None)
        return reply(result.content, identity=f"offline-{len(calls)}")

    return handler


def exhaust(production):
    config, database = production
    calls, delays = [], []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        raise httpx.ReadTimeout("offline-ownership-secret", request=request)

    provider = build(database, handler)
    provider.sleep = delays.append
    result = build_orchestrator(config, creative_provider=provider).generate(ProductionTarget.DRAFT)
    assert result["status"] == "FAILED" and result["blocker"]["error_kind"] == "chain_exhausted"
    assert result["episode_key"] is None
    # PR42's live retry remains bounded to one successor per read timeout.
    assert calls == [model for model in CHAIN for _ in range(2)]
    assert delays == [2.0] * 4
    assert all(row["status"] == "ambiguous" for row in snapshot(database)["generation_requests"])
    return result


@pytest.mark.parametrize("old_count", [1, 3])
def test_new_generate_ignores_all_failed_runs_and_preserves_history(production, old_count):
    config, database = production
    old_runs = [exhaust(production)["run_id"] for _ in range(old_count)]
    before = snapshot(database)
    calls = []
    result = build_orchestrator(
        config, creative_provider=build(database, successful_handler(calls))
    ).generate(ProductionTarget.DRAFT)
    assert result["status"] == "COMPLETE"
    assert result["run_id"] not in old_runs and calls[0] == CHAIN[0]
    assert set(calls) == {CHAIN[0]}
    after = snapshot(database)
    for table, rows in before.items():
        assert after[table][: len(rows)] == rows
    assert len(after["creative_runs"]) == old_count + 1
    assert len(after["production_requests"]) == old_count + 1
    assert after["production_requests"][-1]["run_id"] == result["run_id"]
    fresh = after["generation_requests"][len(before["generation_requests"]) :]
    old_ids = {row["request_id"] for row in before["generation_requests"]}
    assert fresh[0]["run_id"] == result["run_id"]
    assert all(row["request_id"] not in old_ids for row in fresh)
    assert all(row["run_id"] not in old_runs for row in fresh)
    assert all(row["previous_attempt_id"] not in old_ids for row in fresh)


def test_explicit_resume_only_the_named_exhausted_run_without_replay(production):
    config, database = production
    old_runs = [exhaust(production)["run_id"] for _ in range(3)]
    before = snapshot(database)
    flow = build_orchestrator(
        config, creative_provider=build(database, lambda _: pytest.fail("replayed exhausted chain"))
    )
    result = flow.resume(
        reference=ProductionReference(run_id=old_runs[1]), target=ProductionTarget.DRAFT
    )
    assert result["run_id"] == old_runs[1] and result["episode_key"] is None
    assert result["blocker"]["error_kind"] == "chain_exhausted"
    assert snapshot(database) == before


@pytest.mark.parametrize("boundary", ["delay", "started_retry", "prepared_retry"])
def test_pre_episode_resume_preserves_pr42_receipts_and_retry_budget(
    production, monkeypatch, boundary
):
    config, database = production
    initial_calls = []

    def timeout(request):
        initial_calls.append(json.loads(request.content)["model"])
        raise httpx.ReadTimeout("offline timeout", request=request)

    provider = build(database, timeout)
    original_start = CreativeRequestLedger.start

    def crash(ledger, request_id):
        if ledger.get(request_id)["retry_index"] == 1:
            if boundary == "started_retry":
                original_start(ledger, request_id)
            raise SystemExit("offline retry boundary")
        original_start(ledger, request_id)

    with monkeypatch.context() as scoped:
        if boundary == "delay":
            provider.sleep = lambda _: (_ for _ in ()).throw(SystemExit("offline delay boundary"))
        else:
            scoped.setattr(CreativeRequestLedger, "start", crash)
        with pytest.raises(SystemExit):
            build_orchestrator(config, creative_provider=provider).generate(ProductionTarget.DRAFT)
    before = snapshot(database)
    run_id = before["creative_runs"][0]["run_id"]
    calls = []
    success = successful_handler(calls)

    def resumed(request):
        # Prepared retry is allowed to issue once with its original durable UUID/budget.
        if boundary == "prepared_retry" and json.loads(request.content)["model"] == CHAIN[0]:
            calls.append(CHAIN[0])
            raise httpx.ReadTimeout("offline timeout", request=request)
        return success(request)

    provider = build(Database(database.path), resumed)
    provider.sleep = lambda _: pytest.fail("reset spent retry budget")
    flow = build_orchestrator(config, creative_provider=provider)
    result = flow.resume(ProductionReference(run_id=run_id), ProductionTarget.DRAFT)
    assert result["status"] == "COMPLETE" and result["run_id"] == run_id
    assert initial_calls == [CHAIN[0]]
    assert calls[0] == (CHAIN[0] if boundary == "prepared_retry" else CHAIN[1])
    assert calls.count(CHAIN[0]) == (1 if boundary == "prepared_retry" else 0)
    after = snapshot(database)
    assert len(after["creative_runs"]) == 1
    assert after["production_requests"] == before["production_requests"]
    assert after["generation_requests"][0] == before["generation_requests"][0]
    if boundary != "delay":
        retry = after["generation_requests"][1]
        assert retry["request_id"] == before["generation_requests"][1]["request_id"]
        assert retry["retry_index"] == 1 and retry["status"] == "ambiguous"
    # Once bound, a run reference takes the ordinary episode continuation without effects.
    before_bound_resume = snapshot(database)
    calls_before = list(calls)
    again = flow.resume(ProductionReference(run_id=run_id), ProductionTarget.DRAFT)
    assert again["episode_key"] == result["episode_key"] and again["run_id"] == run_id
    assert calls == calls_before and snapshot(database) == before_bound_resume


def test_generate_donor_contract_never_reads_historical_production_requests(
    production, monkeypatch
):
    config, database = production
    exhaust(production)
    connect = Database.connect
    forbidden_reads = []

    def guarded_connect(database):
        connection = connect(database)

        def authorize(action, table, column, schema, source):
            if action == sqlite3.SQLITE_READ and table == "production_requests":
                forbidden_reads.append(column)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(Database, "connect", guarded_connect)
    result = build_orchestrator(
        config, creative_provider=build(database, successful_handler([]))
    ).generate(ProductionTarget.DRAFT)
    assert result["status"] == "COMPLETE" and forbidden_reads == []


@pytest.mark.parametrize(
    "fields",
    [{}, {"episode_key": "episode", "run_id": "run"}, {"run_id": ""}, {"episode_key": " "}],
)
def test_reference_requires_exactly_one_nonempty_identity(fields):
    with pytest.raises(ValueError):
        ProductionReference(**fields)


def test_resume_unknown_run_never_creates_or_adopts_production(production):
    config, database = production
    exhaust(production)
    before = snapshot(database)
    flow = build_orchestrator(config)
    flow._factories = {name: lambda *a: pytest.fail("built provider") for name in flow._factories}
    with pytest.raises(KeyError, match="unknown creative run"):
        flow.resume(ProductionReference(run_id="missing"), ProductionTarget.DRAFT)
    assert snapshot(database) == before


@pytest.mark.parametrize("reference", [None, {}, 7, ""])
def test_resume_invalid_identity_never_starts_generation(production, reference):
    config, database = production
    with pytest.raises((TypeError, ValueError)):
        build_orchestrator(config).resume(reference, ProductionTarget.DRAFT)
    assert snapshot(database)["creative_runs"] == []


def test_studio_create_recover_stop_and_restart_keep_exact_ownership(production, monkeypatch):
    config, database = production
    calls = []

    def timeout(request):
        calls.append(json.loads(request.content)["model"])
        raise httpx.ReadTimeout("Bearer offline-ownership-secret signed-url", request=request)

    flow = build_orchestrator(config, creative_provider=build(database, timeout))
    app = web_app.create_app(config)
    monkeypatch.setattr(web_app, "build_orchestrator", lambda *a, **k: flow)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        ids = []
        for _ in range(2):
            response = client.post("/api/studio/create", json={"target": "draft"})
            assert response.status_code == 202
            job = wait(app.state.jobs, response.json()["job_id"])
            assert job.status == "failed" and job.episode_key is None
            ids.append((job.job_id, job.result["run_id"]))
        assert ids[0][1] != ids[1][1]
        assert calls == [model for model in CHAIN for _ in range(2)] * 2
        before = snapshot(database)
        # Reload durable job payloads; recover the older run, never the newest pending one.
    app.state.jobs.close()
    app = web_app.create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        response = client.post(f"/api/studio/jobs/{ids[0][0]}/recover")
        assert response.status_code == 202
        recovered = wait(app.state.jobs, ids[0][0])
        assert recovered.result["run_id"] == ids[0][1] and recovered.execution == 2
        assert snapshot(database) == before
        assert len(calls) == 16
        payload = client.get(f"/api/jobs/{ids[0][0]}").json()
        assert len(payload["creative_diagnostics"]["attempts"]) == 8
        assert all(
            secret not in json.dumps(payload)
            for secret in ("Bearer", "signed-url", "offline-ownership-secret")
        )
        assert client.post(f"/api/studio/jobs/{ids[0][0]}/stop").status_code == 200
        assert client.post(f"/api/studio/jobs/{ids[0][0]}/recover").status_code == 409
        response = client.post("/api/studio/create", json={"target": "draft"})
        fresh = wait(app.state.jobs, response.json()["job_id"])
        assert fresh.result["run_id"] not in {run_id for _, run_id in ids}
        after = snapshot(database)
        for table, rows in before.items():
            assert after[table][: len(rows)] == rows
        assert len(calls) == 24
    app.state.jobs.close()


@pytest.mark.parametrize("episode_key", [None, "saved-episode"])
def test_studio_recovery_uses_job_identity_and_preserves_it_after_worker_error(
    production, monkeypatch, episode_key
):
    config, database = production
    references = []

    class Workflow:
        def generate(self, target):
            return {"status": "FAILED", "episode_key": episode_key, "run_id": "saved-run"}

        def resume(self, reference, target):
            references.append(reference)
            raise RuntimeError("offline recovery failure")

    monkeypatch.setattr(web_app, "build_orchestrator", lambda *a, **k: Workflow())
    app = web_app.create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        response = client.post("/api/studio/create", json={"target": "draft"})
        job_id = response.json()["job_id"]
        wait(app.state.jobs, job_id)
        for _ in range(2):
            assert client.post(f"/api/studio/jobs/{job_id}/recover").status_code == 202
            failed = wait(app.state.jobs, job_id)
            assert failed.result["run_id"] == "saved-run"
        expected = (
            ProductionReference(episode_key=episode_key)
            if episode_key
            else ProductionReference(run_id="saved-run")
        )
        assert references == [expected, expected]
    app.state.jobs.close()


def test_studio_recovery_without_identity_cannot_call_generate(production, monkeypatch):
    config, _ = production

    class Workflow:
        def generate(self, target):
            return {"status": "FAILED"}

    monkeypatch.setattr(web_app, "build_orchestrator", lambda *a, **k: Workflow())
    app = web_app.create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        response = client.post("/api/studio/create", json={"target": "draft"})
        job_id = response.json()["job_id"]
        wait(app.state.jobs, job_id)
        assert client.post(f"/api/studio/jobs/{job_id}/recover").status_code == 409
        assert snapshot(Database(config.database_path))["creative_runs"] == []
    app.state.jobs.close()
