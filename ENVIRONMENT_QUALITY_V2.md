# Environment Quality V2

## Starting point and scope

Work started from merged `origin/main` SHA
`e52a4870378afc19951c99abbb8dcd8f1e954377` (PR #31) on branch
`codex/environment-quality-v2`. The renderer, VisualStoryPlan,
SceneMotionPlan, deterministic lesson props, Tovi assets, approval/selection
workflow and V4 rendering architecture were not redesigned. No Pilot V5 or
replacement full-pilot render was produced.

The historical production environment set remains immutable:

- manifest artifact: `0663d01d-c660-48e4-93eb-9a2f03adbfe3`
- manifest SHA-256: `faa18016359b1696f26df86d3b4685ec78f8431b3c09dd796e1db41917be65b0`
- model recorded by its plates: `gemini-3.1-flash-image`
- current review/selection state: approved and still selected

## Configuration and provider contract

`RuntimeConfig` now contains a strict, frozen `environment_generation` block:

```yaml
environment_generation:
  provider: google
  model: gemini-3-pro-image
  location: global
  image_size: 2K
```

Only Google and the admitted Flash/Pro model IDs are accepted. Unsupported
providers, arbitrary model IDs, non-global Pro locations, and non-1K use of the
historical admitted Flash contract fail validation. The generation fingerprint
includes provider, model, location, requested image size, theme, prompt version
and the four-role sequence. The premium fingerprint is
`f9a61ef02d06da303ae5447b9bc04a87088c8e799e2d4bfeddcb34c825dd35c7`;
the immutable request-set key is `preschool-world-v1-f9a61ef02d06da30`.

The Vertex `generateContent` translation pins:

- endpoint location: `global`
- model: `gemini-3-pro-image`
- response modalities: `TEXT`, `IMAGE`
- image config: aspect ratio `9:16`, image size `2K`
- tools/grounding: none
- maximum SDK retry attempts: one
- reference input: no image for `meadow_wide`; its successful retained 2K
  source artifact for each of the other three roles

Google's [Vertex model card](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-pro-image?hl=en)
documents the stable model as globally available with 1K/2K/4K image output.
Google's [image-generation dimension matrix](https://ai.google.dev/gemini-api/docs/generate-content/image-generation?hl=en)
lists the 2K 9:16 contract as 1536x2752. Production validation now checks the
requested model/size contract instead of reusing a Flash-specific minimum-size
assumption. Every response was normalized to the renderer's unchanged
1080x1920 PNG plate.

## Offline plan

The provider-free plan reported provider `google`, model
`gemini-3-pro-image`, location `global`, requested size `2K`, four roles,
`meadow_wide` as master and the three downstream reference relationships. It
reported `provider_calls=0` and `live_calls=0`; it loaded no credentials and
made no network request.

## Live generation and immutable artifacts

Date: 2026-10-01. The one explicitly confirmed attempt ran only after local
verification and PR CI passed. It made exactly four remote starts and no
aesthetic retries. All four requests succeeded. No Attempt 2 exists.

New environment-set manifest:

- artifact ID: `60c18523-e127-4fe5-82c2-5f3eb3628866`
- SHA-256: `93b5a308cd0c79b9deeae491a5fbf21ffe255af377ccf0a69eb1b1209f98142c`
- schema: 2
- provider/model/location/size: `google` / `gemini-3-pro-image` / `global` / `2K`
- review state: `pending`
- selection state: unselected; the Flash set remains selected

The higher-resolution provider responses are retained as immutable
`environment_source_plate` artifacts. The renderer-facing artifacts remain
normalized `environment_plate` PNGs.

| Role | Source artifact / SHA-256 | Source dimensions | Normalized artifact / SHA-256 | Normalized dimensions |
| --- | --- | --- | --- | --- |
| `meadow_wide` | `e1aaa450-437a-4255-ada1-56f080de919c` / `7a974bf479d2fb567c72d94ddaad170e1f19bbe9e627469a93b9a1f6f47546f4` | 1536x2752 | `7523d3cb-f319-4bd5-a185-0cc79439740e` / `dc5bc71db9d8adeab482bb183c71365795354d20b0ec3aed89fdf7bbc8d4ea83` | 1080x1920 |
| `lesson_garden` | `e906f527-9217-4827-bef0-5af5f5e4f234` / `e102017eaaa9f4cbb8cb3ecc88647edf4c71b6ed32d22910d4289a35417c6d3f` | 1536x2752 | `18af564f-3615-40d1-95bf-9ad27545c136` / `6caa313e53e78d9ffd342b8778791e3ab66488894ad5a8dc4eb2e67a907e70e5` | 1080x1920 |
| `play_path` | `f46e4c77-87a9-4b1b-8dd3-9f0c29f82550` / `1ea83719c2f8b1a6b4b3df1768dce76f3ddaf78911bc197e26de4ff0ed5f3d3e` | 1536x2752 | `85c22600-7bf2-400f-a277-c7dfe93f78ac` / `e2a6fb0ea3e4f76d5cb5a70eaccf375d1aec5f81eb17d5d10d06a6aeea778cc9` | 1080x1920 |
| `celebration_meadow` | `c3dd122b-3965-443e-8178-2e6087ea19ef` / `36f94c16916bfc8268c70e8a0bee1d82784e36a731e96c5ff5227af506a685c2` | 1536x2752 | `343b335b-686b-4c26-b1ce-fe7198b92102` / `5dd9688656b097b81d43da22ea39965d42d6b425dbe9a0b1dcbd3466aed70a44` | 1080x1920 |

The last three source/normalized pairs pin
`e1aaa450-437a-4255-ada1-56f080de919c` as their world-style reference.

## Request and usage audit

| Role | Local request ID | Provider request ID | Response bytes | Prompt / image / thinking / total tokens |
| --- | --- | --- | ---: | --- |
| `meadow_wide` | `05b0a2ab-5478-4134-80c0-d68fde64d958` | `Fmq-arrmG-X81PIP3b-N0AE` | 4,289,504 | 348 / 1,120 / 313 / 1,781 |
| `lesson_garden` | `fa49c5e3-4888-4ec5-826d-80b8a7b3a666` | `Mmq-avnvHP6B_NUPh7WN8Ak` | 4,311,050 | 933 / 1,120 / 250 / 2,303 |
| `play_path` | `d9c8e246-1c78-4ce9-a7b6-b44790f2bc11` | `T2q-arK9KKSP1PIPtaKMyAc` | 4,320,024 | 934 / 1,120 / 250 / 2,304 |
| `celebration_meadow` | `b8d26c6a-d47e-470c-ae45-d23cfeade2b4` | `bWq-arSNAaSP1PIPtaKMyAc` | 4,430,574 | 930 / 1,120 / 338 / 2,388 |

Aggregate usage metadata: 3,145 prompt tokens, 4,480 candidate image
tokens, 1,151 thinking tokens and 8,776 total tokens. Every response reported
on-demand traffic, one PNG image, `gemini-3-pro-image`, 2K, global, technical
validation passed, and the source/normalized dimensions above. Response
SHA-256 values equal the retained source-artifact SHA-256 values.

Provider-call audit for this premium run: prepared **4**, remote started **4**,
succeeded **4**, terminal failure **0**, retryable failure **0**, ambiguous
**0**, aesthetic retries **0**. Tests/CI made **0** provider calls. Contact and
comparison export made **0** provider calls. No Kimi, Lyria, video-generation,
YouTube, publishing or rendering provider was invoked.

## Human-review exports

- `outputs/PILOT_V4_ENVIRONMENT_PRO_CONTACT_SHEET.png`
  - SHA-256: `18b3ef4782a05b831c65dcff563e3efac9d066a5bdf5ffe849bf96de12446590`
- `outputs/PILOT_V4_FLASH_VS_PRO_ENVIRONMENT_COMPARISON.png`
  - SHA-256: `ef6dd43d8ed6f7a13152a3c5532deabd70d7e5ca74c6bfdb13e5f7316c370645`

The comparison is role-for-role, Flash on the left and Nano Banana Pro on the
right. No artistic approval, rejection or selection was recorded.

## Verification and CI

Local locked Python 3.11 verification:

- `uv run pytest -q`: **580 passed**
- `uv run ruff check .`: passed
- `uv run mypy src`: passed for **69 source files**
- Tovi character lock: **48 artifacts**, valid
- `git diff --check`: passed

PR #32 Windows CI passed the implementation commit `ddacf0d` in 11m44s. CI
used no provider credentials and made zero live image calls.

## Rights and publication state

The four retained Pro sources, four normalized plates and environment-set
manifest all have rights state **unknown**. Technical validation and a pending
review artifact do not establish licensing or publication permission.
Publication remains blocked. The set has not been approved or selected.

PREMIUM_ENVIRONMENT_SET_READY_FOR_HUMAN_REVIEW
