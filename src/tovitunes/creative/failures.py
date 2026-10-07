"""One donor-derived disposition model owns creative failover policy."""

from dataclasses import dataclass
from enum import StrEnum

from tovitunes.errors import FailureCategory, ProviderError, StructuredOutputError


class FailureScope(StrEnum):
    FAIL_CLOSED = "fail_closed"
    MODEL = "model"
    ENDPOINT = "endpoint"


@dataclass(frozen=True)
class FailureDecision:
    scope: FailureScope
    category: FailureCategory
    retry_delays: tuple[float, ...] = ()
    retry_after: float | None = None


def classify_creative_failure(error: Exception) -> FailureDecision:
    if isinstance(error, StructuredOutputError):
        return FailureDecision(FailureScope.MODEL, FailureCategory.STRUCTURED_OUTPUT)
    if not isinstance(error, ProviderError):
        return FailureDecision(FailureScope.FAIL_CLOSED, FailureCategory.CONFIGURATION)
    category = error.category
    if category == FailureCategory.ENDPOINT_UNREACHABLE:
        return FailureDecision(FailureScope.ENDPOINT, category, (2.0, 5.0, 15.0))
    if category in {FailureCategory.HTTP_SERVER, FailureCategory.RATE_LIMITED}:
        return FailureDecision(FailureScope.MODEL, category, (2.0, 5.0), error.retry_after)
    if category in {FailureCategory.READ_TIMEOUT, FailureCategory.STREAM_INTERRUPTED}:
        return FailureDecision(FailureScope.MODEL, category, (2.0,))
    if category in {
        FailureCategory.EMPTY_ANSWER,
        FailureCategory.MODEL_UNAVAILABLE,
        FailureCategory.INCOMPLETE_ANSWER,
        FailureCategory.STRUCTURED_OUTPUT,
        FailureCategory.PROVIDER_REJECTED,
        FailureCategory.AMBIGUOUS,
    }:
        return FailureDecision(FailureScope.MODEL, category)
    return FailureDecision(FailureScope.FAIL_CLOSED, category)
