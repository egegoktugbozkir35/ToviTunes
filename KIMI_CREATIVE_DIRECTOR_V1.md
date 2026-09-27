# Kimi Creative Director V1

## Starting main SHA

`d3da84401f24fdb83596c00f4c9f9d19d28c7ef0`, latest origin/main at clone, includes PR #28.
Branch: `codex/kimi-creative-director-v1`. No old rendering branch used.

## Donor repository + pinned donor SHA

First-party `egegoktugbozkir35/ollama-mpt-youtube`, main
`09cc62d3e72918ad8b7aad47930390c880b09bd5`.
Inspected all requested app/llm/{provider,nvidia_nim_client,factory,prompts,topic_generator,
production_brief,audit}.py, app/memory/{similarity,store}.py, app/{config,models}.py and
tests/{test_nvidia_nim_client,test_prompts}.py.

## Donor code reused

Adapted provider.py schema injection/parsing/domain callback/bounded repair; nvidia_nim_client.py
Bearer chat completions, SSE answer-only extraction, JSON fallback and errors; similarity.py
normalize_topic/lexical_similarity; strict NVIDIA config and MockTransport patterns. Audit/memory
informed native integration; donor tables, embeddings and editorial prompts were excluded.
No donor package dependency. All editorial prompting is ToviTunes-specific.

## NIM adapter

Strict creative_llm selects nvidia / moonshotai/kimi-k3 / https://integrate.api.nvidia.com/v1,
NVIDIA_API_KEY, 1800 seconds, temperature 0.7, max_tokens 8192. No secrets in YAML, no hidden retries
or provider/model fallback. SSE ignores reasoning; JSON fallback, actual request IDs and explicit
malformed/empty errors are tested. Checked NVIDIA's
[API reference](https://docs.nvidia.com/nim/large-language-models/latest/api-reference.html) and
[Kimi K3 model card](https://build.nvidia.com/moonshotai/kimi-k3/modelcard).

## Structured-output contract

Provider-neutral StructuredGenerator returns GeneratedDraft with normal provenance. JSON Schema,
JSON-only instruction, json.loads, Pydantic validation and domain callback are mandatory.
NvidiaCreativeDirector implements existing DraftGenerator. EpisodeSpec/LyricsSpec/MusicSpec and
CreativeDraftService are retained; pure prompts use stable v1 identifiers.

## Repair policy

At most one original POST plus one visible `<kind>_repair`, attempt=2/parent ID. Invalid original
records succeeded_response_invalid. Both messages/responses/errors/hashes persist. Invalid repair
fails closed on reinvocation; no third POST. Two subject rounds INCLUDE the single repair.

## Durable request policy

Migration 0014 creates only creative_runs and extends existing generation_requests, preserving
legacy records/API. Persist run/episode owner, stage, provider/model, prompt version/fingerprint,
local/provider IDs, statuses/timestamps/start time, repair relationship, errors and raw response/SHA.
Use existing execution_leases; save receipts before validation/ingest. Unique inputs and atomic
start prevent duplicate POST starts. No llm_generations or independent audit subsystem.

## Ambiguous-outcome handling

Timeout, connection loss, incomplete SSE, 5xx or interrupted remote starts block as ambiguous.
Known IDs survive failures; changed input cannot bypass unresolved stages. No automatic resend.
Complete saved receipts resume local work. Definitive failures also do not retry identical input.
Absent provider IDs stay null. Operator evidence/explicit recovery is required for ambiguous calls.

## Curriculum eligibility

Pinned catalog owns eligible concepts, objective IDs/text and vocabulary. Existing same-brand/
curriculum episodes across revisions reserve concepts, including drafts/held/archived work.
CURRICULUM_EXHAUSTED fails without a POST. creative eligible is provider-free.

## Duplicate prevention

Exact concept exclusion, then donor lexical similarity of premises/hooks/examples at 0.78 plus
an obvious stop-word/find-discover paraphrase signature. Ordered filtering tries the same pool
before its one repair. No embeddings. Lexical/structural admission is not a semantic safety or
art-quality certification; human review remains available.

## Subject candidate contract

CreativeSubjectCandidate: concept_id, premise, hook, setting, example_objects, song_angle, reason.
CreativeSubjectPool: 3-5 ordered candidates, no scores/virality predictions. Validate eligibility,
concrete familiar examples, prohibited content/extra-character signals and duplicates. Prompts
explicitly prohibit all requested unsafe/adult/imitative material and curriculum mutation.

## Episode creation

Reserve treatment and normal pinned Episode JSON before insertion; derive curriculum-concept keys
and collision-checked ordinal under the planning lease. Digest truncated stems. A crash recovers
the reserved UUID. Normal database validation pins all objective/vocabulary/language/duration/
catalog/Tovi pack identities.

## EpisodeSpec generation

episode-spec-kimi-v1 includes pinned objective, words, Tovi, bible, treatment and duration. Validate
exact IDs/words, Tovi-only cast, short ordered hook/teach/practice/payoff, and all words covered by
teach/practice beats. No timestamps. Domain errors are repairable once.

## Lyrics generation

lyrics-kimi-v1 uses selected EpisodeSpec/artifact ID. Simple English preschool song, concrete
examples, compact repetition/chorus, target-duration budget. Validate word presence/immutability,
at most 16 bounded lines, limited identical repetition and no production directions. No pronunciation ML.

## MusicSpec generation

music-spec-kimi-v1 uses selected lyrics and bible. Warm brief, intelligible foreground vocals,
simple arrangement, moderate 80-130 BPM, pinned IDs/vocabulary/duration, section totals and artist
prohibition. Existing MusicSpec remains a brief; Lyria calls = 0.

## Automated curriculum approval

machine:curriculum_policy / canonical_curriculum_v1 validates exact identity and committed Git HEAD
curriculum bytes. Changed/uncommitted objectives or conflicting review block. It is deterministic,
not an LLM approval. Existing human methods remain.

## Creative structural approval

creative_structural_v1 admits schema/curriculum/brand structure only, not excellence, music QA or
rights. Human rejection/needs_review blocks machine reselection. Selected stages resume without
provider calls; rejected and stale artifacts remain retained.

## Metadata writer

creative metadata requires selected final_render, EpisodeSpec, LyricsSpec, TimedStoryboard and
render_manifest. Check exact manifest dependency, storyboard hash/identity, final duration and
rendered lyric agreement. Supply actual props/intents/facts and final ID/SHA. Bounds: title 100,
description 1000, 20 deduplicated tags/500 total chars; language=en/made_for_kids=true pinned.
Reject foreign concept/absent known props/unsupported lexical claims. Machine-select validated
publication_metadata/main only. Current TimedStoryboard retains red-pilot scope; no renderer extension.

## Provenance

Normal provider Provenance carries nvidia/Kimi, local_request_id, real provider request_id or null,
prompt_version, acquired_at and input_artifact_ids. Metadata depends on exact final_render/SHA,
EpisodeSpec, LyricsSpec, TimedStoryboard and render_manifest. Rights stay unknown; structural
selection grants no publishing rights.

## Idempotency/resume

Identical successful fingerprints reuse receipts. Crash before ingest reuses response; crash after
ingest reuses local request's artifact ID. Incomplete runs resume first; --run-id/--episode-key
supports explicit resume. Unchanged final SHA/dependencies reuse metadata; changed render versions
stale old metadata. Selections rechecked after remote metadata call. Ambiguous starts never resend.

## Offline provider tests

Donor-adapted MockTransport covers explicit model/Bearer/schema/timeout, SSE reasoning exclusion,
JSON fallback, missing key, malformed/provider errors, repair/no third, actual/unknown IDs,
ambiguous/reuse/crash handling and no fallback. Full mocked NIM CLI verifies nvidia/Kimi provenance.
FakeNIMTransport/FakeDraftGenerator stay offline. Creative tests cover pins, pool filtering, history/
exhaustion, duplicates, leases, human overrides and crash boundaries. Metadata tests cover selected
facts, budgets, kid/language pins, dependencies, changed SHA/reuse and out-of-scope call guards.

## Test count

531 passed: 432 existing plus 99 new offline tests. Includes character lock, renderer, music,
production, creative, metadata and NIM contracts; no skipped tests. Full validation uses Python
3.11.9, existing locked dev/video-render extras and installed FFmpeg.

## Ruff/mypy

uv run ruff check . passed. uv run mypy src passed (65 source files). git diff --check passed.
Production dependencies and uv.lock unchanged.

## CI

Existing public windows-latest/Python 3.11 workflow unchanged: locked uv, FFmpeg, character-lock,
Ruff, mypy, pytest. New tests prohibit sockets and use dummy/absent keys, offline fixtures and
MockTransport. NVIDIA calls during CI = 0. No provider credentials required.

## Live-call status

No live smoke run performed. Explicit creative generate-next --live is documented. creative doctor
checks configuration/key presence without posting. Live latency/creative quality remain unverified;
offline contract/integration tests are authoritative for CI.

## Provider-call count

Actual live implementation calls: subject 0, episode-spec 0, lyrics 0, music-spec 0, metadata 0,
repair 0. Music/Lyria 0. YouTube upload 0. Intentional commands report exact starts (including
failed/ambiguous) and separate repair counts. Test fixture/MockTransport calls are not live calls.

## Renderer untouched

No renderer, scene composition, MoviePy/FFmpeg behavior, TimedStoryboard, music QA, WhisperX,
Beat This, Tovi pack or asset changes. Existing character/render/music/production tests pass.

## Publishing untouched

No uploader implementation/call, rights-policy or publication change, analytics, scheduling,
new curriculum categories or renderer work. PR is not automatically merged.

KIMI_CREATIVE_DIRECTOR_READY
