# Visual provider benchmark: executable protocol v1

This protocol executes the ten-scene comparison from the development plan. It does not rank providers. The adapters use the exact durable model identifiers `gemini-3.1-flash-image` and `gpt-image-2.5-sunburst`. The OpenAI model can be overridden with `--openai-model`, including compatible models such as `gpt-image-2.5-flare`.

The implementation follows the first-party [Gemini image generation](https://ai.google.dev/gemini-api/docs/image-generation) and [OpenAI image generation](https://developers.openai.com/api/docs/guides/image-generation) contracts. Gemini requests omit search tools and explicitly keep grounding disabled. OpenAI uses the multiple-image edit endpoint because every request must carry the same three Tovi references. It requests the supported custom size `1008x1792`, an exact 9:16 ratio with dimensions divisible by 16.

## Setup and execution

Live calls read credentials only at call time:

```text
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```

No credential is needed to import the package, inspect state, run tests, or create a dry run. Start with one request and inspect the exact canonical and translated request data:

```text
uv run python -m tovitunes.cli --config config.yaml visual-benchmark run --provider google --case red_apple --attempts 1 --dry-run
uv run python -m tovitunes.cli --config config.yaml visual-benchmark run --provider openai --case red_apple --attempts 1 --dry-run
```

Remove `--dry-run` only after credential and spend approval. Selectors can be repeated. With no provider or case selector, the default two attempts create the full 40-request plan:

```text
uv run python -m tovitunes.cli --config config.yaml visual-benchmark run --dry-run
uv run python -m tovitunes.cli --config config.yaml visual-benchmark run --provider google --case profile --case flying --attempts 2
```

Use `--google-model` or `--openai-model` to record another exact compatible model ID. Model IDs, endpoint contract, size, reference capacity, supplied and omitted reference IDs, and grounding state are persisted with each request.

## Fixed inputs

Use the same approved `CharacterAssetPack` revision and the same front, three-quarter and profile reference artifacts for both providers. Pin their artifact IDs and hashes in every scorecard. Hold palette, negative identity rules, intended portrait framing and the scene brief constant. If a provider needs different prompt syntax or cannot accept the same reference count, record the translation and limitation; do not claim a controlled comparison where inputs differed materially.

The ten locked briefs are in [`benchmarks/visual/cases.v1.yaml`](../benchmarks/visual/cases.v1.yaml). They cover a red apple, exactly three stars, a triangle surprise, flight, profile view, another original character, bedroom, playground, pointing and holding a prop. Generate at least two independent requests per scene and provider. Failed, ambiguous, and successful requests remain durable. A successful exact rerun is reused. An ambiguous or still-started request stops for manual reconciliation and is never blindly repeated.

The canonical prompt is assembled deterministically from the case, teaching check, pack palette, visual identity rules, forbidden changes, 9:16 requirement, and negative constraints. Its normalized JSON and SHA-256 fingerprint are persisted separately from the provider translation.

Generated media is stored in the configured `data_root` through the immutable `AssetStore`. Returned bytes are written, flushed and synced to a temporary sibling, then atomically renamed to a request-scoped file in `data_root/.benchmark-returned`. An immutable SQLite receipt records their hash, byte count, MIME type, provider request ID when known, latency, usage, known cost, safe response metadata and receipt time. Staged bytes are removed only after a valid immutable artifact is mapped and the request is finalized. Artifact provenance records the local benchmark request ID separately from the provider request ID, plus the exact model and three reference dependencies. Ingestion creates `unknown` rights and `pending` approval decisions; generation never grants commercial rights.

## Failure and recovery

HTTP 429 is a safe `retryable_failure` for the same attempt. Ordinary deterministic 4xx errors are `terminal_failure` and require a new attempt. HTTP 408, 5xx, timeouts, lost connections, and malformed 2xx success payloads are `ambiguous`: the provider may already have generated and charged for an image. Generic 5xx must not be blindly retried. A returned image whose bytes are known but fail local image validation is a known failed output. `run` never contacts a provider again for `remote_started`, `ambiguous`, or `terminal_failure` requests.

Provider credentials, canonical references and request payloads are prepared locally before `remote_started` is persisted. The transport persists that state immediately before its network operation. A local preflight error is recorded as a safe `retryable_failure` with `local_preflight` as its error kind; repair the local problem and rerun the same attempt. Once the transport boundary is reached, uncertain outcomes remain fail-closed.

After a local crash, use:

```text
uv run python -m tovitunes.cli --config config.yaml visual-benchmark reconcile --request-id <uuid>
```

Reconciliation **never generates an image**. With a receipt and matching staged bytes, it can ingest those bytes. With one request-owned immutable artifact matching the receipt, model, provider, MIME and canonical dependencies, it can restore a missing output mapping. With a valid existing mapping, it can complete the success transition. Exact repeats are idempotent. It verifies staged bytes against their recorded hash, size and MIME; conflicting or multiple artifacts fail closed. A request with no trustworthy local result evidence stays unresolved and needs operator/provider-side inspection using the provider request ID if available. A crash before the receipt was committed may leave bytes but lacks the metadata needed for automatic recovery; it must not be regenerated automatically.

These images test environment/prop production and difficult Tovi poses. A successful generated Tovi image may be used only as an individually reviewed special asset; it does not become the canonical character or replace sprite animation. Ordinary recurring motion remains tied to the approved pack.

## Review and measurement

Two reviewers score each decoded image without seeing the provider. A third resolves any 2-point or larger disagreement on an axis. The five image axes use 0–4 ratings: 0 broken; 1 major errors; 2 substantial repair; 3 usable; 4 excellent. Use the mean of two scores when their difference is below 2, or the median of three after adjudication. Record a cropped evidence region or clear note for every identity or teaching error. The operator separately records reference-input control, editability, request cost and latency.

| Axis | Weight | Review question |
| --- | ---: | --- |
| Character identity | 30 | Does Tovi retain the approved silhouette, proportions, tuft, eyes, beak, wings and palette? |
| Teaching accuracy | 25 | Is the target object/color/shape/count exactly correct and easy to see? |
| Composition | 15 | Is the pose, interaction and portrait layout useful for a Short? |
| Reference fidelity | 10 | Does the output follow the pinned views and identity constraints without copying a reference composition mechanically? |
| Image quality | 10 | Are anatomy, edges, lighting, detail and required transparency clean enough to use? |
| Production fit | 10 | Can the accepted image be revised, layered/cropped and reproduced with the tested controls? |

Weights are machine-readable in [`rubric.v1.yaml`](../benchmarks/visual/rubric.v1.yaml). `weighted_score = Σ(weight × rating / 4)`. Character identity and teaching accuracy must each score at least 3 for a candidate to count as usable. A wrong target color, count or shape, serious identity drift, unsafe image, unusable file or unlicensed reference is a hard failure regardless of weighted score. Unknown output rights blocks production selection, even when the image is worth scoring.

Report per-scene usable counts, usable outputs per distinct request, usable outputs per actual spend, median latency and the most common repair reasons. For teaching color, count and shape, use exact human verification first. Automated thresholds must be calibrated from ToviTunes' accepted/rejected examples before being treated as pass/fail evidence.

Export the reviewer queue without provider information, fill one [`scorecard.template.yaml`](../benchmarks/visual/scorecard.template.yaml) per reviewer and output, then import it:

```text
uv run python -m tovitunes.cli --config config.yaml visual-benchmark status --blind
uv run python -m tovitunes.cli --config config.yaml visual-benchmark review --scorecard review.yaml
uv run python -m tovitunes.cli --config config.yaml visual-benchmark report
```

The blind queue exposes only blind ID, artifact ID, case, and attempt. It excludes provider, model, cost, and latency. The provider mapping remains in the operator status output and database.

Exactly two initial reviews are required. Any axis difference of 2 or more requires exactly one adjudication review. Other scores use the two-review mean; adjudicated scores use the three-review median. The rubric YAML is the only runtime source for weights and usability minimums. A hard failure, character identity below 3, or teaching accuracy below 3 makes an output unusable.

The report groups facts by provider and model without selecting a winner. It includes request and output counts, hard failures, usable outputs and rate, per-scene usable counts, weighted score summaries, median latency, and repair reasons. `actual_spend` and usable outputs per dollar remain `null` unless every request in the group has a known cost. `known_spend` and `cost_known_requests` show partial information. Real adapters preserve usage but leave cost unknown because API usage alone does not provide a durable billed price; a future dated pricing policy can supply deterministic cost without changing the domain model.

The benchmark ends with a dated human decision describing which image tasks a provider can support, rights evidence, unresolved controls and whether its output is limited to backgrounds/props or reviewed special poses. Software does not select a provider.

