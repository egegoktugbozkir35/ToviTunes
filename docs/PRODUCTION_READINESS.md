# Production integration acceptance — 12–13 October 2026

Current classification: **READY** for the required local production acceptance. Both
authorized real workflows completed, their final videos and metadata are verified, and
completed Yellow Resume reused its finished artifacts. All 1,082 offline tests passed.
PR #44 remains a draft and must remain unmerged for review.

## Executable dependency map

Studio POST `/api/studio/create` and `/api/studio/jobs/{job_id}/recover` submit durable
JobManager work to `build_orchestrator`. Generate reserves the run and its saved target in
one transaction; Resume uses the explicit saved run/episode. The orchestrator holds the
production execution lease and recomputes continuation before effects. Its render target
ends at metadata; publication capability alone does not add release or YouTube stages.

| Stage (implemented and integrated) | Producer and required inputs | Admission and next consumer | Tested behavior and actual live verification | Remaining risks/boundaries |
|---|---|---|---|---|
| Lesson choice and novelty | `creative/topics.py`, `topic_memory.py`; brand, learning policy, candidate batches, retained editorial history | Typed candidate safety, lexical/treatment novelty and configured optional embeddings; selected candidate feeds brief | Historical novelty/safety tests; fresh live Kimi pool repaired once, selected and reserved | No blocker; optional embedding configuration is outside this live run |
| Learning brief | `creative/learning.py`, `creative/service.py`; selected subject and policy | Persisted immutable brief, stable idea fingerprint and reserved episode identity | Reservation/restart tests; fresh live brief admitted under durable run ownership | No blocker; human editorial judgment remains available |
| Episode | `creative/director.py`, `domain/creative_drafts.py`; brief and pinned brand/cast | Creative draft compiled into versioned EpisodeSpec, response receipt/hash and immutable artifact dependencies | Creative compilation tests; retained Yellow and fresh live Kimi episode admitted | No blocker; fallback model availability is bounded below |
| Lyrics | Same creative director; admitted episode and brief | Small lyric draft compiled into source-pinned lyrics; structural/safety gates remain | Historical/creative tests; both live lyrics consumed downstream without exact matching | No blocker; no mandatory transcription or word alignment |
| Music direction | Same creative director plus `pipeline/music_adapter.py`; episode/lyrics | Technical adapter tempo and duration compatibility, stable canonical spec and source pins | Adapter compatibility tests; fresh live Kimi direction consumed by ACE-Step | No blocker; provider variation retains technical adapter gates |
| ACE-Step song | `services/music.py`, `music/ace_step.py`, `music/benchmark.py`; canonical spec and configured local service | Durable task identity, completion receipt, source hash, original WAV admission; uncertain submission cannot be resent | Retained task/restart tests; original Yellow WAV reused and fresh ACE-Step WAV generated | No blocker; uncertain remote work still requires reconciliation |
| Technical audio | `pipeline/duration_source.py`; selected master, original receipt and file | Decoded finite duration/technical metrics, matching identities/hashes; AudioClock; optional trustworthy retained beats | Corrupt-source/IEEE-float tests; both live 37-second AudioClocks admitted | No blocker; optional retained rhythm remains optional |
| Visual plan | `services/visual.py`, `domain/visual_draft.py`; creative sources plus AudioClock | Durable model response, CreativeVisualDraft, deterministic asset keys, Visual Plan V2 and critical safety/cast gates | Flexible draft/dependency tests; retained Nemotron Yellow and fresh Kimi plans consumed | No blocker; creative scenes may differ from lyrics |
| Illustration/environment art | `render/episode_assets.py`, `environment_sets.py`, Qwen ComfyUI adapter; compiled plan, workflow, approved character identity, deployed models | Durable request/source receipts; PNG decoding; safe exterior-white removal and RGBA normalization for props; valid portrait plates and bindings | Deployed-schema/restart/source tests; Yellow 5 props/4 plates and fresh 17 props/4 plates admitted | No blocker; new runtime/model changes require preflight and live validation |
| Timed storyboard | `services/render.py`, `domain/production_v3.py`; admitted AudioClock, plan, props, environment binding | Contiguous positive frame intervals cover real duration; pinned hashes and actual asset existence; V3 replay admission | V3/historical replay tests; Yellow 6 and fresh 10 scenes cover actual 37-second audio | No blocker; integrity gates continue to reject corrupt retained inputs |
| Character/prop/world/motion | `render/production.py`, `character.py`, `props.py`, `composition.py`, `motion.py`; V3 storyboard and approved pack | Pack identity, role mappings, technical composition and motion budgets, source dependency replay, Manifest V2 | 48 actual pack assets and motion envelope regressions; both live manifests and composition pass | No blocker; Yellow has three advisory dead-space observations |
| FFmpeg encoding | `render/production.py`, `render/ffmpeg.py`; composed frames and selected original audio | Bounded MoviePy subprocess, FFmpeg encode/mux, final MP4 registered only after QA | Real FFmpeg integration tests; both live H.264/AAC MP4s encoded | No blocker; encoding and retained replay are CPU-intensive |
| Final media QA | `render/qa.py`; final MP4 and authoritative duration | H.264, 1080x1920, 30 fps, yuv420p, AAC, expected duration/aspect; complete video/audio packet decoding; pinned QA identity | Real packet decode tests; both live videos pass every technical QA check | No blocker; QA establishes technical validity, with visual inspection separately recorded |
| Metadata/local completion | `creative/metadata.py`, `services/render.py`; validated render/storyboard and creative source context | Typed editorial copy compiled with Python-owned identities, bounded tags, durable response/dependencies; local target complete | Repair/reuse tests; live Yellow Nemotron and fresh Kimi metadata complete and pinned to final MP4 | No blocker; publication requires separate intent, rights and human review |

All artifacts are selected through the store, not guessed from output filenames. Completed
render/QA is an immutable reuse boundary. Historical formats retain their validators.
Remote-started/ambiguous image work blocks new prompts; returned source images can be
normalized/re-ingested without provider resubmission. Accepted ComfyUI IDs now persist before
history polling for both illustrations and environments. Music Resume queries its retained
task, never releases another task. Terminal image failures require an explicit new-attempt
authorization; the failed request and its receipt remain unchanged.

## Execution 12 root cause and evidence

The saved Studio job is `9c6bd570-0b85-4941-afc3-bf03b0df96aa`, execution 12, render target,
submitted `2026-10-12T15:10:49.421666Z` (18:10:49 Europe/Istanbul). It encountered the retained
failed image request `0fb13c98-083b-474d-9e6e-bbc42749bd61`, slot `prop_2626562d1705ffb0`.
That request was submitted at 15:09:45Z and marked definitively failed at 15:09:45.970041Z.
Execution 12 did **not** submit a second image prompt: continuation correctly stopped at its
retained failure, but exposed only `missing_assets` as evidence.

The originating adapter exception was `ProviderFailure("ComfyUI rejected /prompt (HTTP 400)",
outcome="terminal_failure")`, in `benchmark/providers.py`, `QwenComfyUIImageProvider.generate`.
The rejection branch was at line 401 in the starting checkout and is at line 507 in this change.
The initial request had no accepted `prompt_id`, no source/normalized artifact, and no returned
image receipt. Its error category/reason were not saved by the old image request transition.
The underlying exception text is reconstructed from this executable branch and the matching
ComfyUI log; the old Studio record does not contain an exception traceback.

The retained log `data/logs/services/comfyui-20261012T150908548763.log` records:

* Output `461`: `prompt_outputs_failed_validation`.
* `CLIPLoader 459:453`: `qwen3vl_8b_int8_convrot.safetensors` absent from the empty model list.
* `UnetLoaderGGUF 459:451`: `qwen_image_2.1_Q8_0.gguf` absent from the empty model list.
* `VAELoader 459:454`: `qwen_image_2.1_vae_bf16.safetensors` absent from the list containing only `pixel_space`.

All three files are installed in Comfy Desktop's shared `models` directory. ToviTunes auto-
discovery launched the ComfyUI checkout on port 8188 without its shared model-path mapping.
The independently running Comfy Desktop instance on 8189 had its own mapping. Connectivity,
response decoding, alpha normalization, environment generation, storyboard and renderer did
not cause this first failure: it happened during server prompt validation before execution.

Diagnostic defects were separate: continuation omitted request identity/category; the
orchestrator also dropped ImageStageBlocked evidence; Studio used generic text for `FAILED`
and projected earlier creative requests regardless of image stage. Progress was reset at
submission and returned before reporting retained milestones. The new projection uses
admitted retained stages, and the orchestrator reports them before a blocked return.

## Coordinated repairs

* Auto-discovered Comfy Desktop launches load a task-generated shared model-path mapping;
  user configuration and original logs are preserved. They use split cross attention and
  CPU VAE decoding, matching the installed Desktop runtime's working launch settings.
  Explicit launcher commands are respected.
* `production preflight` checks deployed ComfyUI node classes, required inputs, compatible graph
  links, model/sampler choices, numeric ranges, dynamic PNG inputs and output-node semantics.
  It performs GETs, file reads, read-only SQLite inspection and binary version checks only.
* Preflight checks actual character hashes/media, filesystem permissions, DB quick-check and
  pending migrations, MoviePy, FFmpeg/FFprobe, ACE-Step API routes/model advertisement and
  creative credential presence. Empty lazy-loaded ACE-Step model lists remain unverified;
  provider generation is never established by these checks.
  A separate read-only NVIDIA `/v1/models` audit returned HTTP 200 and advertised all four
  configured models (80 advertised models total). This establishes model-name advertisement,
  not successful generation. The live endpoint is `https://integrate.api.nvidia.com/v1`;
  the client sends streamed `/chat/completions` requests, persists response/request identity,
  parses SSE or JSON responses and retains bounded repair/failover evidence.
* Image failure categories and local request identities survive persistence and Resume.
  Illustration and environment errors have distinct safe subsystem/retry classifications.
  Public projections use application-owned text and never return provider error prose.
* ComfyUI accepted task IDs persist before polling; loopback transport ignores environment proxies.
* Explicit `production retry-image-request` grants create a new prepared request in one transaction
  under execution ownership. Migration 0028 stores immutable actor/evidence grants. Ambiguous,
  successful, source-retaining or superseded requests cannot be reopened by this command.
* Studio omits creative attempt lists for media subsystems and preserves retained progress, including
  the five creative milestones represented by the aggregate CREATIVE stage.
  Active recovery hides the prior execution's result in its public projection while preserving
  that evidence and its recovery identity in the durable job record.

## Additional live fault: CUDA attention backend

After repairing model discovery, execution 13 accepted ComfyUI task
`0c7829dc-179e-4dd3-8c42-196adcd1716b` for new local request
`42616ba3-5e08-4941-8f35-20fe2d8ed483`. It failed definitively at
`KSampler 459:458`, with `RuntimeError: cuDNN Frontend error:
CUDNN_BACKEND_TENSOR_DESCRIPTOR cudnnFinalize failedptrDesc->finalize() cudnn_status:
CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH`. The retained traceback runs through
`comfy/ldm/qwen_image21/model.py:211`, `comfy/ldm/modules/attention.py:600` and
`comfy/ops.py:97` to PyTorch scaled dot product attention. This was a runtime backend
incompatibility, after task acceptance, rather than another missing-model rejection.

The deployed runtime is ComfyUI 0.39.2, PyTorch 2.12.1+cu130 on an RTX 4070 Laptop
with 8 GB VRAM. Split cross attention avoids the incompatible cuDNN SDPA path; CPU
VAE decoding follows the Desktop runtime's existing memory settings. No package,
checkpoint or user configuration was replaced. The failure history/log and accepted
task identity were preserved. A second explicit immutable grant created request
`b8e6d420-3533-4856-a218-3d052f096efa`; execution 14 uses that request. Successful
live illustration output verifies this repair beyond schema preflight. Its safe
diagnostic category is now `runtime_incompatible` with configuration required.

## Additional live fault: near-opaque environment alpha

Execution 14 generated all five props, then accepted environment task
`fd0a2e96-5674-4216-9b60-e4034a889e94`, local request
`b944e7b2-020c-4ad2-a098-7f54beb5369c`. The 768x1376 returned PNG is a complete
landscape with RGBA alpha extrema 247–255. `_validate_image` rejected it with
`ValueError("environment plate contains transparency")`; its strict 255-only check
was incompatible with the deployed Qwen RGBA VAE. The receipt records returned
bytes/hash/dimensions and a definitive local validation failure.

For Qwen alone, environment normalization requires mean frame alpha of at least
250/255 (98% opacity) and composites it onto white to produce an opaque RGB plate. Substantial
transparency, bad dimensions, blank/black images and invalid bytes remain blocked.
Other providers retain the strict gate. Raw provider source ingestion now precedes
normalization so a local rejection cannot discard usable returned work.

Migration 0029 and `production reconcile-environment-source` record an immutable
operator reconciliation of that exact returned hash/size, admitted source and
normalized plate. Resume and V3 admission verify this separate evidence; the original
terminal receipt remains byte-identical. This performs no provider request and
rejects ambiguous/unreturned sources. Yellow execution 15 reuses the reconciled
first environment and generates only missing plates. Two further local rejections
established why a pixel-minimum rule is inappropriate for Qwen's RGBA VAE: the
second/third complete landscapes had mean opacity 254.36/254.35, but isolated minima
237/215. Their returned hashes were also explicitly reconciled, without rewriting
their failed receipts or submitting new prompts. Execution 17 reuses all three and
generates the fourth plate. The final admission rule measures frame opacity, preserving
the substantial-transparency gate instead of chasing each isolated minimum.
The integrated test reproduces
the old rejection, rejects a wrong hash, reconciles, renders through QA/metadata,
and proves only four environment prompts were sent in total.

## Additional live fault: widest approved pose exceeds its lane

Execution 17 admitted the fourth environment and built the V3 storyboard. Renderer
validation then raised `ValueError("prop motion occludes Tovi")` from
`render/motion.py:792` in scene `visual_004`. Tovi's idle sequence includes a wide
approved pose. `pose_keyframe` scaled every pose to the base character height;
`attach_poses` constrained its anchor to frame margins but did not constrain its
width to the reserved character lane. The inward shift crossed the left prop lane.

The renderer now provides the lane width to `attach_poses`. When necessary it
uniformly reduces the entire pose sequence and base sprite to one shared perceived
height, preserving the bottom-center anchor and original approved sources. It then
applies the existing frame clamp and all motion/overlap/camera/prop checks. Every
stored scale and size remains reproducible from its approved crop. A new regression
reproduces the old overlap, proves safe fitted motion, and verifies shared height
and foot anchoring. Motion/visual-story tests: 46 passed in 7.19 seconds.
Renderer validation failures now receive a safe renderer subsystem/category and
recoverable state while their full traceback remains in local logs. No provider
work is repeated for this repair; execution 18 resumes the retained storyboard/art.

Execution 18 passed the overlap check, then rejected `visual_005` with
`ValueError("major motion budget exceeded")`. Three new props had independent
simultaneous pop events alongside the camera: four major motion groups, above the
unchanged limit of three. Story props now receive sequential pop arrival windows;
each holds the initial pop scale until its assigned window, avoiding a scale jump.
The regression checks independent non-overlapping arrivals, sampled state and the
same motion-budget/envelope validator. No regrouping masks the physical motion count
and the budget is not increased. Motion/visual-story suite: 47 passed in 6.96 seconds.
Execution 19 resumes the retained storyboard and all existing provider outputs.

Executions 19/20 reached the final scene, `visual_006`, and the overlap validator
rejected a character arrival. Read-only replay of the actual stored composition,
approved poses and motion tracks established the cause: Tovi was assigned the right
lane but every entrance started offscreen left, crossing the three left-side props.
Entrances now use the nearest edge of their assigned lane. Pose fitting preserves
that edge, and the layout validator admits both fully offscreen edges. The generic
foreground edge accent was narrowed to stay below the arrival foot envelope.

The cross-stage audit also repaired two further general cases: reused primary props
enter the upper performance lane before traversing a centered character; and three
comparison props receive three distinct lanes rather than duplicating the right lane.
Comparison pose changes wait until the coordinated arrivals finish. Each repair has
an envelope regression, including short/long three-object comparisons and both arrival
edges. All six actual Yellow scene plans now pass read-only replay of the same motion
validator, with approved real pose media.

Historical IEEE-float WAV recovery exposed a scheduling regression: staggering two
ordinary prop pops could overlap an admitted keyword emphasis plus a pose change.
Ordinary two-prop entry timing is preserved; three-prop/character-arrival sequences
are staggered. Optional camera movement becomes static only when necessary to yield
to admitted keyword and object timing. Fixed keyword intervals and the motion budget
remain unchanged. Historical float recovery plus motion/visual-story suite: 64 passed
in 56.58 seconds; final expanded motion/visual-story suite: 53 passed in 16.87 seconds.

Studio's structured blocked-job projection now avoids expensive full media replanning
on every status GET. Recovery submission carries its known progress while the worker
revalidates the retained artifacts under ownership. Three safe diagnostic projection
tests passed in 17.14 seconds. Execution 21 resumes the retained storyboard and media.

## Safe operator commands

```powershell
uv run --no-sync python -m tovitunes.cli --config config.yaml production preflight
uv run --no-sync python -m tovitunes.cli --config config.yaml production retry-image-request --request-id 0fb13c98-083b-474d-9e6e-bbc42749bd61 --confirm-definitive-failure --actor local-operator --reason "Recorded HTTP 400 prompt validation rejection; shared Qwen model paths repaired"
```

The retry grant sends no prompt. A subsequent authorized render-only Resume performs missing
image work. Keep `publication_enabled: true`, `publication.youtube.enabled: true`,
`automation.auto_publish: false`, `automation.require_human_review: true`. Neither run has
publication intent; no private upload, public upload or promotion is authorized.

## Verification and acceptance

Ruff, strict Mypy (121 files), 48-asset character lock and diff checks pass. Focused production,
Qwen, Studio and migration suite: 82 passed in 303.63 seconds. Additional accepted-task identity,
deployed-schema preflight, safe diagnostics and shared-model discovery checks pass.
Final full offline suite: **1,082 passed**, one existing Starlette TestClient deprecation warning,
in **1,712.68 seconds (28 minutes 32 seconds)**. Exact command:
`uv run --locked --no-sync pytest -q -o faulthandler_timeout=120 --tb=short`.
Exit code zero; 21 tests were added to the original 1,061-test suite. This includes real
FFmpeg encoding/QA, process restart, accepted-task interruption and all historical formats.
Dedicated character `validate-lock` passes with 48 assets; `assess` reports ready with no issues.
Yellow execution 21 completed the actual render target at 100%, including live Nemotron
metadata. Its six-scene, 37-second MP4 is H.264, 1080x1920, 30 fps, yuv420p with stereo AAC
at 48 kHz. Every stream check, complete packet decoding, character layout, composition and
the unchanged three-group major motion budget passed. All six scene midpoint frames were visually
inspected: approved Tovi identity, actual Qwen props and landscape plates are visible, with
no previously reported character/prop overlap. Three presentation scenes retain advisory
dead-space warnings; these are editorial observations, not failed technical admission.

Selected final render: `db807fff-9779-45d7-8232-d77d504b8314`, SHA-256
`54a78e6e61706d79c69e57bc02e46e0d25977aae67d9b611fa57356753ab53fb`, 13,366,271 bytes.
QA: `ee4e53bd-7755-4ba7-a0bd-4b56512fc101`. Metadata:
`b43ff920-c1cb-4477-9a02-d89bdcc9fa02`; its final-render ID and SHA match the selected MP4.
The Yellow music binding remains `6f4595ff-2ebd-4ac2-99f1-4d2332725824`.

Execution 22 completed a second render-only Resume. Before/after counts were identical:
45 generation requests, 4 music requests, 13 environment requests, 204 artifact versions,
1 original publication attempt and zero visibility events. All three selected finished
output IDs were identical. No redundant provider request, re-encoding or metadata generation
occurred. Revalidation is CPU-intensive and took several minutes on this machine.

Fresh Studio job `52084389-c03b-465b-992e-a76b7688f2da` was submitted only after that reuse
check passed, with explicit `target=render`. Its live Kimi subject pool initially returned
one four-word example outside the existing typed topic contract; the single bounded repair
succeeded (`a547de98-d229-4246-be85-b6abcd63d255`, provider ID
`chatcmpl-964ae5aa-3856-4dbf-b62f-60737804f7bf`). Novelty selection reserved the new episode,
and the normal episode-writing request followed. No creative response or status was edited.
Fresh episode `680754d0-8bcd-4461-b9d9-95234e8e7e1d`, key
`name-the-sounds-of-a-dog-cat-and-duck-f67720b4-001`, then admitted first-response Kimi
episode, lyrics and music-direction drafts. Normal ACE-Step generation succeeded without
manual reconciliation: local request `0987ec1c-47dd-4025-a246-e6c81774109c`, task
`693f76bb-a846-4bd9-b4bf-cf67cf449a50`, 37-second stereo IEEE-float WAV, 14,208,088 bytes,
SHA-256 `9a0f2a4affb6fed7a66db2ab8e7749e8ac8056e12443b41a9eb79c9b3b41a19e`.
The stored receipt and actual file hash/size match. Audio admission advanced to visual
planning without mandatory ASR. Exactly one new music request was added (five total).
The fresh job completed execution 1 at 100%, through the normal autonomous path: live
Kimi visual planning, all 17 required Qwen illustrations and all four renderer-role
environment plates, a ten-scene V3 storyboard, composition, encoding, QA and metadata.
The four environment roles belong to the renderer contract; they do not impose a fixed
creative scene/environment count. No fresh image retry, source reconciliation, manual
response editing or status override was needed. All accepted task IDs and source receipts
are durable. The normal environment path verifies the final alpha repair independently
of Yellow operator reconciliation.

The initial live metadata response was retained as `succeeded_response_invalid`:
`metadata references another concept absent from the video`. The normal bounded
repair completed without changing the metadata policy or editing the response.

Fresh video: H.264, 1080x1920, 30 fps, yuv420p, stereo AAC at 48 kHz, 37 seconds.
Every stream/duration check, full packet decoding, character layout, composition and
the unchanged three-group major motion budget passed. All ten scene midpoint frames
were inspected; approved Tovi and actual generated props/environment plates are visible.

Selected final render: `f7f2b46f-47e7-47e5-9017-9b85b46faf8b`, SHA-256
`b26e0d494b5c6c605e99a0757e199d30558ee9d4cfbfe0ea435b2de416bcdc3a`, 15,016,347 bytes.
QA: `bd8d3eba-fbc0-4c75-9259-932054913a47`. Metadata:
`0bfc9a95-fc90-4ad3-b91d-313615605d2e`, from request
`9784f2ca-806d-4469-b2a2-ee0fe4b7b2b8` / `chatcmpl-f022476e-5f95-45e3-a1fe-6d2a826a604b`
using `moonshotai/kimi-k3`. Metadata final-render ID and SHA exactly match
the selected MP4. Both outputs have `made_for_kids: true`; metadata does not upload them.


Before any production mutation, a SQLite-safe backup and original-row/file-hash baseline were
saved outside the repository. Original Yellow WAV SHA-256:
`2caace3c7033c8643167167772cf2ae222faa31e57c401b4f099eff2a57173cc`, 14,208,088 bytes.
Untracked `config.yaml` is protected and remains excluded from commits. Original database
records and request evidence are compared against the saved baseline. After both completed live acceptances,
all original rows in 13 protected tables remain unchanged, all 156 original artifact files
match their registered hashes/sizes, and the WAV/config hashes match. No statuses were
forced. The original music request and WAV were not regenerated. Exactly one fresh music
request was added. Publication attempt and visibility-event deltas are both zero.

## Test inventory and verification boundaries

The 21 additional collected cases cover deployed ComfyUI object-info graph/model validation,
GET-only preflight with an unchanged database hash, shared-model-path discovery, accepted
prompt identity before interruption, definitive retry grants and immutable failure evidence,
source-hash reconciliation and bounded environment alpha, safe Studio diagnostics/progress,
wide approved poses with a shared fitted height, both entrance edges, sequential three-prop
arrivals, three comparison lanes at three durations, and reused performance props. These
extend the actual orchestrator/store/Qwen adapter/storyboard/FFmpeg/QA/metadata harness in
`tests/test_creative_production_v3.py` and `tests/test_short_production.py`. Tests use isolated
databases and real representative image/audio media. Historical tests remain enabled.

The final preflight reports `prerequisites_passed: true`, zero provider effects, no pending
migrations or checksum mismatches, 48 valid character assets, actual MoviePy and FFmpeg 8.1,
both Qwen graphs compatible with deployed schemas/checkpoints, and ACE-Step's configured
model advertised after its successful live generation. Its top-level `NOT VERIFIED` and
`not_verified_without_live_call` fields deliberately describe the preflight's own scope:
read-only inspection cannot establish generation. The separate completed acceptance receipts,
source hashes and decoded videos establish the READY classification in this report.
Workflow SHA-256: `fafd7fbc8bc2488e57bfe1b0a7e3a313804acd2798eabc6625e10f7550c5fa9e`.

Actual NVIDIA generation was verified for Kimi in the fresh path and Nemotron in retained
Yellow planning/metadata. GLM and Deepseek names were advertised by the real endpoint;
their fallback/response contracts were tested offline, but no live generation by those two
models is claimed. Endpoint/model/driver changes can invalidate a future deployment and
should be checked again. Provider latency was substantial (each Qwen image took minutes);
progress retains the correct stage rather than promising an artificial time estimate.
Remote-started/ambiguous operations still block new submissions until their retained
evidence is reconciled. These protections remain intentional operator boundaries.

No required local acceptance blocker remains. Publication and YouTube capability stayed
enabled, auto-publish stayed false and human review stayed required. Neither acceptance
invoked release, private upload, public upload or promotion. Rights, channel identity and
review gates remain active; YouTube publication itself was not part of this authorization
or live verification.

## Deliverable evidence and review state

The local delivery folder contains both final MP4s, complete QA and metadata JSON, sixteen
inspected scene-frame tiles in two contact sheets, acceptance/source-preservation evidence,
the completed-output reuse comparison, per-request live stage receipts, selected artifact
IDs, final read-only preflight, NVIDIA model advertisement and the full offline test log.
The selected source files and exported MP4 copies have identical registered SHA-256 hashes.

The implementation belongs to `refactor/creative-production-v3`. PR #44 remains open,
draft and unmerged: https://github.com/egegoktugbozkir35/ToviTunes/pull/44 . The delivered
report records the final pushed source commit and checked PR head; this committed report
is versioned by that commit rather than attempting to include its own hash.
