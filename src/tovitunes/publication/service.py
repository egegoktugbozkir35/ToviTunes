"""Lease-fenced publication attempts with durable remote-start evidence."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig
from tovitunes.creative.models import EpisodePublicationMetadata
from tovitunes.errors import ProductionStop, UploadAmbiguous, UploadRejected, YouTubeError
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.persistence.db import Database
from tovitunes.publication.models import PublicationState, publication_state
from tovitunes.publication.preflight import evaluate_release
from tovitunes.youtube.client import YouTubeClient


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    if result.get("youtube_video_id"):
        result["watch_url"] = f"https://www.youtube.com/watch?v={result['youtube_video_id']}"
    result["state"] = publication_state(row).value
    if publication_state(row) in {
        PublicationState.REMOTE_STARTED,
        PublicationState.AMBIGUOUS_FAILED,
    }:
        result["operator_action"] = "Manual reconciliation required"
    return result


class PublicationService:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        client_factory: Callable[[], YouTubeClient] | None = None,
        ownership: ProductionExecutionOwnership | None = None,
    ) -> None:
        self.config = config
        self.ownership = ownership
        self.database = Database(config.database_path)
        self.client_factory = client_factory or (lambda: YouTubeClient(config.publication.youtube))

    def history(self, episode_id: str) -> list[dict[str, Any]]:
        with closing(self.database.connect()) as db:
            attempts = [
                _row_dict(row)
                for row in db.execute(
                    "SELECT * FROM publication_attempts WHERE episode_id=? "
                    "ORDER BY prepared_at DESC",
                    (episode_id,),
                )
            ]
            for attempt in attempts:
                event = db.execute(
                    "SELECT * FROM publication_visibility_events WHERE upload_attempt_id=? "
                    "ORDER BY prepared_at DESC LIMIT 1",
                    (attempt["attempt_id"],),
                ).fetchone()
                attempt["public_promotion"] = dict(event) if event else None
                if event and event["outcome"] == "succeeded":
                    attempt["privacy_status"] = "public"
                elif event and event["outcome"] in {"remote_started", "ambiguous"}:
                    attempt["operator_action"] = (
                        "Public visibility uncertain; manual reconciliation required"
                    )
            return attempts

    def latest(self, episode_id: str) -> dict[str, Any] | None:
        history = self.history(episode_id)
        return next(
            (row for row in history if str(row.get("youtube_video_id") or "").strip()),
            history[0] if history else None,
        )

    def upload_private(self, episode_key: str) -> dict[str, Any]:
        if not self.config.publication.youtube.enabled:
            raise ValueError("YouTube publication is disabled")
        if not self.config.expected_youtube_channel_id:
            raise ValueError("Expected YouTube channel ID is required")
        self._assert_owned()
        with closing(self.database.connect()) as db:
            episode = db.execute(
                "SELECT episode_id FROM episodes WHERE external_key=?", (episode_key,)
            ).fetchone()
        if episode is None:
            raise KeyError(episode_key)
        eid = str(episode[0])
        with closing(self.database.connect()) as db:
            mismatch = db.execute(
                "SELECT 1 FROM publication_attempts WHERE episode_id=? "
                "AND (youtube_video_id<>'' OR outcome IN "
                "('succeeded','remote_started','ambiguous')) "
                "AND expected_channel_id IS NOT NULL AND expected_channel_id<>? LIMIT 1",
                (eid, self.config.expected_youtube_channel_id),
            ).fetchone()
            if mismatch:
                raise ValueError("Retained publication belongs to another configured channel")
            prior = db.execute(
                "SELECT * FROM publication_attempts WHERE episode_id=? "
                "AND trim(coalesce(youtube_video_id,''))<>'' ORDER BY rowid DESC LIMIT 1",
                (eid,),
            ).fetchone()
            unresolved = db.execute(
                "SELECT * FROM publication_attempts WHERE episode_id=? "
                "AND outcome IN ('remote_started','ambiguous','succeeded') LIMIT 1",
                (eid,),
            ).fetchone()
        if prior is not None:
            return _row_dict(prior)
        if unresolved is not None:
            raise UploadAmbiguous(
                "Previous remote upload outcome is uncertain; reconciliation required"
            )
        preflight = evaluate_release(self.config, episode_key)
        if not preflight.private_test_upload_allowed:
            raise ValueError("Private test upload is blocked by release preflight")
        assert preflight.render_artifact_id and preflight.render_sha256
        assert preflight.metadata_artifact_id and preflight.metadata_fingerprint
        store = AssetStore(self.config.data_root, self.database, initialize=False)
        metadata = EpisodePublicationMetadata.model_validate(
            store.read_json(preflight.metadata_artifact_id)
        )
        video_path: Path = store.path_for(preflight.render_artifact_id)
        client = self.client_factory()
        client.assert_channel(self.config.expected_youtube_channel_id)
        # Recheck after the network channel lookup and immediately before a durable attempt.
        if evaluate_release(self.config, episode_key).as_dict() != preflight.as_dict():
            raise ValueError("Release evidence changed before upload")
        self._assert_owned()
        attempt_id = str(uuid4())
        with closing(self.database.connect()) as db:
            db.execute(
                "INSERT INTO publication_attempts "
                "(attempt_id,episode_id,platform,mode,render_artifact_id,render_sha256,"
                "metadata_fingerprint,prepared_at,outcome,privacy_status,expected_channel_id) "
                "VALUES (?,?,'youtube','private_test',?,?,?,?,'prepared','private',?)",
                (
                    attempt_id,
                    eid,
                    preflight.render_artifact_id,
                    preflight.render_sha256,
                    preflight.metadata_fingerprint,
                    _now(),
                    self.config.expected_youtube_channel_id,
                ),
            )
            db.commit()
        started = False

        def remote_start() -> None:
            nonlocal started
            self._assert_owned()
            with closing(self.database.connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    "UPDATE publication_attempts SET outcome='remote_started', "
                    "remote_started_at=? "
                    "WHERE attempt_id=? AND outcome='prepared'",
                    (_now(), attempt_id),
                )
                if cursor.rowcount != 1:
                    db.rollback()
                    raise ValueError("Publication attempt is no longer prepared")
                db.commit()
            started = True

        try:
            video_id = client.upload_private(
                video_path,
                metadata,
                on_remote_start=remote_start,
                assert_ownership=self._assert_owned,
            )
            if not started or not isinstance(video_id, str) or not video_id.strip():
                raise UploadAmbiguous("Upload returned no trusted remote identity")
        except UploadRejected as exc:
            self._finish(attempt_id, "terminal_failure", "upload_rejected", str(exc))
            raise
        except Exception as exc:
            if started:
                self._finish(
                    attempt_id,
                    "ambiguous",
                    "remote_outcome_unknown",
                    "Remote outcome is uncertain; manual reconciliation required",
                )
                raise UploadAmbiguous(
                    "Remote outcome is uncertain; manual reconciliation required"
                ) from exc
            self._finish(
                attempt_id, "terminal_failure", "preflight_error", "Upload preparation failed"
            )
            if isinstance(exc, YouTubeError):
                raise
            raise YouTubeError("Upload preparation failed") from exc
        with closing(self.database.connect()) as db:
            db.execute(
                "UPDATE publication_attempts SET outcome='succeeded', completed_at=?, "
                "youtube_video_id=? WHERE attempt_id=? AND outcome='remote_started'",
                (_now(), video_id, attempt_id),
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM publication_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        assert row is not None
        # A returned remote ID is evidence even if the lease expired during upload.
        # Retain it before fencing the caller from promotion or any subsequent effect.
        self._assert_owned()
        return _row_dict(row)

    def publish_public(self, episode_key: str) -> dict[str, Any]:
        """Promote one durable private upload, never inserting a new video."""
        if (
            not self.config.publication.youtube.enabled
            or not self.config.expected_youtube_channel_id
        ):
            raise ValueError("YouTube channel is not configured")
        self._assert_owned()
        preflight = evaluate_release(self.config, episode_key)
        if not preflight.public_release_allowed:
            raise ValueError("Public release is blocked by release preflight")
        with closing(self.database.connect()) as db:
            episode = db.execute(
                "SELECT episode_id FROM episodes WHERE external_key=?", (episode_key,)
            ).fetchone()
            if episode is None:
                raise KeyError(episode_key)
            eid = str(episode[0])
            unresolved = db.execute(
                "SELECT event_id FROM publication_visibility_events WHERE episode_id=? "
                "AND outcome IN ('prepared','remote_started','ambiguous') LIMIT 1",
                (eid,),
            ).fetchone()
            if unresolved:
                raise ValueError("Public visibility is uncertain; manual reconciliation required")
            upload = db.execute(
                "SELECT * FROM publication_attempts WHERE episode_id=? "
                "AND trim(coalesce(youtube_video_id,''))<>'' "
                "ORDER BY prepared_at DESC LIMIT 1",
                (eid,),
            ).fetchone()
            if upload is None:
                raise ValueError("No successful private YouTube upload exists")
            previous = db.execute(
                "SELECT * FROM publication_visibility_events WHERE upload_attempt_id=? "
                "AND outcome='succeeded' LIMIT 1",
                (upload["attempt_id"],),
            ).fetchone()
        if (
            upload["render_artifact_id"] != preflight.render_artifact_id
            or upload["render_sha256"] != preflight.render_sha256
            or upload["metadata_fingerprint"] != preflight.metadata_fingerprint
            or preflight.metadata_artifact_id is None
        ):
            raise ValueError("Selected render or metadata changed since private upload")
        if previous:
            return dict(previous)
        video_id = str(upload["youtube_video_id"])
        client = self.client_factory()
        client.assert_channel(self.config.expected_youtube_channel_id)
        remote = client.video_status(video_id)
        if (
            not remote.get("available")
            or remote.get("video_id") != video_id
            or remote.get("channel_id") != self.config.expected_youtube_channel_id
            or remote.get("privacy") != "private"
            or remote.get("upload_status") != "processed"
            or remote.get("processing_status") != "succeeded"
            or remote.get("self_declared_made_for_kids") is not True
            or remote.get("contains_synthetic_media")
            is not self.config.publication.youtube.contains_synthetic_media
        ):
            raise ValueError("Recorded private video is not ready or its policy differs")
        if evaluate_release(self.config, episode_key).as_dict() != preflight.as_dict():
            raise ValueError("Release evidence changed before public promotion")
        self._assert_owned()
        event_id = str(uuid4())
        with closing(self.database.connect()) as db:
            db.execute(
                "INSERT INTO publication_visibility_events "
                "(event_id,upload_attempt_id,episode_id,youtube_video_id,prior_privacy,"
                "target_privacy,render_artifact_id,render_sha256,metadata_artifact_id,"
                "metadata_fingerprint,prepared_at,outcome) VALUES (?,?,?,?,'private','public',"
                "?,?,?,?,?,'prepared')",
                (
                    event_id,
                    upload["attempt_id"],
                    eid,
                    video_id,
                    preflight.render_artifact_id,
                    preflight.render_sha256,
                    preflight.metadata_artifact_id,
                    preflight.metadata_fingerprint,
                    _now(),
                ),
            )
            db.commit()
        started = False
        try:
            self._assert_owned()
            with closing(self.database.connect()) as db:
                db.execute(
                    "UPDATE publication_visibility_events SET outcome='remote_started', "
                    "remote_started_at=? WHERE event_id=? AND outcome='prepared'",
                    (_now(), event_id),
                )
                db.commit()
            started = True
            self._assert_owned()
            result = client.publish_video(video_id, remote)
            status = result.get("status", {})
            if (
                result.get("id") != video_id
                or status.get("privacyStatus") != "public"
                or status.get("selfDeclaredMadeForKids") is not True
                or status.get("containsSyntheticMedia")
                is not self.config.publication.youtube.contains_synthetic_media
            ):
                raise UploadAmbiguous("Public visibility response is uncertain")
        except Exception as exc:
            self._finish_visibility(
                event_id,
                "ambiguous" if started else "terminal_failure",
                "Public visibility outcome is uncertain; manual reconciliation required"
                if started
                else "Public promotion preparation failed",
            )
            if started:
                raise UploadAmbiguous(
                    "Public visibility outcome is uncertain; manual reconciliation required"
                ) from exc
            raise
        self._finish_visibility(event_id, "succeeded", None)
        self._assert_owned()
        with closing(self.database.connect()) as db:
            event = db.execute(
                "SELECT * FROM publication_visibility_events WHERE event_id=?", (event_id,)
            ).fetchone()
        assert event is not None
        return dict(event)

    def _finish_visibility(self, event_id: str, outcome: str, summary: str | None) -> None:
        with closing(self.database.connect()) as db:
            db.execute(
                "UPDATE publication_visibility_events SET outcome=?,completed_at=?,"
                "safe_error_summary=? WHERE event_id=?",
                (outcome, _now(), summary, event_id),
            )
            db.commit()

    def _finish(self, attempt_id: str, outcome: str, classification: str, summary: str) -> None:
        with closing(self.database.connect()) as db:
            db.execute(
                "UPDATE publication_attempts SET outcome=?, completed_at=?, "
                "error_classification=?, safe_error_summary=? WHERE attempt_id=?",
                (outcome, _now(), classification, summary, attempt_id),
            )
            db.commit()

    def _assert_owned(self) -> None:
        if self.ownership is None:
            raise ValueError("Publication effects require orchestrator execution ownership")
        self.ownership.assert_owned()

    def publish(self, episode_key: str) -> dict[str, Any]:
        self._assert_owned()
        release = evaluate_release(self.config, episode_key)
        allowed = (
            release.public_release_allowed
            if self.config.automation.publish_visibility == "public"
            else release.private_test_upload_allowed
        )
        if not allowed:
            raise ProductionStop(
                "NEEDS_REVIEW" if self.config.automation.require_human_review else "BLOCKED",
                "Configured release gates block publication",
                {"release": release.as_dict()},
            )
        try:
            uploaded = self.upload_private(episode_key)
            if self.config.automation.publish_visibility == "public":
                self.publish_public(episode_key)
        except UploadAmbiguous as exc:
            raise ProductionStop("AMBIGUOUS", "YouTube outcome requires reconciliation") from exc
        return {"status": "COMPLETE", "publication": uploaded}
