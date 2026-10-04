"""Local OAuth and one-session resumable private YouTube upload."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tovitunes.config import YouTubeConfig
from tovitunes.creative.models import EpisodePublicationMetadata

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
UPDATE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPES = (SCOPE, READ_SCOPE, UPDATE_SCOPE)
RETRYABLE = {429, 500, 502, 503, 504}


class YouTubeError(RuntimeError):
    """Safe operator-facing YouTube failure."""


class UploadAmbiguous(YouTubeError):
    """The remote attempt began and its final outcome is unknown."""


class UploadRejected(YouTubeError):
    """Google explicitly rejected the request without creating a video."""


class ChannelMismatch(YouTubeError):
    """OAuth selected a channel other than the pinned channel."""


class YouTubeClient:
    def __init__(
        self,
        config: YouTubeConfig,
        *,
        service: Any | None = None,
        media_factory: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._service = service
        self._media_factory = media_factory
        self._sleeper = sleeper

    @staticmethod
    def _invalid_grant(error: Exception) -> bool:
        return (
            not bool(getattr(error, "retryable", False))
            and len(error.args) >= 2
            and isinstance(error.args[1], Mapping)
            and error.args[1].get("error") == "invalid_grant"
        )

    def service(self, *, interactive: bool = False) -> Any:
        if self._service is not None:
            return self._service
        try:
            from google.auth.exceptions import RefreshError
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise YouTubeError("Install the youtube extra to connect") from exc
        credentials = None
        if self.config.token_file.is_file():
            try:
                token_data = json.loads(self.config.token_file.read_text(encoding="utf-8"))
                if not isinstance(token_data, dict):
                    raise ValueError("token must be an object")
                token_scopes = token_data.get("scopes", [])
                if not isinstance(token_scopes, list) or not set(SCOPES).issubset(token_scopes):
                    if not interactive:
                        raise YouTubeError(
                            "YouTube authorization needs reconnect for public visibility access"
                        )
                else:
                    credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                        str(self.config.token_file), list(SCOPES)
                    )
            except YouTubeError:
                raise
            except (OSError, ValueError) as exc:
                raise YouTubeError("YouTube token file is invalid") from exc
        if credentials is not None and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                if not self._invalid_grant(exc):
                    raise YouTubeError(
                        "YouTube token refresh failed; retry connection later"
                    ) from exc
                credentials = None
            except Exception as exc:
                raise YouTubeError("YouTube token refresh failed; retry connection later") from exc
        if credentials is None or not credentials.valid:
            if not interactive:
                raise YouTubeError("YouTube authorization is required; use Connect YouTube")
            if not self.config.credentials_file.is_file():
                raise YouTubeError("YouTube OAuth client secret file is missing")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.config.credentials_file), list(SCOPES)
                )
                credentials = flow.run_local_server(port=0)
            except Exception as exc:
                raise YouTubeError("YouTube authorization failed") from exc
        self.config.token_file.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.config.token_file.parent,
                prefix=self.config.token_file.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(credentials.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.config.token_file)
        except OSError as exc:
            raise YouTubeError("Could not save YouTube OAuth token") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        try:
            self._service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        except Exception as exc:
            raise YouTubeError("Could not initialize YouTube API") from exc
        return self._service

    def channel(self, expected_id: str, *, interactive: bool = False) -> dict[str, Any]:
        try:
            response = (
                self.service(interactive=interactive)
                .channels()
                .list(part="id,snippet", mine=True)
                .execute()
            )
        except YouTubeError:
            raise
        except Exception as exc:
            raise YouTubeError("Could not read the connected YouTube channel") from exc
        items = response.get("items", [])
        if len(items) != 1 or not isinstance(items[0].get("id"), str):
            raise YouTubeError("OAuth account has no unique YouTube channel")
        item = items[0]
        actual = item["id"]
        return {
            "channel_id": actual,
            "title": item.get("snippet", {}).get("title", ""),
            "expected_channel_id": expected_id,
            "matches_expected": actual == expected_id,
        }

    def assert_channel(self, expected_id: str) -> dict[str, Any]:
        channel = self.channel(expected_id)
        if not channel["matches_expected"]:
            raise ChannelMismatch("Connected YouTube channel differs from configured channel")
        return channel

    def video_status(self, video_id: str) -> dict[str, Any]:
        try:
            response = (
                self.service()
                .videos()
                .list(part="snippet,status,processingDetails", id=video_id)
                .execute()
            )
        except Exception as exc:
            raise YouTubeError("Could not read YouTube video status") from exc
        items = response.get("items", [])
        if len(items) != 1 or items[0].get("id") != video_id:
            return {"video_id": video_id, "available": False}
        item = items[0]
        status = item.get("status", {})
        return {
            "video_id": video_id,
            "available": True,
            "title": item.get("snippet", {}).get("title"),
            "channel_id": item.get("snippet", {}).get("channelId"),
            "privacy": status.get("privacyStatus"),
            "upload_status": status.get("uploadStatus"),
            "processing_status": item.get("processingDetails", {}).get("processingStatus"),
            "made_for_kids": status.get("madeForKids"),
            "self_declared_made_for_kids": status.get("selfDeclaredMadeForKids"),
            "contains_synthetic_media": status.get("containsSyntheticMedia"),
            "embeddable": status.get("embeddable"),
            "license": status.get("license"),
            "public_stats_viewable": status.get("publicStatsViewable"),
        }

    def publish_video(self, video_id: str, current_status: dict[str, Any]) -> dict[str, Any]:
        """Update the recorded video in place; the caller owns durable remote-start fencing."""
        status: dict[str, Any] = {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": True,
            "containsSyntheticMedia": self.config.contains_synthetic_media,
        }
        for local, remote in (
            ("embeddable", "embeddable"),
            ("license", "license"),
            ("public_stats_viewable", "publicStatsViewable"),
        ):
            if current_status.get(local) is not None:
                status[remote] = current_status[local]
        try:
            result = (
                self.service()
                .videos()
                .update(part="status", body={"id": video_id, "status": status})
                .execute()
            )
        except Exception as exc:
            raise UploadAmbiguous(
                "Public visibility outcome is uncertain; manual reconciliation required"
            ) from exc
        if not isinstance(result, dict) or result.get("id") != video_id:
            raise UploadAmbiguous(
                "Public visibility outcome is uncertain; manual reconciliation required"
            )
        return result

    @staticmethod
    def _http_status(error: Exception) -> int | None:
        value = getattr(getattr(error, "resp", None), "status", None)
        return value if isinstance(value, int) else None

    @staticmethod
    def _upload_limit(error: Exception) -> bool:
        content = getattr(error, "content", None)
        if not isinstance(content, (bytes, str)):
            return False
        try:
            payload = json.loads(content)
            return any(
                part.get("reason") == "uploadLimitExceeded"
                for part in payload.get("error", {}).get("errors", [])
                if isinstance(part, dict)
            )
        except (ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def _transport(error: Exception) -> bool:
        try:
            from httplib2.error import ServerNotFoundError
        except ImportError:
            server_not_found: type[BaseException] | None = None
        else:
            server_not_found = ServerNotFoundError
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, (OSError, TimeoutError)) or (
                server_not_found is not None and isinstance(current, server_not_found)
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    def upload_private(
        self,
        video_path: Path,
        metadata: EpisodePublicationMetadata,
        *,
        on_remote_start: Callable[[], None],
        assert_ownership: Callable[[], None],
    ) -> str:
        if not self.config.enabled:
            raise YouTubeError("YouTube publication is disabled")
        if not video_path.is_file() or video_path.stat().st_size == 0:
            raise YouTubeError("Selected render file is unavailable")
        body = {
            "snippet": {
                "title": metadata.youtube_title,
                "description": metadata.youtube_description,
                "tags": list(metadata.tags),
                "categoryId": self.config.category_id,
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": True,
                "containsSyntheticMedia": self.config.contains_synthetic_media,
            },
        }
        try:
            if self._media_factory is None:
                from googleapiclient.http import MediaFileUpload

                media_factory = MediaFileUpload
            else:
                media_factory = self._media_factory
            media = media_factory(
                str(video_path),
                mimetype="video/mp4",
                chunksize=self.config.upload_chunk_size,
                resumable=True,
            )
            request = (
                self.service().videos().insert(part="snippet,status", body=body, media_body=media)
            )
        except YouTubeError:
            raise
        except Exception as exc:
            raise YouTubeError("Could not prepare the private YouTube upload") from exc
        expected_chunks = (
            1
            if self.config.upload_chunk_size == -1
            else max(1, math.ceil(video_path.stat().st_size / self.config.upload_chunk_size))
        )
        maximum_calls = expected_chunks + self.config.max_retries + 2
        retries = 0
        on_remote_start()
        for _ in range(maximum_calls):
            assert_ownership()
            try:
                _, response = request.next_chunk()
            except Exception as exc:
                status = self._http_status(exc)
                if status == 400 and self._upload_limit(exc):
                    raise UploadRejected("YouTube upload limit exceeded") from exc
                if (
                    not (self._transport(exc) or status in RETRYABLE)
                    or retries >= self.config.max_retries
                ):
                    raise UploadAmbiguous(
                        "Remote upload outcome is uncertain; manual reconciliation required"
                    ) from exc
                self._sleeper(min(2**retries, 64))
                retries += 1
                continue
            if response is None:
                continue
            video_id = response.get("id") if isinstance(response, dict) else None
            if not isinstance(video_id, str) or not video_id.strip():
                raise UploadAmbiguous(
                    "YouTube returned no video ID; manual reconciliation required"
                )
            return video_id.strip()
        raise UploadAmbiguous(
            "Resumable upload call limit exceeded; manual reconciliation required"
        )
