"""Offline regression checks for same-video public promotion and rights closeout."""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_web_youtube_v1 import artifact, rights

from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.rights import closeout_rights, reviewed_graph_template
from tovitunes.publication.service import PublicationService
from tovitunes.web.app import create_app
from tovitunes.youtube.client import ChannelMismatch, UploadAmbiguous, YouTubeClient

pytest_plugins = ("test_web_youtube_v1",)


def clear_graph(ready):
    (config, _, _, store), _, _, _ = ready
    report = evaluate_release(config, "colors-red")
    ids = {c.artifact_id for c in report.checks if c.name == "immutable_sha"}
    for aid in ids:
        rights(store, store.get(aid), "commercial_use_confirmed")
    assert evaluate_release(config, "colors-red").public_release_allowed


def record_upload(ready):
    (config, db, episode, _), _, _, _ = ready
    report = evaluate_release(config, "colors-red")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO publication_attempts "
            "(attempt_id,episode_id,platform,mode,render_artifact_id,render_sha256,"
            "metadata_fingerprint,prepared_at,remote_started_at,completed_at,outcome,"
            "youtube_video_id,privacy_status) VALUES "
            "('upload-1',?,'youtube','private_test',?,?,?,"
            "'2026-10-05T00:00:00Z','2026-10-05T00:00:01Z',"
            "'2026-10-05T00:00:02Z','succeeded','video-123','private')",
            (
                episode.episode_id,
                report.render_artifact_id,
                report.render_sha256,
                report.metadata_fingerprint,
            ),
        )
        connection.commit()


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.channel = "expected-channel"
        self.remote = {
            "video_id": "video-123",
            "available": True,
            "privacy": "private",
            "channel_id": "expected-channel",
            "upload_status": "processed",
            "processing_status": "succeeded",
            "self_declared_made_for_kids": True,
            "contains_synthetic_media": True,
        }
        self.raise_update = False

    def assert_channel(self, expected):
        if self.channel != expected:
            raise ChannelMismatch("Connected YouTube channel differs from configured channel")

    def video_status(self, video_id):
        return self.remote

    def publish_video(self, video_id, remote):
        self.calls += 1
        if self.raise_update:
            raise UploadAmbiguous("unknown")
        return {
            "id": video_id,
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": True,
                "containsSyntheticMedia": True,
            },
        }


def test_public_requires_private_upload_and_rights(ready):
    (config, _, _, store), render, _, _ = ready
    with pytest.raises(ValueError, match="preflight"):
        PublicationService(config, client_factory=FakeClient).publish_public("colors-red")
    clear_graph(ready)
    with pytest.raises(ValueError, match="No successful private"):
        PublicationService(config, client_factory=FakeClient).publish_public("colors-red")
    record_upload(ready)
    rights(store, render, "review_required")
    with pytest.raises(ValueError, match="preflight"):
        PublicationService(config, client_factory=FakeClient).publish_public("colors-red")


def test_public_promotion_same_video_and_idempotent(ready):
    (config, db, episode, _), _, _, _ = ready
    clear_graph(ready)
    record_upload(ready)
    client = FakeClient()
    service = PublicationService(config, client_factory=lambda: client)
    first = service.publish_public("colors-red")
    second = service.publish_public("colors-red")
    assert first["event_id"] == second["event_id"]
    assert first["youtube_video_id"] == "video-123" and first["outcome"] == "succeeded"
    assert client.calls == 1
    history = service.history(episode.episode_id)
    assert history[0]["outcome"] == "succeeded"
    assert history[0]["privacy_status"] == "public"
    assert history[0]["public_promotion"]["outcome"] == "succeeded"
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM publication_attempts").fetchone()[0] == 1


@pytest.mark.parametrize(
    "drift", ["channel", "remote_channel", "render", "metadata", "remote_id", "processing", "kids"]
)
def test_public_promotion_fails_closed(ready, drift):
    (config, db, _, _), _, _, _ = ready
    clear_graph(ready)
    record_upload(ready)
    client = FakeClient()
    if drift == "channel":
        client.channel = "other"
    elif drift == "remote_channel":
        client.remote["channel_id"] = "other"
    elif drift == "render":
        with db.connect() as connection:
            connection.execute("UPDATE publication_attempts SET render_sha256='wrong'")
            connection.commit()
    elif drift == "metadata":
        with db.connect() as connection:
            connection.execute("UPDATE publication_attempts SET metadata_fingerprint='wrong'")
            connection.commit()
    elif drift == "remote_id":
        client.remote["video_id"] = "other"
    elif drift == "processing":
        client.remote["processing_status"] = "processing"
    else:
        client.remote["self_declared_made_for_kids"] = False
    with pytest.raises((ValueError, ChannelMismatch)):
        PublicationService(config, client_factory=lambda: client).publish_public("colors-red")
    assert client.calls == 0


def test_uncertain_public_update_is_durable_and_never_retried(ready):
    (config, db, _, _), _, _, _ = ready
    clear_graph(ready)
    record_upload(ready)
    client = FakeClient()
    client.raise_update = True
    service = PublicationService(config, client_factory=lambda: client)
    with pytest.raises(UploadAmbiguous):
        service.publish_public("colors-red")
    with pytest.raises(ValueError, match="uncertain"):
        service.publish_public("colors-red")
    assert client.calls == 1
    with db.connect() as connection:
        event = connection.execute("SELECT * FROM publication_visibility_events").fetchone()
        assert event["outcome"] == "ambiguous" and event["remote_started_at"]


def test_youtube_client_uses_update_without_insert(ready):
    (config, _, _, _), _, _, _ = ready

    class VideoAPI:
        def __init__(self):
            self.body = None
            self.inserts = 0

        def update(self, **kwargs):
            self.body = kwargs["body"]
            return self

        def execute(self):
            return {"id": "video-123", "status": self.body["status"]}

        def insert(self, **kwargs):
            self.inserts += 1
            raise AssertionError("must not upload")

    api = VideoAPI()

    class Service:
        def videos(self):
            return api

    result = YouTubeClient(config.publication.youtube, service=Service()).publish_video(
        "video-123", {"license": "youtube", "embeddable": True}
    )
    assert result["id"] == "video-123"
    assert api.body["status"]["privacyStatus"] == "public"
    assert api.body["status"]["selfDeclaredMadeForKids"] is True
    assert api.body["status"]["containsSyntheticMedia"] is True
    assert api.inserts == 0


def test_rights_closeout_appends_only_reviewed_graph(ready):
    (config, db, _, store), render, _, _ = ready
    unrelated = artifact(ready[0], "unrelated", {"fixture": "unrelated"})
    evidence = reviewed_graph_template(config, "colors-red")
    graph = evidence["graph_sha256"]
    for detail in evidence["decisions"].values():
        detail.update(
            {
                "actor": "human:reviewer",
                "evidence_uri": "https://example.test/evidence",
                "rationale": "Reviewed provider and source terms for publication",
                "decided_at": datetime.now(UTC).isoformat(),
            }
        )
    rights(store, render, "review_required")
    with db.connect() as connection:
        before = connection.execute("SELECT count(*) FROM rights_decisions").fetchone()[0]
    with pytest.raises(ValueError):
        closeout_rights(config, {**evidence, "graph_sha256": {}})
    with pytest.raises(ValueError, match="Reviewed release inputs"):
        closeout_rights(config, {**evidence, "render_artifact_id": unrelated.identity.artifact_id})
    with pytest.raises(ValueError, match="uncleared selected release graph"):
        closeout_rights(
            config,
            {
                **evidence,
                "decisions": {
                    **evidence["decisions"],
                    unrelated.identity.artifact_id: {
                        "sha256": unrelated.sha256,
                        "kind": unrelated.identity.kind,
                        "slot_key": unrelated.identity.slot_key,
                        "actor": "human:reviewer",
                        "evidence_uri": "https://example.test/evidence",
                        "rationale": "Unrelated evidence",
                        "decided_at": datetime.now(UTC).isoformat(),
                    },
                },
            },
        )
    with pytest.raises(ValueError):
        closeout_rights(
            config,
            {
                **evidence,
                "decisions": {
                    **evidence["decisions"],
                    render.identity.artifact_id: {
                        **evidence["decisions"][render.identity.artifact_id],
                        "evidence_uri": "",
                    },
                },
            },
        )
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM rights_decisions").fetchone()[0] == before
    changes = closeout_rights(config, evidence)
    assert len(changes) == len(graph)
    assert evaluate_release(config, "colors-red").public_release_allowed
    assert closeout_rights(config, evidence) == []
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM rights_decisions WHERE artifact_id=?",
                (render.identity.artifact_id,),
            ).fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM rights_decisions WHERE artifact_id=?",
                (unrelated.identity.artifact_id,),
            ).fetchone()[0]
            == 1
        )


def test_web_public_endpoint_and_confirmation(ready):
    (config, _, _, _), _, _, _ = ready
    with TestClient(create_app(config), base_url="http://127.0.0.1:8765") as client:
        assert (
            client.post(
                "/api/episodes/colors-red/youtube/publish", json={"video_id": "arbitrary"}
            ).status_code
            == 409
        )
    script = (Path(__file__).resolve().parents[1] / "src/tovitunes/web/static/app.js").read_text()
    assert "window.confirm('Publish this ToviTunes Short publicly" in script
    assert "publish.disabled=true" in script
    assert "video_id" not in script.split("/youtube/publish", 1)[1].split("POST", 1)[0]


def test_web_metadata_action_uses_existing_writer(ready, monkeypatch):
    (config, _, _, _), _, _, _ = ready
    calls = []

    def generate(writer, episode_key):
        calls.append((type(writer).__name__, episode_key))
        return {"publication_metadata_artifact_id": "existing"}

    monkeypatch.setattr("tovitunes.creative.metadata.MetadataWriter.generate", generate)
    with TestClient(create_app(config), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/episodes/colors-red/publication-metadata")
        assert response.status_code == 202
        for _ in range(100):
            job = client.get("/api/jobs/" + response.json()["job_id"]).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert job["status"] == "succeeded"
    assert calls == [("MetadataWriter", "colors-red")]
