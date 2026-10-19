# Gate 0 branch and file ownership contract

Status: **PENDING OWNER APPROVAL**. Boundaries below implement only the six gates
in [Locked Roadmap v1.0](LOCKED_ROADMAP_V1.0.md). They create no implementation task,
branch, provider authorization or concurrent worker. Gate 0 is sequential.

## Branch targets and order

| Gate | Future branch boundary | Base / PR target and prerequisites |
|---|---|---|
| 0 | `codex/v2-gate0-baseline` (created) | Exact #49 SHA `790da5502a966675b94a32838e303a97fb16503e`; draft PR targets `fix/native-comfy-crash-recovery`. Docs and audit evidence only. |
| 1 | `codex/v2-gate1-reliability` | After explicit Gate 0 acceptance, pin the accepted Gate 0 SHA; isolated branch/PR targets `codex/v2-gate0-baseline`. |
| 2 | `codex/v2-gate2-art-assets` | Same accepted Gate 0 SHA/target; may overlap Gate 1 only in time and only on distinct files. Shared-file sequencing below overrides parallelism. |
| 3 | `codex/v2-gate3-creative-music` | Starts after Gate 2 prompt/asset contracts freeze; pin and base/target the owner-accepted Gate 2 head including required accepted shared-file commits. |
| 4 | `codex/v2-gate4-render-studio` | Starts after relevant Gate 2/3 interfaces stabilize; pin/base/target the owner-accepted Gate 3 head including required reliability commits. |
| 5 | `codex/v2-gate5-integration` | Only after Gates 0–4 pass and owner authorization. New isolated worktree from accepted Gate 0 integration line; integrate accepted heads once by verified ancestry. PR targets that integration line; any later main merge sequence requires separate owner approval. |

These are reserved names/targets, not existing implementation branches or authority
to merge. Later task headers must pin full base SHAs, permitted files, gate number,
acceptance checks, forbidden changes, separately approved provider budget and stop
condition. No task automatically dispatches its successor.

## Exclusive areas (paths relative to repository)

Abbreviated Python paths below are rooted at `src/tovitunes/`; bare filenames
continue the immediately preceding package directory. Test filenames are rooted
at `tests/`. The shared-file table takes precedence over every area listing.

| Gate owner | Source files / bounded area | Checks owned for that area |
|---|---|---|
| 1 — GPU and durable reliability | `src/tovitunes/web/supervisor.py`, `web/launcher.py`, `execution.py`, `persistence/leases.py`, `persistence/requests.py`, `persistence/creative_reconciliation.py`, `render/image_recovery.py`, `render/environment_recovery.py`, `creative/recovery.py`, `creative/visual_recovery.py` | `tests/test_generate_resume_ownership.py`, `test_mpt_architecture.py`, `test_creative_reconciliation.py`, `test_ace_step_float_recovery.py`, `offline_reservation_process.py`, `offline_resume_process.py`; coordinated shared recovery tests below. |
| 2 — image / environment / admission | `src/tovitunes/benchmark/providers.py`, `render/environment_sets.py`, `render/lesson_objects.py`, `render/props.py`, `render/environment.py`, `render/visual_evidence.py`, `artifacts/media.py`, `artifacts/store.py`, `workflows/qwen_image_2_1_t2i_api.json` (existing model/workflow contract only) | `tests/test_qwen_art_quality.py`, `test_qwen_comfyui.py`, `test_lesson_objects.py`, `test_teaching_cues.py`, `test_artifacts.py`, `test_visual_benchmark.py`, `tests/fixtures/comfyui_object_info_0392.json`; historical fixture bytes remain frozen unless an explicitly approved gate task requires an additive version. |
| 3 — creative / educational / music direction | `src/tovitunes/creative/prompts.py`, `director.py`, `factory.py`, `failures.py`, `fake.py`, `learning.py`, `metadata.py`, `models.py`, `nvidia.py`, `ollama.py`, `provider.py`, `resilience.py`, `service.py`, `similarity.py`, `topic_memory.py`, `topics.py`, `validation.py`; `pipeline/creative.py`, `pipeline/music_adapter.py`, `pipeline/duration_source.py`, `pipeline/production.py`; `music/ace_step.py`, `music/audio.py`, `music/benchmark.py`, `music/models.py`, `music/providers.py` | Existing creative/director/fallback/metadata/topic/NVIDIA/retired-model/music/ACE-Step/audio/configuration regressions as applicable. No new model, mandatory ASR or automatic evaluator. Brand creative/safety policies require the same scoped Gate 3 ownership if explicitly listed in its task. |
| 4 — compositor / motion / Studio | `src/tovitunes/render/admission_v3.py`, `character.py`, `composer.py`, `composition.py`, `ffmpeg.py`, `layout.py`, `models.py`, `motion.py`, `production.py`, `qa.py`, `story.py`; `services/render.py`; `web/app.py`, `web/__main__.py`, `web/studio.py`, `web/services.py`, `web/diagnostics.py`, `web/jobs.py`, `web/static/app.js`, `index.html`, `styles.css`; `progress.py`, `run_history.py`; `review/visual_quality_preview.py`, `review/teaching_cue_preview.py` | Existing render/composition/motion/visual-quality/visual-story/production-storyboard/Studio/web/Node frontend tests. Publication tests remain mandatory. No default change before Gate 5 acceptance. |
| 5 — final integration / acceptance | `.github/workflows/ci.yml`, `docs/v2-gate0/*` acceptance records and final operator docs; integration resolutions in the explicitly accepted changes above | Full exact-head hosted Windows checks and unchanged historical/publication/recovery coverage; one separately authorized frozen three-category batch. No new integration feature stream. |

Package `__init__.py` files and modules not required by these deliverables remain
unchanged. `music/analysis.py`, `analysis_models.py`, `analysis_runtime.py`,
`timing_runtime.py`, `vertex_lyria.py`, and historical benchmark/domain utilities
remain compatibility dependencies; a later edit requires an explicit allowed-file
entry inside the responsible approved gate, never a cleanup task. Character
intake/lock/PNG/pack modules and `brands/tovitunes/characters/tovi/**` are preserved;
their validators may be run, but the approved pack cannot be changed.

## Shared-file restrictions (whole-file exclusivity)

Disjoint functions in one file do **not** permit concurrent edits. A task may touch
a shared file only while it holds the recorded exclusive gate ownership. Record
file, gate, branch/base SHA, intended change and handoff SHA in the task/PR. Before
handoff the earlier gate commits and stops editing; the next gate incorporates the
exact commit and verifies the relevant checks. Uncommitted cross-worktree copying
and independent concurrent changes to a shared module are forbidden.

| Shared file(s) | Sequential ownership / integration rule |
|---|---|
| `src/tovitunes/render/episode_assets.py` | **Gate 1 first** for successful-source durability and submission boundaries, then **Gate 2** for prompt/normalization/cue admission. Gate 2 may work on its other files in parallel but waits for this file's explicit handoff. Gate 1 cannot edit it after handoff. |
| `src/tovitunes/services/visual.py` | **Gate 2 sole initial owner** for asset/environment contracts. Any Gate 1 dependency change is supplied as a reviewed requirement and applied by that owner after coordination; Gate 3 consumes the frozen interface only after Gate 2 stops editing. |
| `src/tovitunes/domain/visual_plan.py`, `domain/visual_draft.py` | **Gate 2 → Gate 3**, sequential schema/prompt compiler ownership. Preserve historical serialized schemas/fingerprints and explicit cue fields. Gate 4 consumes them after freeze. |
| `src/tovitunes/orchestrator.py`, `continuation.py`, `preflight.py`, `services/context.py`, `services/music.py` | **Gate 1 → Gate 2 (only needed admission wiring) → Gate 3 → Gate 4**, with explicit whole-file release at each step. GPU handoff is Gate 1; later consumers must retain its ownership/recovery contracts. |
| `src/tovitunes/cli.py`, `config.py`, `persistence/db.py`, `persistence/migrations/*` | One sequential integrator: **Gate 1 → Gate 2 → Gate 3 → Gate 4 → Gate 5**, only for changes required by each approved deliverable. No production migrations, unrelated schema design or config mutations follow from this allocation. Existing migrations remain checksum-frozen; any necessary new migration must be separately scoped. |
| `src/tovitunes/errors.py` | **Gate 1 → Gate 2 → Gate 3 → Gate 4 → Gate 5** for required shared failure categories only; preserve historical meanings and release the entire file between gates. |
| `src/tovitunes/artifacts/store.py`, `artifacts/media.py`, `benchmark/providers.py` | **Gate 2 is the sole initial source owner**, despite use by other subsystems. Gate 1 supplies any required reliability change as a coordinated requirement; no concurrent edit is permitted. Later changes require a recorded sequential handoff. |
| `src/tovitunes/catalog.py`, `domain/artifact.py`, `domain/review.py`, `domain/brand.py`, `domain/character.py`, `domain/curriculum.py`, `domain/episode.py` | **Gate 5 integration custodian**, frozen compatibility contracts by default. Any earlier necessary in-scope interface edit must first receive an explicit sequential whole-file allocation; ownership is never inferred from an import. Approved pack identities and roadmap exclusions still forbid changes. |
| `src/tovitunes/domain/production_v3.py`, `domain/storyboard.py`, `domain/creative.py`, `domain/creative_drafts.py` | **Gate 3 → Gate 4** after Gate 2 contracts freeze. Gate 2 declares needed interface changes first; one gate applies them sequentially. Do not alter old serialized meanings. |
| `tests/test_creative_production_v3.py`, `test_short_production.py` | **Gate 1 → Gate 2 → Gate 3 → Gate 4 → Gate 5**, same handoff rule. Run existing tests in all branches; only the holder may edit. No test weakening or fixture replacement. |
| `tests/test_studio_v1.py` | **Gate 1 → Gate 4**: reliability checks first; Studio ergonomics after handoff. |
| `src/tovitunes/web/jobs.py`, `web/diagnostics.py`, `web/services.py`, `progress.py`, `run_history.py` | **Gate 1 → Gate 4**: execution/readiness/recovery truth first, then Studio presentation. The Gate 4 listing above is not permission to overlap Gate 1 edits. |
| `tests/test_config.py`, `test_preflight.py`, `test_migrations.py` | **Gate 1 → Gate 2 → Gate 3 → Gate 4 → Gate 5**, only the exclusive holder edits; all branches may run unchanged checks. |
| `pyproject.toml`, `uv.lock`, `tests/conftest.py` | **Gate 5 integration owner**, frozen by default. Earlier gates can request only an explicitly in-scope, owner-approved required change; pause all competing edits first. No optional ML extras or infrastructure added by inference. |
| `src/tovitunes/publication/*`, `youtube/client.py`, release/YouTube tests | Preserved safety contracts. **Gate 5** owns final regression/integration resolution only; any necessary Gate 4 UI-facing adjustment must be explicitly allocated sequentially. No auto-publication or publication-policy relaxation. |

Gate 5 must use `merge-base --is-ancestor` and exact diff review before integrating
an accepted head, including any shared commits already incorporated by handoff.
Passing branch checks never grants permission to merge existing PRs #44–49.
