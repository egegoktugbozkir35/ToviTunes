# Generic visual composition V1 review / Colors Red Pilot V2

## Defects discovered in Pilot V1

Encoded V1 frames show a polygonal lower apple silhouette, a swatch above a
screen-right pointing gesture, a ball entering in the upper half of the frame,
repeated central staging, a new pose/layout during the 0.24-second final lyric,
and an outro without lesson props. V1 media correctness was already passing.
These observations concern composition and deterministic artwork, not music.

## Generalized root causes

Layout was embedded in scene drawing and character placement independently.
Actions mapped to sprites without gesture direction; motion selected a named ball
rather than semantic ground contact. Scenes had no retained visual state, and
outro movement did not explicitly divide celebration from settling. The organic
prop silhouette used a coarse polygon and lacked a shared supersampled style.

## Generic composition policy

`SceneComposition` resolves before scene art or character animation. Nine normalized
semantic slots, primary/secondary placements, ground plane, background, sprite role,
state origin/identity/persistence, emphasis, and phases form a compact renderer plan.
The policy consumes action, prop count/semantics, duration and previous composition;
it never branches on curriculum, lesson words or particular scene IDs. Supported
prop names remain confined to the drawing/semantic registry. No storyboard language,
background generator, or renderer architecture replacement was introduced.

Present uses opposite character/object sides and preserves an unchanged single target.
Recall groups objects on the gesture side with a primary/secondary size hierarchy.
Sing makes Tovi central with a context arc above. New presentation targets alternate
sides deterministically. Safe separation is selected before fail-closed validation.

## Direction-aware layout

Renderer metadata declares `sprite/pointing` screen-right. Pointing places Tovi in
`lower_left` with the lesson target to its right; synthetic left-facing metadata
reverses this. Question uses the same directional pose and a deliberate recall group.
The canonical character pack is unchanged. The pointing swatch center is x=864,
while Tovi's slot center is x=345.6 at production resolution.

## Visual-state continuity

A scene can replace, modify, or inherit state. Existing single targets keep their
positions. Scenes without new requirements retain already introduced elements.
Recall/performance explicitly uses a display arrangement above Tovi for remembered
objects; the plan records this semantic exception to resting on the ground.
No new educational content is invented. Base meadow/background stays consistent.
`CompositionRequest.explicit_visual_reset` provides a renderer-level opt-out; the
current admitted storyboard has no reset field and is not altered.

## Micro-scene policy

The threshold is 0.6 seconds, or 18 frames at 30 fps. Below that threshold, a scene
whose required targets are already present retains the previous pose, background,
prop positions and layout, continues its motion clock, and adds a gentle vertical
pulse with zero displacement at either boundary. Amplitude is 12.672 pixels at
production resolution, under 0.7% of frame height. New necessary targets can still
appear; an explicit reset opts out. No timestamp, lyric or audio changes occur.

The final 0.24-second lyric inherits the three-prop sing state and singing sprite.
The following outro keeps those exact prop positions. On encoded frames at 29.06s
and 29.18s, upper-region (first 940 rows) RMSE is
73.344 in V1 and
0.000 in V2. This narrow check measures retained
artwork; it is not an aesthetic score or a claim that character motion is absent.

## Instrumental-tail policy

Canonical lyric end is 29.319s; storyboard/audio end is 38.164897959183676s.
The derived tail is 8.845897959184s.
Tails over three seconds resolve three phases inside the existing outro: celebration
0–2.2805s, recap 2.2805–4.561s, settle 4.561–8.845897959184s, relative to its start.
Hops use the two actual measured downbeats. Gentle non-rhythmic drift keeps phases
alive without fabricated beats. The final measured hop finishes before settling;
settling eases into a stable final ~1.07-second hold. No tail beat grid is extended.
A sampled-position QA check rejects long character scenes with no movement.

## Ground-plane motion

Ground plane is normalized y=0.92, rounded y=1766 at 1080x1920. The rolling object
enters horizontally from x=1080 toward x=702 over 0.95 seconds. Its square target
bbox is [702,1442,1026,1766]; its bottom stays on that plane at every sampled time.
The generic position function uses motion class/grounded metadata and the resolved
bbox; it does not inspect a ball or curriculum name. Ball rotation is not added.
The single presented apple also rests on this plane. Recall/sing are explicit
pedagogical display arrangements, not rolling/floating motion.

## Generic prop-style system

`preschool_soft_v1` uses shared rounded-mask, soft shadow, radial shading/tonal
contour, highlight and leaf helpers. Every prop draws at 4x its output resolution
and downsamples with LANCZOS. Fruit silhouettes use ellipses/rounded primitives;
no coarse polygon silhouette remains. The contract and style version are embedded
in scene metadata. High-resolution tests verify deterministic bytes and many
intermediate alpha levels. Visual inspection found a horizontal shading seam in an
intermediate V2 candidate; the common helper was corrected, a regression added,
and new immutable affected artifacts rendered. Intermediate bytes were retained.

## Renderer-version change

`colors_red_render_v1` becomes `tovitunes_sprite_render_v2`. Core production export
naming now derives from episode key. This pilot exports as
`TOVITUNES_COLORS_RED_001_PILOT_V2.mp4`. MoviePy 2.2.1, system FFmpeg/ffprobe 8.1,
two-stage encode/mux, manifest/dependencies, AssetStore, deadlines, media QA and
idempotent reuse remain intact. V1 is reproducible from commit
`acdb15008b91d5e0e6bddf56f365de2a8b3f49eb` and its retained immutable artifacts.

## Episode-agnostic tests

26 added cases use generic primary/secondary/abstract/rolling metadata, both gesture
directions, synthetic recall/sing/micro/outro states, explicit resets/new targets,
repeat diagnostics, several grounded timestamps, real animation boundary continuity,
shared high-resolution prop edges, and shading-seam prevention. Layout tests do not
need red, apple, ball, Colors, or a future curriculum. Negative checks reject wrong
direction, ungrounded targets, micro resets, and missing long-outro phases.

## Colors Red V1 → V2 result

Observed encoded frames and deterministic metadata support these differences.
Final visual approval still belongs to the user; technical QA is not publication approval.

| Review dimension | V1 observed/measured | V2 observed/measured |
| --- | --- | --- |
| Prop style | Polygonal apple base; arc highlights and mostly flat shapes | Smooth rounded apple; common radial shading, soft highlights/shadows, 4x antialiasing |
| Pointing coherence | Centered target above centered Tovi | Tovi left, swatch on screen-right; direction QA passes |
| Ball motion plane | Target bbox upper half, [260,370,820,930] | Grounded bbox [702,1442,1026,1766]; horizontal entrance |
| Micro-scene continuity | Sing→hopping pose and replacement swatch in 0.24s | Same sing pose/layout/three props, gentle accent; upper artwork RMSE 0 |
| Outro activity | No lesson props; measured hops then hold | Three retained props; celebration→recap→settle, stable final hold |
| Layout variety | Character centered throughout | Four deterministic composition styles; left/right/center character slots; max repeated style count 3 |
| Tovi visibility | Approved sprites, 768px perceived height | Same 768px height throughout, uniform scaling; safe bounds and separation pass |
| Audio/timing integrity | Frozen 38.164897959183676s timeline | Identical storyboard boundaries/input audio; identical AAC packet and decoded PCM hashes |
| Media QA | Passed H.264/AAC/1080x1920/30fps/full decode | Same passing format/duration/decode checks plus composition QA |

| Scene | Character slot | Composition | State | Visible props |
| --- | --- | --- | --- | --- |
| `intro` | lower_center | character_center_object_high | replace | none |
| `lyric_01` | lower_left | character_left_object_right | replace | red_swatch |
| `lyric_02` | lower_left | character_left_object_right | modify | red_swatch |
| `lyric_03` | lower_right | character_right_object_left | replace | red_apple |
| `lyric_04` | lower_left | character_left_object_right | replace | red_ball |
| `lyric_05` | lower_left | character_left_object_right | replace | red_apple, red_ball |
| `lyric_06` | lower_center | character_center_multi_object_arc | replace | red_swatch, red_apple, red_ball |
| `lyric_07` | lower_center | character_center_multi_object_arc | inherit | red_swatch, red_apple, red_ball |
| `outro` | lower_center | character_center_multi_object_arc | inherit | red_swatch, red_apple, red_ball |

Review exports include all scene midpoints, early/late outro, a contact sheet,
and side-by-side encoded V1/V2 comparisons. No perceptual AI was used.

## New artifact identities

Final selected manifest: `c8b90130-e592-4cab-8f5b-814901105a6c`.
Final selected MP4: `aa208df9-cce7-4886-97bd-3a88e624d6bf`; 2555779 bytes.
Final selected media QA: `9d799aef-fa61-4715-8a78-ab9947a3185f`.
MP4 SHA-256: `5ce825a7000b9f594df38d82ada8b3d19696fb18c1978d5d19a51f912d47be20`.
Storyboard remains `25937e67-b373-42e5-a816-9d21a732d96c`.

| Scene | Scene image ID | Character animation ID |
| --- | --- | --- |
| `intro` | `76496d9d-9d9b-46a0-99d7-8e9e3fdf10d0` | `bb299f95-ce46-4894-8753-7ef6f08cbf0d` |
| `lyric_01` | `25e51671-f975-4702-8cd1-2375298890a7` | `8190089e-2225-4f7c-a990-911861dc1fb6` |
| `lyric_02` | `b5c04d71-6216-4218-b4c6-e3c0f0600ab0` | `0a2e1fbe-008f-4100-b6fd-8a046728baa4` |
| `lyric_03` | `8ce29db6-b5c8-4090-a3ca-052054e95c9d` | `52800534-f9b7-4a8c-ab44-1e6ebcbb46f7` |
| `lyric_04` | `5af38881-af97-42ea-92a7-09871c7ed460` | `29201033-06a1-4bbe-a56f-91f881db4032` |
| `lyric_05` | `92a2aa4b-bfb6-4e8c-bbc7-a5beb0c3a932` | `a8265138-54cb-42c4-b22c-fa4cb5191801` |
| `lyric_06` | `1468b4e2-045e-4a50-b059-07de86f92da6` | `928acc4a-d354-4a80-8293-5e11336001b2` |
| `lyric_07` | `cfbfefbc-9fce-4516-81d4-91c21b20a11b` | `0f6a3704-5b80-43c6-b9ea-2ade90585d1b` |
| `outro` | `4b911eab-c98b-4cc4-b86b-bf557df02de0` | `03af8d2e-e1c6-40bc-af9b-6449dddc0dc3` |

Every original V1 artifact row/file is retained. Initial revision created all 21
V2 downstream targets. The shared-shading repair reused unchanged plans/backgrounds
and appended affected scene/final versions; it did not overwrite historical bytes.
Across the whole revision, 31 immutable
versions were added. Original 94 artifact files were hash-audited before/after; the
refinement additionally checked all 115 existing files. Protected database tables
(including music, curriculum, episodes and character pack) stayed exactly unchanged.
Only authorized downstream selection slots moved. Historical approval/rights rows
were retained; new unknown-rights ingestion rows are standard AssetStore behavior.

| Table | Before entire revision | After final render |
| --- | ---: | ---: |
| artifact_versions | 94 | 125 |
| artifact_selections | 68 | 68 |
| artifact_dependencies | 180 | 283 |
| rights_decisions | 140 | 171 |
| approval_decisions | 169 | 231 |
| artifact_validation | 94 | 125 |

## Media QA

Passed artifact `9d799aef-fa61-4715-8a78-ab9947a3185f`. Video is 1080x1920, 30 fps, H.264,
yuv420p, square pixels. Audio is AAC, 44100Hz, stereo. Container/audio duration
38.165011s; video 38.133333s. Both remain within the unchanged
0.07166666666666667s tolerance. Full FFmpeg decode succeeds. Nine original
scenes have zero gaps/overlaps. Prop counts/presence, separation, sprite alpha,
pointing direction, micro inheritance, long activity and outro phases pass.

Source audio SHA remains
`06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991`.
Encoded AAC packet SHA (both versions):
`b0d4dd1a084f4bebf4e7cf7af8b396364cc686f23317be15e3b49dc9cbb853a1`.
Decoded PCM SHA (both versions):
`8c801c4805c96a2b3c9907156956f40244770a8db4868b064f6fe897bdd1e0ab`.

## Test count

`uv run pytest -q`: 430 passed (404 original + 26 new cases).
`uv run ruff check .`, `uv run mypy src` (54 source files), and
`git diff --check`: passed. Tovi character lock: 48 artifacts, valid.
Fresh minimal installation without MoviePy: passed. Render-extra import/version and
system FFmpeg/ffprobe doctor: passed. Tiny real 270x480/two-second fixture and full
1080x1920 pilot: passed. CI keeps the tiny CPU fixture and no full pilot/provider run.

## Idempotency

Final second invocation reused all 21 selected downstream artifacts with encoding
explicitly blocked. Artifact IDs/SHA and every database table/selection/timestamp
remained exactly unchanged. Audit checked 125
existing artifact files and found no byte changes or forbidden entry-point attempts.
Evidence: `production-render-first.json`, `production-render-second.json`, both
`production-integrity-*.json`, media QA, and `V1_V2_AUDIO_AND_CONTINUITY_CHECKS.json`
in local review outputs. AssetStore remains authoritative.

## Provider audit

Lyria POSTs=0; image generation=0; video generation API calls=0; LLM runtime calls=0;
YouTube uploads=0. Local deterministic rendering only. Parent provider/network
entry points were blocked; worker audit hook rejects outbound connections/DNS.
Forbidden-entry-point attempts=0. Protected provider/music tables were unchanged.

## Rights/publication state

Music rights remain unknown. Publication remains blocked. No upload, release or
commercial-rights approval is inferred. Mouth animation remains unsupported.

PR #27 was already merged before this request began. The requested branch is retained;
a follow-up PR is required by GitHub's merged-PR workflow exception. No automatic merge.

PILOT_V2_READY_FOR_VISUAL_REVIEW
