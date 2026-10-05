"""Offline control-surface, release gate and remote-side-effect regression tests."""

import json
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.catalog import load_brand
from tovitunes.config import PublicationConfig, RuntimeConfig, YouTubeConfig
from tovitunes.creative.models import EpisodePublicationMetadata
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.persistence.db import Database
from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.service import PublicationService
from tovitunes.render.models import RenderManifest, SceneRender
from tovitunes.web.app import create_app
from tovitunes.web.jobs import JobBusy, JobManager
from tovitunes.youtube.client import ChannelMismatch, UploadAmbiguous, YouTubeClient


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    connect = socket.socket.connect
    create_connection = socket.create_connection

    def local_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            pytest.fail("external network call")
        return connect(sock, address)

    def local_create(address, *args, **kwargs):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            pytest.fail("external network call")
        return create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket, "create_connection", local_create)


@pytest.fixture
def context(tmp_path):
    root = Path(__file__).resolve().parents[1] / "brands" / "tovitunes"
    catalog = load_brand(root)
    config = RuntimeConfig(
        database_path=tmp_path / "state.db",
        data_root=tmp_path / "data",
        brand_root=root,
        expected_youtube_channel_id="expected-channel",
        publication=PublicationConfig(
            youtube=YouTubeConfig(
                enabled=True,
                credentials_file=tmp_path / "secret.json",
                token_file=tmp_path / "token.json",
            )
        ),
    )
    db = Database(config.database_path)
    db.migrate()
    episode = Episode.create(catalog, "red", "colors-red")
    db.create_episode(catalog, episode)
    store = AssetStore(config.data_root, db)
    return config, db, episode, store


def artifact(context, kind, content, deps=(), *, slot_key="main"):
    config, _, episode, store = context
    suffix = ".mp4" if kind == "final_render" else ".json"
    source = config.database_path.parent / f"{kind}{suffix}"
    source.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
    record = store.ingest(
        source,
        owner_scope="episode",
        owner_id=episode.episode_id,
        kind=kind,
        slot_key=slot_key,
        provenance=Provenance.manual("fixture", f"local://{kind}"),
        dependencies=[InputDependency(item.identity.artifact_id, "fixture") for item in deps],
    )
    store.record_approval(
        ApprovalDecision(
            target_id=record.identity.artifact_id,
            target_kind="artifact",
            status="approved",
            actor="fixture",
            policy_version="fixture",
            decided_at=datetime.now(UTC),
        )
    )
    store.select(record.identity.artifact_id)
    return record


@pytest.fixture
def ready(context):
    config, _, episode, store = context
    base = [
        artifact(context, name, {"fixture": name})
        for name in (
            "audio_master",
            "audio_alignment",
            "beat_analysis",
            "timed_storyboard",
            "scene_image",
            "character_animation",
        )
    ]
    ids = {r.identity.kind: r.identity.artifact_id for r in base}
    manifest = RenderManifest(
        renderer_version="tovitunes_sprite_render_v2",
        episode_id=episode.episode_id,
        audio_master_artifact_id=ids["audio_master"],
        audio_alignment_artifact_id=ids["audio_alignment"],
        beat_analysis_artifact_id=ids["beat_analysis"],
        timed_storyboard_artifact_id=ids["timed_storyboard"],
        character_pack_revision=episode.character_packs[0].revision_id,
        dependency_sha256={r.identity.artifact_id: r.sha256 for r in base},
        scenes=(
            SceneRender(
                scene_id="red",
                start=0,
                end=2,
                scene_image_artifact_id=ids["scene_image"],
                character_animation_artifact_id=ids["character_animation"],
            ),
        ),
        duration_seconds=2,
        ffmpeg_version="fixture",
        ffprobe_version="fixture",
    )
    manifest_record = artifact(context, "render_manifest", manifest.model_dump(mode="json"), base)
    render = artifact(context, "final_render", b"\0\0\0\x18ftypisomfixture-one", (manifest_record,))
    qa = artifact(
        context,
        "media_qa",
        {
            "passed": True,
            "render_artifact_id": render.identity.artifact_id,
            "render_sha256": render.sha256,
        },
        (render,),
    )
    metadata = EpisodePublicationMetadata(
        youtube_title="Red with Tovi",
        youtube_description="Let's learn red together.",
        tags=("preschool", "red"),
        episode_id=episode.episode_id,
        concept_id="red",
        final_render_artifact_id=render.identity.artifact_id,
        final_render_sha256=render.sha256,
    )
    meta = artifact(context, "publication_metadata", metadata.model_dump(mode="json"), (render,))
    return context, render, qa, meta


def rights(store, record, status):
    store.record_rights(
        RightsDecision(
            artifact_id=record.identity.artifact_id,
            status=status,
            actor="fixture",
            policy_version="fixture",
            decided_at=datetime.now(UTC),
            evidence_uri="local://review" if status == "commercial_use_confirmed" else None,
        )
    )


def test_preflight_missing_render_and_no_provider_calls(context):
    config, _, _, _ = context
    report = evaluate_release(config, "colors-red")
    assert not report.render_ready and not report.private_test_upload_allowed
    assert any(c.name == "selected_final_render" and not c.passed for c in report.checks)


def test_preflight_independent_approval_rights_and_private_policy(ready):
    (config, _, _, store), render, qa, _ = ready
    report = evaluate_release(config, "colors-red")
    assert report.render_ready and report.private_test_upload_allowed
    assert not report.public_release_allowed
    rights(store, render, "review_required")
    assert evaluate_release(config, "colors-red").private_test_upload_allowed
    rights(store, render, "blocked")
    blocked = evaluate_release(config, "colors-red")
    assert not blocked.private_test_upload_allowed and not blocked.public_release_allowed
    rights(store, render, "commercial_use_confirmed")
    assert evaluate_release(config, "colors-red").private_test_upload_allowed
    store.record_approval(
        ApprovalDecision(
            target_id=qa.identity.artifact_id,
            target_kind="artifact",
            status="rejected",
            actor="fixture",
            policy_version="fixture",
            reason="bad",
            decided_at=datetime.now(UTC),
        )
    )
    assert not evaluate_release(config, "colors-red").private_test_upload_allowed


def test_all_commercial_rights_clear_public_gate(ready):
    (config, db, _, store), _, _, _ = ready
    with db.connect() as connection:
        artifact_ids = [
            row[0] for row in connection.execute("SELECT artifact_id FROM artifact_versions")
        ]
    for artifact_id in artifact_ids:
        rights(store, store.get(artifact_id), "commercial_use_confirmed")
    report = evaluate_release(config, "colors-red")
    assert report.private_test_upload_allowed and report.public_release_allowed


def test_complete_graph_is_inspected_and_dependency_sha_drift_blocks(ready):
    (config, db, _, _), render, _, _ = ready
    before = evaluate_release(config, "colors-red")
    immutable_ids = {
        check.artifact_id
        for check in before.checks
        if check.name == "immutable_sha" and check.artifact_id
    }
    rights_ids = {
        check.artifact_id
        for check in before.checks
        if check.name in {"commercial_rights_direct", "commercial_rights_inherited"}
        and check.artifact_id
    }
    assert rights_ids == immutable_ids
    with db.connect() as connection:
        connection.execute("DROP TRIGGER artifact_dependencies_no_update")
        connection.execute(
            "UPDATE artifact_dependencies SET input_sha256=? WHERE consumer_artifact_id=?",
            ("0" * 64, render.identity.artifact_id),
        )
        connection.commit()
    drifted = evaluate_release(config, "colors-red")
    assert not drifted.render_ready and not drifted.public_release_allowed
    assert any(check.name == "dependency_sha" and not check.passed for check in drifted.checks)
    assert {
        check.artifact_id
        for check in drifted.checks
        if check.name == "immutable_sha" and check.artifact_id
    } == immutable_ids


def test_selected_failed_media_qa_blocks_private(ready):
    context, render, _, _ = ready
    config, _, _, _ = context
    artifact(
        context,
        "media_qa",
        {
            "passed": False,
            "render_artifact_id": render.identity.artifact_id,
            "render_sha256": render.sha256,
        },
        (render,),
    )
    report = evaluate_release(config, "colors-red")
    assert not report.private_test_upload_allowed
    assert any(c.name == "media_qa_passed" and not c.passed for c in report.checks)


def test_metadata_review_blocks_upload_without_invalidating_render(ready):
    (config, _, _, store), _, _, metadata = ready
    store.record_approval(
        ApprovalDecision(
            target_id=metadata.identity.artifact_id,
            target_kind="artifact",
            status="needs_review",
            actor="fixture",
            policy_version="fixture",
            decided_at=datetime.now(UTC),
        )
    )
    report = evaluate_release(config, "colors-red")
    assert report.render_ready
    assert not report.private_test_upload_allowed
    assert any(
        c.name == "approval" and c.scope == "private" and not c.passed for c in report.checks
    )


def test_failed_media_qa_blocks_private(ready):
    (config, _, _, store), _, qa, _ = ready
    path = store.path_for(qa.identity.artifact_id)
    payload = json.loads(path.read_text())
    payload["passed"] = False
    path.write_text(json.dumps(payload))
    report = evaluate_release(config, "colors-red")
    assert not report.private_test_upload_allowed
    assert any(c.name == "immutable_sha" and not c.passed for c in report.checks)


def test_web_health_projection_media_and_host(ready, monkeypatch):
    (config, _, _, store), render, _, _ = ready
    app = create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/episodes").json()[0]["external_key"] == "colors-red"
        detail = client.get("/api/episodes/colors-red").json()
        assert detail["video_url"].endswith(render.identity.artifact_id)
        assert detail["preflight"]["private_test_upload_allowed"]
        assert (
            client.get(detail["video_url"]).content
            == store.path_for(render.identity.artifact_id).read_bytes()
        )
        assert client.get("/api/media/../../secret.json").status_code == 404
        assert client.get("/api/media/00000000-0000-0000-0000-000000000000").status_code == 404
        assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
        assert (
            client.post(
                "/api/youtube/connect", headers={"origin": "http://evil.example"}
            ).status_code
            == 403
        )
        called = []
        monkeypatch.setattr(
            "tovitunes.web.app.ProductionRenderer.render",
            lambda self, key, **kw: called.append((key, kw)) or {"ok": True},
        )
        response = client.post("/api/episodes/colors-red/render")
        assert response.status_code == 202
        for _ in range(100):
            if client.get("/api/jobs/" + response.json()["job_id"]).json()["status"] == "succeeded":
                break
            time.sleep(0.01)
        assert called == [("colors-red", {"visual_story": True})]


def test_job_manager_one_worker_and_sanitized_failure():
    manager = JobManager()
    import threading

    gate = threading.Event()
    first = manager.submit("render", "colors-red", lambda: (gate.wait(), {"ok": True})[1])
    with pytest.raises(JobBusy):
        manager.submit("render", "blue", lambda: {})
    gate.set()
    for _ in range(100):
        if manager.get(first.job_id).status == "succeeded":
            break
        time.sleep(0.01)
    assert manager.get(first.job_id).status == "succeeded"
    second = manager.submit(
        "render", "red", lambda: (_ for _ in ()).throw(ValueError("SECRET_TOKEN"))
    )
    for _ in range(100):
        if manager.get(second.job_id).status == "failed":
            break
        time.sleep(0.01)
    assert "SECRET_TOKEN" not in manager.get(second.job_id).error
    manager.close()


class FakeUpload:
    def __init__(self, responses):
        self.responses = iter(responses)

    def next_chunk(self):
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return None, value


class FakeVideos:
    def __init__(self, responses):
        self.responses = responses
        self.body = None
        self.inserts = 0

    def insert(self, **kwargs):
        self.inserts += 1
        self.body = kwargs["body"]
        return FakeUpload(self.responses)


class FakeService:
    def __init__(self, responses, channel="expected-channel"):
        self.video_api = FakeVideos(responses)
        self.channel_id = channel

    def videos(self):
        return self.video_api

    def channels(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        return {"items": [{"id": self.channel_id, "snippet": {"title": "ToviTunes"}}]}


def test_youtube_body_channel_retry_and_video_id(ready):
    (config, _, episode, store), render, _, _ = ready
    service = FakeService([TimeoutError("temporary"), {"id": "exact-video-id"}])
    client = YouTubeClient(
        config.publication.youtube,
        service=service,
        media_factory=lambda *a, **k: object(),
        sleeper=lambda _: None,
    )
    assert client.assert_channel("expected-channel")["matches_expected"]
    metadata = EpisodePublicationMetadata.model_validate(
        store.read_json(next(r.identity.artifact_id for r in [ready[3]]))
    )
    starts = []
    video_id = client.upload_private(
        store.path_for(render.identity.artifact_id),
        metadata,
        on_remote_start=lambda: starts.append(True),
        assert_ownership=lambda: None,
    )
    assert video_id == "exact-video-id" and starts == [True]
    assert service.video_api.inserts == 1
    assert service.video_api.body["status"] == {
        "privacyStatus": "private",
        "selfDeclaredMadeForKids": True,
        "containsSyntheticMedia": True,
    }
    assert service.video_api.body["snippet"]["title"] == "Red with Tovi"
    bad = YouTubeClient(config.publication.youtube, service=FakeService([], "wrong"))
    with pytest.raises(ChannelMismatch):
        bad.assert_channel("expected-channel")


def test_youtube_remote_ambiguity_and_explicit_rejection(ready):
    (config, _, _, store), render, _, metadata_record = ready
    metadata = EpisodePublicationMetadata.model_validate(
        store.read_json(metadata_record.identity.artifact_id)
    )
    failing = YouTubeClient(
        config.publication.youtube,
        service=FakeService([TimeoutError("network down")] * 7),
        media_factory=lambda *a, **k: object(),
        sleeper=lambda _: None,
    )
    with pytest.raises(UploadAmbiguous):
        failing.upload_private(
            store.path_for(render.identity.artifact_id),
            metadata,
            on_remote_start=lambda: None,
            assert_ownership=lambda: None,
        )

    class Rejected(Exception):
        resp = type("Response", (), {"status": 400})()
        content = b'{"error":{"errors":[{"reason":"uploadLimitExceeded"}]}}'

    rejected = YouTubeClient(
        config.publication.youtube,
        service=FakeService([Rejected()]),
        media_factory=lambda *a, **k: object(),
        sleeper=lambda _: None,
    )
    from tovitunes.youtube.client import UploadRejected

    with pytest.raises(UploadRejected, match="limit"):
        rejected.upload_private(
            store.path_for(render.identity.artifact_id),
            metadata,
            on_remote_start=lambda: None,
            assert_ownership=lambda: None,
        )


def test_oauth_saved_token_refresh_and_invalid_grant(context, monkeypatch):
    import googleapiclient.discovery
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    config, _, _, _ = context
    yt = config.publication.youtube
    yt.token_file.write_text(
        json.dumps(
            {
                "scopes": [
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/youtube.force-ssl",
                ]
            }
        ),
        encoding="utf-8",
    )
    yt.credentials_file.write_text("local secret", encoding="utf-8")
    service = FakeService([])
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: service)

    class CredentialsFake:
        expired = False
        refresh_token = "refresh"
        valid = True
        refresh_error = None
        refreshed = 0

        def refresh(self, request):
            self.refreshed += 1
            if self.refresh_error:
                raise self.refresh_error
            self.expired = False
            self.valid = True

        def to_json(self):
            return json.dumps(
                {
                    "token": "locally-persisted",
                    "scopes": [
                        "https://www.googleapis.com/auth/youtube.upload",
                        "https://www.googleapis.com/auth/youtube.readonly",
                        "https://www.googleapis.com/auth/youtube.force-ssl",
                    ],
                }
            )

    fake = CredentialsFake()
    monkeypatch.setattr(
        Credentials, "from_authorized_user_file", staticmethod(lambda *a, **k: fake)
    )

    class FlowFake:
        launched = 0

        def run_local_server(self, port):
            self.launched += 1
            assert port == 0
            return fake

    flow = FlowFake()
    monkeypatch.setattr(
        InstalledAppFlow, "from_client_secrets_file", staticmethod(lambda *a, **k: flow)
    )
    assert YouTubeClient(yt).service() is service
    assert flow.launched == 0 and fake.refreshed == 0
    assert yt.token_file.read_text() == fake.to_json()
    fake.expired, fake.valid = True, False
    assert YouTubeClient(yt).service() is service
    assert fake.refreshed == 1 and flow.launched == 0
    fake.expired, fake.valid = True, False
    fake.refresh_error = RefreshError("secret refresh data", {"error": "temporarily_unavailable"})
    from tovitunes.youtube.client import YouTubeError

    with pytest.raises(YouTubeError) as error:
        YouTubeClient(yt).service(interactive=True)
    assert "secret refresh data" not in str(error.value)
    assert flow.launched == 0
    fake.refresh_error = RefreshError("invalid", {"error": "invalid_grant"})
    fake.valid = True
    assert YouTubeClient(yt).service(interactive=True) is service
    assert flow.launched == 1
    yt.token_file.write_text(
        json.dumps({"scopes": ["https://www.googleapis.com/auth/youtube.upload"]}),
        encoding="utf-8",
    )
    with pytest.raises(YouTubeError, match="reconnect"):
        YouTubeClient(yt).service()
    fake.refresh_error = None
    fake.expired, fake.valid = False, True
    assert YouTubeClient(yt).service(interactive=True) is service
    assert flow.launched == 2


def test_durable_success_deduplicates_and_ambiguity_blocks(ready):
    (config, db, episode, _), _, _, _ = ready

    class Client:
        calls = 0
        ambiguous = False

        def assert_channel(self, expected):
            assert expected == "expected-channel"

        def upload_private(self, path, metadata, *, on_remote_start, assert_ownership):
            self.calls += 1
            on_remote_start()
            assert_ownership()
            if self.ambiguous:
                raise UploadAmbiguous("uncertain")
            return "video-123"

    fake = Client()
    publisher = PublicationService(config, client_factory=lambda: fake)
    first = publisher.upload_private("colors-red")
    second = publisher.upload_private("colors-red")
    assert first["youtube_video_id"] == second["youtube_video_id"] == "video-123"
    assert fake.calls == 1
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM publication_attempts").fetchone()[0] == 1
    with db.connect() as connection:
        connection.execute("DELETE FROM publication_attempts")
        connection.commit()
    fake.ambiguous = True
    with pytest.raises(UploadAmbiguous):
        publisher.upload_private("colors-red")
    with pytest.raises(ValueError, match="manual reconciliation"):
        publisher.upload_private("colors-red")
    assert fake.calls == 2
    latest = publisher.latest(episode.episode_id)
    assert latest["outcome"] == "ambiguous" and latest["operator_action"]
