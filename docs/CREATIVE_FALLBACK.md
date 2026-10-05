# Creative fallback and durable recovery

The same prompts, schemas, pinned curriculum and domain validators apply to every model.
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
| `authentication` | HTTP 401/403; fail closed with no fallback. |
| `configuration` | Missing key, changed request settings, corrupt receipt, unrecognized legacy failure or local configuration error; fail closed. |
| `provider_rejected` | Unclassified provider rejection, bad request, bare 404, rate limiting; fail closed. |
| `ambiguous` | Read/write timeout, connection loss, generic connect error or connect timeout without proof of non-interaction, HTTP 408/5xx, interrupted/malformed stream, or started request without a receipt; stop and reconcile evidence. |

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

Compatibility recognizes only a stored **failed** NVIDIA row with `error_kind=ProviderError` and
the exact prior transport error `NVIDIA NIM returned empty answer content`. That prior transport
produced this error after completed response handling. It is interpreted as `empty_answer` in memory;
the old row is never relabeled. Generic legacy errors and ambiguous rows remain blocked.
The old NIM fingerprint settings serialization is preserved, excluding fallback configuration so
enabling a chain does not hide historical requests. Changed inference settings fail closed.

After updating code, keep the original `config.yaml` settings and run:

```powershell
uv run --locked python -m tovitunes.cli --config config.yaml creative doctor
uv run --locked python -m tovitunes.cli --config config.yaml creative generate-next --live
```

The normal workflow discovers the oldest incomplete brand run, retains its episode reservation,
reuses selected stages and resumes the missing stage. The reported real Kimi empty-answer failure
should therefore advance safely to GLM **if the local ledger has the exact conclusive failure**.
No run ID is hard-coded and no historical selected asset is regenerated. If configuration explicitly
disables fallbacks, add the desired list from `config.example.yaml`.

For ambiguity, the CLI reports the blocked local request ID and asks the operator to inspect the
ledger and reconcile provider evidence. Retain the provider request ID, status, error and receipt
evidence and obtain the provider outcome before any explicit recovery action. This change does not
add an ambiguity-clearing or force-resend command. Do not delete state or start another episode to
bypass the hold.

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
redaction, doctor and call counts. This work does not invoke generation providers or YouTube, or
change image/music/render contracts or the full-short orchestrator.
