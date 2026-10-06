"""Read-only, typed creative diagnostics. Provider prose and receipts never cross the API."""

from contextlib import closing
from typing import Any

from tovitunes.config import RuntimeConfig
from tovitunes.creative.provider import stored_failure
from tovitunes.errors import FailureCategory
from tovitunes.persistence.db import Database
from tovitunes.web.jobs import Job

OUTCOMES = {
    "empty_answer": "returned an empty answer",
    "model_unavailable": "is unavailable",
    "incomplete_answer": "returned an incomplete answer",
    "structured_output": "returned invalid structured output",
    "provider_rejected": "rejected the request",
    "authentication": "could not authenticate. Check credentials in Settings",
    "configuration": "could not run. Check configuration in Settings",
    "rate_limited": "was rate limited. Wait before resuming",
    "endpoint_unreachable": "could not reach the endpoint before interaction",
    "ambiguous": "has an uncertain result. Continuing with the next configured model",
}


def model_label(model: object, index: int) -> str:
    # Display names only: arbitrary durable model strings can contain secrets or URLs.
    value = str(model)
    for prefix, label in (
        ("moonshotai/kimi-", "Kimi"),
        ("z-ai/glm-", "GLM"),
        ("nvidia/nemotron-", "Nemotron"),
        ("deepseek-ai/deepseek-", "DeepSeek"),
        ("qwen", "Qwen"),
    ):
        if value.startswith(prefix):
            return label
    return f"Creative model {index + 1}"


def studio_job(config: RuntimeConfig, job: Job) -> dict[str, Any]:
    result = job.model_dump()
    if job.operation != "studio":
        return result
    database = Database(config.database_path)
    run_id = (job.result or {}).get("run_id")
    request_id = (job.blocker or {}).get("request_id")
    with closing(database.connect()) as db:
        episode_id = None
        if job.episode_key:
            owner = db.execute(
                "SELECT episode_id FROM episodes WHERE external_key=?", (job.episode_key,)
            ).fetchone()
            episode_id = owner[0] if owner else None
        if request_id and not (run_id or episode_id):
            owner = db.execute(
                "SELECT run_id,episode_id FROM generation_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if owner:
                run_id, episode_id = owner
        rows = db.execute(
            "SELECT g.request_id,g.provider,g.model,g.kind,g.status,g.error_kind,g.error_reason,"
            "g.fallback_index,"
            "g.previous_attempt_id,g.fallback_reason,r.action FROM generation_requests g "
            "LEFT JOIN creative_request_reconciliations r ON r.request_id=g.request_id "
            "WHERE g.prompt_version IS NOT NULL AND (g.run_id=? OR g.episode_id=? OR g.run_id IN "
            "(SELECT run_id FROM creative_runs WHERE episode_id=?)) ORDER BY g.rowid",
            (run_id, episode_id, episode_id),
        ).fetchall()
    attempts = []
    categories = {c.value for c in FailureCategory}
    statuses = {
        "prepared",
        "remote_started",
        "ambiguous",
        "failed",
        "succeeded",
        "succeeded_response_invalid",
        "response_received",
    }
    for row in rows:
        index = row["fallback_index"] or 0
        category = (
            stored_failure(row).value
            if row["status"] in {"failed", "ambiguous"}
            else row["error_kind"]
        )
        if category not in categories:
            category = "configuration" if row["status"] == "failed" else None
        label = model_label(row["model"], index)
        outcome = (
            OUTCOMES.get(category, "is running")
            if category
            else "completed successfully"
            if row["status"] == "succeeded"
            else "is running"
            if row["status"] in {"remote_started", "response_received"}
            else "is queued"
        )
        attempts.append(
            {
                "model_label": label,
                "status": row["status"] if row["status"] in statuses else "unknown",
                "error_kind": category,
                "fallback_index": index,
                "fallback_reason": row["fallback_reason"]
                if row["fallback_reason"]
                in categories | {"operator_abandoned_ambiguous", "sticky_success"}
                else None,
                "reconciled": row["action"] == "abandon_remote_result",
                "message": f"{label} {outcome}.",
            }
        )
    exhausted = (job.blocker or {}).get("error_kind") == "chain_exhausted"
    message = (
        "Configured creative model chain exhausted. No requests were resent." if exhausted else None
    )
    if not exhausted and attempts:
        latest = attempts[-1]
        if latest["status"] in {"prepared", "remote_started"} and len(attempts) > 1:
            prior = attempts[-2]
            if prior["error_kind"]:
                message = f"{prior['message']} Continuing with {latest['model_label']}Ã¢â‚¬Â¦"
        elif latest["error_kind"]:
            message = latest["message"]
    result["creative_diagnostics"] = {
        "attempts": attempts,
        "exhausted": exhausted,
        "message": message,
    }
    return result
