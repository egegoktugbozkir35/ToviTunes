# Creative Director V1

The committed curriculum owns educational objectives and vocabulary. Kimi chooses creative
treatment from deterministic eligible concepts; it cannot invent curriculum, change Tovi's
identity, or approve an educational claim. Existing `EpisodeSpec`, `LyricsSpec`, `MusicSpec`,
`DraftGenerator`, and `CreativeDraftService` remain authoritative.

## Configuration and commands

`creative_llm` in `config.example.yaml` explicitly selects NVIDIA NIM and `moonshotai/kimi-k3`,
with `/v1/chat/completions`, temperature `0.7`, `max_tokens=8192`, and configurable `1800` second
timeout. Credentials belong only in `NVIDIA_API_KEY` (or the configured `api_key_env`). YAML
rejects inline keys. No alternate model/provider, Ollama, OpenAI or embeddings fallback exists.

Provider-free inspection:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml creative eligible
uv run python -m tovitunes.cli --config config.example.yaml creative doctor
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
rendering, scheduling or uploading occurs. Missing key or unavailable Kimi fails clearly.

Targeted resume:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml creative generate-next --run-id <run-id>
uv run python -m tovitunes.cli --config config.example.yaml creative generate-next --episode-key <key>
```

Without a resume argument, an incomplete brand run resumes first. After completion, the next
invocation plans a fresh eligible episode. A pending run with an older catalog stops for explicit
recovery instead of silently changing pinned inputs.

## Curriculum, subject planning and duplicates

The brand catalog pins curriculum bytes, brand/bible/safety rules and Tovi pack identity before
provider calls. All existing episodes for the same brand and curriculum ID reserve their concept,
including draft, held and archived episodes, across revisions. This conservative V1 rule prevents
pending work or revision changes from resetting duplicate protection. Repeats need a future explicit
policy. Exhaustion fails `CURRICULUM_EXHAUSTED` without a provider call.

`subject-planner-v1` supplies each eligible concept's ID, objective ID, exact objective and vocabulary,
plus history. `CreativeSubjectPool` has 3-5 ordered candidates with concept, premise, hook, setting,
familiar example objects, song angle and reason; no scores or view predictions. Validate in response
order and select the first acceptable candidate. Invalid rank one does not cause another call
while a later candidate is acceptable.

Exact concept exclusion is strongest. Donor `normalize_topic` and `lexical_similarity`, plus a
small stop-word/find-discover signature, catch obvious treatment paraphrases at a `0.78` threshold.
History includes premises, hooks and example objects where retained. Familiar physical examples
use a V1 allowlist. Prompts forbid all specified unsafe/adult content, extra permanent characters,
franchise/artist/celebrity imitation, abstract explanations and curriculum changes. Lexical checks
are conservative structural guards, not a general semantic safety/art-quality certification.

If no candidate passes, the structured repair policy allows one corrected pool: at most two
subject rounds/POSTs TOTAL, rather than two rounds times two attempts. A selected treatment reserves
normal Episode JSON before insertion. Curriculum/concept-derived keys use collision-checked ordinals
under the planning lease; truncated long stems include a digest. A crash recovers the reserved UUID.
The normal database pins objective, vocabulary, language, duration, catalog and pack revisions.

## Structured generation and durable requests

`NvidiaCreativeDirector` implements the existing `DraftGenerator`. Pure prompts are versioned
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
errors remain inspectable. Invalid repair fails closed, including on reinvocation; no third POST.

HTTP transport retries are `0`. Timeout, connection loss, HTTP 5xx, interrupted streaming, lease
loss during a request, or started calls without a receipt block as `ambiguous`. Changed input is
not recovery authorization. Definitive failures and malformed complete transport results do not
silently retry or trigger structured repair. Inspect the ledger and obtain remote evidence before
an explicit operator action; V1 deliberately has no automatic resend/recovery CLI. Prepared requests
that never started can continue. Complete saved receipts resume local validation after crashes.

Existing `execution_leases` fence planning, creation, creative stages and metadata. Lease durations
cover bounded worst-case calls. A unique creative-input index and atomic prepared-to-started
transition prevent duplicate POST starts even if a caller races.

## Structural approval and human review

Machine objective approval requires exact pinned identity and curriculum SHA matching committed
Git HEAD bytes. Uncommitted curriculum, changed objective/vocabulary or conflicting review blocks.
Actor `machine:curriculum_policy`, policy `canonical_curriculum_v1`, means only that the exact
version-controlled objective is used; it is not an LLM judgment.

EpisodeSpec requires exact IDs/vocabulary, Tovi-only cast, short ordered hook/teach/practice/payoff,
and every target word covered by teach/practice beats. It has no scene timestamps. Lyrics preserve
selected EpisodeSpec identity/vocabulary, include target words, use at most 16 bounded lines and a
duration-based word budget, prohibit directions and limit identical-line repetition. MusicSpec
preserves selected lyrics, duration, vocabulary, artist prohibition, section totals and moderate
tempo. It remains a Lyria brief; this workflow never invokes Lyria.

Valid creative candidates can be selected by `creative_structural_v1`: curriculum/brand/schema
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
the call. TimedStoryboard/renderer remain unchanged, including their existing red-pilot scope;
creative support for other eligible concepts does not generalize the renderer.

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
