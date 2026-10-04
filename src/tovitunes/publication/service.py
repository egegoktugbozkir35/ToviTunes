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
from tovitunes.persistence.db import Database
from tovitunes.persistence.leases import LeaseStore
from tovitunes.publication.preflight import evaluate_release
from tovitunes.youtube.client import UploadAmbiguous, UploadRejected, YouTubeClient, YouTubeError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    if result.get("youtube_video_id"):
        result["watch_url"] = f"https://www.youtube.com/watch?v={result['youtube_video_id']}"
    if result["outcome"] in {"remote_started", "ambiguous"}:
        result["operator_action"] = "Manual reconciliation required"
    return result


class PublicationService:
    def __init__(
        self, config: RuntimeConfig, *, client_factory: Callable[[], YouTubeClient] | None = None
    ) -> None:
        self.config = config
        self.database = Database(config.database_path)
        self.client_factory = client_factory or (lambda: YouTubeClient(config.publication.youtube))

    def history(self, episode_id: str) -> list[dict[str, Any]]:
        with closing(self.database.connect()) as db:
            return [
                _row_dict(row)
                for row in db.execute(
                    "SELECT * FROM publication_attempts WHERE episode_id=? "
                    "ORDER BY prepared_at DESC",
                    (episode_id,),
                )
            ]

    def latest(self, episode_id: str) -> dict[str, Any] | None:
        history = self.history(episode_id)
        return history[0] if history else None

    def upload_private(self, episode_key: str) -> dict[str, Any]:
        if not self.config.publication.youtube.enabled:
            raise ValueError("YouTube publication is disabled")
        if not self.config.expected_youtube_channel_id:
            raise ValueError("Expected YouTube channel ID is required")
        leases = LeaseStore(self.database)
        lease = leases.acquire(f"youtube-private:{episode_key}", duration_seconds=14400)
        try:
            preflight = evaluate_release(self.config, episode_key)
            if not preflight.private_test_upload_allowed:
                raise ValueError("Private test upload is blocked by release preflight")
            assert preflight.render_artifact_id and preflight.render_sha256
            assert preflight.metadata_artifact_id and preflight.metadata_fingerprint
            with closing(self.database.connect()) as db:
                episode = db.execute(
                    "SELECT episode_id FROM episodes WHERE external_key=?", (episode_key,)
                ).fetchone()
            if episode is None:
                raise KeyError(episode_key)
            eid = str(episode[0])
            with closing(self.database.connect()) as db:
                unresolved = db.execute(
                    "SELECT attempt_id FROM publication_attempts WHERE episode_id=? "
                    "AND outcome IN ('remote_started','ambiguous') LIMIT 1",
                    (eid,),
                ).fetchone()
                if unresolved is not None:
                    raise ValueError(
                        "Previous remote upload outcome is uncertain; "
                        "manual reconciliation required"
                    )
                prior = db.execute(
                    "SELECT * FROM publication_attempts WHERE episode_id=? AND render_sha256=? "
                    "AND metadata_fingerprint=? AND outcome IN "
                    "('succeeded','remote_started','ambiguous') "
                    "ORDER BY prepared_at DESC LIMIT 1",
                    (eid, preflight.render_sha256, preflight.metadata_fingerprint),
                ).fetchone()
            if prior is not None:
                if prior["outcome"] == "succeeded":
                    return _row_dict(prior)
                raise ValueError(
                    "Previous remote upload outcome is uncertain; manual reconciliation required"
                )
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
            leases.assert_owner(lease)
            attempt_id = str(uuid4())
            with closing(self.database.connect()) as db:
                db.execute(
                    "INSERT INTO publication_attempts "
                    "(attempt_id,episode_id,platform,mode,render_artifact_id,render_sha256,"
                    "metadata_fingerprint,prepared_at,outcome,privacy_status) "
                    "VALUES (?,?,'youtube','private_test',?,?,?,?,'prepared','private')",
                    (
                        attempt_id,
                        eid,
                        preflight.render_artifact_id,
                        preflight.render_sha256,
                        preflight.metadata_fingerprint,
                        _now(),
                    ),
                )
                db.commit()
            started = False

            def remote_start() -> None:
                nonlocal started
                leases.assert_owner(lease)
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
                    assert_ownership=lambda: leases.assert_owner(lease),
                )
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
            return _row_dict(row)
        finally:
            leases.release(lease)

    def _finish(self, attempt_id: str, outcome: str, classification: str, summary: str) -> None:
        with closing(self.database.connect()) as db:
            db.execute(
                "UPDATE publication_attempts SET outcome=?, completed_at=?, "
                "error_classification=?, safe_error_summary=? WHERE attempt_id=?",
                (outcome, _now(), classification, summary, attempt_id),
            )
            db.commit()
