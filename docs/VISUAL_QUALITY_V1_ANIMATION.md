# Visual Quality V1: supported character performance

This iteration continues draft PR #45 from verified remote head
`aa7a4ab0c0485fb4e1309e88adafb42f3b318fcf`, stacked on draft #44. It changes only
the opt-in strategy to `tovitunes_visual_quality_v1_r3`. The accepted production
renderer remains the default. Neither PR is to be merged by this work.

## Root cause and transition choice

The compositor multiplies the outgoing approved sprite's alpha by the inverse
smoothstep and the incoming sprite's alpha by smoothstep over 0.12 seconds.
Those are independent illustrations, with different heads, crests, wings and
body outlines. Uniform height and a shared bottom-center anchor do not make
their contours coincide. Alpha compositing two partially transparent drawings
therefore produces doubled outlines, visible through one another, rather than
an in-between pose. Bigger, clearer framing makes this defect more obvious.

The reported Animals 14.18s and Yellow 30.60s samples show conspicuous doubled
faces/crests. Animals 23.18s is near the tail of a blend; adjacent native frames
are included in the dense transition inspection. The baseline has 42 Yellow
and 71 Animals output frames inside overlapping pose-alpha intervals. This is
an exact scheduling count, not a claim that all those frames are equally bad.

New opt-in keyframes use the existing `cut` transition. No opacity overlap or
fabricated in-between drawing is introduced. The pose remains fully readable
until its replacement appears. The decision applies to the approved pack as a
whole: neutral/pointing, pointing/singing, greeting/hopping and celebration
silhouettes all differ enough that a shorter dissolve would retain the same
failure for fewer frames. Repeated identical poses are omitted by the planner.
Historical crossfade defaults and the compositor's crossfade implementation
are unchanged. Historical V1 and V1-r2 manifests retain their meaning; V1-r2's
visible-foot contact-shadow behavior is explicitly retained alongside r3.

## Timing and supported performance

The existing planner now uses admitted scene intent, previous pose, composition
and optional measured downbeats rather than replacing every performance with
neutral → pointing → neutral. It still uses only five approved sprite roles.

- A first scene greets, then directs attention. A later farewell greets in
  place and resolves to a quiet neutral hold, without pointing again.
- Discovery acknowledges the object once and holds. A direct presentation can
  start in the pointing pose. A retained presentation starts from the preceding
  pose instead of resetting to neutral at every scene boundary.
- Musical intent already present in descriptions (singing, duet, dance or
  call-and-response) can use the approved singing illustration, including
  scenes whose unsupported action was conservatively compiled to `idle`.
  This represents a supported musical reaction, never paw swishes, grasping,
  walking cycles, eye animation or mouth articulation.
- Singing has a small whole-body vertical bob (maximum 0.24 of the existing
  amplitude, about 5.5px here). Measured beat evidence is used when present.
  Duration-only audio gets an explicitly free 1.8-second bob, not invented
  musical or word-level timing. The motion eases into and out of each hold.
- Celebration gets one restrained hop, with 0.16 seconds of tiny anticipation,
  a 0.72-second zero-velocity landing and time to settle. It does not hop on
  every beat. Teaching holds keep Tovi's feet still.

The existing long-scene activity check now accepts an approved pose change or
an active visible lesson presentation during an intentionally stationary
character hold. It still rejects an inactive long scene. The default renderer's
positional-activity requirement is unchanged. Motion-envelope, non-overlap,
grounding and the maximum of three simultaneous major motions remain enforced.
Full singing/hop intervals are included in the existing activity accounting.

## Continuity and preservation

The existing story `continuity_source` records adjacent scenes sharing the
same environment role, character lane, ground and scale reservation. The motion
planner checks timeline adjacency, retains the prior final camera transform
and pose, and omits a fresh character entry effect. Its dependency includes
the prior motion artifact. Different plates or character staging keep deliberate
composition cuts and their existing camera choices. No continuity subsystem
or new schema was added.

All six Yellow scenes share their retained kitchen plate and character anchor;
five boundaries now retain the camera and pose. Animals changes plates and
lesson arrangements deliberately. Its scenery override remains the explicit
review-only `play_path=meadow_wide` substitution used in the baseline.

Tovi remains fitted at 695px in all sixteen scenes. No framing, layout, source
art, original alpha, color, proportions, physical placement or approved pack
was changed. Contact shadows remain foot-aligned. New behavior is versioned in
the existing dependency/content-addressed artifact path; accepted production
artifact identities are not replaced.

Read-only before/after snapshots cover all 52 production tables (3,289 rows),
292 registered artifact files and the configuration hash. All 238 original
selections remain in each isolated preview copy. Original music, source art,
approved pack, successful/uncertain provider receipts and publication records
are preserved. No provider calls, regeneration or publication are authorized
or performed by these preview runners. No Studio/job/ownership code is changed.

## Source-art assessment, separate from animation

The retained Animals illustrations still mix styles. This work does not make
the flat outlined puppy/duck artwork match Tovi's dimensional shading.

`render/episode_assets.py:image_spec` already asks for soft tonal contours,
gentle dimensional shading, warm even light and controlled saturation, and
discourages hard black outlines. `render/environment_sets.py:environment_spec`
likewise asks for dimensional shading and a coherent world. Qwen's existing
text-to-image adapter has no image-reference capability. Individual creative
descriptions can imply different styles, independent generation varies, and
normalization/semantic checks establish usable imagery rather than an artistic
match. A textual style request is not evidence of visual consistency.

No additional prompt change is needed to explain or fix pose ghosting, and no
prompt was changed in this iteration. For a separately authorized future art
trial, a small shared direction could clarify: "Keep comparable tonal depth
across animals and props; reserve graphic flatness for explicitly conceptual
cards." Apply it through the current shared visual-direction path, preserving
freedom in subject, setting and composition. Evaluate the results together with
Tovi at final phone size. This recommendation, and the existing dimensional
style guidance, remain **unvalidated against fresh Qwen generations** here.

## Review evidence and validation

The local handoff contains both complete 37-second 1080×1920, 30fps previews,
full synchronized before/after MP4s (left = aa7a4ab0, right = r3), native frames
at the three reported times, slow comparison clips, all-scene start/mid/end
sheets, dense samples around the union of old/new transition timestamps and
complete chronological progression sheets. Final media QA, test results, CI
status and editorial observations are recorded in the PR description and the
local review handoff; a draft PR does not imply approval to change the default.
