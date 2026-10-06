# Creative fallback and durable recovery

The same prompts, schemas, pinned learning facts and domain validators apply to every model.
New open topic pools use this same durable chain; [editorial memory](OPEN_EDITORIAL_MEMORY_V1.md)
does not introduce another NVIDIA client or request ledger.
The default NVIDIA chain is:

1. `moonshotai/kimi-k3`
2. `z-ai/glm-5.3`
3. `nvidia/nemotron-3-ultra-550b-a55b`
4. `deepseek-ai/deepseek-v4.1-flash`

`CreativeLLMConfig` accepts explicit namespace/model identifiers and a bounded ordered list
of up to eight unique fallback models. Existing YAML inherits this chain. Set
`fallback_models: []` for a single model. Duplicate or malformed identifiers fail validation.
Credentials remain in the named environment variable; inline keys and credential URLs are rejected.
Configuration error formatting hides raw input values to avoid echoing rejected inline secrets.
Keep the existing endpoint, inference settings and prompt versions when recovering older requests.
No live provider availability claim is made by the offline doctor or test suite.

## Exact fallback decisions

`FailureCategory` is the typed transport/ledger vocabulary. There are no transport retries,
delays, same-model retry loops or automatic resends.

| Category | Behavior |
| --- | --- |
| `empty_answer` | A conclusively completed response has no usable answer; advance to the next NVIDIA model. SSE requires `finish_reason=stop`. |
| `model_unavailable` | Explicit machine-readable provider error code `model_not_found`, `model_unavailable` or `model_not_supported`; advance to the next NVIDIA model. A bare HTTP 404 is insufficient evidence. |
| `incomplete_answer` | A known terminal finish reason (`length`, `content_filter`, `tool_calls`, `function_call`) reports an unusable answer; advance to the next NVIDIA model. Unknown completion markers remain ambiguous. |
| `structured_output` | Invalid JSON/schema/domain answer permits one visible repair on that model. Only after a conclusively invalid repair may the next model start. |
| `endpoint_unreachable` | A `ConnectError` with a typed DNS (`socket.gaierror`) or connection-refused cause, before response interaction, can use enabled local Ollama. Never cycle NVIDIA models for this condition. |
| `authentication` | HTTP 401/403 or typed authentication code/type; fail closed with no fallback. |
| `configuration` | Bare unclassified 4xx, typed invalid-request/configuration error, missing key, changed settings, corrupt receipt, unrecognized legacy failure or local configuration error; fail closed. |
| `provider_rejected` | Explicit conclusive provider/model rejection with a matching durable `failed` row; advance to the next configured model. No same-request resend. |
| `rate_limited` | HTTP 429, typed throttling or quota errors; stop without model fallback because the condition is endpoint-wide. |
| `ambiguous` | Read/write timeout, connection loss, generic connect error or connect timeout without proof of non-interaction, HTTP 408/409/425/5xx, interrupted/malformed stream, or started request without a receipt; stop and reconcile evidence. |

Local operator/curriculum validation, lease and persistence exceptions do not authorize fallback.
Even a typed exception requires matching durable terminal evidence before preparing another request.
A `[DONE]` marker alone does not establish conclusive SSE completion. Only answer content is
collected; `reasoning_content` is never stored or surfaced. Remote error bodies, exception bodies
and transport exception text are not echoed into persisted diagnostics.
HTTP clients initialize only when their model is called, preserving fast doctor/reuse/worker setup.

Optional endpoint fallback uses local `http://127.0.0.1:11434`, model
`qwen3.8:27b-q4_K_M`, timeout 240 seconds and temperature 0.7. Enable it explicitly with
`fallback_to_ollama_on_endpoint_failure: true`. Ollama is instantiated only when enabled,
receives the same JSON/schema instructions, disables thinking, and must return `done: true`.
An ambiguous NVIDIA call can never switch to Ollama. Model-chain exhaustion can never switch to
Ollama. An Ollama failure has no further provider fallback.

## Restart and immutable history

Migration `0018` only adds nullable audit columns to `generation_requests`. It does not update old
rows, receipts, errors, provider request IDs, episode/artifact records or leases. New attempts record
`requested_provider`, `requested_model`, actual `provider`/`model`, `fallback_index` (zero-based,
Ollama follows the NVIDIA positions), `fallback_reason` and `previous_attempt_id`, alongside existing
local/provider IDs, timestamps and outcomes. Repair continues to use `parent_request_id`; fallback
uses a separate relationship so the original repair constraints remain intact.

The durable stage history takes precedence over sticky fallback. A valid succeeded request reuses
its exact receipt and artifact provenance, regardless of later stages switching model. A prepared
request can start once. A started request without a receipt becomes ambiguous. A saved response
resumes local validation without another POST. Corrupt response hashes fail closed.

For a failed model-scoped attempt, the original row stays unchanged and the next configured model
gets a newly prepared request, linked to the previous failure (or failed repair). Each model has
at most one initial request and one repair for the same stage/input. Recovery of exhausted invalid
repairs revalidates saved responses, with no third request to that model.

The latest successful model in the same run becomes sticky for new stages. History joins the
planning-run subject requests with that run's reserved episode requests, so this survives a new
CLI process. It does not leak across episodes/runs. Existing stage requests are checked first.
Lease budgets cover the bounded chain and optional emergency requests; ownership is checked before
each preparation/start and artifact operation.

Empty-answer compatibility recognizes only a stored **failed** NVIDIA row with `error_kind=ProviderError` and
the exact prior transport error `NVIDIA NIM returned empty answer content`. That prior transport
produced this error after completed response handling. It is interpreted as `empty_answer` in memory;
the old row is never relabeled. Generic legacy errors and ambiguous rows remain blocked.
Older bare HTTP 4xx errors labeled `provider_rejected` are recognized by the exact fixed
transport diagnostic and reclassified in memory. Bare bad-request/configuration and rate-limit
failures do not become safe model fallback simply because the rejection category now advances.
The original row remains unchanged. Typed conclusive provider rejections advance normally.
The old NIM fingerprint settings serialization is preserved, excluding fallback configuration so
enabling a chain does not hide historical requests. Changed inference settings fail closed.

After updating code, keep the original `config.yaml` settings and run:

```powershell
uv run --locked python -m tovitunes.cli --config config.yaml creative doctor
uv run --locked python -m tovitunes.cli --config config.yaml creative generate-next --live
```

The normal workflow discovers the oldest incomplete brand run, retains its episode reservation,
reuses selected stages and resumes the missing stage. Conclusive failures advance through the
configured chain. No run ID is hard-coded and no historical selected asset is regenerated. If
configuration explicitly disables fallbacks, add the desired list from `config.example.yaml`.

## Explicit abandonment of an inaccessible ambiguous result

An ambiguous request still stops automatically. HTTP 504 remains ambiguous, never a definitive
model failure. An operator can acknowledge that the remote interaction **may have executed**, but
that no usable result can be recovered:

```powershell
uv run --locked python -m tovitunes.cli --config config.yaml creative request-status `
  --request-id <REQUEST_ID>

uv run --locked python -m tovitunes.cli --config config.yaml creative reconcile `
  --request-id <REQUEST_ID> `
  --action abandon-remote-result `
  --actor human:operator `
  --reason "Remote result is inaccessible; continue through configured fallback chain"
```

An optional `--evidence-uri` records a stable incident reference. Credential-bearing URIs, query
parameters and fragments are rejected; do not provide signed download URLs or provider bodies.
Actor and rationale are bounded, nonempty operator audit text. Neither command constructs a provider
or HTTP client, checks provider credentials, calls a provider, nor starts production. Run the normal
creative or production command afterward to resume. Status projects request/owner identity, kind,
provider/model, status, provider request ID, typed error, reconciliation and the next configured model;
it omits messages, response content, error bodies, rationale, evidence URI and secrets.

Migration `0021_creative_reconciliation.sql` adds only
`creative_request_reconciliations(reconciliation_id, request_id, action, actor, rationale,
evidence_uri, created_at)` and invariant triggers. IDs are UUIDs and timestamps are UTC ISO 8601.
`request_id` is a unique foreign key. The initial action is `abandon_remote_result`. Updates,
deletes and replacement inserts are rejected; the reconciled generation row is also protected
from updates, deletes and replacements. The migration does not rewrite existing tables or data.
Replaying the exact same operator decision is idempotent, including concurrent commands; different
audit values are rejected instead of overwriting the first decision.

Only a persisted ambiguous structured Creative Director request is eligible: a known episode or
planning-run owner, nonempty prompt version, saved JSON messages, and **no durable response content
or response hash**. Initial requests and repair requests both qualify. A linked successful
replacement makes a new decision ineligible. Prepared, started, failed, successful, invalid-response,
music-generation, image-generation and external publication requests are rejected. Creative music
specifications, visual plans and publication *metadata* remain structured Director outputs, distinct
from those external generation/upload systems. This version conservatively refuses abandonment
when any response receipt exists; an evidence URI does not override that guard.

The original request remains `status=ambiguous`, with all its timestamps and evidence unchanged.
The same ordered engine locates its provider/model in the current configured chain and advances
one model position. It prepares a new request with `previous_attempt_id` pointing to the abandoned
request (including a repair), `fallback_index` set to the configured position, and
`fallback_reason=operator_abandoned_ambiguous`. Requested provider/model continue to identify the
configured primary. The decision is an authorization to move on, never a fabricated model failure.

| Durable outcome | Engine action |
| --- | --- |
| Successful validated response | Reuse artifact/receipt; successful model becomes sticky. |
| Typed conclusive model failure | Next configured model automatically. |
| Invalid structured response | One repair on this model; a conclusively invalid repair advances automatically. |
| Ambiguous, no decision | Stop; no retry, fallback or resend. |
| Ambiguous, explicit abandonment | Next configured model; preserve the ambiguous original. |
| Authentication, configuration, rate limiting or local preflight validation failure | Stop without fallback. |
| Final configured model consumed | Report chain exhaustion; never repeat the primary or use emergency Ollama. |

For example, Kimi ambiguity plus abandonment advances to GLM. A completed GLM empty answer then
advances automatically to Nemotron. A conclusively invalid Nemotron answer and its one invalid
repair then advance automatically to DeepSeek. A new ambiguity at any position needs its own
decision. There are no model-name branches: arbitrary names, additions and reordering are driven by
the configured chain. Optional Ollama retains its endpoint-only emergency semantics.

A fresh process reconstructs stage position from configured identity, generation history and
decisions. A previously prepared fallback starts once; a completed fallback reuses its receipt; a
started fallback without a receipt stops as a new ambiguity. Missing configured historical models,
changed inference settings and corrupt request contracts fail closed. Changing the input cannot
bypass an abandoned request: the ledger requires linkage through its authorized attempt ancestry.
Only successful models become sticky. GLM success after abandonment makes subsequent stages start
on GLM; later Nemotron success makes subsequent stages start on Nemotron. Existing stage history
continues to take precedence over sticky selection, across planning-run and episode owners.

Short production reports unresolved creative ambiguity as `status=AMBIGUOUS` at its current creative
stage (`CREATIVE` for topic/spec/lyrics/music-spec), with safe local request identity, provider,
model, kind, recovery action and example reconciliation command. Episode stage events retain that
safe evidence. If a durable receipt exists, guidance points to request inspection instead of the
ineligible abandonment action. Remote response bodies, exception bodies, credentials and signed
URLs are omitted.

`creative doctor` reports primary/fallback configuration, key presence and the emergency flag with
zero generation calls. Successful creative/metadata output adds `generation_attempts`, including
actual/requested identity and reasons, without credentials. Existing `provider_calls` still counts
actual newly started requests: initial/fallback requests count under their stage, and repair requests
count under `repair`. Reused requests contribute zero new calls.

## Donor adaptation

Inspected current first-party `ollama-mpt-youtube` at
[`7ef50aaf9b4aa13590a6edba5034e2fbad56cc79`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/tree/7ef50aaf9b4aa13590a6edba5034e2fbad56cc79),
including `app/llm/factory.py`, `resilient_generator.py`, `nvidia_nim_client.py`, `ollama_client.py`,
`provider.py` and `app/config.py`, before implementation.

Adapted its ordered explicit factory, model-versus-endpoint failure scopes, sticky successful model,
requested-versus-actual audit identity, shared JSON instructions, bounded repair and Ollama chat
payload. ToviTunes' existing AGPL source and durable ledger remain the runtime boundary; no donor
checkout or import is required.

Intentionally did not copy retry delays, Retry-After retries, 120-second timeout shortening,
read-timeout/stream-interruption model fallback, broad connect-error fallback, bare-404 inference,
reasoning handling without conclusive finish validation, embedding-client construction or
in-memory-only recovery. Those would weaken durable ambiguity/no-resend guarantees. The adapted
sticky decision is reconstructed from SQLite instead of an in-memory active-index cache.

Offline mocked tests cover the full order, repairs/exhaustion, no-resend ambiguity, pre-interaction
Ollama, sticky planning-to-episode ownership, restart at a prepared fallback, pre-migration failure
recovery, actual artifact provenance, unchanged selected artifacts/historical episode state, secret
redaction, doctor and call counts. Operator-recovery tests additionally cover every configured
position, repair abandonment, subsequent conclusive failures, repeated ambiguity, prepared/started
fallback restart boundaries, arbitrary model configuration, immutable decisions/requests (including
SQLite replacement inserts), concurrent/idempotent decisions, invalid targets, additive upgrade,
open topic-to-episode sticky reconstruction, historical Red preservation, and zero-call CLI/status.
Full-short integration verifies both topic-planning and episode ambiguity/reconciliation. Tests do
not invoke live generation providers or YouTube, or change image/music/render contracts.
