# Prop Art V2 report

## Baseline

- Starting `origin/main`: `5e94a05e6f1f0f711805c7a0211197eaa5b33c79`
- Branch: `codex/prop-art-v2`
- Pull request: `#33`, `feat: add reviewed lesson-object assets`
- New style version: `lesson_object_assets_v2`
- Historical style retained: `preschool_soft_v1`

## Original rendering problem

The historical swatch, apple, and ball all passed through the same `sphere_shading()` and
`soft_highlight()` pipeline. That made the teaching swatch look inflated, reduced the apple to a
ball-like silhouette with a leaf, and gave all three lesson objects essentially the same glossy
material language.

## Architecture

The renderer now supports three per-prop strategies registered on `PropDefinition`:

- `deterministic_swatch`
- `reviewed_asset`
- `legacy_procedural`

Provider sources and normalized renderer PNGs use the existing `AssetStore`; no parallel asset
registry was introduced. Normalized objects are brand-owned `lesson_object` artifacts keyed by
semantic `slot_key`, with immutable provider evidence stored as `lesson_object_source`. Existing
approval, rights, dependency, SHA, and selection gates remain authoritative. Production activates
`lesson_object_assets_v2` only when its reviewed manifest is approved and selected. Motion plans
continue to consume semantic keys and bounding boxes only.

The resolver verifies current approval/selection through `AssetStore`, immutable SHA validity,
semantic slot identity, PNG/RGBA mode, a non-empty alpha bound, the normalized 1024 by 1024 canvas,
and bottom-center ground contact. Render metadata carries the selected artifact ID, SHA-256, anchor,
strategy, and the independent authoritative `lesson_color` value.

## Deterministic swatch

The v2 swatch is generated reproducibly from `#E53935` with a softly rounded paper/card silhouette,
a restrained inset edge highlight, mild linear top tone, and soft shadow. It does not call
`sphere_shading()` or `soft_highlight()`. The center surface remains exactly RGB `(229, 57, 53)`.

## Provider generation

- Intended provider: Google Vertex AI
- Intended model: `gemini-3-pro-image`
- Location: `global`
- Requested size: `2K`
- Requested aspect ratio: `1:1`
- Explicit generation flag: `--confirm-provider-generation`

### Exact call audit

1. `red_apple`: one live request attempted after local tests and PR CI passed; Vertex AI returned
   HTTP 403 before image bytes or a provider response ID were returned.
2. `red_ball`: not called. The two-call ceiling left only one possible request after the apple
   failure, which is insufficient to produce exactly one canonical candidate for both objects.

Totals:

- Gemini image requests: 1
- Successful Gemini image responses: 0
- NVIDIA requests: 0
- Lyria requests: 0
- YouTube requests: 0
- Automatic aesthetic retries: 0

## Generated artifacts and transparency

No provider image bytes were returned. Therefore:

- Apple source artifact ID: not created
- Ball source artifact ID: not created
- Apple normalized asset ID/SHA-256/dimensions: not created
- Ball normalized asset ID/SHA-256/dimensions: not created
- Alpha validation: not run against live output
- Review state: no generated artifact exists; nothing was approved or selected
- Rights state: no generated artifact exists; intended initial state remains `review_required`
- Contact sheet: not created because canonical apple and ball candidates do not exist

The code rejects non-PNG, non-RGBA, opaque, empty-alpha, or non-transparent-corner provider output
with `LESSON_OBJECT_TRANSPARENCY_BLOCKED`; it performs no chroma keying or segmentation.

## Verification before the provider attempt

- Full pytest: `575 passed, 10 skipped`
- Ruff: passed
- Strict mypy: passed for 70 source files
- Character lock: valid, 48 artifacts
- `git diff --check`: passed
- Pull-request CI: passed on Windows, run `36980355110`, job `110753234860`
- Provider calls made by tests/CI: 0

Coverage includes deterministic swatch reproduction, proof that v2 swatch rendering does not use
sphere shading, reviewed resolver behavior, missing/unapproved failure, SHA tamper detection,
RGBA/alpha bounds, bottom-contact normalization, scale/rotation compatibility, apple fall and
bounce motion, ball roll/grounding, prop/Tovi separation, semantic identity retention, and legacy
`preschool_soft_v1` readability.

No pilot render was started.

PROP_ART_V2_BLOCKED

## Multi-candidate generation attempt — 2026-10-03

### Scope and implementation

This continuation used PR head `a15e1e49db73b3053a74760ce5eaa397185f6205` on existing
PR `#33` / branch `codex/prop-art-v2`. It added a narrow, separately invoked
`lesson-objects generate-candidates` path for up to four immutable review candidates per semantic
object. Candidate sources and technically valid normalized assets use distinct AssetStore slots
such as `red_apple_candidate_01`; the existing canonical reviewed-asset resolver, approval flow,
selection flow, renderer, deterministic swatch, and lesson color remain unchanged.

The candidate path hard-limits a run to eight explicitly enumerated requests, uses no retry loop,
retains provider source evidence before transparency validation, leaves every retained artifact at
`review_status = pending` and `rights_status = review_required`, and records separate source and
normalized identities, hashes, dimensions, MIME type, request IDs, usage, validation state, and
generation time. Regression coverage verifies the eight-call ceiling and order, distinct slots,
pending review/rights state, rejected-source retention, continuation after a technical rejection,
contact-sheet layout, and immediate termination after a first-candidate access failure.

### Authorized run configuration

- Run time: `2026-10-03T13:16:38+03:00` (Europe/Istanbul)
- Provider: `google`
- Model: `gemini-3-pro-image`
- Resource project: `project-e3968bf9-fde5-4d99-aea`
- Location: `global`
- Requested resolution: `2K`
- Requested aspect ratio: `1:1`
- Fresh generation budget: at most 8 requests
- Intended order: four `red_apple` candidates, then four `red_ball` candidates
- First local request ID: `3b20c3594edccfd28090f4de76f94c414ab673afc7f4e959eb41392b3aaaa9be`

### New-run provider-call audit

- `prepared`: 1
- `remote_started`: 1
- `succeeded`: 0
- `terminal_failure`: 1
- `retryable_failure`: 0
- `ambiguous`: 0
- `technically_rejected_after_success`: 0
- `actual_live_image_requests`: 1

The first fresh request was `red_apple` candidate 01. It returned a terminal access failure. The
critical stop rule was applied immediately; candidates 02–04 and all `red_ball` candidates were not
prepared or started. No automatic retry occurred.

### Safe Vertex diagnostics

- HTTP status: `403`
- Canonical status: `PERMISSION_DENIED`
- Sanitized provider message: `Lightning dunning decision is deny for project: projects/74415701220`
- ErrorInfo reason: unavailable
- ErrorInfo type: unavailable
- Provider request ID: unavailable
- Model: `gemini-3-pro-image`
- Location: `global`
- Endpoint family: `Vertex AI Gemini generateContent`

No token, credential, ADC content, cookie, Authorization header, arbitrary response header, or raw
provider response body was printed or persisted.

### Candidates and review output

The provider returned no image bytes, so no source or normalized candidate artifact was created.
Consequently there are no candidate artifact IDs, SHA-256 values, dimensions, MIME types, usage
metadata, alpha-validation results, review decisions, or rights decisions for this run. No artifact
was approved, selected, or ranked. `outputs/PROP_ART_V2_REVIEW.png` was not created because there is
no technically valid new apple candidate and no technically valid new ball candidate.

The deterministic V2 swatch remains unchanged and was not regenerated. No pilot was rendered.

### Cumulative Prop Art V2 provider totals

Across the immutable historical attempt and this continuation:

- Gemini image requests: 2
- Successful Gemini image responses: 0
- Terminal failures: 2 (both HTTP 403; only the new response retained structured diagnostics)
- Technically rejected successful responses: 0
- NVIDIA Kimi requests: 0
- Lyria requests: 0
- Video-generation requests: 0
- YouTube requests: 0

### Validation

- Full pytest: `583 passed, 10 skipped`
- Ruff: passed
- Strict mypy: passed for 70 source files
- Character lock: valid, 48 artifacts
- `git diff --check`: passed
- Tests and validation provider calls: 0
- GitHub CI: passed on Windows for implementation head `19893c1`, run `37116086662`, job
  `111183070084`

PROP_ART_VERTEX_ACCESS_BLOCKED

## Temporary local Qwen candidate provider — 2026-10-04

Prop Art V2 can use `lesson_object_generation.provider: qwen_comfyui` independently of the frozen
environment generator. The local ComfyUI endpoint is `http://127.0.0.1:8188`. Launch ComfyUI
with `--use-split-cross-attention --cpu-vae`. The model stack is
`qwen_image_2.1_Q8_0.gguf`, `qwen3vl_8b_int8_convrot.safetensors`, and
`qwen_image_2.1_vae_bf16.safetensors` with ComfyUI-GGUF installed. The saved API graph at
`workflows/qwen_image_2_1_t2i_api.json` was extracted from the exact API prompt embedded in a
successful local 20-step PNG. Its real node IDs and inputs are validated before submission.
The separately supplied ComfyUI API export is preserved at `review/QWEN_EXPORTED_API_WORKFLOW.json`.
It has the same node IDs, types, links, and model stack; its only input-value difference is a saved
32-step sampler setting. The active 20-step workflow preserves the exact live-run workflow hash.

Settings are 1024 × 1024, batch 1, 20 steps, CFG 1.0, Euler, simple scheduler, and an explicit
seed derived from each candidate's canonical request identity. The provider posts once to
`/prompt`, polls `/history/{prompt_id}`, and retrieves the recorded output through `/view`.
Prompt ID, seed, settings, and workflow SHA-256 are retained with each candidate. There are no
hidden retries.

The exact opaque PNG returned by ComfyUI is stored unchanged as `lesson_object_source` before
preparation. A deterministic edge-connected flood removes a verified, uniform near-white exterior;
interior white details are preserved. Unsafe backgrounds produce a technically rejected candidate
while retaining the raw source. Normalization then produces the usual RGBA lesson-object candidate.
Human review remains pending and rights remain `review_required`; nothing is selected automatically.
This local backend is temporary pending Azure integration.

### Local review run

On 2026-10-04, the two-call smoke generated one apple and one ball. After both passed technical
validation, the bounded review run generated four apples and four balls. The review run audit is
`8 prepared / 8 remote-started / 8 succeeded / 0 technically rejected / 0 ambiguous / 0 retried`.
There were ten local Qwen generations in total across the smoke and review runs; the first apple
and ball were reproduced with the same seeds. There were no Google, Azure, or paid image calls in
this continuation. Workflow SHA-256 was
`fafd7fbc8bc2488e57bfe1b0a7e3a313804acd2798eabc6625e10f7550c5fa9e`.

The final review sheet is `outputs/PROP_ART_V2_REVIEW.png`. Review copies of the sheet and the
exact eight-candidate CLI audit are committed under `review/` for PR review. The candidate manifest
artifact ID is `69a105a0-7151-4aa9-83bb-264af9c3a3b7`. All eight source PNGs and eight
normalized RGBA candidates passed SHA and asset-store integrity checks. Each is still pending
human review with `review_required` rights and no selected pointer. The local AssetStore contains
the immutable full-resolution source and normalized artifacts. Human choice and rights clearance
are outstanding.

## Human-reviewed canonical selection — 2026-10-04

Human review chose `red_apple_candidate_02` and `red_ball_candidate_02`. The explicit
`lesson-objects finalize-review` command validated both pinned candidates, sources, SHA-256 values,
PNG/RGBA dimensions, alpha bounds, bottom contact, Qwen provenance, candidate-manifest membership,
and source dependencies before selecting anything. It then selected each source, candidate, and
byte-for-byte canonical copy in dependency order. The selected canonical manifest activates
`lesson_object_assets_v2` in the existing renderer. The exact decision and actor/reason are in
`review/PROP_ART_V2_FINAL_SELECTION.json`; the earlier review sheet, audit, and candidate manifest
remain unchanged.

| Object | Reviewed source ID / SHA-256 | Reviewed candidate ID / SHA-256 | Canonical ID / SHA-256 |
| --- | --- | --- | --- |
| Red apple | `2a4bdf50-1985-4817-8c7d-5914f07ed908` / `2da9d6061712061e45ce2255599e696f9bb77f91662f20140127e899d54cf2b2` | `5f976552-0d62-45ed-89ab-fbd6801056f3` / `0c8a2d7e3c57042e618e2fbebbe688165779e7d10d152c3edde31a686eb848b0` | `d9d2adb6-3753-4c63-86ca-7a7954b8ec6e` / `0c8a2d7e3c57042e618e2fbebbe688165779e7d10d152c3edde31a686eb848b0` |
| Red ball | `1623fb15-6c0b-4b1c-99c2-10c33e8d2ff4` / `6f1c5bfb3af891287adc03053c3b1b4aea9780f6ef327579e8098d3bd52f495e` | `ea342196-5aa1-473b-ad34-a1929f0b2ba7` / `fa15e44535a885602c963f97183f6284c827b9e090a53b01cfc58dd703de165a` | `2999861d-41aa-4479-acba-b1d405527f13` / `fa15e44535a885602c963f97183f6284c827b9e090a53b01cfc58dd703de165a` |

The selected canonical manifest is `2e5b6568-3126-4ae8-9580-2b2b22c89507`, SHA-256
`bd7fa0db33b3797997c3dcf57acd728705472e74a8560bf3be9be389e7854613`.
The source, candidate, canonical object, and canonical manifest each have an appended human visual
approval under `lesson_object_human_v1`. Every one of them retains rights `review_required`.
Commercial-use clearance and release remain separate. This finalization made **zero** new image
generation or provider calls.

### Frozen Colors–Red V4 smoke

The local candidate store initially had no episode. The frozen episode, selected music/storyboard,
Tovi pack, and selected V4 environment were copied from the existing local production store into
this branch's AssetStore without regenerating them. The original production store and its older
render outputs remain intact. A backup of the candidate store precedes the local merge. Existing
derived render versions were left in that older store because their dependency pins belong to the
legacy prop selection; the new V4 artifacts were rendered from the frozen inputs and the selected
canonical lesson objects.

`production render-v4 --episode-key colors-red-001` passed media QA at 1080 × 1920 for 38.165
seconds. The final MP4 is `outputs/TOVITUNES_COLORS_RED_001_PILOT_V4.mp4`, artifact
`bdda4b54-c487-429c-837e-94e52e7d4af3`, SHA-256
`e0ce789eb49a40471dd1927ef19d9d9239e4b031fd052893fa25b30afebffafa`.
Render manifest `ddf6f790-86fa-479d-9318-88c3f077ce0e`, SHA-256
`982d116c052a2432ab0b6e0e0262bcf22039c4449527073c979addf88388e9e3`, pins the
canonical manifest. Scene PNG metadata records `lesson_object_assets_v2` and the exact canonical
IDs/SHAs: apple in `lyric_03`, `lyric_05`–`lyric_07`, and `outro`; ball in `lyric_04`–`lyric_07`
and `outro`. The red swatch records `deterministic_swatch` in its scenes. The representative
`outputs/TOVITUNES_COLORS_RED_001_PILOT_V4_SCENE_CONTACT_SHEET.png` shows all nine scenes;
`review/PROP_ART_V2_PILOT_V4_SCENE_CONTACT_SHEET.png` is its checked-in review copy.

A second run through the default `config.example.yaml` reused every render artifact, passed QA,
and recorded `provider_calls: 0`. No Qwen `/prompt`, Google, Azure, OpenAI, NVIDIA image, or other
image-provider call occurred during finalization or rendering. Visual review is approved; all four
lesson-object artifact levels retain `review_required` rights. Publication remains blocked until
commercial rights and the wider release gates are cleared.
