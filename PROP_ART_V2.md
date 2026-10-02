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
