"""Production preflight uses actual workflows and never migrates or generates."""

import json
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from tovitunes.config import load_config
from tovitunes.persistence.db import Database
from tovitunes.preflight import production_preflight


@pytest.mark.parametrize("corrupt_checksum", [False, True])
def test_preflight_is_read_only_with_explicit_live_verification_boundary(
    tmp_path,
    monkeypatch,
    corrupt_checksum,
):
    database = Database(tmp_path / "preflight.db")
    database.migrate()
    with closing(database.connect()) as db, db:
        if corrupt_checksum:
            db.execute(
                "UPDATE schema_migrations SET checksum='wrong' WHERE version=?",
                ("0001_initial.sql",),
            )
        before = db.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    config = load_config(Path("config.example.yaml")).model_copy(
        update={"database_path": database.path, "data_root": tmp_path},
    )
    config = config.model_copy(
        update={
            "creative_llm": config.creative_llm.model_copy(
                update={"api_key_env": "TOVITUNES_PREFLIGHT_TEST_KEY"}
            )
        }
    )
    monkeypatch.delenv("TOVITUNES_PREFLIGHT_TEST_KEY", raising=False)
    nodes = json.loads(Path("tests/fixtures/comfyui_object_info_0392.json").read_text())
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/object_info":
            return httpx.Response(200, json=nodes)
        if request.url.path == "/openapi.json":
            return httpx.Response(
                200,
                json={"paths": {p: {} for p in ("/release_task", "/query_result", "/v1/audio")}},
            )
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"object": "list", "data": []})

    client = httpx.Client

    def offline_client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return client(**kwargs)

    monkeypatch.setattr("httpx.Client", offline_client)
    result = production_preflight(config)
    assert result["readiness"] == "NOT VERIFIED" and result["provider_effects"] == 0
    assert result["checks"]["illustrations"]["status"] == "verified"
    assert result["checks"]["environments"]["status"] == "verified"
    assert result["checks"]["ace_step"]["status"] == "not_verified_without_live_call"
    assert result["checks"]["creative_provider"]["status"] == "missing"
    assert result["checks"]["database"]["status"] == (
        "misconfigured" if corrupt_checksum else "verified"
    )
    assert result["checks"]["database"]["checksum_mismatches"] == (
        ["0001_initial.sql"] if corrupt_checksum else []
    )
    assert all(method == "GET" for method, _ in calls)
    with closing(database.connect()) as db:
        assert before == db.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
