"""Donor-adapted structured contract with durable, visible repair requests."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from sqlite3 import Row
from typing import Any, Protocol, TypedDict, TypeVar

from pydantic import BaseModel

from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import CreativeRequestLedger
from tovitunes.pipeline.creative import GeneratedDraft

ModelT = TypeVar("ModelT", bound=BaseModel)
Message = dict[str, str]


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def structured_json_instruction(schema: dict[str, Any]) -> Message:
    # Direct adaptation of app/llm/provider.py at the pinned first-party donor commit.
    return {
        "role": "system",
        "content": (
            "Return exactly one JSON object and no Markdown or commentary. The object must "
            "validate against this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        ),
    }


@dataclass(frozen=True)
class GenerationContext:
    kind: str
    prompt_version: str
    episode_id: str | None = None
    run_id: str | None = None
    assert_owner: Callable[[], None] = lambda: None

    def __post_init__(self) -> None:
        if (self.episode_id is None) == (self.run_id is None):
            raise ValueError("generation requires exactly one episode or planning-run owner")


@dataclass(frozen=True)
class ChatResponse:
    content: str
    request_id: str | None = None


class FailureCategory(StrEnum):
    EMPTY_ANSWER = "empty_answer"
    MODEL_UNAVAILABLE = "model_unavailable"
    INCOMPLETE_ANSWER = "incomplete_answer"
    STRUCTURED_OUTPUT = "structured_output"
    ENDPOINT_UNREACHABLE = "endpoint_unreachable"
    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    PROVIDER_REJECTED = "provider_rejected"
    AMBIGUOUS = "ambiguous"


MODEL_FAILURES = {
    FailureCategory.EMPTY_ANSWER,
    FailureCategory.MODEL_UNAVAILABLE,
    FailureCategory.INCOMPLETE_ANSWER,
    FailureCategory.STRUCTURED_OUTPUT,
}


def stored_failure(row: Row) -> FailureCategory:
    if row["status"] == "ambiguous":
        return FailureCategory.AMBIGUOUS
    # Compatibility with the exact old transport's conclusive empty-answer terminal error.
    # Never infer safety from substrings, generic ProviderError, or ambiguous rows.
    if (
        row["status"] == "failed"
        and row["provider"] == "nvidia"
        and row["error_kind"] == "ProviderError"
        and row["error_reason"] == "NVIDIA NIM returned empty answer content"
    ):
        return FailureCategory.EMPTY_ANSWER
    try:
        return FailureCategory(row["error_kind"])
    except (ValueError, TypeError):
        return FailureCategory.CONFIGURATION


class RequestAudit(TypedDict):
    requested_provider: str | None
    requested_model: str | None
    fallback_reason: str | None
    fallback_index: int
    previous_attempt_id: str | None


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        ambiguous: bool = False,
        category: FailureCategory = FailureCategory.CONFIGURATION,
    ) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous
        self.category = FailureCategory.AMBIGUOUS if ambiguous else category


class StructuredOutputError(ValueError):
    pass


class CreativeAmbiguity(ProviderError):
    """Safe request identity for operator recovery; never carries a remote response body."""

    def __init__(self, row: Row, message: str | None = None) -> None:
        has_receipt = row["response_content"] is not None or row["response_sha256"] is not None
        self.evidence: dict[str, object] = {
            "request_id": row["request_id"],
            "provider": row["provider"],
            "model": row["model"],
            "kind": row["kind"],
            "recovery_action": (
                "inspect_durable_receipt" if has_receipt else "abandon_remote_result"
            ),
            "recovery_command": (
                f"creative request-status --request-id {row['request_id']}"
                if has_receipt
                else f"creative reconcile --request-id {row['request_id']} "
                "--action abandon-remote-result --actor human:operator "
                '--reason "Remote result is inaccessible; '
                'continue through configured fallback chain"'
            ),
        }
        super().__init__(
            message
            or f"creative request {row['request_id']} is ambiguous; "
            "explicit recovery required; do not resend",
            ambiguous=True,
        )


class ChatTransport(Protocol):
    provider_name: str
    model_name: str

    @property
    def settings(self) -> dict[str, object]: ...

    def check_ready(self) -> None: ...

    def chat(
        self, messages: Sequence[Message], *, record_identity: Callable[[str], None]
    ) -> ChatResponse: ...


class StructuredGenerator(Protocol):
    def generate(
        self,
        model_type: type[ModelT],
        messages: Sequence[Message],
        *,
        context: GenerationContext,
        validate: Callable[[ModelT], None] | None = None,
    ) -> GeneratedDraft[ModelT]: ...


class DurableStructuredGenerator:
    """One initial POST and at most one auditable schema/domain repair, never transport retry."""

    def __init__(
        self,
        database: Database,
        transport: ChatTransport,
        *,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        fallback_reason: str | None = None,
        fallback_index: int = 0,
        previous_attempt_id: str | None = None,
    ) -> None:
        self.ledger = CreativeRequestLedger(database)
        self.transport = transport
        self.audit: RequestAudit = dict(
            requested_provider=requested_provider,
            requested_model=requested_model,
            fallback_reason=fallback_reason,
            fallback_index=fallback_index,
            previous_attempt_id=previous_attempt_id,
        )

    def generate(
        self,
        model_type: type[ModelT],
        messages: Sequence[Message],
        *,
        context: GenerationContext,
        validate: Callable[[ModelT], None] | None = None,
    ) -> GeneratedDraft[ModelT]:
        schema = model_type.model_json_schema()
        enriched = [structured_json_instruction(schema), *messages]
        digest = fingerprint(
            {
                "provider": self.transport.provider_name,
                "model": self.transport.model_name,
                "settings": self.transport.settings,
                "prompt_version": context.prompt_version,
                "messages": enriched,
            }
        )
        first = self.ledger.find(context.episode_id, context.run_id, context.kind, digest)
        if first is None:
            self.transport.check_ready()
            context.assert_owner()
            first = self.ledger.prepare_creative(
                episode_id=context.episode_id,
                run_id=context.run_id,
                kind=context.kind,
                provider=self.transport.provider_name,
                model=self.transport.model_name,
                prompt_version=context.prompt_version,
                input_fingerprint=digest,
                messages_json=canonical(enriched),
                **self.audit,
            )
        parent: str | None = None
        current_messages = enriched
        for attempt in (1, 2):
            row = (
                first
                if attempt == 1
                else self.ledger.find(
                    context.episode_id, context.run_id, context.kind + "_repair", digest
                )
            )
            if row is None:
                self.transport.check_ready()
                context.assert_owner()
                row = self.ledger.prepare_creative(
                    episode_id=context.episode_id,
                    run_id=context.run_id,
                    kind=context.kind + "_repair",
                    provider=self.transport.provider_name,
                    model=self.transport.model_name,
                    prompt_version=context.prompt_version,
                    input_fingerprint=digest,
                    messages_json=canonical(current_messages),
                    attempt=2,
                    parent_request_id=parent,
                    **self.audit,
                )
            request_id = str(row["request_id"])
            if (
                row["provider"] != self.transport.provider_name
                or row["model"] != self.transport.model_name
                or row["prompt_version"] != context.prompt_version
                or row["messages_json"] != canonical(current_messages)
            ):
                raise ProviderError("durable request contract differs; operator recovery required")
            content = row["response_content"]
            if (
                content is not None
                and sha256(content.encode("utf-8")).hexdigest() != row["response_sha256"]
            ):
                raise ProviderError("durable response hash differs; operator recovery required")
            if row["status"] in {"ambiguous", "failed"}:
                category = stored_failure(row)
                if row["status"] == "ambiguous":
                    raise CreativeAmbiguity(row)
                raise ProviderError(
                    f"creative request {request_id} is {row['status']}; "
                    "explicit recovery required; "
                    "inspect the ledger and reconcile provider evidence; do not resend",
                    ambiguous=row["status"] == "ambiguous",
                    category=category,
                )
            if content is None:
                if row["status"] != "prepared":
                    self.ledger.finish(request_id, "ambiguous", error_kind="interrupted")
                    raise CreativeAmbiguity(self.ledger.get(request_id))
                self.transport.check_ready()
                context.assert_owner()
                self.ledger.start(request_id)
                try:
                    response = self.transport.chat(
                        json.loads(row["messages_json"]),
                        record_identity=lambda value: self.ledger.identity(request_id, value),
                    )
                    # Receipt is persisted before local validation or artifact ingestion.
                    self.ledger.receipt(request_id, response.content, response.request_id)
                    content = response.content
                    context.assert_owner()
                except Exception as exc:
                    ambiguous = not isinstance(exc, ProviderError) or exc.ambiguous
                    self.ledger.finish(
                        request_id,
                        "ambiguous" if ambiguous else "failed",
                        error_kind=(
                            exc.category.value
                            if isinstance(exc, ProviderError)
                            else FailureCategory.AMBIGUOUS.value
                        ),
                        error_reason=(
                            str(exc)
                            if isinstance(exc, ProviderError)
                            else "local failure after remote start; reconcile receipt"
                        ),
                    )
                    if ambiguous:
                        raise CreativeAmbiguity(
                            self.ledger.get(request_id),
                            str(exc) if isinstance(exc, ProviderError) else None,
                        ) from exc
                    raise
            try:
                result = model_type.model_validate(json.loads(content))
                if validate is not None:
                    validate(result)
            except ValueError as exc:
                context.assert_owner()
                self.ledger.finish(
                    request_id,
                    "succeeded_response_invalid",
                    error_kind=FailureCategory.STRUCTURED_OUTPUT.value,
                    error_reason=str(exc),
                )
                if attempt == 2:
                    raise StructuredOutputError(
                        "structured output remained invalid after one repair"
                    ) from exc
                parent = request_id
                current_messages = [
                    *enriched,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "The previous response was invalid. Return only one corrected JSON "
                            "object that conforms exactly to the supplied schema and pinned rules. "
                            "Do not add Markdown or commentary. Validation error: "
                            + str(exc)[:1200]
                        ),
                    },
                ]
                continue
            context.assert_owner()
            self.ledger.finish(request_id, "succeeded")
            saved = self.ledger.get(request_id)
            return GeneratedDraft(
                result,
                str(saved["provider"]),
                str(saved["model"]),
                saved["provider_request_id"],
                context.prompt_version,
                datetime.fromisoformat(saved["updated_at"]),
                request_id,
            )
        raise AssertionError("bounded structured generation exhausted")
