"""Generate is fresh; explicit resume alone owns continuation of retained production."""

import json
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_creative_fallback import CHAIN, build, reply
from test_studio_v1 import wait

from tovitunes.config import RuntimeConfig
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.service import CreativeService
from tovitunes.errors import ExecutionOwnershipLostError
from tovitunes.orchestrator import ProductionReference, build_orchestrator
from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import CreativeRequestLedger
from tovitunes.pipeline.targets import ProductionTarget
from tovitunes.web import app as web_app
from tovitunes.web.jobs import JobManager


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
        def generate(self, target, **kwargs):
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
        def generate(self, target, **kwargs):
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


@pytest.mark.parametrize("failure", [RuntimeError, ExecutionOwnershipLostError])
def test_initial_studio_exception_retains_run_and_receipt_across_restart(
    production, monkeypatch, failure
):
    config, database = production
    historical = exhaust(production)["run_id"]
    before = snapshot(database)
    calls = []
    flow = build_orchestrator(config, creative_provider=build(database, successful_handler(calls)))
    monkeypatch.setattr(web_app, "build_orchestrator", lambda *a, **k: flow)
    original = CreativeService._reserve_brief

    def fail(*a, **k):
        raise failure("Bearer secret signed-url")

    monkeypatch.setattr(CreativeService, "_reserve_brief", fail)
    app = web_app.create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        job_id = client.post("/api/studio/create", json={"target": "draft"}).json()["job_id"]
        job = wait(app.state.jobs, job_id)
        assert job.status == "failed" and job.result is None and job.episode_key is None
        reference = job.recovery_reference
        assert reference and reference.run_id and reference.run_id != historical
        payload = client.get(f"/api/jobs/{job_id}").json()
        assert len(payload["creative_diagnostics"]["attempts"]) == 1
        assert all(
            secret not in json.dumps(payload) for secret in ("Bearer", "secret", "signed-url")
        )
    app.state.jobs.close()
    retained = snapshot(database)
    assert calls == [CHAIN[0]]
    root = retained["generation_requests"][-1]
    assert root["run_id"] == reference.run_id and root["status"] == "succeeded"
    for table, rows in before.items():
        assert retained[table][: len(rows)] == rows

    app = web_app.create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        # A failing recovery worker keeps the dedicated identity even without a result.
        assert client.post(f"/api/studio/jobs/{job_id}/recover").status_code == 202
        assert wait(app.state.jobs, job_id).recovery_reference == reference
        assert snapshot(database) == retained and calls == [CHAIN[0]]
        monkeypatch.setattr(CreativeService, "_reserve_brief", original)
        assert client.post(f"/api/studio/jobs/{job_id}/recover").status_code == 202
        recovered = wait(app.state.jobs, job_id)
        assert recovered.status == "complete" and recovered.recovery_reference == reference
        assert recovered.result["run_id"] == reference.run_id
        after = snapshot(database)
        assert after["production_requests"] == retained["production_requests"]
        assert (
            after["generation_requests"][: len(retained["generation_requests"])]
            == retained["generation_requests"]
        )
        assert calls == [CHAIN[0]] * 4  # Original subject receipt plus three new draft requests.
    app.state.jobs.close()


@pytest.mark.parametrize("boundary", ["reserved", "intent", "identity", "committed"])
def test_process_death_at_reservation_boundaries_is_atomic_and_restart_safe(production, boundary):
    config, database = production
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("offline_reservation_process.py")),
            str(config.database_path),
            str(config.data_root),
            str(config.brand_root),
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert completed.returncode == 73, completed.stderr
    state = snapshot(database)
    jobs = JobManager(Database(config.database_path))
    try:
        job = jobs.list()[0]
        assert job.status == "interrupted"
        assert state["generation_requests"] == []
        if boundary == "committed":
            assert len(state["creative_runs"]) == len(state["production_requests"]) == 1
            assert job.recovery_reference == ProductionReference(
                run_id=state["creative_runs"][0]["run_id"]
            )
            # Emulate the dead process's lease expiry; never bypass a live owner in production.
            with closing(database.connect()) as db:
                db.execute(
                    "UPDATE production_execution_lease SET expires_at='2000-01-01T00:00:00+00:00'"
                )
                db.commit()
            calls = []
            resumed = build_orchestrator(
                config, creative_provider=build(database, successful_handler(calls))
            ).resume(job.recovery_reference, ProductionTarget.DRAFT)
            assert resumed["run_id"] == job.recovery_reference.run_id
            assert snapshot(database)["production_requests"] == state["production_requests"]
        else:
            assert job.recovery_reference is None
            assert state["creative_runs"] == state["production_requests"] == []
    finally:
        jobs.close()


@pytest.mark.parametrize("boundary", ["intent", "identity"])
def test_failed_reservation_persistence_rolls_back_before_provider_effect(production, boundary):
    config, database = production
    calls = []
    flow = build_orchestrator(config, creative_provider=build(database, successful_handler(calls)))
    if boundary == "intent":
        with closing(database.connect()) as db:
            db.execute(
                "CREATE TRIGGER reject_intent BEFORE INSERT ON production_requests "
                "BEGIN SELECT RAISE(ABORT, 'offline intent failure'); END"
            )
            db.commit()

    def reject_identity(db, reference):
        assert reference.run_id
        assert db.execute("SELECT count(*) FROM production_requests").fetchone()[0] == 1
        raise RuntimeError("offline job persistence failure")

    with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
        flow.generate(ProductionTarget.DRAFT, retain_reservation=reject_identity)
    assert calls == []
    assert all(not rows for rows in snapshot(database).values())


@pytest.mark.parametrize(
    "fields",
    [{}, {"run_id": " "}, {"episode_key": ""}, {"run_id": "pending", "episode_key": "other"}],
)
def test_creative_preparation_requires_explicit_identity_without_adoption(production, fields):
    config, database = production
    exhaust(production)
    before = snapshot(database)
    service = CreativeService(
        config, build(database, lambda _: pytest.fail("provider effect")), assert_owner=lambda: None
    )
    with pytest.raises(ValueError):
        service.prepare(**fields)
    with pytest.raises(ValueError):
        service._run(None)
    assert snapshot(database) == before


def test_pre_episode_resume_history_identifies_named_run(production):
    config, database = production
    run_id = exhaust(production)["run_id"]
    build_orchestrator(
        config, creative_provider=build(database, lambda _: pytest.fail("replay"))
    ).resume(ProductionReference(run_id=run_id), ProductionTarget.DRAFT)
    with closing(database.connect()) as db:
        diagnostic = db.execute(
            "SELECT * FROM production_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert diagnostic["operation"] == "resume" and diagnostic["episode_key"] == run_id


def test_studio_identity_write_failure_aborts_reservation_without_effect(production, monkeypatch):
    config, database = production
    calls = []
    flow = build_orchestrator(config, creative_provider=build(database, successful_handler(calls)))
    monkeypatch.setattr(web_app, "build_orchestrator", lambda *a, **k: flow)
    with closing(database.connect()) as db:
        db.execute(
            "CREATE TRIGGER reject_studio_identity BEFORE UPDATE ON studio_jobs "
            "WHEN json_extract(NEW.payload_json,'$.recovery_reference.run_id') IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT, 'offline identity persistence failure'); END"
        )
        db.commit()
    app = web_app.create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        job_id = client.post("/api/studio/create", json={"target": "draft"}).json()["job_id"]
        job = wait(app.state.jobs, job_id)
        assert job.status == "failed" and job.recovery_reference is None
        assert client.post(f"/api/studio/jobs/{job_id}/recover").status_code == 409
    app.state.jobs.close()
    assert calls == [] and all(not rows for rows in snapshot(database).values())
