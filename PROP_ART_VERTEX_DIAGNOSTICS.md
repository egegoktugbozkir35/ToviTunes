# Prop Art Vertex access diagnosis

## Scope and preserved failure

This diagnosis continues PR #33 on `codex/prop-art-v2`. It made no Gemini image-generation
request, did not resend the failed apple request, did not create a ball request, and did not alter
the terminal-failure record in `PROP_ART_V2.md`.

The historical apple failure retained only HTTP 403. Its response body, canonical Google status,
provider message, structured `ErrorInfo`, and response request ID were not retained and cannot be
recovered. Cloud Logging returned no matching Vertex audit entries for the successful environment
window, so no missing historical error text is inferred or fabricated.

## Safe diagnostic patch

`GeminiImageProvider` now extracts only an allowlisted diagnostic subset from `errors.APIError`:

- HTTP status
- canonical Google status
- a whitespace-normalized, truncated, credential-redacted provider message
- `x-request-id`, when present
- Google RPC `ErrorInfo` reason and type, when present
- model, location, and the static `Vertex AI Gemini generateContent` endpoint family

It does not retain access tokens, Authorization headers, cookies, credential/ADC contents, raw
headers, arbitrary `ErrorInfo.metadata`, or raw provider bodies. Benchmark and environment request
ledgers persist the same sanitized mapping alongside the bounded error summary. Existing
429/408/5xx outcome classification is unchanged.

## Current non-secret runtime identity

- `GOOGLE_CLOUD_PROJECT`: `project-e3968bf9-fde5-4d99-aea`
- `GOOGLE_CLOUD_LOCATION`: `global`
- model: `gemini-3-pro-image`
- ADC credential class: `google.oauth2.credentials.Credentials`
- ADC credential kind: `authorized_user`
- ADC principal: `aaafffggghhhh@gmail.com`
- ADC quota project: `tovitunes`
- active gcloud account: `aaafffggghhhh@gmail.com`
- active gcloud configured project: `tovitunes`

The resource project used by the provider comes from `GOOGLE_CLOUD_PROJECT`, not the active gcloud
project setting. The quota project and resource project are intentionally distinct values here.

## Comparison with Environment Quality V2

The immutable Environment Quality V2 database contains four successful request rows from
2026-10-01. Every row records:

- project: `project-e3968bf9-fde5-4d99-aea`
- location: `global`
- model/model version: `gemini-3-pro-image`
- requested size: `2K`
- backend: Vertex AI

Those values exactly match the failed prop-art runtime except for the prop request's square aspect
ratio and prompt/content. The ADC file used by `google.auth.default()` was last modified on
2026-09-25, before the four successful 2026-10-01 calls, and remains the file used by the current
runtime. The prior request rows did not separately persist `principalEmail`; nevertheless, the
unchanged ADC file plus the current token's safely queried userinfo establishes the same operational
ADC principal for both runs.

## Read-only access checks

Checks were performed with the current ADC token without printing or storing it:

- `aiplatform.endpoints.predict = granted`
- Vertex AI service `aiplatform.googleapis.com = ENABLED`
- project billing = enabled

No IAM binding, service setting, billing setting, credential, quota-project setting, or project
configuration was changed.

## Conclusion

The evidence rules out a simple absence of generic Vertex prediction permission, a disabled Vertex
AI API, disabled billing, a different resource project, a different location, and—based on the
unchanged ADC file—a different ADC principal. Because the historical 403 body was discarded, this
run cannot distinguish request-specific model access, conditional access, provider policy/safety,
or another provider-side denial. A future explicitly authorized request would now preserve the
exact sanitized Google status, message, request ID, and `ErrorInfo` reason if Google returns them.

No provider call was made during this diagnosis.

PROP_ART_VERTEX_DIAGNOSTIC_INCONCLUSIVE
