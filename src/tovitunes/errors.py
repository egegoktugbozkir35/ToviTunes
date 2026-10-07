"""Typed application errors used at external and orchestration boundaries."""

from __future__ import annotations

from enum import StrEnum
from sqlite3 import Row


class PipelineError(Exception):
    """Base class for expected operator-facing failures."""


class ConfigurationError(PipelineError):
    """Configuration is missing or invalid for the requested operation."""


class StateError(PipelineError):
    """A persisted item cannot make the requested state transition."""


class ExecutionOwnershipError(PipelineError):
    """Production execution ownership could not be obtained or retained."""


class ExecutionOwnershipConflictError(ExecutionOwnershipError):
    """Another live production execution already owns the global lease."""


class ExecutionOwnershipLostError(ExecutionOwnershipError):
    """The caller's production execution lease is no longer valid."""


class ProductionStop(PipelineError):
    def __init__(self, status: str, reason: str, evidence: dict[str, object] | None = None) -> None:
        super().__init__(reason)
        self.status, self.evidence = status, evidence or {}


class LLMError(PipelineError):
    """A configured creative provider failed."""


class FailureCategory(StrEnum):
    EMPTY_ANSWER = "empty_answer"
    MODEL_UNAVAILABLE = "model_unavailable"
    INCOMPLETE_ANSWER = "incomplete_answer"
    STRUCTURED_OUTPUT = "structured_output"
    ENDPOINT_UNREACHABLE = "endpoint_unreachable"
    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    PROVIDER_REJECTED = "provider_rejected"
    RATE_LIMITED = "rate_limited"
    AMBIGUOUS = "ambiguous"
    HTTP_SERVER = "http_server"
    READ_TIMEOUT = "read_timeout"
    STREAM_INTERRUPTED = "stream_interrupted"


class ProviderError(LLMError):
    def __init__(
        self,
        message: str,
        *,
        ambiguous: bool = False,
        category: FailureCategory = FailureCategory.CONFIGURATION,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous
        self.category = (
            FailureCategory.AMBIGUOUS
            if ambiguous and category == FailureCategory.CONFIGURATION
            else category
        )
        self.retry_after = retry_after


class StructuredOutputError(ValueError):
    pass


class CreativeAmbiguity(ProviderError):
    """Safe request identity for operator recovery; never carries a remote response body."""

    def __init__(
        self,
        row: Row,
        message: str | None = None,
        *,
        category: FailureCategory = FailureCategory.AMBIGUOUS,
    ) -> None:
        self.evidence: dict[str, object] = {
            "request_id": row["request_id"],
            "provider": row["provider"],
            "model": row["model"],
            "kind": row["kind"],
        }
        super().__init__(
            message or "Creative outcome is ambiguous; the original request is never resent",
            ambiguous=True,
            category=category,
        )


class CreativeChainExhausted(ProviderError):
    """All configured creative models failed; no wraparound or resends."""


class YouTubeError(PipelineError):
    """Safe operator-facing YouTube failure."""


class UploadAmbiguous(YouTubeError):
    """The remote attempt began and its final outcome is unknown."""


class UploadRejected(YouTubeError):
    """Google explicitly rejected the request without creating a video."""


class ChannelMismatch(YouTubeError):
    """OAuth selected a channel other than the pinned channel."""
