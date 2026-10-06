"""Donor ordered/sticky fallback adapted to authoritative durable receipts, without retries."""

from collections.abc import Callable, Sequence
from contextlib import closing
from sqlite3 import Row

from tovitunes.creative.failures import FailureScope, classify_creative_failure
from tovitunes.creative.provider import (
    ChatTransport,
    DurableStructuredGenerator,
    GenerationContext,
    Message,
    ModelT,
    canonical,
    fingerprint,
    stored_failure,
    structured_json_instruction,
)
from tovitunes.errors import (
    CreativeChainExhausted,
    FailureCategory,
    ProviderError,
    StructuredOutputError,
)
from tovitunes.persistence.db import Database
from tovitunes.pipeline.creative import GeneratedDraft


class ResilientStructuredGenerator:
    def __init__(
        self,
        database: Database,
        transports: Sequence[ChatTransport],
        *,
        emergency: ChatTransport | None = None,
    ) -> None:
        if not transports:
            raise ValueError("creative model chain cannot be empty")
        self.database = database
        self.transports = tuple(transports)
        self.emergency = emergency

    def _history(self, context: GenerationContext) -> list[Row]:
        # Subject requests have a planning owner; later stages have its reserved episode owner.
        with closing(self.database.connect()) as db:
            run = db.execute(
                "SELECT run_id,episode_id FROM creative_runs WHERE run_id=? OR episode_id=?",
                (context.run_id, context.episode_id),
            ).fetchone()
            run_id = run["run_id"] if run else context.run_id
            episode_id = run["episode_id"] if run else context.episode_id
            return list(
                db.execute(
                    "SELECT * FROM generation_requests WHERE prompt_version IS NOT NULL AND "
                    "((run_id IS NOT NULL AND run_id=?) OR "
                    "(episode_id IS NOT NULL AND episode_id=?)) ORDER BY rowid",
                    (run_id, episode_id),
                )
            )

    def generate(
        self,
        model_type: type[ModelT],
        messages: Sequence[Message],
        *,
        context: GenerationContext,
        validate: Callable[[ModelT], None] | None = None,
    ) -> GeneratedDraft[ModelT]:
        context.assert_owner()
        enriched = [structured_json_instruction(model_type.model_json_schema()), *messages]
        history = self._history(context)
        choices = [*self.transports, *([self.emergency] if self.emergency else [])]
        stage = [
            row
            for row in history
            if row["kind"] == context.kind
            and row["episode_id"] == context.episode_id
            and row["run_id"] == context.run_id
            and row["prompt_version"] == context.prompt_version
            and row["messages_json"] == canonical(enriched)
        ]
        existing = stage[-1] if stage else None
        sticky = next((row for row in reversed(history) if row["status"] == "succeeded"), None)
        anchor = existing if existing is not None else sticky
        index = 0
        reason: str | None = None
        previous: str | None = None
        if anchor is not None:
            match = next(
                (
                    i
                    for i, transport in enumerate(choices)
                    if (transport.provider_name, transport.model_name)
                    == (anchor["provider"], anchor["model"])
                ),
                None,
            )
            if match is None:
                raise ProviderError("durable model is absent from configured chain; restore config")
            index = match
            reason = anchor["fallback_reason"]
            previous = anchor["previous_attempt_id"]
            if existing is None and index:
                reason = reason or "sticky_success"
                previous = str(anchor["request_id"])
        while True:
            transport = choices[index]
            digest = fingerprint(
                dict(
                    provider=transport.provider_name,
                    model=transport.model_name,
                    settings=transport.settings,
                    prompt_version=context.prompt_version,
                    messages=enriched,
                )
            )
            if existing is not None:
                if digest != existing["input_fingerprint"]:
                    raise ProviderError("durable request settings differ; restore original config")
            generator = DurableStructuredGenerator(
                self.database,
                transport,
                requested_provider=self.transports[0].provider_name,
                requested_model=self.transports[0].model_name,
                fallback_reason=reason,
                fallback_index=index,
                previous_attempt_id=previous,
            )
            try:
                return generator.generate(model_type, messages, context=context, validate=validate)
            except (StructuredOutputError, ProviderError) as exc:
                decision = classify_creative_failure(exc)
                category = decision.category
                if decision.scope is FailureScope.FAIL_CLOSED:
                    raise
            current = self._history(context)
            attempts = [
                row
                for row in current
                if row["kind"] in {context.kind, context.kind + "_repair"}
                and row["provider"] == transport.provider_name
                and row["model"] == transport.model_name
                and row["episode_id"] == context.episode_id
                and row["run_id"] == context.run_id
                and row["input_fingerprint"] == digest
            ]
            if not attempts:
                raise ProviderError("fallback requires durable terminal failure evidence")
            terminal = attempts[-1]
            safe_invalid = (
                category == FailureCategory.STRUCTURED_OUTPUT
                and terminal["status"] == "succeeded_response_invalid"
                and terminal["attempt"] == 2
                and terminal["parent_request_id"] == attempts[0]["request_id"]
            )
            safe_failed = terminal["status"] == "failed" and stored_failure(terminal) == category
            ambiguous = terminal["status"] == "ambiguous"
            if not (safe_invalid or safe_failed or ambiguous):
                raise ProviderError("fallback requires an immutable terminal receipt")
            previous = str(attempts[-1]["request_id"])
            reason = "ambiguous" if ambiguous else category.value
            if category == FailureCategory.ENDPOINT_UNREACHABLE:
                if self.emergency is None or transport is self.emergency:
                    raise ProviderError(
                        "endpoint unreachable; local Ollama fallback is disabled", category=category
                    )
                index = len(self.transports)
            elif index + 1 < len(self.transports):
                index += 1
            else:
                raise CreativeChainExhausted(
                    "configured creative model chain exhausted; no requests resent",
                    category=category,
                )
            existing = next(
                (
                    row
                    for row in reversed(stage)
                    if (row["provider"], row["model"])
                    == (choices[index].provider_name, choices[index].model_name)
                ),
                None,
            )


def generation_audit(database: Database, episode_id: str) -> list[dict[str, object]]:
    with closing(database.connect()) as db:
        rows = db.execute(
            "SELECT request_id,provider,model,requested_provider,requested_model,fallback_reason,"
            "fallback_index,previous_attempt_id,status FROM generation_requests "
            "WHERE prompt_version IS NOT NULL AND (episode_id=? OR run_id IN "
            "(SELECT run_id FROM creative_runs WHERE episode_id=?)) ORDER BY rowid",
            (episode_id, episode_id),
        ).fetchall()
    return [
        dict(row) | {"actual_provider": row["provider"], "actual_model": row["model"]}
        for row in rows
    ]
