"""Donor-style tests of public effects, durable continuation and observational failures."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_creative_fallback import CHAIN, build, records, reply, run
from test_creative_fallback import owner as owner
from test_short_production import case as case

from tovitunes.continuation import plan_continuation
from tovitunes.errors import ExecutionOwnershipConflictError, ExecutionOwnershipLostError
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.persistence.db import Database
from tovitunes.pipeline.targets import ProductionTarget
from tovitunes.web.app import create_app

pytest_plugins = ("test_web_youtube_v1",)


def test_singleton_lease_conflicts_across_processes(tmp_path):
    database = Database(tmp_path / "state.db")
    database.migrate()
    with ProductionExecutionOwnership(database, operation="generate"):
        code = (
            "from pathlib import Path; from datetime import timedelta; "
            "from tovitunes.persistence.db import Database; "
            "Database(Path(__import__('sys').argv[1])).acquire_production_execution("
            "operation='web',ttl=timedelta(seconds=10))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(database.path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0
        assert "ExecutionOwnershipConflictError" in result.stderr
        with pytest.raises(ExecutionOwnershipConflictError):
            database.acquire_production_execution(operation="resume", ttl=timedelta(seconds=10))
    assert database.get_production_execution_lease() is None


def test_expired_owner_cannot_fence_new_owner(tmp_path):
    database = Database(tmp_path / "state.db")
    database.migrate()
    first = database.acquire_production_execution(operation="generate", ttl=timedelta(seconds=10))
    with closing(database.connect()) as db:
        db.execute("UPDATE production_execution_lease SET expires_at='2000-01-01T00:00:00+00:00'")
        db.commit()
    second = database.acquire_production_execution(operation="resume", ttl=timedelta(seconds=10))
    with pytest.raises(ExecutionOwnershipLostError):
        database.renew_production_execution(first.owner_token, ttl=timedelta(seconds=10))
    with pytest.raises(ExecutionOwnershipLostError):
        database.release_production_execution(first.owner_token)
    database.release_production_execution(second.owner_token)


def test_continuation_ignores_poisoned_diagnostics(case):
    config, episode = case["config"], case["episode"]
    before = plan_continuation(config, episode.external_key, target=ProductionTarget.RENDER)
    database = case["store"].database
    with closing(database.connect()) as db:
        db.execute(
            "INSERT INTO production_stage_events VALUES ('poison',?,'MUSIC','COMPLETE','{}','now')",
            (episode.episode_id,),
        )
        db.execute("INSERT INTO studio_jobs VALUES ('poison','{}')")
        db.execute(
            "INSERT INTO production_runs VALUES "
            "('poison','resume','render',?,'now',NULL,'running')",
            (episode.external_key,),
        )
        db.commit()
    assert plan_continuation(config, episode.external_key, target=ProductionTarget.RENDER) == before
    assert before["authoritative_stage"] == "CREATIVE"
    assert before["next_stage"] == "MUSIC"
    # Invalid diagnostic history must not prevent Web access to retained production.
    from tovitunes.web.supervisor import LocalServiceSupervisor

    app = create_app(config, supervisor=LocalServiceSupervisor(config, probe=False))
    app.state.jobs.close()


def test_draft_uses_only_creative_dependencies_and_broken_reporter_is_observational(case):
    def broken(_):
        raise OSError("disconnected progress consumer")

    application = case["flow"]
    application._reporter = broken
    application._factories = {
        name: lambda *args: pytest.fail("completed draft built a provider")
        for name in application._factories
    }
    result = application.resume(case["episode"].external_key, ProductionTarget.DRAFT)
    assert result["target_complete"]
    assert not case["music_events"] and not case["image_events"]


def test_new_generate_retains_topic_brief_and_stops_at_draft(case):
    result = case["flow"].generate(ProductionTarget.DRAFT)
    assert result["status"] == "COMPLETE"
    with closing(case["store"].database.connect()) as db:
        row = db.execute(
            "SELECT learning_brief_id FROM episodes WHERE external_key=?", (result["episode_key"],)
        ).fetchone()
        assert row[0]
        assert db.execute("SELECT count(*) FROM production_requests").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM production_next_runs").fetchone()[0] == 0
    assert not case["music_events"] and not case["image_events"]


@pytest.mark.parametrize(
    "next_stage",
    [
        "MUSIC",
        "AUDIO_ANALYSIS",
        "VISUAL_PLAN",
        "VISUAL_ASSETS",
        "STORYBOARD",
        "RENDER",
        "METADATA",
        "COMPLETED",
    ],
)
def test_restart_reuses_authoritative_stages(case, next_stage):
    config = case["config"]
    key = case["episode"].external_key
    config_file = config.database_path.parent / "restart-config.json"
    config_file.write_text(config.model_dump_json(), encoding="utf-8")
    runner = Path(__file__).with_name("offline_resume_process.py")
    first = config.database_path.parent / "first-process.json"
    command = [sys.executable, str(runner), str(config_file), key]
    interrupted = subprocess.run(
        [*command, next_stage, str(first)], capture_output=True, text=True, timeout=150
    )
    assert interrupted.returncode == 75, interrupted.stderr
    saved_plan = plan_continuation(config, key, target=ProductionTarget.RENDER)
    assert saved_plan["next_stage"] == (None if next_stage == "COMPLETED" else next_stage)
    old_ids = {
        s["name"]: s["evidence"].get("artifact_ids")
        for s in saved_plan["stages"]
        if s["status"] == "COMPLETE"
    }
    second = config.database_path.parent / "second-process.json"
    resumed = subprocess.run(
        [*command, "none", str(second)], capture_output=True, text=True, timeout=150
    )
    assert resumed.returncode == 0, resumed.stderr
    metrics = [json.loads(path.read_text(encoding="utf-8")) for path in (first, second)]
    result = metrics[1]["result"]
    assert result["target_complete"], result
    new_ids = {s["name"]: s["evidence"].get("artifact_ids") for s in result["stages"]}
    assert all(new_ids[name] == ids for name, ids in old_ids.items())
    assert sum(m["music_events"].count("/release_task") for m in metrics) == 1
    assert sum(m["image_events"].count("/prompt") for m in metrics) == 6
    assert sum(m["analysis_calls"] for m in metrics) == 1


def test_history_failure_cannot_downgrade_completed_production(case, monkeypatch):
    with closing(case["store"].database.connect()) as db:
        db.execute(
            "CREATE TRIGGER fail_history BEFORE UPDATE ON production_runs "
            "BEGIN SELECT RAISE(ABORT, 'offline diagnostic failure'); END"
        )
        db.commit()
    result = case["flow"].resume(case["episode"].external_key, ProductionTarget.DRAFT)
    assert result["status"] == "COMPLETE"
    assert plan_continuation(
        case["config"], case["episode"].external_key, target=ProductionTarget.DRAFT
    )["target_complete"]


def test_html_pins_asset_bytes_and_rejects_old_version(context):
    config, *_ = context
    app = create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        import re

        html = client.get("/")
        assert html.headers["cache-control"] == "no-store"
        paths = re.findall(r"/static/[a-f0-9]{16}/(?:app.js|styles.css)", html.text)
        assert len(paths) == 2
        for path in paths:
            asset = client.get(path)
            assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
        assert client.get("/static/0000000000000000/app.js").status_code == 404
    app.state.jobs.close()


def test_migration_preserves_all_historical_rows_and_checksums(context):
    config, database, *_ = context
    with sqlite3.connect(database.path) as db:
        before = db.execute(
            "SELECT version,checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        episodes = db.execute("SELECT * FROM episodes").fetchall()
    database.migrate()
    with sqlite3.connect(database.path) as db:
        assert (
            db.execute("SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall()
            == before
        )
        assert db.execute("SELECT * FROM episodes").fetchall() == episodes


def test_creative_ambiguity_advances_without_reconciliation(owner, monkeypatch):
    database, context = owner
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == CHAIN[0]:
            return reply("", identity="kimi-empty")
        if model == CHAIN[1]:
            return httpx.Response(504)
        if model == CHAIN[2]:
            return httpx.Response(422, json={"error": {"code": "provider_rejected"}})
        return reply(identity="deepseek-success")

    generator = build(database, handler)
    history = generator._history

    def crash_after_rejection(context):
        rows = history(context)
        if rows and rows[-1]["model"] == CHAIN[2] and rows[-1]["status"] == "failed":
            raise SystemExit()
        return rows

    monkeypatch.setattr(generator, "_history", crash_after_rejection)
    with pytest.raises(SystemExit):
        run(generator, context)
    retained = records(database)
    draft = run(build(Database(database.path), handler), context)
    assert draft.model == CHAIN[3] and calls == list(CHAIN)
    assert records(database)[:3] == retained
    with closing(database.connect()) as db:
        assert (
            db.execute("SELECT count(*) FROM creative_request_reconciliations").fetchone()[0] == 0
        )
    assert run(build(database, lambda r: pytest.fail("resent creative request")), context) == draft


def test_youtube_ambiguity_remains_blocked_in_a_new_process(case):
    from tovitunes.errors import UploadAmbiguous
    from tovitunes.orchestrator import build_orchestrator
    from tovitunes.publication.service import PublicationService

    key = case["episode"].external_key
    assert case["flow"].resume(key, ProductionTarget.RENDER)["target_complete"]
    base = case["config"]
    config = base.model_copy(
        update={
            "expected_youtube_channel_id": "offline-channel",
            "automation": base.automation.model_copy(update={"require_human_review": False}),
            "publication": base.publication.model_copy(
                update={"youtube": base.publication.youtube.model_copy(update={"enabled": True})}
            ),
        }
    )
    calls = []

    class LostResponse:
        def assert_channel(self, expected):
            assert expected == "offline-channel"

        def upload_private(self, path, metadata, *, on_remote_start, assert_ownership):
            assert_ownership()
            on_remote_start()
            calls.append("upload")
            raise UploadAmbiguous("offline lost response")

    app = build_orchestrator(
        config,
        publisher_factory=lambda owner: PublicationService(
            config, ownership=owner, client_factory=LostResponse
        ),
    )
    result = app.resume(key, ProductionTarget.PUBLISH)
    assert result["status"] == "AMBIGUOUS" and calls == ["upload"]
    with closing(app.database.connect()) as db:
        retained = [tuple(row) for row in db.execute("SELECT * FROM publication_attempts")]
        assert len(retained) == 1
    config_path = config.database_path.parent / "publication-restart.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    output = config.database_path.parent / "publication-result.json"
    code = (
        "from pathlib import Path; import json,sys,socket; "
        "socket.socket.connect=lambda *a:(_ for _ in ()).throw(RuntimeError('live network')); "
        "from tovitunes.config import RuntimeConfig; "
        "from tovitunes.orchestrator import build_orchestrator; "
        "from tovitunes.pipeline.targets import ProductionTarget; "
        "c=RuntimeConfig.model_validate_json(Path(sys.argv[1]).read_text(encoding='utf-8')); "
        "p=lambda owner:(_ for _ in ()).throw(RuntimeError('duplicate upload')); "
        "r=build_orchestrator(c,publisher_factory=p).resume(sys.argv[2],ProductionTarget.PUBLISH); "
        "Path(sys.argv[3]).write_text(json.dumps(r),encoding='utf-8')"
    )
    child = subprocess.run(
        [sys.executable, "-c", code, str(config_path), key, str(output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 0, child.stderr
    resumed = json.loads(output.read_text(encoding="utf-8"))
    assert resumed["status"] == "AMBIGUOUS" and resumed["current_stage"] == "YOUTUBE"
    with closing(app.database.connect()) as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM publication_attempts")] == retained


def test_lost_lease_after_music_preflight_prevents_remote_submission(case, monkeypatch):
    original_health = case["music"].health

    def expired_after_preflight():
        result = original_health()
        with closing(case["store"].database.connect()) as db:
            db.execute(
                "UPDATE production_execution_lease SET expires_at='2000-01-01T00:00:00+00:00'"
            )
            db.commit()
        return result

    monkeypatch.setattr(case["music"], "health", expired_after_preflight)
    with pytest.raises(ExecutionOwnershipLostError):
        case["flow"].resume(case["episode"].external_key, ProductionTarget.RENDER)
    assert "/release_task" not in case["music_events"]
    next_owner_plan = plan_continuation(
        case["config"], case["episode"].external_key, target=ProductionTarget.RENDER
    )
    assert next_owner_plan["allowed"] and next_owner_plan["next_stage"] == "MUSIC"
    with closing(case["store"].database.connect()) as db:
        assert not db.execute(
            "SELECT 1 FROM music_requests WHERE remote_started_at IS NOT NULL"
        ).fetchone()


def test_lost_lease_during_image_stage_prevents_further_submissions_and_selection(
    case, monkeypatch
):
    from tovitunes.benchmark.providers import QwenComfyUIImageProvider

    generate = QwenComfyUIImageProvider.generate

    def expired_after_result(provider, *args, **kwargs):
        result = generate(provider, *args, **kwargs)
        with closing(case["store"].database.connect()) as db:
            db.execute(
                "UPDATE production_execution_lease SET expires_at='2000-01-01T00:00:00+00:00'"
            )
            db.commit()
        return result

    monkeypatch.setattr(QwenComfyUIImageProvider, "generate", expired_after_result)
    with pytest.raises(ExecutionOwnershipLostError):
        case["flow"].resume(case["episode"].external_key, ProductionTarget.RENDER)
    assert case["image_events"].count("/prompt") == 1
    with closing(case["store"].database.connect()) as db:
        assert not db.execute(
            "SELECT 1 FROM production_image_receipts WHERE normalized_artifact_id IS NOT NULL"
        ).fetchone()
        # Retain the immutable image response for the next owner without issuing another request.
        assert (
            db.execute(
                "SELECT count(*) FROM production_image_receipts "
                "WHERE source_artifact_id IS NOT NULL"
            ).fetchone()[0]
            == 1
        )
