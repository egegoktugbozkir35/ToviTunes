# Creative freedom and deterministic local production

This change separates model proposals from durable production structure. It adds a visual
draft, Visual Plan V2, duration authority, Storyboard V3 and Render Manifest V2. Historical
formats keep their original validators. No retained episode, provider endpoint, YouTube
operation, local configuration or production database was executed or modified during this work.

## Audit and baseline

The checkout and fetched `origin/main` both started at
`e0c6cb989264ececf5dd52192ff84de08e08486d` (PR #43). The only pre-existing untracked
file was `config.yaml`; it is excluded from this branch. The implementation branch is
`refactor/creative-production-v3`.

The donor was inspected at the documented pin
`egegoktugbozkir35/ollama-mpt-youtube@1b82232ed794ccf611cb939ff95a1a52f3b06f52`.
Relevant code: `app/models.py` (`StructuredProductionDraft`, `production_brief_from_draft`),
`app/llm/production_brief.py`, `provider.py`, `resilient_generator.py`, `topic_generator.py`,
`app/continuation.py` and `app/orchestrator.py`. The adopted boundary is a small typed
creative draft compiled into application-owned structure. ToviTunes retains its stronger
durable receipt/hash evidence, preschool safety and publication gates. Its existing topic
batch/rank/novelty/additional-round design is preserved, rather than replaced by a curriculum list.

The live database was opened only through SQLite URI `mode=ro` plus `PRAGMA query_only=ON`.
No application migration was run against it. It remains at migration 0026. Prompts, credentials
and complete private response bodies were not included in this report.

Retained identities:

| Evidence | Identity |
|---|---|
| Episode key | `identify-yellow-in-familiar-objects-f8de3ea4-001` |
| Episode ID | `e4299d67-961b-4d4c-bd65-0ed55fc91cdd` |
| Creative run | `116223ca-f142-4feb-b3f2-168f078ec191` |
| Production request | `2de50fe0-b8c5-4d15-b55f-a97751773bc7` |
| Studio render job | `9c6bd570-0b85-4941-afc3-bf03b0df96aa` |
| Music request | `6f4595ff-2ebd-4ac2-99f1-4d2332725824` |
| Blind ID | `mb_1d22beb6e2fc4f35b65cada709bd2733` |
| Selected audio master | `bc915bc1-c1fa-4405-a376-c16deef78d84` |
| Original WAV | `data/music-benchmark/6f4595ff-2ebd-4ac2-99f1-4d2332725824.wav` |
| WAV size and SHA-256 | 14,208,088 bytes; `2caace3c7033c8643167167772cf2ae222faa31e57c401b4f099eff2a57173cc` |

The selected upstream creative IDs are EpisodeSpec
`226b1698-c6ef-4924-bf03-019c8dce908d`, lyrics
`3ccf531e-d460-40df-810d-9bdd0c69e3fd` and music spec
`14e9ff27-a486-4ac1-b381-5a2d73cc9509`. The retained music adapter is
`984fbea5-e656-44a9-9467-679d193fb0b9`. There was no selected visual plan,
timed storyboard or final render for this episode. Studio's failed job and 33% progress
are observations, not continuation authority.

### Actual retained failures

All four VISUAL_PLAN response hashes were verified. All four responses parsed as JSON and
passed the original `EpisodeVisualPlan` structural schema. Their recorded terminal status is
`succeeded_response_invalid`, category `structured_output`; the actual failures are domain checks:

| Attempt | Model / fallback index | Attempt / parent | Recorded error |
|---|---|---|---|
| `42f0825c-8cbb-4c3f-a827-13093ad8411b` | Nemotron / 2 | 1 | `visual asset invents an educational claim outside the curriculum` |
| `c9132d52-2c3e-4ff8-9fb7-a6d3d7149303` | Nemotron / 2 | 2; parent `42f0825c-8cbb-4c3f-a827-13093ad8411b` | Same claim error |
| `02b7b362-85ff-409d-8437-cef175d33ade` | DeepSeek / 3 | 1 | `swatch requires a committed Colors V1 palette entry` |
| `a529173b-0fa1-4773-8d58-6ca37857ab3f` | DeepSeek / 3 | 2; parent `02b7b362-85ff-409d-8437-cef175d33ade` | Same palette error |

Nemotron's previous successful attempt was music spec
`026983a1-c030-446c-8298-58eb42ab5ea7`. DeepSeek's previous attempt was
`c9132d52-2c3e-4ff8-9fb7-a6d3d7149303`. Nemotron used input fingerprint
`cd5669dd9debd97de346c98c92e492eee4aa047476e48bfa8de19d9ef83dd929`; DeepSeek used
`734e446e5a2d111008aba91cb3b0ea9be24d2b9463a017571f9cced13ffbea05`.

| Attempt suffix | Stored response SHA-256 |
|---|---|
| `13093ad8411b` | `511102d5da3cda4199e662de88449503a8479001100c9ae8f2e12b59260c226a` |
| `a6d3d7149303` | `96b0013589157721ab6304c0dee23f62f19a04137d78c8e75ae2988d76ebbf57` |
| `cef175d33ade` | `052ebdfa6103c829086d8f87efdbecd9269a563c29a566e212115f889f4ad500` |
| `a6ca37857ab3f` | `9a46ac9d685068714dd34620fa6ea5141ac4c8a589b3e40cf06b424f599a37e8` |

Nemotron's first four assets contained educational claims outside the exact frozen objective/
vocabulary strings. All visual proposals supplied a swatch as a hex color: `#FFD700`, or
`#FFFF00` in DeepSeek's repair. The old validator required a committed palette *name*.
Nemotron would also encounter the palette failure after its claim error was resolved.

Kimi and GLM were **not attempted at VISUAL_PLAN**: sticky failover retained Nemotron after
its successful creative stages. Their earlier EpisodeSpec attempts and repairs were
`0dfa570b-ab3f-42a7-b6f1-af79e6ca632c`, `2fc117b8-f34d-4f78-98da-2c5fcc99dc89`,
`63db27d0-2645-41f6-a1f3-2df0caf7ac12`, `e2227544-77ea-4622-a83a-1a330dbdc43c`.
All supplied the correct episode/concept/objective/vocabulary, but cast `["Tovi"]` instead of
the required `["tovi"]`, triggering `episode spec differs from pinned learning objective or cast`.
Python now supplies cast identity. Topic generation separately had an earlier bounded term/schema
failure for possessive body-part examples and then a successful repair; this working selection
contract is preserved.

Supported failure categories: an earlier topic structural failure, earlier application-owned
cast bookkeeping disagreement, and visual creative/domain exact-match constraints. The retained
visual evidence does **not** establish malformed JSON, incorrect visual IDs, unsafe content,
provider failure or a continuation defect. Exhaustion followed the old sticky chain and bounded
repair budget correctly; reopening that exhausted contract in place would be unsafe.

## Before and after

| Responsibility | Before | New production |
|---|---|---|
| Episode idea | Full EpisodeSpec with known IDs, cast, vocabulary and beat taxonomy | Model supplies premise/hook/setting/progression; Python supplies pins/cast and compiles EpisodeSpec V3 |
| Lyrics | Model copies IDs and exact vocabulary | Model supplies lines; Python compiles LyricsSpec V2 with retained source context |
| Music direction | Model copies IDs, duration and sections | Model supplies direction/optional tempo; Python supplies source identity, duration and production section |
| Visual planning | Full V1 plan with exact lyric indexes, swatch/claims, IDs and four environment roles | Small creative draft with independent scene descriptions, optional actions/objects/settings/style suggestions |
| Production assets | Model names renderer slots and cross-artifact relationships | Python hashes requested art into stable keys and binds produced, verified assets |
| Timeline | ASR, WER, aligned lyrics and measured beats required | Original decoded audio duration; optional trustworthy retained rhythm; Python assigns contiguous frames |
| Local rendering | Lyric scenes in Storyboard V2 and Manifest V1 | Visual scenes in Storyboard V3 and Manifest V2; approved character pack, props, world plates, motion and real final QA remain |
| Final metadata | Model copies episode/render pins | Model supplies accurate copy; Python adds selected render IDs, SHA and audience flags under the existing metadata policy |
| Recovery | Exhausted immutable old contract | Explicit append-only new-contract authorization under the same episode and execution lease |

```mermaid
flowchart LR
    T[Novel selected topic] --> C[Model creative drafts]
    C --> S[Python compiled creative specs]
    S --> M[Retained music request and original WAV]
    M --> A[Decode, receipts, SHA and technical measurements]
    A --> K[AudioClock duration authority]
    S --> D[Model visual draft]
    K --> D
    D --> V[Python Visual Plan V2]
    V --> I[Durable Qwen prop and environment generation]
    I --> B[Python Storyboard V3]
    K --> B
    B --> R[Approved Tovi composition and FFmpeg]
    R --> Q[Manifest V2 and real final media QA]
    Q --> P[Existing rights and human publication review]
```

### Creative checks moved to guidance

For the new versions, exact beat vocabulary coverage, fixed hook/teach/practice/payoff progression,
exact teaching claims, lyric-index/scene-index equality, exact lyric text, lyric-matching props,
required Colors swatches/palette names and four model-named settings are removed. Repetition and
the former mandatory moderate-tempo range become creative guidance (bounded schema/resource
limits still apply, including the retained music adapter's supported 60–180 BPM range). A simple
theme, recognizable examples, preschool language, relevant imagery,
gentle actions and approved brand direction remain prompts. The new visual plan explicitly
records that thematic relevance needs editorial review. No semantic model-quality certification
is claimed. Topic novelty screening runs only for selection of new topics; a retained episode
is not re-screened on Resume.

Selected objectives/vocabulary/creative artifacts still pin provenance and supply context.
They are copied by Python; changing a stored source identity remains an integrity failure.
Existing publication metadata truthfulness, made-for-kids, source identity and review rules
remain unchanged. New V3 renders request only title/description/tags; Python compiles the
unchanged publication artifact with the selected final identity/hash and audience flags.
Existing metadata requests retain their original generation contract and bounded budget.

### Preserved hard gates

* Strict JSON/typed minimum structure, nonfinite-number rejection and bounded production capacity.
* Existing prohibited content, dangerous imitation, franchise/character substitution and
  named-artist protections; new drafts also reject common dangerous verb inflections.
* Actual audio decode, original file and master/receipt SHA/count/identity, path containment,
  clipping, silence/dropout/edge-silence/nonfinite PCM checks and maximum media duration.
* Current selected source pins and exact deterministic compiler replay, raw model response
  hashes/status/model/provider/prompt/episode, and complete dependency hashes.
* Required real prop PNGs with usable transparency, source images and successful durable image
  receipts; all four actual environment plates and their source bytes/provider receipts.
* Positive contiguous frame intervals, exact timeline coverage, required asset references and
  approved renderer operations; missing resources and unsupported capacity fail admission.
* Approved pinned Tovi pack, full-body role sprites, compositing/layout/motion QA and actual
  FFmpeg/MP4/audio-video duration verification.
* Singleton execution ownership, remote-start fencing, immutable receipts, bounded repairs/
  fallback, no blind resend of uncertain requests and atomic final render/QA acceptance.
* Rights inheritance, human overrides/review, made-for-kids, configuration permissions, channel
  identity, private upload/public promotion and uncertain YouTube reconciliation remain intact.
  V3 release preflight also rechecks source media/model/image/dependency evidence.

## Version boundaries and renderer details

| Artifact or generation boundary | Existing meaning | Added meaning |
|---|---|---|
| Episode spec | Schema 2 | Schema 3; embedded original idea draft, no historical beat taxonomy |
| Lyrics and music specs | Schema 1 | Schema 2; Python identities/context and model-owned content |
| Idea/lyrics/music prompts | Existing prompt constants retained | `episode_idea_v2`, `lyrics_idea_v2`, `music_idea_v2` |
| Metadata generation | Existing `youtube-metadata-kimi-v1` and artifact unchanged | `metadata_copy_v2` requests copy only; Python supplies known pins; pending old requests keep the old prompt |
| Visual generation | `episode_visual_plan_v1` | `visual_creative_v2`; raw `creative_visual_draft` artifact and receipt |
| Compiled visual plan | Schema 1 | Schema 2, `visual_compiler_v2`; embeds proposal and pins raw draft SHA |
| Audio evidence | Existing ASR/alignment/rhythm/QA retained | `audio_clock` schema 1, `duration_audio_v1`; actual technical measurements and optional measured rhythm |
| Storyboard | V1 and V2 unchanged | V3, `duration_storyboard_v1`; visual scene IDs/source IDs/frame intervals |
| Render manifest | V1 unchanged | V2, `tovitunes_creative_render_v1`; AudioClock pin; lyric keyword policy disabled |
| Structural creative admission | Existing `creative_structural_v1` | New specs use `creative_structural_v2`; neither grants publication approval |
| Recovery | Existing failed rows retained | Migration 0027 adds one immutable authorization table and no historical updates |

All parsers dispatch explicitly by version and reject attempts to reinterpret a new artifact as
an old one. Existing pending creative contracts and successful old visual receipts stay on their
original prompt and renderer path. Historical production remains read-only. No earlier migration
or checksum is changed. Existing selected completed renders continue to be validated/reused.

The environment renderer really requires four plate roles. The compiler assigns the first four
distinct creative settings in proposal order to those resources; unused roles use the first
supplied setting. Additional settings share their role's first plate with a diagnostic. The
original settings stay in the raw proposal. New episode-specific settings are not silently
replaced by an unrelated shared brand background.

Ordinary actions map through a fixed supported table (wave/walk → enter; show/hold/discover →
present; dance/hop/clap → celebrate; look/wonder → question). Other safe actions use approved
idle pose/gentle motion with a recorded diagnostic. Camera/transition suggestions use supported
gentle camera/cuts. These are composited representations, not arbitrary physical simulations.

At 30 FPS, the compiler covers `ceil(actual_duration * 30)` frames, extending by less than one
frame. It allocates balanced positive intervals and merges excess adjacent proposals to prefer
shots of at least 0.5 seconds. A merged shot preserves source scene IDs/focus/required props and
uses its first action/plate. More than three simultaneous merged props is a real renderer-capacity
failure. Optional retained reliable beats can snap cuts within 15 frames. Absent/unreliable rhythm
uses `scheduling_mode=duration`; no beat or word timestamps are invented. The clock contains no
ASR transcript, WER, lyric alignment or passing replacement of an old analysis.

## Explicit recovery of the retained yellow episode

`production recover-visual-contract` requires explicit confirmation, a nonempty operator actor,
publication disabled and the production execution lease. It verifies that old visual requests
are terminal failed/invalid, repair failure evidence exists, response bytes match hashes and
all retained upstream selections/source music are valid. Successful, prepared, remote-started
or ambiguous old requests cannot be recovered this way. It refuses to replace a selected visual
plan/storyboard/final render or historical production.

Migration 0027 adds `visual_contract_recoveries` with a unique episode/new-contract key and
update/delete rejection triggers. The grant pins old request evidence and current upstream
identities/hashes. Repeated authorization is idempotent. It performs zero provider calls and
does not change old requests, production intents or Studio jobs. Changed evidence invalidates
the authorization. New generation has its own prompt/schema/messages fingerprint and existing
bounded retry ledger; another invocation cannot reopen its exhausted or uncertain requests.

After authorization, the read-only planner derives reuse from persisted source evidence. It
selects/reuses the completed creative/music stages, measures/adopts an AudioClock without ACE-Step
or ASR, generates the new visual draft, produces missing images, compiles V3 and renders locally.
Studio job Resume keeps its exact saved reference/target. Existing draft exports are untouched:
new exports use `_PILOT_CREATIVE_V2.mp4`, `_PILOT_CREATIVE_V2_MEDIA_QA.json` and
`_creative_v2_frames`. Immutable final artifact files still use the existing `main_v4` slot so
Studio/release consumers remain connected; manifest/storyboard versions identify their meaning.

## Operator procedure (not executed by this change)

1. Stop any active production/Studio worker. Keep the original database, WAV, configuration and
   old draft exports. Make a normal SQLite-safe backup while workers are stopped.
2. Inspect `git status --short`; preserve `config.yaml` and any additional local work. Fetch and
   switch to the reviewed feature branch without reset, clean or forced checkout:

   ```powershell
   Set-Location C:\Users\Victus\Desktop\ToviTunes
   git status --short
   git fetch origin
   git switch refactor/creative-production-v3
   git pull --ff-only origin refactor/creative-production-v3
   uv sync --python 3.11 --locked --inexact --extra dev --extra video-render --extra web --extra youtube
   ```

3. **The operator must edit their own `config.yaml`** before recovery or live Resume:

   ```yaml
   publication_enabled: false
   automation:
     auto_publish: false
     require_human_review: true
   publication:
     youtube:
       enabled: false
   ```

   Change these existing keys while preserving all other provider/path/model settings. This work
   did not edit the file. The audited configuration still had publication enabled. `--inexact`
   preserves already-installed optional historical ASR/rhythm packages; the new render path does
   not require those packages.
4. Inspect the provider-free plan (no confirmation flag):

   ```powershell
   uv run python -m tovitunes.cli --config config.yaml production auto-resume --episode-key identify-yellow-in-familiar-objects-f8de3ea4-001
   ```

   This existing CLI plan uses the publish target for reporting, but does not execute production
   or publish. It may identify AUDIO_ANALYSIS/AudioClock as the first missing stage before the
   explicit visual recovery barrier. The VISUAL_PLAN evidence identifies `authorize_visual_v2`.
5. Authorize only the new contract (this operator command applies the additive migration and
   grant, with no remote effects):

   ```powershell
   uv run python -m tovitunes.cli --config config.yaml production recover-visual-contract --episode-key identify-yellow-in-familiar-objects-f8de3ea4-001 --confirm-new-contract --actor local-operator
   ```

6. Restart Studio on this code, select the existing job
   `9c6bd570-0b85-4941-afc3-bf03b0df96aa`, and use Retry / Resume. Its saved render target/reference
   remains unchanged. Do not use Generate. CLI alternative for the same episode and local render
   target:

   ```powershell
   uv run python -m tovitunes.cli --config config.yaml production render-v4 --episode-key identify-yellow-in-familiar-objects-f8de3ea4-001
   ```

   The existing render-v4 command explicitly runs the render target, including missing upstream
   stages. It can call configured creative/image providers after authorization. No ACE-Step
   submission should occur because eligible original music is retained. Stop on any identity,
   missing-evidence or uncertainty barrier and reconcile it; do not edit statuses or create a song.
7. Inspect the original audio SHA, new proposal/plan/storyboard, required assets, final MP4,
   media QA and scene frames. Confirm audio sync, thematic relevance, Tovi continuity and
   preschool suitability on the actual machine. Leave publication disabled pending separate
   rights/human-review decisions. A test pass is not that production review.

## Changed files

| Files | Purpose |
|---|---|
| `domain/creative_drafts.py`; `creative/director.py`, `prompts.py`, `validation.py`, `fake.py`; `pipeline/creative.py` | Small generation contracts, deterministic compilation, versioned admission, original-contract fallback and offline fixtures |
| `domain/visual_draft.py` | Creative visual draft, stable asset/scene keys, supported action/plate compilation and explicit parsing |
| `domain/production_v3.py`; `domain/storyboard.py` | Actual audio clock, contiguous frame compiler and new/old parsing |
| `pipeline/duration_source.py`; `services/music.py` | Original receipt/audio admission and ASR-independent duration stage |
| `services/visual.py`; `render/episode_assets.py` | New draft/compiled-plan flow, durable image generation and zero-prop support |
| `services/render.py`; `render/admission_v3.py`, `visual_evidence.py` | V3 construction and strict model/source/image/dependency replay admission |
| `render/production.py`, `models.py`, `motion.py`, `character.py`, `props.py` | V3 renderer preparation, duration motion, Manifest V2 and distinct safe exports |
| `continuation.py`; `orchestrator.py`; `services/context.py`; `creative/metadata.py` | Version-aware source consumers, planner readiness/reuse and metadata decoupling from lyric shots |
| `creative/visual_recovery.py`; `persistence/migrations/0027_visual_contract_recovery.sql`; `cli.py` | Explicit append-only recovery with ownership and publication barriers |
| `web/studio.py`; `publication/preflight.py` | Actionable sanitized recovery/technical messages and V3 source release admission |
| `music/analysis_runtime.py` | Precise typing for the optional cuDNN version callable; no diagnostic behavior change or Mypy suppression |
| `tests/test_creative_production_v3.py` | New architecture, real offline rendering, recovery, source/asset failures, receipt and ownership tests |
| `tests/test_creative_director.py`, `test_creative_fallback.py`, `test_topic_planner.py`, `test_short_production.py` | New generation/renderer expectations while keeping explicit historical tests |
| `docs/CREATIVE_PRODUCTION_V3.md` | Audit, contracts, gate inventory, recovery, operator steps and limitations |

Source paths above are relative to `src/tovitunes/` where applicable. Configuration, existing
database migrations, topic novelty algorithms and provider remote retry/publication policies
were not replaced.

## Test coverage and practical limits

The new tests exercise three realistic proposal shapes: three independent settings with a
non-lyric prop and ordinary actions, missing optional art/action fields with no props, and one
safe unsupported action represented by approved idle motion. All run through real isolated
ledgers/audio/image adapters, deterministic compilation, MoviePy/FFmpeg and final media QA.
They assert independent scene count, no ASR calls/evidence, Manifest V2 and effect-free reuse.

Additional coverage includes frame coverage for 0.02s/0.7s/10.017s audio, 64 proposed moments,
duration-only and optional measured rhythm, stable IDs, palette-free Colors proposals, old/new
version isolation, dangerous/franchise content, authoritative-field injection, image/pack/
manifest/QA corruption, immutable dependency gates, source file/SHA/receipt/episode failures,
explicit recovery and retained audio/intent/job bytes, grant immutability/idempotence, uncertain
old requests, ownership loss before/after calls, response-before-ingest restart, uncertain image
restart and a real second Python process completing retained work without a second music call.
New metadata is tested with real final-render dependencies, rejected tag budget followed by
durable repair, model identity injection rejected, Python-supplied pins, normalized tags and
effect-free reuse. Retained unavailable ASR is also tested alongside reusable measured rhythm: its empty transcript,
missing WER and immutable analysis/policy rows remain unchanged. A 55 BPM draft is repaired before
being marked successful; a 60 BPM draft compiles through the actual music adapter.
The existing suite continues to cover historical schemas/migrations, exact old alignment,
provider fallback budgets, ownership, image recovery, render timing/format QA and publication.

Offline provider fixtures establish architecture and technical behavior. They do not establish
live Kimi/GLM/Nemotron/DeepSeek quality, ComfyUI art quality, thematic accuracy or production
readiness. Live Resume and rendered-video inspection remain necessary. The supported animation
is approved sprite/prop compositing; elaborate interactions and extra settings may use documented
safe representations. Lexical safety is conservative and is not a semantic safety certificate.

Validation commands and final branch/commit/CI results are recorded in the accompanying handoff
report after the complete suite and GitHub checks finish. Before opening the review branch,
`uv run ruff check .` passed, `uv run mypy src` passed across 118 source files,
the CI character-pack lock command validated all 48 assets, and the focused command
`uv run --locked pytest -q tests/test_creative_production_v3.py tests/test_creative_director.py tests/test_creative_fallback.py tests/test_topic_planner.py --tb=short`
passed 216 tests in 190.18 seconds. The first complete implementation run passed 1,047 tests
in 867.06 seconds; final review added two tests and strengthened admission checks, followed by
a fresh full run and exact-commit GitHub CI. The only observed warning is Starlette's existing
httpx TestClient deprecation.

### Added test inventory

All tests below are in `tests/test_creative_production_v3.py` (31 parameter-expanded cases).

| Test | Evidence |
|---|---|
| `test_model_draft_has_no_bookkeeping_lyric_or_palette_requirements` | Three proposal shapes, deterministic keys, three settings, no palette requirement and old/new visual parsing |
| `test_frame_coverage_merging_and_optional_measured_beats` | Four duration/count/rhythm combinations, contiguous frames, merge provenance and no invented word evidence |
| `test_flexible_draft_keeps_prohibited_content_gates` | Dangerous and franchise content rejected |
| `test_model_cannot_supply_authoritative_identity_or_character_image` | Forbidden bookkeeping/Tovi imagery and blank substantive content rejected |
| `test_pipeline_repairs_unsupported_adapter_tempo_before_success` | Supported technical tempo range validated inside the durable repair boundary |
| `test_non_lyric_proposals_render_real_complete_mp4_and_reuse` | Three actual FFmpeg/QA renders, source-free scene wording, no ASR or publication and effect-free reuse |
| `test_explicit_recovery_preserves_failures_upstream_music_and_episode` | Real legacy palette-failure receipts, new grant/contract, same episode/music/intent/job, unchanged audio/old draft and immutable grant |
| `test_v3_media_pack_and_dependency_failures_still_block_admission` | Missing/corrupt prop, wrong/missing character resources, mismatched manifest/QA and immutable dependency defenses |
| `test_duration_source_fails_closed` | Original audio missing/corrupt/SHA mismatch, receipt guard and source episode mismatch |
| `test_failed_retained_asr_stays_failed_while_optional_rhythm_is_reused` | Old unavailable ASR/WER stays unchanged, valid retained beats can be used independently |
| `test_recovery_rejects_publication_and_unresolved_receipts` | Enabled publication and uncertain old requests cannot authorize a new contract |
| `test_restart_after_visual_response_persistence_does_not_resend` | Stored successful draft response reused after process interruption before ingest |
| `test_new_visual_ambiguity_and_ownership_never_duplicate_call` | New visual ambiguity and loss of ownership before effects cannot duplicate requests |
| `test_ownership_loss_after_response_preserves_receipt_and_blocks_resend` | Immutable returned response retained after lease loss; automatic resend blocked |
| `test_v3_uncertain_image_survives_restart_without_a_second_prompt` | ComfyUI uncertainty in the new asset path survives Resume without another prompt/music submission |
| `test_v3_readiness_rejects_required_image_and_response_corruption` | Raw response corruption blocks renderer and read-only continuation |
| `test_new_contract_survives_real_process_restart` | Two real Python processes share durable V3 work and only one music submission |
