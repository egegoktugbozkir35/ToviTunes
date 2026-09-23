# ToviTunes architecture proposal

Status: proposal only. No production pipeline or provider integration is implemented by this document. Repository observations below were verified on 2026-09-23. Donor source references are pinned to commit `09cc62d3e72918ad8b7aad47930390c880b09bd5` so later donor changes do not silently change the evidence.

## A. Verified repository state

| Repository | Observed state | Tooling and tree |
| --- | --- | --- |
| `egegoktugbozkir35/ToviTunes` | GitHub reports default branch `main`, but the repository is **truly empty**: `GET /git/ref/heads/main` returned `409 Git Repository is empty`, contents returned `404 This repository is empty`, and the branches collection was `[]`. There is no HEAD commit, branch ref, or tracked file. No local ToviTunes checkout was found; local working-tree status and configured local remotes are therefore not applicable. GitHub advertises `https://github.com/egegoktugbozkir35/ToviTunes.git` as its clone URL. | No Python package, interpreter declaration, dependency manager, CI, or other tooling is present. The host has Python 3.12 and `uv`, but these are **not** repository state. |
| `egegoktugbozkir35/ollama-mpt-youtube` | GitHub's `refs/heads/main` resolves to `09cc62d3e72918ad8b7aad47930390c880b09bd5`, confirming the supplied SHA. The exact main tree was read through the GitHub connector. Remote dirty/clean status has no meaning. A separate local reference checkout is clean on feature branch `codex/streambeats-bgm-library` at `f38d06661fff180b64cbd87e8f2e5f2cd80962ab`, with `origin` pointing to the donor URL; it is **not** the inspected main revision. No donor files, refs, branches, configuration, tests, or schema were changed. | Main has `pyproject.toml` (Python `>=3.11`, setuptools, Pydantic 2, PyYAML, httpx, optional YouTube/web/dev extras), `uv.lock`, `app/` modules for config, LLM, SQLite state, continuation, execution, MPT, YouTube, CLI and web, `tests/`, `docs/`, and Windows CI. It has no ToviTunes package or standalone brand data. |

The target's observed initial state is preserved here even though a minimal base commit and proposal branch may be added to make a draft PR possible. Direct `git`/`gh` GitHub traffic from this environment failed at its network proxy; the GitHub connector supplied the remote evidence and is the available write route.

## B. Donor repository findings

All linked paths below refer to donor `main` at the verified commit.

* **Configuration and models.** [`app/config.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/config.py) defines frozen Pydantic configuration models with `extra="forbid"`, a YAML loader, explicit environment overrides, and config-relative paths. Its `MPTConfig` is tightly coupled to stock/local MPT sources. `YouTubeConfig` exposes `self_declared_made_for_kids`, currently defaulting false, and has no expected channel ID. [`app/models.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/models.py) uses typed `ContentState`, `ProductionBrief`, and `ContentItem`, but the brief merges script, visual terms, and YouTube metadata; that shape is unsuitable for a song and storyboard pipeline.
* **Persistence and history.** [`app/state/migrations.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/state/migrations.py) defines versioned SQLite DDL for content items, transitions, platform publications, one execution lease, runs, and attempts. [`ContentStore` in `app/state/db.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/state/db.py) uses foreign keys, WAL, a busy timeout, short `BEGIN IMMEDIATE` transactions, compare-and-set state changes, and persisted run/attempt diagnostics. The separate [`app/llm/audit.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/llm/audit.py) creates `llm_generations` lazily outside the migration set; ToviTunes should keep all schema changes in one migration history.
* **Continuation and ownership.** [`plan_continuation` in `app/continuation.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/continuation.py) is a side-effect-free decision over persisted item/publication facts. It reuses trusted MP4s, requires generation identity before regeneration, and blocks ambiguous uploads. It is built around brief → MPT render → publication, so its stage enum and state transitions cannot be lifted into a many-artifact episode. [`ProductionExecutionOwnership` in `app/execution.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/execution.py) heartbeats a SQLite singleton lease and reasserts ownership at live boundaries; `ContentStore.acquire/renew/release_production_execution` fences by owner token and expiry. [`app/orchestrator.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/orchestrator.py) lazily constructs dependencies and records run attempts separately from authoritative item state. Its reuse of a valid render after failure is an important pattern.
* **Artifacts and renderer.** [`MoneyPrinterTurboAdapter` in `app/mpt/adapter.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/mpt/adapter.py) invokes a subprocess with `shell=False`, guards protected CLI options, insists MPT auto-upload is off, validates task IDs and output paths under `storage/tasks/<task-id>`, rejects empty/non-MP4 output, and can recover an existing `final-*.mp4`. Its command passes a script, visual terms, and stock/local video source to MPT. It does not manage scene-level cartoon assets, song timing, artifact hashes, rights, or educational QA. [`inspect_render_artifact`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/continuation.py) checks path, size, suffix, and trusted directory, but does not hash or probe the MP4.
* **LLM and creative memory.** [`StructuredGenerator`, `EmbeddingProvider`, and `generate_structured_with_repair` in `app/llm/provider.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/llm/provider.py) provide narrow typed-output boundaries and one validation repair. [`app/llm/factory.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/llm/factory.py) selects Ollama, OpenCode, or NVIDIA NIM. [`TopicPlanner`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/llm/topic_generator.py) reads recent items and optionally compares embeddings; ToviTunes needs structured creative dimensions first, without adopting an embedding service. Donor prompts and `ProductionBriefGenerator` serve a different adult explainer product.
* **YouTube.** [`YouTubeUploader` in `app/youtube/client.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/youtube/client.py) uses OAuth and the official Data API, optional dependencies, token refresh/atomic token replacement, `videos.insert` resumable upload, bounded retries, and an `on_remote_start` callback before its first upload chunk. It sets `selfDeclaredMadeForKids` from config. It does **not** verify an expected authenticated channel ID before upload. [`platform_publications` and methods in `ContentStore`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/state/db.py) distinguish preflight retryable failure, remote-started ambiguity, known upload-limit rejection, success, and operator reconciliation. The persisted remote ID is written before ownership is checked again. ToviTunes should keep this safety distinction and add media hash/channel identity to each publication attempt.
* **Operations and tests.** [`app/cli.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/cli.py) exposes generate, resume, status, runs, and explicit YouTube reconciliation. [`app/logging_utils.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/logging_utils.py) emits structured JSON; [`app/errors.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/app/errors.py) separates configuration, state, ownership, MPT, and YouTube failures. [`tests/conftest.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/tests/conftest.py), [`tests/test_continuation.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/tests/test_continuation.py), [`tests/test_execution_ownership.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/tests/test_execution_ownership.py), [`tests/test_mpt_adapter.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/tests/test_mpt_adapter.py), and [`tests/test_youtube_client.py`](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/tests/test_youtube_client.py) demonstrate fake clients/subprocesses, temporary SQLite, fake clocks, and network-blocked tests. [CI](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/blob/09cc62d3e72918ad8b7aad47930390c880b09bd5/.github/workflows/ci.yml) runs on Windows with `uv`, pytest, Ruff, and mypy. Its branch trigger and MPT/web jobs should not be copied unchanged.

## C. Reuse matrix

`REUSE` would mean substantially unchanged code. No examined donor component qualifies as a whole: ToviTunes' domain and artifact contracts differ. Small pure helpers may be copied later with targeted tests, but the architectural decision for each area is below.

| Area | Donor implementation | Decision | Reason |
| --- | --- | --- | --- |
| Application and typed configuration | `app/config.py`, `config.example.yaml` | ADAPT | Keep strict Pydantic/YAML and explicit env override pattern; replace adult channel, MPT, and unused platforms with brand/runtime/publishing policy. |
| Package and dependency tooling | `pyproject.toml`, `uv.lock` | ADAPT | Use `uv` and locked development installs; create a distinct `src/tovitunes` package and lean extras. |
| SQLite access and migrations | `app/state/db.py`, `app/state/migrations.py` | ADAPT | Keep short transactions, FK/WAL/busy timeout, explicit versions; design artifact graph and make each migration atomic, including generation audit. |
| Content/run persistence | `ContentStore`, `production_runs`, `production_attempts` | ADAPT | Separate episode truth from diagnostic attempts; donor `ContentItem` columns and state enum do not express scene artifacts. |
| Continuation | `plan_continuation`, `Orchestrator.resume` | REFERENCE | Preserve pure planning and fail-closed decisions; replace fixed brief/render progression with requirement and dependency evaluation. |
| Execution leases | `ProductionExecutionOwnership`, singleton lease | ADAPT | Retain tokens, expiry, heartbeat, and pre-call assertion; key leases by episode/resource and fence DB writes. |
| Artifact/path checks | MPT `_validate_paths`, `inspect_render_artifact` | ADAPT | Trusted-root and nonempty checks are useful; add SHA-256, media probe, immutable versions, and stored validator results. |
| Hashing | No artifact hash in inspected render/store paths | REFERENCE | Implement a new content-hash contract; path existence alone is insufficient for safe reuse. |
| LLM abstraction | `StructuredGenerator`, structured repair, factory | ADAPT | Keep typed generation and one bounded repair; separate prompts, model/request audit, and educational validators. |
| Retry policy | YouTube bounded backoff; MPT recovery | ADAPT | Retry only operations with a proven safe boundary; record expensive generation request identity and ambiguous outcomes. |
| YouTube authentication | `YouTubeUploader._load_service` | ADAPT | Keep official OAuth client and atomic token persistence; add channel identity preflight and secret handling. |
| Channel identity | No expected-channel check in inspected config/uploader | REJECT | Never publish under an assumed account; require configured channel ID and authenticated `channels.list(mine=true)` match. |
| YouTube publication | `YouTubeUploader.upload`, publication store methods | ADAPT | Preserve remote-start/ambiguous distinction and MP4 reuse; bind attempt to exact hash and channel. |
| MPT renderer | `MoneyPrinterTurboAdapter` | REJECT | Script/stock-source workflow conflicts with song-led, scene-addressable cartoon composition. Reuse its safety ideas, not its renderer. |
| CLI | `app/cli.py` | ADAPT | Keep explicit inspect/resume/reconcile verbs; use episode/artifact/review terms and dry plan output. |
| Logging and errors | JSON formatter and typed error hierarchy | ADAPT | Keep structured correlation and meaningful failure categories; make logs diagnostic, not durable truth, and redact prompt/secrets as configured. |
| Dependency injection/fakes | Orchestrator factories and fake services/runners | ADAPT | Preserve lazy external dependencies and deterministic fake providers across all new boundaries. |
| Offline tests | `tests/`, `pytest-socket`, Windows CI | ADAPT | Keep no-network default and temp DB/files; add dependency invalidation, rights, QA, and media fixture tests. |
| Adult topic/brief prompts, embedding duplicate service, MPT cross-post, web UI | `app/llm/prompts.py`, `ProductionBrief`, `app/web/` | REJECT | Different product and out of initial scope. |

## D. Recommended ToviTunes architecture

Build one standalone local Python application, with no Boardroom Relics compatibility layer or generic multi-brand engine. The domain has `Brand → Character/Curriculum → Episode → Artifact`; the first brand is ToviTunes, and additional characters are data. An episode pins the exact brand, character reference, curriculum, prompt, and policy versions it used. Default product values are English, ages about 3–6, 30–45 seconds, and YouTube Shorts, validated but configurable.

Use `src/tovitunes` to avoid accidentally importing from the working directory and to make tests exercise an installed package. Use Python `>=3.11`, `uv` for lockfile/development commands, Pydantic 2 at YAML/JSON/provider/database boundaries, and standard typed code inside the application. Avoid a broad plugin framework: five narrow provider protocols and a deterministic planner are enough. SQLite owns durable state; files under a configured local data root hold large immutable artifacts. Logs describe execution but never decide whether work is complete.

An episode has one primary learning objective (a stable curriculum concept/version and an explicit outcome). A small traceability contract follows its objective ID and target vocabulary through `EpisodeSpec`, `LyricsSpec`, `SongSpec`, storyboard scenes, visual specifications, render manifest, and QA findings. This permits a deterministic check that all stages claim the same target and a human or future visual check that the teaching imagery actually matches it.

## E. Target pipeline

```text
versioned brand + curriculum + character references + policy
  → select concept and pin learning objective
  → episode concept → educational/creative/safety review
  → lyrics → lyric review
  → SongSpec → music generation or manual import → music/rights review
  → timed storyboard → scene/visual specifications → scene assets/review
  → immutable render manifest → deterministic compositor → final MP4
  → content/educational/visual QA + deterministic media QA
  → YouTube metadata → rights/provenance gate → publication approval
  → channel-identity preflight → official YouTube upload → publication record
```

Each arrow consumes explicit accepted artifact versions. The planner can stop at any review or unmet requirement and later continue without replaying accepted expensive work. Lyrics and music are separate contracts. The song is the timeline's audio master; the storyboard maps timecoded lyric segments and learning purpose to scenes.

## F. Proposed repository tree

This is a **future layout**, not files to create in this architecture-only task.

```text
ToviTunes/
  pyproject.toml                 # src layout, uv scripts, lean optional extras
  uv.lock
  .gitignore                     # data/, secrets/, cache, temporary renders
  config.example.yaml           # runtime paths, provider choices, QA/publish defaults
  brands/tovitunes/
    brand.yaml                   # brand ID, audience, language, output defaults
    creative_bible.yaml          # visual and musical direction
    safety_policy.yaml           # versioned prohibited/flagged categories
    curriculum/colors.v1.yaml   # concept IDs and objective templates, including red…review
    characters/tovi/character.yaml
    prompts/                    # versioned generation templates
    references/                 # small approved canonical references, if Git-suitable
  src/tovitunes/
    __init__.py
    cli.py
    config.py
    domain/                      # brand, curriculum, episode, song, scene, artifact, review models
    persistence/                 # SQLite store, numbered migrations, queries
    artifacts/                   # trusted paths, hashes, atomic writes, import/validation
    pipeline/                    # requirements, pure planner, execution/lease coordination
    providers/                   # protocols and later concrete LLM/music/visual/YouTube adapters
    rendering/                   # render-manifest builder and later FFmpeg compositor
    qa/                          # educational/safety and media checks
  tests/                         # no-network fakes and temp-root fixtures
  docs/                           # architecture, development, operations
```

Generated files live outside Git under a configured root, e.g. `data/episodes/<episode-uuid>/<role>/<slot>/<artifact-uuid>.<ext>` and `data/brand-assets/<asset-uuid>/<artifact-uuid>.<ext>`. UUIDs and enumerated roles/slots avoid user-supplied names, Windows reserved names, and traversal. Scene IDs are stable machine IDs, not filenames. The database stores root-relative paths and SHA-256, never trusts a path supplied by a prompt/provider, and resolves every read/write against its configured root (including symlink/reparse-point escape checks). Use staging files on the same volume, validation and hash before registration, atomic replace into final immutable names, then a DB transaction. Startup reconciliation should quarantine unregistered staging/orphan files, never infer that they are accepted artifacts. Episode directories are easy to archive with a DB export/manifest.

## G. Principal typed domain models

| Model | Responsibility and important invariants |
| --- | --- |
| `BrandDefinition`, `BrandVersion`, `CharacterDefinition` | Stable brand/character IDs, exact source revision/hash, canonical references and allowed style traits. No `if character == "Tovi"` paths. |
| `Curriculum`, `CurriculumConcept`, `LearningObjective` | Versioned concept ID, age/language, target vocabulary and observable learning outcome; colors are data. |
| `EpisodeConcept`, `EpisodeSpec` | Story premise, hook, setting, cast, objective ID, target duration, creative dimensions, pinned source versions. `EpisodeSpec` is a reviewed contract, not the donor brief. |
| `LyricsSpec`, `SongSpec` | Time/section-aware lyrics and separate musical instructions: BPM range, mood, instrumentation, voice/pronunciation, repetitions, call-response, duration, loopability, original-melody/no-imitation constraints. The recorded song may later supply measured duration/timing. |
| `Storyboard`, `SceneSpec`, `VisualAssetSpec` | Ordered nonoverlapping scene IDs/time ranges, lyric segments, learning purpose, action/expression, setting/objects, target concept, composition/motion and required visual slots. Validate coverage of the song timeline. |
| `MediaArtifact`, `ArtifactProvenance`, `RightsStatus`, `ApprovalDecision` | Immutable artifact version, owner/slot/hash/path/media facts, exact generation or import source, reference IDs, rights evidence, reviewer/policy decision. Unknown rights never mean approved. |
| `RenderManifest` | Exact scene/artifact versions and hashes, audio/SFX/caption inputs, frame/timebase, transforms, FFmpeg/tool versions and output settings; reproducible within a pinned toolchain. |
| `ContentQAReport`, `MediaQAReport` | Structured findings with category, severity, objective/scene/artifact/time reference, evidence, checker version, and disposition. |
| `PublicationSpec`, `PublicationRecord` | Approved metadata, expected channel ID, Made for Kids true, exact validated MP4 hash, preflight/retry/remote outcome and video ID. |

Use Pydantic `extra="forbid"` and schema versions on persisted JSON/YAML and provider outputs; validate again when loading old artifacts. Store bounded typed payloads for complex specifications while indexing key fields in SQLite. Internal orchestration commands can be frozen dataclasses. Do not create a table for every nested scene field, and do not hide searchable facts in prose.

## H. Persistence model and migrations

SQLite is the initial database. Enable foreign keys, WAL, busy timeout, UTC timestamps, and short explicit transactions. Large media stays on disk; SQLite stores identity, decisions, dependencies and indexes. Proposed initial logical tables (some can be introduced in later numbered migrations):

| Table | Major fields and relationships | Key indexes/constraints |
| --- | --- | --- |
| `schema_migrations` | version, checksum, applied_at | Unique version; migration and version marker commit together. |
| `brand_revisions` | brand_id, source Git revision, definition/curriculum/policy hashes, created_at | Unique brand/revision/hash; pinned by episodes. |
| `episodes` | id, brand_revision_id, curriculum_concept_id/version, objective text/ID, language, target age/duration, lifecycle (`draft`, `active`, `held`, `complete`, `archived`), created/updated | Index brand, concept, created time; unique external episode key if imported. Lifecycle is not stage completion. |
| `episode_creative_facts` | episode_id, story premise, setting, props, hook pattern, lyrical hook, song structure, cast IDs, style tags, chosen duration | Index concept, cast/style tags through normalized join or SQLite JSON only where query plan remains sound; keep major dimensions as columns. |
| `artifact_versions` | id, owner scope (`episode` or `brand`), episode_id/brand_id, kind, slot_key, schema_version, root-relative path, SHA-256, byte count, MIME, created_by_stage, generation_request_id, created_at, superseded_at | Unique ID/path; `(episode_id,kind,slot_key,created_at)`; SHA index. Immutable content and identity. |
| `artifact_dependencies` | consumer_artifact_id, input_artifact_id, input_hash, purpose | Composite PK and reverse input index; includes pinned brand reference assets and policy/spec inputs. |
| `artifact_validation` | artifact_id, validator/version, result, observed_hash, media facts, findings, checked_at | Latest validation per artifact/checker; keep history where required. |
| `rights_decisions`, `approval_decisions` | artifact or episode/slot target, status, policy version, evidence/reference, actor, timestamp, superseding decision | Index target/latest; append-only audit. Current status is a derived or transactionally maintained projection. |
| `generation_requests` | id, episode_id, stage/slot, provider/model/version, prompt/template hash and exact safe prompt location, input hashes, provider request ID, cost/idempotency key, status (`prepared`, `remote_started`, `succeeded`, `failed`, `ambiguous`), timestamps | Unique provider request ID when present; `(episode_id,stage,slot,status)`. Never silently repeat an ambiguous expensive request. |
| `runs`, `stage_attempts` | run ID, episode, requested goal, actor, attempt stage/slot, state, reused artifact ID, error category, start/end | Index episode/time and run; diagnostic history, not source of completion. |
| `execution_leases` | resource key (`episode:<id>`; possibly `youtube:<channel-id>`), owner token, heartbeat, expiry | PK resource key; compare-and-set and owner-token fencing. |
| `publication_attempts` | episode, platform, exact MP4 artifact ID/hash, metadata version, expected/observed channel IDs, state (`prepared`, `remote_started`, `succeeded`, `retryable_failed`, `ambiguous`), session/request ID, video ID, error, timestamps | Unique successful episode/platform policy; index state/channel; append attempts and preserve remote history. |

The current artifact chosen for each logical requirement is an explicit selected artifact ID (a small `artifact_selections` table with `(owner,kind,slot_key)` unique and a foreign key), not "newest file". `artifact_versions` and decisions remain immutable/audited; a rejected version is superseded by a new version rather than deleted. Store specification payloads as JSON files/artifacts with schema version and checksum, plus the small indexed fields above. Versioned migrations must be deterministic, tested from an empty database and from the prior schema, and never created opportunistically in unrelated code. SQLite JSON payload changes need explicit model migration or an upcaster; silently accepting unknown schema versions is unsafe.

## I. Artifact model

An artifact is identified by an opaque ID and immutable bytes; a logical slot is `(episode, kind, scene/segment ID, variant)` or `(brand, reference role, character ID)`. The selected slot points to one version. Registration stores SHA-256, size, media type, trusted relative path, producer stage, exact provider/model/request ID, prompt and template version, acquisition/creation time, reference assets, and dependency IDs/hashes. Manual import is a producer with an operator/source URI and acquisition timestamp, not a provenance exception.

Separate four axes: **durability** (file and DB record exist), **validity** (schema/media/content checks pass for this version and validator version), **approval** (human or configured policy decision), and **rights** (`unknown`, `review_required`, `commercial_use_confirmed`, `blocked`). **Supersession** marks that a newer version is selected; it never erases old history. Generation attempt status is another axis and cannot turn a file into an accepted artifact. A file is reusable for a requirement only when selected, dependency hashes and pinned policy inputs still match, path/hash/validation pass, it is not rejected or superseded, and required review has accepted it. Publishing adds the stricter rights and QA gate across the transitive input graph. Derived MP4 rights are never presumed to cure an unresolved song, image, video or SFX right.

On every reuse, recheck trusted root, file type/size/hash, and cached validation version. A changed byte at the same path invalidates reuse. Generated assets should never overwrite accepted versions. Store evidence for `commercial_use_confirmed` (license/terms snapshot, source account/contract if applicable, reviewer and date) without committing sensitive documents or tokens.

## J. Continuation and resume

Define a versioned **requirement graph**, not a giant progress enum. Static nodes include episode concept, lyrics, song spec/audio, storyboard, render, QA, metadata and publication. The accepted storyboard creates dynamic scene-image/video requirements keyed by stable scene ID. Each node declares required upstream artifact slots and acceptance gates. A pure `plan(episode, requested_goal, snapshot)` reads one consistent DB snapshot plus revalidated file facts, walks dependencies in topological order, and returns either the first executable missing/stale requirement, a review/rights/identity hold with reasons, or complete. The runner then claims an episode lease and rechecks the plan and owner token immediately before each external call or DB selection change. Stage-attempt rows record execution, not completion truth.

* **A — scene 3 missing:** valid accepted EpisodeSpec, lyrics, song, storyboard, and scenes 1–2 remain selected. The storyboard demands scene 3; the planner schedules only that slot (and any later missing slots), then the dependent manifest/render/QA. No upstream generation repeats.
* **B — MP4 valid, upload failed before the remote request:** the publication attempt is `retryable_failed` with no remote start. Revalidate and reuse the **same MP4 artifact ID/hash** and metadata version; start a new upload attempt after gates and channel preflight.
* **C — remote outcome ambiguous:** `remote_started` or `ambiguous` blocks automatic upload. Preserve any resumable session/request ID. Query/continue the same known session if that can establish a definitive result; otherwise require documented operator reconciliation against the configured channel before any new upload. Never treat a timeout as proof of no video.
* **D — song rights unresolved:** other independent work may proceed under policy, but publication is blocked because transitive rights include `unknown` or `review_required`. No default approval.
* **E — lyrics rejected:** append the rejection with reviewer/reason; deselect that lyrics version. Preserve EpisodeSpec/objective and all old files for audit. Every selected artifact whose dependency chain includes the rejected lyrics (SongSpec/audio, lyric-timed storyboard, scenes that consume it, render, QA, metadata as applicable) becomes stale or unselected. Only artifacts with unchanged inputs can be explicitly rebound after validation and approval. A newly generated lyric version starts a new dependency branch.

An interrupted external music/image request gets a persisted request ID and `ambiguous` status when its outcome is unknown; look up that request or import/attach its completed output before paying to regenerate. If a provider offers a true idempotency key, reuse it; otherwise require an explicit new-generation decision. Crashes between file finalization and DB registration produce quarantined orphans for inspection, never automatic acceptance. Lease expiry alone does not prove that an in-flight remote side effect did not occur.

## K. Provider boundaries

| Boundary | Contract |
| --- | --- |
| LLM | `generate_typed(schema, prompt, pinned inputs) -> validated result + provider/model/request metadata`; prompts are versioned, repair bounded, safety/educational validation outside vendor client. |
| Music | `MusicGenerator.submit/status/fetch` or `ManualMusicImporter.import`; consumes `SongSpec` and refs, returns audio plus provenance and request identity. Suno is only a possible future official-API adapter. No scraping or consumer-session automation. |
| Visual | Image/video generation or manual import per `VisualAssetSpec`; consumes canonical brand references and scene instructions, returns immutable scene asset with reference IDs and provenance. No stock-video assumption. |
| Rendering | `Renderer.render(RenderManifest) -> candidate MP4 + tool report`; local deterministic FFmpeg implementation later; no publishing or creative choices inside renderer. |
| Publishing | `YouTubePublisher.preflight/publish/reconcile`; only the official API, exact MP4 hash and metadata, channel match, remote-state callbacks and typed result. |

Providers must not write authoritative stage success directly. The application validates and registers their outputs. Deterministic fakes implement each boundary for offline tests.

## L. Brand and configuration

Version-controlled YAML defines the brand, curriculum and concept IDs (red, blue, yellow, green, orange, purple, pink, black, white, rainbow/review as initial data), creative bible, characters, safety policy and prompt templates. Each episode pins source Git revision and content hashes so editing `colors.v1.yaml` or Tovi's model sheet affects only new episodes unless an explicit upgrade is chosen. Canonical Tovi references belong to brand assets with their own artifact IDs, provenance/rights and approval, and can include model sheets, expressions, poses, settings and recurring props. Add characters through data and generic IDs.

Runtime YAML covers database/data roots, target duration/resolution, provider selections, review policy, QA tolerances and expected YouTube channel ID. Pydantic rejects unknown/invalid keys. Secrets come from named environment variables or ignored local secret files, never Git or persisted prompts/logs. Store the resolved nonsecret configuration hash per run; keep secret values out of audit. Human gates are policy data (`manual`, `automatic_if_checks_pass`, or `required`) applied to objective, concept, lyrics, music, storyboard, visuals, final video and publishing; every decision records actor or policy version. Early releases default to manual for creative and publish-sensitive decisions.

## M. Rendering recommendation

| Option | Fit | Cost/risk |
| --- | --- | --- |
| A. Adapt MPT renderer | Existing guarded subprocess, 9:16 switch, output recovery and local-material option. | Its script/visual-term/stock-source workflow does not give deterministic scene-to-lyric control, song-first audio, per-scene art dependencies, transitions, text/karaoke timing or inspectable manifests without substantial fork work. MPT and its configuration/Whisper dependencies would dominate a distinct product. |
| B. Dedicated Python/FFmpeg compositor | Exact storyboard scene intervals, still-image motion, transitions, lyrics/subtitles, later karaoke highlighting, continuous song, SFX/fades, normalization and pinned 9:16 encoding. Manifest and command can be reviewed and tested. | Requires implementing and maintaining a focused FFmpeg filtergraph, font handling, Windows path quoting, media probing and fixtures. |
| C. Reuse selected MPT concepts, not renderer | Keep no-shell subprocess execution, cheap preflight, bounded output/error taxonomy, trusted-root checks and crash recovery while using B's compositor. | Small adaptation effort; preserves product-specific media control. |

**Recommend C, implemented as B.** Start with accepted still images and a continuous song; add motion, transitions and karaoke only as manifest features with tests. Pin FFmpeg version, fonts, frame rate, codec/profile, pixel format, audio codec and loudness target. Capture exact command/filtergraph and input hashes; deterministic output is expected within a pinned toolchain, not guaranteed byte-identical across FFmpeg builds or hardware encoders. Final QA should use `ffprobe`/FFmpeg to check file/container, compatible streams, portrait ratio/resolution, configured duration, non-silent audio, clipping/loudness and decode errors before selection for publishing.

## N. YouTube publication architecture

Use OAuth credentials in ignored local storage and the official YouTube Data API. Before any upload, require an explicitly configured expected Channel ID, call `channels.list(part="id", mine=true)` with authorized credentials, and fail closed unless the returned channel ID matches exactly. Google's [channel ID guide](https://developers.google.com/youtube/v3/guides/working_with_channel_ids) documents this lookup. Set `status.selfDeclaredMadeForKids=true` for this children's product; Google's [video resource](https://developers.google.com/youtube/v3/docs/videos) documents the field. Keep privacy status explicitly configured (initially private during trial) and review the separate synthetic-media field against the actual asset type rather than assuming a cartoon triggers it.

An approved `PublicationSpec` pins metadata and final MP4 artifact ID/hash. Preflight verifies channel, rights/approval/QA, hash and path, then records a `prepared` attempt. Persist `remote_started` before the first network chunk, retry only within the same known [resumable upload session](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol) when its state is knowable, and store remote video ID before marking success. A definite pre-request failure is retryable with the same file. Any lost response after remote start is ambiguous until API/session or operator reconciliation proves the outcome. Prevent duplicate success for the episode/platform by a DB constraint and fail-closed state. No Selenium, MPT auto-upload, third-party service, or credential sharing.

## O. Safety and QA

`ContentQAReport` uses stable issue codes, severity, evidence span/scene/artifact IDs, objective ID, checker/policy version, and accepted/rejected/needs-review disposition. The safety policy must cover sexual content, graphic violence or realistic injury, preschool-inappropriate fear, dangerous imitation/challenges, weapon focus, substances, manipulative purchasing and deceptive engagement, adult themes, profanity, humiliation/bullying, copyrighted-character or named-artist/franchise imitation, and high-stakes medical instruction. Uncertain findings require review; a prompt instruction alone is not a safety gate.

Educational checks compare concept/objective/vocabulary across EpisodeSpec, lyrics, SongSpec and scenes; flag wrong concept, contradictions, weak repetition, age-inappropriate or unclear words, bad pronunciation guidance, and story overload. A scene teaching red with an orange teaching object is a representable `visual_concept_mismatch` issue tied to that scene and asset. Visual consistency initially uses canonical-reference provenance plus human review; automated CV scoring is a later optional checker, not a prerequisite. Media QA is deterministic: trusted nonempty file and hash, expected MP4 container/video/audio codecs, 9:16 aspect and resolution, duration bounds, decodable streams, non-silent song, and clipping/loudness sanity. Only a current passing QA report against the exact final hash permits publication.

## P. Testing architecture

Default tests block network. Fakes return fixed typed LLM, music and visual results with deterministic request IDs, a fake renderer writes small known fixtures, and a fake publisher models preflight failure, remote start, lost response and success. Use temp SQLite/data roots and a fake clock for leases. Test migration application/upgrade, model schema versions, path traversal and reparse/symlink escape, hash changes, atomic artifact registration, selected-version dependencies, scene-3-only resume, lyric rejection invalidation, rights unknown blocking publication, exact-MP4 retry, ambiguous upload hold/reconciliation, expected-channel mismatch, and Windows path behavior. Run a few local FFmpeg fixture checks only after the compositor PR; no live Suno, image model, Ollama, NVIDIA, OpenCode, Google or YouTube calls in CI. Match donor's Windows/offline CI pattern, adapted to `main`, the new package and test set.

## Q. Future implementation phases (small PRs)

1. **Foundation:** package/config/typed core models, brand/curriculum loading, initial SQLite identities and migration tests (details below).
2. **Artifact store:** immutable file registration, trusted-root/hash validation, provenance/rights/approval decisions and recovery tests.
3. **Planner/ownership:** requirement graph, selected artifact dependencies, per-episode lease and dry `plan`/`status` CLI; cover A–E without external calls.
4. **Creative specifications:** objective/concept/lyrics/SongSpec typed generation using a fake LLM and manual review records; no live model required.
5. **Music intake:** manual audio importer, request ledger and rights evidence; later a separately reviewed official music adapter if viable.
6. **Storyboard and visual intake:** timed scene contracts, brand references and manual asset import/fake generation.
7. **Compositor:** manifest and local FFmpeg render with controlled fixtures, captions and audio; then deterministic media QA.
8. **Content QA and review:** educational/safety findings, visual review linkage, gate policy and inspection CLI.
9. **YouTube publication:** metadata, OAuth/channel identity, rights gate, official upload and ambiguity reconciliation behind fakes.
10. **Optional external generation:** one official visual/music provider adapter per PR, with terms, provenance, cost and offline tests verified before enablement.

## R. Recommended first coding PR

Keep the first implementation PR limited to **durable identity and typed boundaries**:

* Add `pyproject.toml`, `uv.lock`, `.gitignore`, `.github/workflows/ci.yml`, `src/tovitunes/__init__.py`, `src/tovitunes/config.py`, `src/tovitunes/domain/{brand,episode,artifact}.py`, `src/tovitunes/persistence/{db.py,migrations/0001_initial.sql}`, `brands/tovitunes/{brand.yaml,creative_bible.yaml,safety_policy.yaml,curriculum/colors.v1.yaml,characters/tovi/character.yaml}`, `config.example.yaml`, `tests/{test_config,test_domain,test_migrations}.py`, and `docs/DEVELOPMENT.md`. Empty canonical image slots need no placeholder binary art.
* `0001_initial.sql` creates migration history, brand revisions, episodes, artifact versions/selections/dependencies, and append-only rights/approval decisions with the identity/foreign-key constraints above. Later numbered migrations add request, run, lease, and publication tables immediately before the corresponding capability. Methods in this PR only create/read a pinned episode; artifact registration begins in PR 2, after file/hash validation exists.
* Acceptance: `uv sync --locked --extra dev`, Ruff, mypy and offline pytest pass on Windows; versioned YAML validates; an episode pins brand/curriculum/objective; migrated DB can reopen without drift; unknown rights and missing channel ID have no permissive defaults; path/slot IDs reject traversal; schema constraints prevent duplicate selected slots and orphan dependencies. Document exact local commands and ignored secret/data paths.
* Non-goals: no Suno, live LLM/image generation, song audio generation, storyboard rendering, FFmpeg, OAuth/upload, web UI, scheduler, analytics or model scoring. A migration and fake metadata fixture are not a production pipeline.

## S. Open questions requiring product or account evidence

1. What is the exact YouTube Channel ID to pin for publication, and which OAuth account is authorized to upload there? Publication remains disabled until verified.
2. Which canonical Tovi images/model sheets are approved as original brand references, and where is the supporting ownership/rights evidence? This determines the first visual asset records, not the software structure.
3. What are the commercial-use and API terms for the music provider actually chosen after the Suno experiment? No generated song should be marked rights-confirmed by default.
4. Which early creative gates may move from human to automatic, and who is the accountable approver for rights and final publication? The policy/decision model supports either choice.

No provider connectivity, rendering quality, YouTube credentials or publication behavior is claimed to work by this proposal.

