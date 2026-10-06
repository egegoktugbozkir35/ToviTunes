"""Studio targets, durable recovery and one-click supervision, entirely offline."""

import json
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import test_short_production
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tovitunes.catalog import load_brand
from tovitunes.config import LocalServiceLaunchConfig, LocalServicesConfig
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.domain.episode import Episode
from tovitunes.persistence.creative_reconciliation import CreativeReconciliations
from tovitunes.pipeline import short_production
from tovitunes.pipeline.targets import ProductionTarget, stages_for, steps_for
from tovitunes.publication.preflight import evaluate_release
from tovitunes.web import app as web_app
from tovitunes.web.jobs import Job, JobManager
from tovitunes.web.launcher import open_when_ready, resolve_config
from tovitunes.web.studio import library
from tovitunes.web.supervisor import LocalServiceSupervisor

case = test_short_production.case
pytest_plugins = ("test_web_youtube_v1",)


def wait(manager, job_id):
    for _ in range(500):
        job = manager.get(job_id)
        if job.status not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    pytest.fail("job did not finish")


@pytest.mark.parametrize("entry", ["new", "existing", "next"])
def test_draft_stops_before_every_media_provider(case, entry):
    flow = case["flow"]
    before = len(case["fake"].calls)
    if entry == "next":
        result = flow.produce_next(confirmed=True, target=ProductionTarget.DRAFT)
    else:
        key = case["episode"].external_key
        if entry == "new":
            catalog = load_brand(flow.config.brand_root)
            episode = Episode.create(catalog, "green", "studio-green")
            case["store"].database.create_episode(catalog, episode)
            key = episode.external_key
        result = flow.produce(key, confirmed=True, target=ProductionTarget.DRAFT)
    assert result["status"] == "COMPLETE", result
    assert [s["name"] for s in result["stages"]] == ["CREATIVE"]
    assert not case["music_events"] and not case["image_events"]
    assert not case["environment_events"]
    assert (
        len(case["fake"].calls) == before
        if entry == "existing"
        else (len(case["fake"].calls) > before)
    )
    with closing(case["store"].database.connect()) as db:
        assert db.execute("SELECT count(*) FROM publication_attempts").fetchone()[0] == 0


def test_render_real_mp4_reuses_draft_and_publish_does_not_rerender(case, monkeypatch):
    flow, key = case["flow"], case["episode"].external_key
    creative_before = len(case["fake"].calls)
    draft = flow.produce(key, confirmed=True, target=ProductionTarget.DRAFT)
    assert draft["status"] == "COMPLETE"
    rendered = flow.produce(key, confirmed=True, target=ProductionTarget.RENDER)
    assert rendered["status"] == "COMPLETE", (rendered, case["errors"])
    assert case["store"].path_for(rendered["final_render_id"]).read_bytes()[4:8] == b"ftyp"
    assert any(
        c.name == "media_qa_passed" and c.passed
        for c in evaluate_release(case["config"], key).checks
    )
    assert len(case["fake"].calls) == creative_before + 2  # visual plan and metadata only
    assert (
        next(i for i in library(case["config"]) if i["episode_key"] == key)["category"] == "renders"
    )
    with closing(case["store"].database.connect()) as db:
        selections = [tuple(r) for r in db.execute("SELECT * FROM artifact_selections")]
        assert not db.execute("SELECT 1 FROM publication_attempts").fetchone()
    for name in ("_music", "_analysis", "_visual", "_environment", "_storyboard"):
        monkeypatch.setattr(flow, name, lambda *a: pytest.fail("reran upstream render stage"))
    monkeypatch.setattr(
        short_production,
        "ProductionRenderer",
        lambda *a, **k: pytest.fail("rerendered valid video"),
    )
    resumed = flow.produce(
        key, confirmed=True, target=ProductionTarget.PUBLISH, operator_publish=True
    )
    assert resumed["status"] == "NEEDS_REVIEW"  # explicit action still enforces policy
    with closing(case["store"].database.connect()) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM artifact_selections")] == selections
        assert not db.execute("SELECT 1 FROM publication_attempts").fetchone()
    # The same render can then be released under a policy that permits private publication.
    from tovitunes.publication.service import PublicationService

    uploads = []

    class Client:
        def assert_channel(self, expected):
            assert expected == "fixture-channel"

        def upload_private(self, path, metadata, *, on_remote_start, assert_ownership):
            assert_ownership()
            on_remote_start()
            uploads.append(path)
            return "retained-render-video"

    config = flow.config.model_copy(
        update={
            "expected_youtube_channel_id": "fixture-channel",
            "publication": flow.config.publication.model_copy(
                update={
                    "youtube": flow.config.publication.youtube.model_copy(update={"enabled": True})
                }
            ),
            "automation": flow.config.automation.model_copy(update={"require_human_review": False}),
        }
    )
    flow.config = config
    monkeypatch.setattr(
        short_production,
        "PublicationService",
        lambda cfg: PublicationService(cfg, client_factory=Client),
    )
    for _ in range(2):
        result = flow.produce(
            key, target=ProductionTarget.PUBLISH, confirmed=True, operator_publish=True
        )
        assert result["status"] == "COMPLETE", result
    assert len(uploads) == 1
    assert next(i for i in library(config) if i["episode_key"] == key)["category"] == "published"


@pytest.mark.parametrize("entry", ["draft", "nothing"])
def test_publish_traverses_one_mocked_path(case, monkeypatch, entry):
    flow = case["flow"]
    visited = []
    original_event = flow._event

    def event(episode, stage, status, evidence):
        if status == "RUNNING":
            visited.append(stage)
        original_event(episode, stage, status, evidence)

    monkeypatch.setattr(flow, "_event", event)
    monkeypatch.setattr(flow, "_music", lambda *a: ("request", "blind"))
    monkeypatch.setattr(flow, "_analysis", lambda *a: {})
    monkeypatch.setattr(flow, "_visual", lambda *a: (case["plan"], "plan"))
    monkeypatch.setattr(short_production, "generate_assets", lambda *a, **k: {})
    monkeypatch.setattr(flow, "_environment", lambda *a: "environment")
    monkeypatch.setattr(flow, "_storyboard", lambda *a: {})
    monkeypatch.setattr(flow, "_render", lambda *a: {"final_render_id": "offline-render"})
    monkeypatch.setattr(flow, "_media_qa", lambda *a: {"passed": True})
    monkeypatch.setattr(flow, "_metadata", lambda *a: {})

    def publication(episode, *, operator_publish):
        assert operator_publish and not flow.config.automation.auto_publish
        return {"status": "COMPLETE"}

    monkeypatch.setattr(flow, "_publication", publication)
    result = (
        flow.produce(
            case["episode"].external_key,
            confirmed=True,
            target=ProductionTarget.PUBLISH,
            operator_publish=True,
        )
        if entry == "draft"
        else flow.produce_next(
            confirmed=True, target=ProductionTarget.PUBLISH, operator_publish=True
        )
    )
    assert result["status"] == "COMPLETE", result
    assert visited == list(stages_for(ProductionTarget.PUBLISH))


def test_library_validates_draft_bytes_and_historical_publication(case, ready):
    from test_public_release_v1 import record_upload

    items = library(case["config"])
    blue = next(i for i in items if i["episode_key"] == case["episode"].external_key)
    assert blue["draft_ready"] and blue["category"] == "drafts" and blue["can_render"]
    source = case["store"].path_for(case["ids"][0])
    source.write_bytes(b"invalid selected bytes")
    blue = next(i for i in library(case["config"]) if i["episode_key"] == blue["episode_key"])
    assert not blue["draft_ready"] and not blue["can_render"]
    record_upload(ready)
    published = library(ready[0][0])[0]
    assert published["category"] == "published" and not published["can_publish"]
    assert published["publication"]["youtube_video_id"] == "video-123"


@pytest.mark.parametrize("target", list(ProductionTarget))
def test_progress_is_monotonic_target_specific_and_live(target):
    manager = JobManager()
    seen = []

    def run():
        for stage, status in [
            ("CREATIVE", "TOPIC_RUNNING"),
            ("CREATIVE", "TOPIC_COMPLETE"),
            ("CREATIVE", "BRIEF_COMPLETE"),
            ("CREATIVE", "EPISODE_SPEC_COMPLETE"),
            ("CREATIVE", "LYRICS_COMPLETE"),
            ("CREATIVE", "MUSIC_SPEC_COMPLETE"),
            ("CREATIVE", "RUNNING"),
        ]:
            manager.update_progress(stage, status)
            job = manager.list()[0]
            seen.append(job.progress_percent)
            assert job.current_stage == stage and job.current_substage == status
        for stage in stages_for(target):
            manager.update_progress(stage, "RUNNING")
            seen.append(manager.list()[0].progress_percent)
            manager.update_progress(stage, "COMPLETE")
            seen.append(manager.list()[0].progress_percent)
        return {"status": "COMPLETE"}

    job = manager.submit("studio", None, run, target=target)
    complete = wait(manager, job.job_id)
    assert seen == sorted(seen) and any(0 < p < 100 for p in seen)
    assert complete.steps == list(steps_for(target))
    assert complete.progress_percent == 100
    if target != ProductionTarget.PUBLISH:
        assert "YOUTUBE" not in complete.steps
    manager.close()


def test_job_restart_reconstructs_interrupted_task(context):
    database = context[1]
    job = Job(
        job_id="persisted",
        operation="studio",
        episode_key="saved-draft",
        status="running",
        submitted_at="2026-01-01",
        target=ProductionTarget.RENDER,
    )
    with closing(database.connect()) as db:
        db.execute("INSERT INTO studio_jobs VALUES (?,?)", (job.job_id, job.model_dump_json()))
        db.commit()
    manager = JobManager(database)
    restored = manager.get(job.job_id)
    assert restored.episode_key == job.episode_key and restored.target == job.target
    assert restored.status == "interrupted" and restored.recovery_action == "resume"
    manager.close()


@pytest.mark.parametrize("entry", ["next", "existing"])
def test_web_creative_recovery_reconciles_without_calls_then_same_job_target(
    case, monkeypatch, entry
):
    from test_creative_fallback import CHAIN, build, reply

    from tovitunes.catalog import load_brand

    monkeypatch.setenv("NVIDIA_API_KEY", "offline-studio-key")
    monkeypatch.setattr("socket.socket.connect", case["socket_connect"])
    flow = case["flow"]
    calls = []

    def ambiguous(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(504, text="remote-secret-body")

    flow.creative_provider = build(case["store"].database, ambiguous)
    app = web_app.create_app(case["config"])
    flow.progress = app.state.jobs.update_progress
    monkeypatch.setattr(web_app, "ShortProductionWorkflow", lambda *a, **k: flow)
    key = None
    if entry == "existing":
        catalog = load_brand(flow.config.brand_root)
        episode = Episode.create(catalog, "green", "studio-recovery")
        case["store"].database.create_episode(catalog, episode)
        key = episode.external_key
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        response = (
            client.post(f"/api/studio/episodes/{key}/continue", json={"target": "render"})
            if key
            else client.post("/api/studio/create", json={"target": "draft"})
        )
        stopped = wait(app.state.jobs, response.json()["job_id"])
        assert stopped.status == "ambiguous" and stopped.recovery_action == "abandon_remote_result"
        assert calls == [CHAIN[0]] and "remote-secret-body" not in stopped.model_dump_json()
        original = CreativeReconciliations.abandon

        def abandon(service, *args, **kwargs):
            before = list(calls)
            value = original(service, *args, **kwargs)
            assert calls == before and kwargs["actor"] == "human:webui-operator"
            return value

        monkeypatch.setattr(CreativeReconciliations, "abandon", abandon)
        fake = FakeNIMTransport()

        def recovered(request):
            payload = json.loads(request.content)
            calls.append(payload["model"])
            if payload["model"] == CHAIN[1]:
                return httpx.Response(404, json={"error": {"code": "model_not_found"}})
            return reply(
                fake.chat(payload["messages"], record_identity=lambda _: None).content,
                identity=f"studio-{len(calls)}",
            )

        flow.creative_provider = build(case["store"].database, recovered)
        case["music_behavior"]["state"] = "pending"
        response = client.post(f"/api/studio/jobs/{stopped.job_id}/recover")
        assert response.status_code == 202 and response.json()["job_id"] == stopped.job_id
        resumed = wait(app.state.jobs, stopped.job_id)
        assert resumed.execution == 2 and resumed.target == stopped.target
        assert resumed.status == ("pending_provider" if key else "complete"), (resumed, calls)
        assert CHAIN[2] in calls and calls.count(CHAIN[0]) == 1
        if key:
            assert resumed.episode_key == key
        assert client.post(f"/api/studio/jobs/{stopped.job_id}/stop").status_code == 200
    app.state.jobs.close()


def test_api_concurrency_failure_safety_and_body_validation(context, monkeypatch):
    gate = threading.Event()
    app = web_app.create_app(
        context[0], supervisor=LocalServiceSupervisor(context[0], probe=lambda _: False)
    )

    class Workflow:
        def produce_next(self, **kwargs):
            assert kwargs["operator_publish"] is True
            gate.wait(5)
            return {
                "status": "AMBIGUOUS",
                "current_stage": "YOUTUBE",
                "blocker": {
                    "reason": "Bearer secret signed-url?token=private",
                    "response_body": "private-provider-body",
                },
            }

    monkeypatch.setattr(web_app, "ShortProductionWorkflow", lambda *a, **k: Workflow())
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        first = client.post("/api/studio/create", json={"target": "publish"})
        assert first.status_code == 202
        assert client.post("/api/studio/create", json={"target": "draft"}).status_code == 409
        assert client.post("/api/studio/create", json={"target": "secret-token"}).status_code == 422
        gate.set()
        failed = wait(app.state.jobs, first.json()["job_id"])
        assert failed.status == "ambiguous" and failed.recovery_action is None
        assert all(
            secret not in failed.model_dump_json()
            for secret in ("Bearer secret", "private-provider-body", "signed-url")
        )
        assert client.post(f"/api/studio/jobs/{failed.job_id}/recover").status_code == 409
        system = client.get("/api/system").json()
        assert system["services"]["ollama"]["status"] == "disabled"
    app.state.jobs.close()


@pytest.mark.parametrize("name", ["ace_step", "comfyui"])
def test_launcher_reuses_healthy_service_and_never_owns_it(context, name):
    supervisor = LocalServiceSupervisor(
        context[0],
        probe=lambda _: True,
        spawn=lambda *a, **k: pytest.fail("duplicated healthy service"),
    )
    supervisor.start(name)
    assert supervisor.status(refresh=False)[name] == {"status": "ready", "owned": False}
    supervisor.close()


@pytest.mark.parametrize("name", ["ace_step", "comfyui"])
def test_launcher_starts_missing_service_and_cleans_up_only_owned(context, name, monkeypatch):
    config = context[0]
    launch = LocalServiceLaunchConfig(
        command=("python", "service.py"), cwd=config.database_path.parent
    )
    config = config.model_copy(update={"local_services": LocalServicesConfig(**{name: launch})})
    monkeypatch.setattr("tovitunes.web.supervisor.shutil.which", lambda _: "python.exe")
    spawned = []
    terminated = []
    process = SimpleNamespace(
        poll=lambda: None, terminate=lambda: terminated.append(name), wait=lambda **k: None
    )

    def spawn(command, **kwargs):
        assert not kwargs["shell"] and kwargs["stderr"] == -2
        assert "NVIDIA_API_KEY" not in kwargs["env"]
        spawned.append(command)
        assert kwargs["cwd"] == config.database_path.parent
        return process

    monkeypatch.setenv("NVIDIA_API_KEY", "secret-value")
    supervisor = LocalServiceSupervisor(config, probe=lambda _: bool(spawned), spawn=spawn)
    supervisor.start(name)
    supervisor.start(name)
    assert len(spawned) == 1 and supervisor.status(refresh=False)[name]["status"] == "ready"
    supervisor.close()
    assert terminated == [name]


def test_failed_startup_still_opens_ui_and_config_fallback(context, tmp_path, monkeypatch):
    config = context[0]
    supervisor = LocalServiceSupervisor(config, probe=lambda _: False)
    monkeypatch.setattr(supervisor, "discover", lambda _: (["missing.exe"], tmp_path))
    monkeypatch.setattr(
        supervisor, "spawn", lambda *a, **k: (_ for _ in ()).throw(OSError("secret"))
    )
    supervisor.start("ace_step")
    assert supervisor.status(refresh=False)["ace_step"]["status"] == "failed"
    assert "secret" not in json.dumps(supervisor.status(refresh=False))
    opened = []
    open_when_ready("http://127.0.0.1:8766", probe=lambda _: True, opener=opened.append)
    assert opened == ["http://127.0.0.1:8766"]
    fallback = tmp_path / "config.example.yaml"
    fallback.touch()
    assert resolve_config(tmp_path / "config.yaml") == fallback
    supervisor.close()


@pytest.mark.parametrize("command", [("python", "--api-key=secret"), ("python", "a\nb")])
def test_launcher_rejects_credential_arguments_and_control_characters(command):
    with pytest.raises(ValidationError):
        LocalServiceLaunchConfig(command=command)


def test_no_user_specific_paths_in_changed_runtime_modules():
    root = Path(__file__).resolve().parents[1]
    for path in [*root.joinpath("src/tovitunes/web").rglob("*.py"), root / "start-ui.bat"]:
        assert "C:" + "\\Users\\" + "Victus" not in path.read_text(encoding="utf-8")


def test_explicit_publish_uses_real_publication_gates_without_auto_publish(ready, monkeypatch):
    from test_web_youtube_v1 import rights

    from tovitunes.pipeline.short_production import ProductionStop, ShortProductionWorkflow
    from tovitunes.publication.service import PublicationService

    (config, database, episode, store), render, _, _ = ready
    uploads = []

    class Client:
        def assert_channel(self, expected):
            assert expected == config.expected_youtube_channel_id

        def upload_private(self, path, metadata, *, on_remote_start, assert_ownership):
            assert_ownership()
            on_remote_start()
            uploads.append(path)
            return "studio-video"

    service = PublicationService(config, client_factory=Client)
    monkeypatch.setattr(short_production, "PublicationService", lambda _: service)
    flow = ShortProductionWorkflow(config)
    assert not config.automation.auto_publish
    for _ in range(2):
        assert flow._publication(episode, operator_publish=True)["status"] == "COMPLETE"
    assert len(uploads) == 1
    with closing(database.connect()) as db:
        # Metadata edits must never cause a second upload of an already successful episode.
        db.execute("UPDATE publication_attempts SET metadata_fingerprint='older-copy'")
        db.commit()
    assert service.upload_private(episode.external_key)["youtube_video_id"] == "studio-video"
    assert len(uploads) == 1
    with closing(database.connect()) as db:
        db.execute("UPDATE publication_attempts SET outcome='ambiguous'")
        db.commit()
    with pytest.raises(ProductionStop):
        flow._publication(episode, operator_publish=True)
    assert len(uploads) == 1
    rights(store, render, "blocked")
    with pytest.raises(ProductionStop, match="release gates"):
        flow._publication(episode, operator_publish=True)
    assert len(uploads) == 1


def test_launcher_serves_ui_even_when_service_startup_fails(context, monkeypatch):
    from tovitunes.web import launcher

    config = context[0]
    supervisor = LocalServiceSupervisor(config, probe=lambda _: False)
    monkeypatch.setattr(supervisor, "discover", lambda _: None)
    monkeypatch.setattr(launcher, "load_config", lambda _: config)
    monkeypatch.setattr(launcher, "resolve_config", lambda path: path)
    monkeypatch.setattr(launcher, "LocalServiceSupervisor", lambda _: supervisor)
    opened = []
    served = []
    monkeypatch.setattr(launcher, "open_when_ready", lambda url: opened.append(url))

    class Server:
        def __init__(self, server_config):
            self.app = server_config.app

        def run(self, *, sockets):
            served.append(self.app)
            assert sockets[0].getsockname()[0] == "127.0.0.1"

    monkeypatch.setattr(launcher.uvicorn, "Server", Server)
    launcher.launch(Path("config.yaml"), port=0)
    assert served and opened == ["http://127.0.0.1:0"]
    for thread in supervisor._threads:
        thread.join(2)
    assert all(
        s["status"] in {"failed", "disabled"} for s in supervisor.status(refresh=False).values()
    )


@pytest.mark.parametrize("name", ["ace_step", "comfyui"])
def test_user_relative_service_discovery(context, tmp_path, monkeypatch, name):
    home = tmp_path / "home"
    monkeypatch.setattr("tovitunes.web.supervisor.Path.home", lambda: home)
    monkeypatch.delenv("TOVITUNES_ACE_STEP_HOME", raising=False)
    monkeypatch.delenv("TOVITUNES_COMFYUI_HOME", raising=False)
    monkeypatch.setattr("tovitunes.web.supervisor.shutil.which", lambda _: "uv.exe")
    if name == "ace_step":
        root = home / "Desktop/ACE-Step-1.5"
        entry = root / "acestep/api_server.py"
        entry.parent.mkdir(parents=True)
        entry.touch()
    else:
        root = home / "Documents/ComfyUI"
        python = root / ".venv/Scripts/python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        (root / "main.py").touch()
    command, cwd = LocalServiceSupervisor(context[0]).discover(name)
    assert cwd == root and "127.0.0.1" in command
    assert command[-1] == ("8001" if name == "ace_step" else "8188")


def test_startup_timeout_does_not_spawn_a_second_live_process(context, monkeypatch):
    launch = LocalServiceLaunchConfig(command=("python",), startup_timeout_seconds=0.1)
    config = context[0].model_copy(update={"local_services": LocalServicesConfig(ace_step=launch)})
    ticks = iter(range(100))
    spawned, terminated = [], []
    process = SimpleNamespace(
        poll=lambda: None, terminate=lambda: terminated.append(True), wait=lambda **kwargs: None
    )
    supervisor = LocalServiceSupervisor(
        config,
        probe=lambda _: False,
        spawn=lambda *a, **k: spawned.append(a) or process,
        clock=lambda: next(ticks),
        sleep=lambda _: pytest.fail("unbounded readiness sleep"),
    )
    monkeypatch.setattr(supervisor, "discover", lambda _: (["python.exe"], config.data_root))
    supervisor.start("ace_step")
    assert supervisor.status(refresh=False)["ace_step"]["status"] == "failed"
    supervisor.start("ace_step")
    assert len(spawned) == 1
    supervisor.close()
    assert terminated == [True]


def test_ollama_required_only_for_configured_fallback_or_embeddings(context):
    config = context[0]
    assert "ollama" not in LocalServiceSupervisor(config).urls
    enabled = config.model_copy(
        update={
            "creative_llm": config.creative_llm.model_copy(
                update={"fallback_to_ollama_on_endpoint_failure": True}
            )
        }
    )
    assert LocalServiceSupervisor(enabled).urls["ollama"].endswith("/api/tags")
    embedding = config.creative_topics.embedding.model_copy(
        update={"enabled": True, "model": "test"}
    )
    enabled = config.model_copy(
        update={
            "creative_topics": config.creative_topics.model_copy(update={"embedding": embedding})
        }
    )
    assert "ollama" in LocalServiceSupervisor(enabled).urls


def test_browser_opening_is_bounded_when_ui_never_ready():
    checks = []
    open_when_ready(
        "http://127.0.0.1:8766",
        probe=lambda url: checks.append(url) or False,
        opener=lambda _: pytest.fail("opened unready server"),
        sleep=lambda _: None,
    )
    assert len(checks) == 100


def test_retained_upload_channel_mismatch_blocks_duplicate_attempt(ready):
    from test_public_release_v1 import record_upload

    from tovitunes.pipeline.short_production import ProductionStop, ShortProductionWorkflow
    from tovitunes.publication.service import PublicationService

    record_upload(ready)
    (config, database, episode, _), _, _, _ = ready
    with closing(database.connect()) as db:
        db.execute(
            "UPDATE publication_attempts SET expected_channel_id=?",
            (config.expected_youtube_channel_id,),
        )
        db.commit()
    changed = config.model_copy(update={"expected_youtube_channel_id": "different-channel"})
    with pytest.raises(ProductionStop, match="another channel"):
        ShortProductionWorkflow(changed)._publication(episode, operator_publish=True)
    with pytest.raises(ValueError, match="another configured channel"):
        PublicationService(changed).upload_private(episode.external_key)
    item = library(changed)[0]
    assert not item["can_publish"] and "another configured channel" in item["blocker"]


def test_system_reports_channel_mismatch_without_repeated_remote_calls(context, monkeypatch):
    config = context[0]
    config.publication.youtube.token_file.write_text("{}", encoding="utf-8")
    calls = []

    class Client:
        def __init__(self, config):
            pass

        def channel(self, expected, *, interactive):
            calls.append(expected)
            return {"channel_id": "wrong-channel", "matches_expected": False}

    monkeypatch.setattr(web_app, "YouTubeClient", Client)
    app = web_app.create_app(
        config, supervisor=LocalServiceSupervisor(config, probe=lambda _: False)
    )
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        response = client.post("/api/youtube/connect")
        job = wait(app.state.jobs, response.json()["job_id"])
        assert job.status == "failed" and job.error_category == "ChannelMismatch"
        for _ in range(2):
            assert client.get("/api/system").json()["services"]["youtube"]["status"] == "mismatch"
        assert calls == [config.expected_youtube_channel_id]
    app.state.jobs.close()
