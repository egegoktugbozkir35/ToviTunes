"""MPT publication lifecycle mapped onto retained ToviTunes receipt storage."""

from enum import StrEnum
from typing import Any


class PublicationState(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    REMOTE_STARTED = "remote_started"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    AMBIGUOUS_FAILED = "ambiguous_failed"
    TERMINAL_FAILED = "terminal_failed"


def publication_state(row: Any | None) -> PublicationState:
    if row is None:
        return PublicationState.NOT_ATTEMPTED
    if str(row["youtube_video_id"] or "").strip():
        return PublicationState.SUCCEEDED
    outcome = row["outcome"]
    if outcome == "prepared":
        return PublicationState.NOT_ATTEMPTED
    if outcome == "remote_started":
        return PublicationState.REMOTE_STARTED
    if outcome in {"ambiguous", "succeeded"}:
        return PublicationState.AMBIGUOUS_FAILED
    if row["error_classification"] == "preflight_error":
        return PublicationState.RETRYABLE_FAILED
    return PublicationState.TERMINAL_FAILED
