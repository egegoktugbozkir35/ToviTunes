# NVIDIA retired-model compatibility

This ports the NVIDIA creative-provider changes from first-party
`egegoktugbozkir35/ollama-mpt-youtube` PR #43, merge commit
`7a8170986c4c34100142b08eefc1bf72d2b648d6`.

## Reused behavior

- `config.example.yaml`: primary `z-ai/glm-5.3`, followed by
  `z-ai/glm-5.3-flash`, `nvidia/nemotron-3-ultra-550b-a55b`, and
  `deepseek-ai/deepseek-v4.1-flash`. ToviTunes' Python defaults match.
- `app/llm/nvidia_nim_client.py::_http_status_error`: HTTP 410 means
  `MODEL_UNAVAILABLE`. Here `creative/nvidia.py` already delegates HTTP status
  classification to `creative/provider.py::http_failure`; the new mapping is
  restricted to that NVIDIA path.
- `classify_nvidia_failure` and `app/llm/resilient_generator.py`: model
  unavailability has model scope and no retry delays. ToviTunes already implements
  this in `creative/failures.py`; `creative/resilience.py` continues to require
  durable terminal evidence before advancing the configured chain.
- `tests/test_nvidia_fallback.py::test_http_410_deprecated_model_advances_to_fallback_without_retry`:
  the same Kimi-to-GLM mock HTTP scenario, extended to assert immutable rows and
  restart reuse in `tests/test_retired_nvidia_model.py`.

The donor's `app/llm/factory.py` was reviewed. ToviTunes retains its existing
factory and duplicate-chain validation. OpenCode, WebUI settings migration,
generic provider-error handling, bare HTTP 404 behavior, timeout bounds and the
donor's in-memory retry loop are not copied.

## Durable recovery adaptation

The retained acceptance run `59f71b9b-cee2-47d8-994b-e37552a33942` owns request
`ae722786-2707-4972-8593-e92dbf49ff5b`. Its terminal row records model
`moonshotai/kimi-k3`, status `failed`, error kind `configuration`, and exact
diagnostic `NVIDIA NIM HTTP 410 for moonshotai/kimi-k3`.

`stored_failure` interprets only a failed NVIDIA row with the exact fixed
model-matching 410 diagnostic, old `configuration`/`provider_rejected` category,
and no response content/hash as model unavailability. It never updates the row.
Ambiguous status takes precedence. Authentication and other configuration
failures retain their policies. General HTTP 4xx errors gain no retries.

Resume must use the saved isolated `runtime-config.json`, including the original
Kimi, GLM, Nemotron, DeepSeek chain. Do not apply the new global defaults to this
run, edit its recorded model, rewrite its fingerprint, or retry Kimi. Existing
resilience verifies the original request fingerprint and contract before
advancing to GLM with `previous_attempt_id` pointing at the failed request.
Missing historical models or changed settings require restoring the saved
configuration, not editing the ledger.

No automatic migration of the user's real `config.yaml` occurs. Historical model
identities and receipts remain unchanged. Live acceptance is gated on successful
Windows CI for the updated PR head and uses only `ProductionTarget.RENDER` in the
retained isolated workspace. PR #47 remains draft and unmerged.
