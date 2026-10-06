"""Read-only operator projection; no transports, response bodies or secret configuration."""

from tovitunes.config import CreativeLLMConfig
from tovitunes.creative.provider import MODEL_FAILURES, stored_failure
from tovitunes.persistence.creative_reconciliation import CreativeReconciliations
from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import CreativeRequestLedger


def request_status(
    database: Database, config: CreativeLLMConfig, request_id: str
) -> dict[str, object]:
    row = CreativeRequestLedger(database).get(request_id)
    decision = CreativeReconciliations(database).get(request_id)
    chain = [(config.provider, model) for model in (config.model, *config.fallback_models)]
    identity = (row["provider"], row["model"])
    position = chain.index(identity) if identity in chain else None
    may_advance = (
        (row["status"] == "ambiguous" and decision is not None)
        or (row["status"] == "failed" and stored_failure(row) in MODEL_FAILURES)
        or (row["status"] == "succeeded_response_invalid" and row["attempt"] == 2)
    )
    next_model = None
    if may_advance and position is not None and position + 1 < len(chain):
        next_model = {"provider": chain[position + 1][0], "model": chain[position + 1][1]}
    return {
        **{
            key: row[key]
            for key in (
                "request_id",
                "episode_id",
                "run_id",
                "kind",
                "provider",
                "model",
                "status",
                "provider_request_id",
                "error_kind",
                "previous_attempt_id",
                "fallback_index",
                "fallback_reason",
            )
        },
        "reconciliation": {
            key: decision[key] for key in ("reconciliation_id", "action", "actor", "created_at")
        }
        if decision is not None
        else None,
        "next_configured_model": next_model,
        "chain_exhausted": may_advance and position == len(chain) - 1,
        "configured_position": position,
        "provider_calls": 0,
    }
