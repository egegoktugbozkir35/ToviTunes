"""Offline regression checks for same-video public promotion and rights closeout."""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_web_youtube_v1 import artifact, rights

from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.rights import closeout_rights, reviewed_graph_template
from tovitunes.publication.rights_policy import evaluate_inherited_rights
from tovitunes.publication.service import PublicationService
from tovitunes.web.app import create_app
from tovitunes.youtube.client import ChannelMismatch, UploadAmbiguous, YouTubeClient

pytest_plugins = ("test_web_youtube_v1",)


def clear_graph(ready):
    (config, _, _, store), _, _, _ = ready
    report = evaluate_release(config, "colors-red")
    ids = {
        c.artifact_id
        for c in report.checks
        if c.name == "commercial_rights_direct" and c.artifact_id
    }
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
    audio = store.selected("episode", render.identity.owner_id, "audio_master", "main")
    assert audio is not None
    rights(store, audio, "review_required")
    with pytest.raises(ValueError, match="preflight"):
        PublicationService(config, client_factory=FakeClient).publish_public("colors-red")


def test_operator_metadata_uses_new_fingerprint_without_external_rights_root(ready):
    (config, db, episode, store), render, _, old = ready
    first = evaluate_release(config, "colors-red")
    assert first.metadata_artifact_id == old.identity.artifact_id
    assert not any(
        check.name == "commercial_rights_direct" and check.artifact_id == old.identity.artifact_id
        for check in first.checks
    )
    payload = store.read_json(old.identity.artifact_id)
    assert isinstance(payload, dict)
    replacement = artifact(
        ready[0],
        "publication_metadata",
        {**payload, "youtube_title": "Learn the Color Red with Tovi | ToviTunes Short"},
        (render,),
    )
    clear_graph(ready)
    report = evaluate_release(config, "colors-red")
    assert report.render_artifact_id == first.render_artifact_id
    assert report.render_sha256 == first.render_sha256
    assert report.metadata_artifact_id == replacement.identity.artifact_id
    assert report.metadata_fingerprint != first.metadata_fingerprint
    assert (
        report.render_ready and report.private_test_upload_allowed and report.public_release_allowed
    )
    snapshot = reviewed_graph_template(config, "colors-red")
    assert replacement.identity.artifact_id not in snapshot["direct_rights_roots"]
    assert not snapshot["decisions"]
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE artifact_id=?",
                (old.identity.artifact_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE kind='final_render'"
            ).fetchone()[0]
            == 1
        )
    record_upload(ready)
    client = FakeClient()
    result = PublicationService(config, client_factory=lambda: client).publish_public("colors-red")
    assert result["youtube_video_id"] == "video-123" and client.calls == 1
    assert (
        store.selected("episode", episode.episode_id, "publication_metadata", "main") == replacement
    )


@pytest.mark.parametrize("status", ["unknown", "review_required", "blocked"])
def test_direct_source_status_blocks_public_release(ready, status):
    (config, _, episode, store), _, _, _ = ready
    clear_graph(ready)
    audio = store.selected("episode", episode.episode_id, "audio_master", "main")
    assert audio is not None
    rights(store, audio, status)
    report = evaluate_release(config, "colors-red")
    direct = [
        check
        for check in report.checks
        if check.name == "commercial_rights_direct"
        and check.artifact_id == audio.identity.artifact_id
    ]
    assert len(direct) == 1 and not direct[0].passed
    assert not report.public_release_allowed


def test_cleared_direct_source_allows_derived_unknown_rights(ready):
    (config, _, _, store), render, _, _ = ready
    clear_graph(ready)
    rights(store, render, "review_required")
    report = evaluate_release(config, "colors-red")
    inherited = [
        check
        for check in report.checks
        if check.name == "commercial_rights_inherited"
        and check.artifact_id == render.identity.artifact_id
    ]
    assert len(inherited) == 1 and inherited[0].passed
    assert report.public_release_allowed


def test_explicitly_blocked_derived_ancestor_blocks_release(ready):
    (config, _, _, store), render, _, _ = ready
    clear_graph(ready)
    manifest = store.selected("episode", render.identity.owner_id, "render_manifest", "main")
    assert manifest is not None
    rights(store, manifest, "blocked")
    report = evaluate_release(config, "colors-red")
    assert not report.private_test_upload_allowed and not report.public_release_allowed
    render_rights = next(
        check
        for check in report.checks
        if check.name == "commercial_rights_inherited"
        and check.artifact_id == render.identity.artifact_id
    )
    assert not render_rights.passed and manifest.identity.artifact_id in render_rights.reason


@pytest.mark.parametrize(
    ("kinds", "expected_root_kind"),
    [
        (
            (
                "lesson_object_source",
                "lesson_object_candidate",
                "lesson_object",
                "lesson_object_manifest",
            ),
            "lesson_object_source",
        ),
        (
            ("environment_source_plate", "environment_plate", "environment_set", "scene_image"),
            "environment_source_plate",
        ),
        (
            ("audio_master", "beat_analysis", "timed_storyboard", "final_render"),
            "audio_master",
        ),
        (
            ("character_reference", "character_animation", "scene_image", "final_render"),
            "character_reference",
        ),
    ],
)
def test_rights_inherit_across_creative_derivative_chains(context, kinds, expected_root_kind):
    records = []
    for kind in kinds:
        content = b"\0\0\0\x18ftypisomfixture" if kind == "final_render" else {"fixture": kind}
        records.append(
            artifact(
                context,
                kind,
                content,
                (records[-1],) if records else (),
            )
        )
    by_id = {record.identity.artifact_id: record for record in records}
    dependencies = {
        record.identity.artifact_id: [records[index - 1].identity.artifact_id] if index else []
        for index, record in enumerate(records)
    }
    statuses = {record.identity.artifact_id: "unknown" for record in records}
    statuses[records[0].identity.artifact_id] = "commercial_use_confirmed"
    result = evaluate_inherited_rights(
        records[-1].identity.artifact_id,
        records=by_id,
        dependencies=dependencies,
        latest_rights=statuses,
        local_integrity={artifact_id: True for artifact_id in by_id},
    )
    assert result.commercially_cleared
    assert result.direct_roots == (records[0].identity.artifact_id,)
    assert by_id[result.direct_roots[0]].identity.kind == expected_root_kind


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
    assert set(evidence["decisions"]) <= set(evidence["direct_rights_roots"])
    assert not set(evidence["decisions"]) & set(evidence["derived_artifact_ids"])
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
    with pytest.raises(ValueError, match="direct rights roots"):
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
    direct_id = next(iter(evidence["decisions"]))
    with pytest.raises(ValueError, match="incomplete"):
        closeout_rights(
            config,
            {
                **evidence,
                "decisions": {
                    **evidence["decisions"],
                    direct_id: {**evidence["decisions"][direct_id], "evidence_uri": ""},
                },
            },
        )
    with pytest.raises(ValueError, match="direct rights roots"):
        closeout_rights(
            config,
            {
                **evidence,
                "decisions": {
                    **evidence["decisions"],
                    render.identity.artifact_id: {
                        "sha256": render.sha256,
                        "kind": render.identity.kind,
                        "slot_key": render.identity.slot_key,
                        "actor": "human:reviewer",
                        "evidence_uri": "https://example.test/evidence",
                        "rationale": "A derived render cannot be closed out directly",
                        "decided_at": datetime.now(UTC).isoformat(),
                    },
                },
            },
        )
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM rights_decisions").fetchone()[0] == before
    changes = closeout_rights(config, evidence)
    assert len(changes) == len(evidence["decisions"])
    assert len(changes) < len(graph)
    assert evaluate_release(config, "colors-red").public_release_allowed
    assert closeout_rights(config, evidence) == []
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM rights_decisions WHERE artifact_id=?",
                (render.identity.artifact_id,),
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM rights_decisions WHERE artifact_id=?",
                (unrelated.identity.artifact_id,),
            ).fetchone()[0]
            == 1
        )


def test_two_attested_character_roots_clear_append_only(ready):
    (config, db, _, store), render, _, old_metadata = ready
    profile = artifact(
        ready[0],
        "character_reference",
        {"fixture": "operator-attested profile"},
        slot_key="source_original_profile",
    )
    banner = artifact(
        ready[0],
        "character_reference",
        {"fixture": "operator-attested banner"},
        slot_key="source_original_banner",
    )
    content = store.read_json(old_metadata.identity.artifact_id)
    replacement = artifact(ready[0], "publication_metadata", content, (render, profile, banner))
    evidence = reviewed_graph_template(config, "colors-red")
    assert {profile.identity.artifact_id, banner.identity.artifact_id} <= set(evidence["decisions"])
    assert replacement.identity.artifact_id not in evidence["direct_rights_roots"]
    for detail in evidence["decisions"].values():
        detail.update(
            actor="human:operator",
            evidence_uri="repo://docs/rights/TOVI_ORIGINAL_SOURCE_ATTESTATION.md",
            rationale="Operator attested and approved the exact source for commercial release",
            decided_at=datetime.now(UTC).isoformat(),
        )
    changes = closeout_rights(config, evidence)
    assert {profile.identity.artifact_id, banner.identity.artifact_id} <= {
        change["artifact_id"] for change in changes
    }
    assert evaluate_release(config, "colors-red").public_release_allowed
    with db.connect() as connection:
        for record in (profile, banner):
            assert [
                row[0]
                for row in connection.execute(
                    "SELECT status FROM rights_decisions WHERE artifact_id=? ORDER BY rowid",
                    (record.identity.artifact_id,),
                )
            ] == ["unknown", "commercial_use_confirmed"]
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE artifact_id=?",
                (old_metadata.identity.artifact_id,),
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
