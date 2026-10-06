# Creative Director V1

New autonomous episodes use [open editorial memory](OPEN_EDITORIAL_MEMORY_V1.md). The Creative
Director invents a subject, objective, vocabulary and working title within broad versioned policy.
Application logic selects and persists an immutable LearningBrief before EpisodeSpec. Historical
Colors curriculum episodes retain their original facts and resume paths. `EpisodeSpec`, `LyricsSpec`,
`MusicSpec`, `DraftGenerator` and `CreativeDraftService` remain the downstream creative contracts.

## Configuration and commands

`creative_llm` in `config.example.yaml` explicitly selects NVIDIA NIM and `moonshotai/kimi-k3`,
with `/v1/chat/completions`, temperature `0.7`, `max_tokens=16384`, and configurable `1800` second
timeout. Credentials belong only in `NVIDIA_API_KEY` (or the configured `api_key_env`). YAML
rejects inline keys. The default ordered chain is Kimi, `z-ai/glm-5.3`,
`nvidia/nemotron-3-ultra-550b-a55b`, then `deepseek-ai/deepseek-v4.1-flash`.
`fallback_models: []` explicitly disables model fallback. Optional local Ollama is disabled
unless `fallback_to_ollama_on_endpoint_failure: true`; it is not required for normal operation.
See [fallback and recovery](CREATIVE_FALLBACK.md) for exact failure rules and donor adaptation.

Provider-free inspection:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml creative eligible
uv run python -m tovitunes.cli --config config.example.yaml creative doctor
uv run python -m tovitunes.cli --config config.example.yaml creative history
```

`eligible` reports current brand/curriculum/pack revisions, used and eligible concepts, duration
and history. `doctor` reports configuration and key presence without printing a secret or posting.

Intentional live creative generation, also the explicit live smoke command:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml creative generate-next --live
```

`--live` documents smoke-test intent; ordinary `generate-next` also intentionally uses the real
configured provider. Neither is invoked by CI. A successful command returns run/episode ID and key,
three selected creative artifact IDs, and exact call counts, then stops. No music, storyboard,
rendering, scheduling or uploading occurs. Missing keys fail closed; model failures follow the configured chain.

Targeted resume:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml creative generate-next --run-id <run-id>
uv run python -m tovitunes.cli --config config.example.yaml creative generate-next --episode-key <key>
```

Without a resume argument, an incomplete brand run resumes first. After completion, the next
invocation plans a fresh novel LearningBrief and generic episode. A pending run with an older catalog stops for explicit
recovery instead of silently changing pinned inputs.

## Open subject planning and duplicates

New runs use TopicPlanner / open-topic-planner-v1, never an eligible Colors ID list.
The model returns a configured batch (default 15) with subject, objective, small vocabulary,
creative treatment, provisional working title and batch-relative score. Application logic
sorts by score and admits the first safe, bounded, novel idea. History covers persisted selected
briefs and all historical episodes, including unpublished/failed work. Exact database uniqueness,
educational lexical comparisons and separate treatment comparisons fence duplicates; optional
local embeddings can supplement them. Same-run exclusions and frozen round inputs support
bounded multi-selection and restart recovery. See [open editorial memory](OPEN_EDITORIAL_MEMORY_V1.md)
for schemas, thresholds, migration and pinned donor adaptation.

Existing incomplete subject-planner-v1 runs keep their old candidate schema and committed
curriculum path for durable recovery. Their restrictions do not govern new autonomous episodes.
Legacy Episode JSON and prompts retain old request fingerprints. `creative eligible` remains
an inspection command for that historical catalog.

## Structured generation and durable requests

`CreativeDirector` (with the existing `NvidiaCreativeDirector` compatibility alias) implements the existing `DraftGenerator`. Pure prompts are versioned
`episode-spec-kimi-v1`, `lyrics-kimi-v1`, `music-spec-kimi-v1` and `youtube-metadata-kimi-v1`.
Prompt behavior changes require a version bump.

Provider-neutral `StructuredGenerator.generate(model_type, messages, context=..., validate=...)`
injects Pydantic JSON Schema and exact JSON-only instructions, uses `json.loads`, validates the
model and invokes domain validation. SSE collects only answer `content`, ignores `reasoning_content`,
accepts non-SSE JSON fallback and rejects malformed/empty transport results. Incomplete streaming
is an unknown remote outcome.

`creative_runs` is the small pre-episode planning record. Both subject calls and episode stages use
existing `generation_requests`, with exactly one run or episode owner. Migration `0014` preserves
legacy requests and adds prompt version, remote-start time, attempt, repair parent, actual messages,
errors, durable response and SHA. No donor `llm_generations` or disconnected logging table is added.

Local request IDs are committed before remote start. Known header/body provider IDs are persisted
as observed; absent IDs stay null. Fingerprints include provider, explicit model, endpoint/settings,
prompt version, schema, pinned facts and artifact identities. Save the response before validation
or ingestion and check its hash on reuse. Successful identical inputs reuse the saved receipt.

Invalid JSON/schema/domain output records `succeeded_response_invalid`. At most one second request,
`<kind>_repair`, has `attempt=2` and points to the original. Both prompts, responses, identities and
errors remain inspectable. Invalid repair may advance to the next configured NVIDIA model;
there is no third POST to the same model, including on reinvocation. Both initial and repair
receipts remain unchanged.

HTTP transport retries are `0`. Timeout, connection loss, HTTP 5xx, interrupted streaming, lease
loss during a request, or started calls without a receipt block as `ambiguous`. Changed input is
not recovery authorization. Definitive failures and malformed complete transport results do not
silently retry or trigger structured repair. Typed model failures may prepare a new request for
the next model; typed pre-interaction endpoint failures may use explicitly enabled local Ollama.
Inspect the ledger and obtain remote evidence before an explicit operator action for any ambiguous
request; there is no automatic resend or ambiguity-clearing CLI. Prepared requests
that never started can continue. Complete saved receipts resume local validation after crashes.

Existing `execution_leases` fence planning, creation, creative stages and metadata. Lease durations
cover bounded worst-case calls. A unique creative-input index and atomic prepared-to-started
transition prevent duplicate POST starts even if a caller races.

## Structural approval and human review

Generated objective approval checks exact persisted LearningBrief pins and broad learning policy
with actor `machine:learning_policy`; it cannot overwrite human rejection or needs_review.
Legacy objective approval requires exact pinned identity and curriculum SHA matching committed
Git HEAD bytes. Uncommitted curriculum, changed objective/vocabulary or conflicting review blocks.
Actor `machine:curriculum_policy`, policy `canonical_curriculum_v1`, means only that the exact
version-controlled objective is used; it is not an LLM judgment.

EpisodeSpec requires exact IDs/vocabulary, Tovi-only cast, short ordered hook/teach/practice/payoff,
and every target word covered by teach/practice beats. It has no scene timestamps. Lyrics preserve
selected EpisodeSpec identity/vocabulary, include target words, use at most 16 bounded lines and a
duration-based word budget, prohibit directions and limit identical-line repetition. MusicSpec
preserves selected lyrics, duration, vocabulary, artist prohibition, section totals and moderate
tempo. It remains a Lyria brief; this workflow never invokes Lyria.

Valid creative candidates can be selected by `creative_structural_v1`: pinned learning/brand/schema
admission only, not artistic merit, music QA or rights. Existing human `review_objective` and
`review_candidate` methods remain. Machine policy cannot overwrite rejection/needs_review.
Selected successful stages are reused, and missing stages use the selected upstream artifact.
Crashes after response/ingest reuse the same request/artifact ID. Rejected and stale work stays retained.

Normal `Provenance` records provider/model, local ID, actual provider ID if known, prompt version,
generation time and exact input artifacts. Rights remain `unknown`; selection grants no publication rights.

## Post-render metadata

```powershell
uv run python -m tovitunes.cli --config config.example.yaml creative metadata --episode-key <key>
```

Require selected final_render, EpisodeSpec, LyricsSpec, TimedStoryboard and render_manifest. Check
final dependency on that manifest, manifest storyboard ID/hash and duration, episode/concept/objective/
pack identity and exact rendered lyric agreement. Kimi receives those facts, duration, scene props/
intents and final artifact ID/SHA. It must never guess what was rendered. Recheck selections after
the call. The existing PR #38 Storyboard V2 and Renderer V4 consume generated episodes through
ShortProductionWorkflow. Historical Red storyboards retain their frozen contract.

`EpisodePublicationMetadata` bounds title to 100 characters, description to 1000, tags to 20
deduplicated entries/500 aggregate characters, and pins `language=en`, `made_for_kids=true`.
Validate exact concept/final ID/SHA, concept presence in title, ToviTunes description, and lexical
rejection of absent concepts/known props and unsupported claims. Machine selection uses
`metadata_structural_v1` after validation. Human metadata rejection also blocks reselection.

Persist `publication_metadata/main` with exact final_render, EpisodeSpec, LyricsSpec, TimedStoryboard
and render_manifest dependencies. Unchanged inputs reuse the same metadata ID/title; changed final
SHA or creative dependency permits a new version and makes prior metadata stale.
Publishing remains separate and blocked by existing rights policy. Upload calls are zero.

## Offline tests and call reports

`FakeDraftGenerator` remains available. `FakeNIMTransport` is a fully offline structured fixture
using the same durability path. `httpx.MockTransport` tests the real adapter, including the full
creative CLI with dummy credentials. New tests prohibit sockets and require no NVIDIA_API_KEY.

Commands report `subject`, `episode_spec`, `lyrics`, `music_spec`, `metadata`, `repair` starts during
the invocation, including failed/ambiguous starts. Stage counters EXCLUDE repairs, so their sum is
total POST starts. Error output includes the same report. CI and normal pytest make no NVIDIA calls.
