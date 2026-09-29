# Dynamic preschool renderer V1 / Colors Red Pilot V3

## Starting main SHA

`10d3f24c1874575a51a1c26a025b30ba690b2621`, freshly fetched `origin/main`.
Includes merged PR #28 (generic visual composition) and #29 (Kimi Creative Director).
Branch: `codex/dynamic-preschool-renderer-v1`. No automatic merge.

## Renderer version

`tovitunes_dynamic_render_v1.1`. Camera policy `gentle_camera_v1`, motion grammar
`preschool_motion_v1`, keyword policy `canonical_target_words_v1`, environment
`playful_meadow_v2`. No new dependency; Pillow, MoviePy 2.2.1 and system FFmpeg remain.

Initial development exposed MoviePy's offscreen composition-mask bug and an unattached
prop-position callback. The opaque composition boundary and callback were corrected;
the rendered-pixel regression now checks the actual lesson slot and empty sky corner.
The corrected worker received a distinct renderer identity before its final encode.
Intermediate immutable versions are retained; only the reviewed final IDs below are selected.

## Why V2 felt static

V2 solved staging, timing and provenance, but most educational props were baked into
the scene PNG. One approved pose occupied each scene, with small motion around its
resting position. The static meadow and absent camera motion limited sustained activity.

## Motion architecture

The existing TimedStoryboard → SceneComposition → character animation → MoviePy →
FFmpeg mux → media QA path is preserved. A deterministic SceneMotionPlan resolves
between composition and animation. It contains one camera track, approved pose cues,
prop tracks, depth-specific ambient tracks, exact keyword bindings, entry/exit policy,
activity intervals and retained outro phases. Models contain no MoviePy objects.

Camera, prop and pose functions operate on generic action, physical metadata, duration,
composition and admitted timing. They do not branch on Red, apple, or scene names.
Decorative phase seeds hash storyboard artifact ID + scene ID + renderer version.

## SceneMotion artifact

Each scene persists immutable JSON under `kind=scene_motion`, `slot=<scene_id>`.
Dependencies pin the storyboard, BeatAnalysis, AudioAlignment, background/composition
artifact and every approved full-body sprite used. Micro scenes additionally pin the
inherited motion artifact. Character-animation JSON explicitly embeds its motion ID;
the manifest pins all scene-motion IDs, all animation IDs and every used sprite SHA.
This prevents identical animation bytes from accidentally inheriting changed dependencies.

Thirty selected downstream artifacts cover nine backgrounds, nine motion plans, nine
character animations, the manifest, final MP4 and media QA. AssetStore is authoritative.
V2 artifact bytes, source pack, audio, alignment, beats and historical database rows
are retained. The original V2 export still hashes to
`5ce825a7000b9f594df38d82ada8b3d19696fb18c1978d5d19a51f912d47be20`.

## Layered environment

New scene images are background plates with no baked lesson objects. The same meadow
contains a soft sky gradient, rounded distant hills and grass plane. Transparent clouds,
flowers and corner leaves occupy far background, mid background and foreground planes;
props and Tovi share the clear lesson/character plane. `wide`, `lesson_focus`,
`performance` and `celebration` variants keep the episode in the same world.
Foreground accents stay outside Tovi and lesson-object envelopes.

## Parallax

Clouds use slow 22–25 second tracks, flowers an 8 second track, and the foreground
leaf a stronger opposite drift on a 10 second track. Movement amplitudes remain small.
Singing adds one quiet note; celebration adds one sparkle. These low-alpha decorations
are excluded from the major-motion budget. Sprites, props and halos are drawn once per
scene and then transformed; expensive drawing does not run on every frame.

## Camera tracks

Supported behaviors: static, slow push/pull, gentle left/right pan and focus push.
Intro pushes in, teaching focuses toward the primary prop, question/outro pull out,
and performance pans gently. Production zoom is 1.02–1.04, with pan at most 0.8%.
Overscan is checked against zoom at sampled times. The camera transforms the complete
opaque composition once, preventing border exposure. One track serves each scene.

## Tovi pose sequencing

PoseKeyframe pins role, artifact ID, alpha crop, scalar scale, size and cut/crossfade.
Only hello, neutral_full_body, pointing, hopping and singing are permitted. The first
pose cuts; later transitions crossfade over 0.12 seconds (validated range 0.08–0.16).
All crops share a perceived height and bottom-center anchor; the widest crop adjusts
the shared resting anchor inward when necessary. V3's height cap is 38% of the frame.
No mirroring, rigging, parts overlay, deformation, new Tovi artwork or mouth animation.

Enter returns from hello to neutral. Present uses pointing when its direction matches
the composition; a right-side character uses an approved greeting reaction instead.
Point/question return briefly to neutral. Singing retains its singing pose. Outro
changes hopping → hello → neutral. There are 13 within-scene pose changes.

## Prop motion grammar

Reusable grammar supports pop_in, gentle_bounce, pulse, wiggle, roll_in, float_in and
settle. Production entrances pop 0.75 → 1.06 → 1.00 over 0.4 seconds. Pulses reach
1.07, combined reactions cap at 1.09, bounce is 1% of height, and wiggle stays within
±4°. Grounded props scale around their base. The tested 0.95 second nearest-edge
horizontal roll is retained; rotation is omitted to preserve visible ground contact.
Intentional character/rolling arrivals are the only declared temporary edge exceptions.
Existing `preschool_soft_v1` supersampled artwork is unchanged. 18 prop-motion
events appear in the final plan. Question objects react individually; the performance
objects receive sequential emphasis while retaining the primary-object hierarchy.

## Keyword-synchronized emphasis

Episode target_vocabulary is compared with admitted AudioAlignment.words using exact
case/punctuation-normalized lexical equality. Each supported occurrence creates one
KeywordEmphasisEvent with original word index, vocabulary item, word start/end and
primary prop key. No fuzzy matching or invented times. QA checks occurrence coverage
and exact word intervals. There are 10 target-word events in this pilot.
A bounded pulse plus low-alpha rings reacts over the measured interval. No subtitles.
Synthetic tests cover multiple Red and Blue occurrences and reject unrelated words.

## Beat synchronization

Pose changes and secondary prop reactions prefer nearby actual measured downbeats.
Scene fractions are explicitly tagged when no useful downbeat exists. Singing bob
uses the admitted beat slices; no beat grid is fabricated or extended into the tail.
Every beat does not trigger a major effect. Recap timing is tagged as a semantic phase.

## Activity budget

Maximum three simultaneous major motions. Overlap QA counts distinct camera,
character, lesson-object or foreground motion groups. Synchronized object entrances
form one coordinated lesson entry; later per-object reactions count independently.
Keyword reactions share the affected object's group. Persistent small decor is ambient.
Plans exceeding the budget or declaring a different budget fail. Scenes over 2.5 seconds
require activity; over 4.5 seconds require at least two meaningful events. Constant
character bob is not an activity event. Diagnostics measure execution, not retention.

## Micro-scene behavior

The existing 0.6 second continuity threshold is retained. The final 0.24 second lyric
inherits the preceding pose sequence, camera clock, background variant and prop boxes,
without entry/reset. It adds only its admitted target-word pulse and existing small
character accent. Its camera finishes the inherited track instead of restarting.
The stored inherited-motion ID makes this state reproducible.

## Outro behavior

The original 8.845898 second instrumental tail keeps its admitted scene boundaries.
Celebrate: 0–2.2805 seconds. Recap: 2.2805–4.561. Settle: 4.561–8.845898, relative
to the tail start. Approved hopping/greeting reactions and a quiet sparkle lead into
individual learned-object pulses. The camera holds from settle; the sparkle fades,
props rest, ambient movement freezes near the end, and neutral Tovi eases to its
existing final stable hold. No new teaching objects or invented tail beats.

## V2 → V3 implementation comparison

| Dimension | V2 | V3 |
| --- | --- | --- |
| Environment layers | Static meadow artwork | Background plate, separate far/mid decor, ground and foreground accent |
| Camera motion | Fixed frame | One bounded semantic camera track per normal scene |
| Tovi pose changes | One full-body pose per scene | 13 within-scene approved changes with common base/height |
| Animated props | Grounded ball entrance; other props baked | All props separate; 18 entrance/reaction events |
| Target-word reactions | No aligned-word visual channel | 10 exact admitted-word pulse/halo bindings |
| Ambient animation | Static environment | Cloud drift, flower movement, foreground drift; restrained note/sparkle |
| Long-scene activity | Small character motion/drift | Explicit activity minimum and overlap maximum |
| Outro activity | Character celebrate/recap/settle | Pose changes, sequential object recap, decor and camera settling |
| Media QA | Format, decode, composition and provenance | Same checks plus motion, keyword binding and envelope QA |

## Final V3 render ID

Final render: `6fe77b2c-e285-42b4-90e4-fd8769d9c7fc`.
Manifest: `eef20082-64c6-41b8-9f6d-dcb30b87ba40`.
Media QA: `3e603f93-743f-45d9-8ff9-6454d575d86b`.
Admitted storyboard remains `25937e67-b373-42e5-a816-9d21a732d96c`.

## Final SHA

MP4 SHA-256: `f753990b3ebce60940177896818f8ddf22374e5a7d171f00648942bdc737793b`. File size: 5618399 bytes.
The code commit is the head of the associated PR; this SHA identifies the media artifact.

## MP4 path

`outputs/TOVITUNES_COLORS_RED_001_PILOT_V3.mp4` in the rendering checkout, also exported
to the current chat's outputs directory. Eleven encoded review frames and
`COLORS_RED_PILOT_V3_CONTACT_SHEET.png` are supplied. Six silent 360×640 review MP4s
cover intro, teaching object, ball, question, singing and the complete outro under
`outputs/pilot_v3_motion_review/`. The full MP4 is authoritative for audio/animation review.

## Media QA

Passed: 1080×1920, 30 fps, H.264, yuv420p, square pixels; AAC, 44.1 kHz, stereo.
Container/audio duration 38.165011 seconds; video duration 38.133333. Both fit the
unchanged 0.071667 second tolerance. Full FFmpeg decode succeeds. All nine scenes
cover the unchanged timeline without gaps/overlaps. Motion envelopes sample camera,
every approved pose crop, prop entry/reaction/rotation extents and decoration collisions.
Maximum major overlap is three. Source master SHA remains
`06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991`.
V2/V3 AAC packet SHA and decoded PCM SHA are exactly identical, verified separately
in `V2_V3_AUDIO_INTEGRITY.json`. Audio was not trimmed, stretched, faded or retimed.

## Visual activity diagnostics per scene

50 activity records total. Counts describe planned rendered behavior, with no
engagement score or prediction of viewer retention. The micro scene records its
inherited camera policy while creating no new camera movement.

| Scene | Seconds | Activity | Pose changes | Prop events | Keywords | Ambient | Camera | Max major overlap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| intro | 2.462 | 3 | 1 | 0 | 0 | 4 | slow_push_in | 2 |
| lyric_01 | 4.463 | 7 | 2 | 2 | 2 | 4 | focus_push | 3 |
| lyric_02 | 4.222 | 5 | 2 | 0 | 2 | 4 | focus_push | 2 |
| lyric_03 | 4.423 | 6 | 2 | 2 | 1 | 4 | focus_push | 3 |
| lyric_04 | 4.623 | 6 | 2 | 2 | 1 | 4 | focus_push | 3 |
| lyric_05 | 4.443 | 8 | 2 | 3 | 2 | 4 | slow_pull_out | 3 |
| lyric_06 | 4.443 | 8 | 0 | 6 | 1 | 5 | gentle_pan_right | 3 |
| lyric_07 | 0.240 | 1 | 0 | 0 | 1 | 5 | gentle_pan_right | 1 |
| outro | 8.846 | 6 | 2 | 3 | 0 | 5 | slow_pull_out | 3 |

Full intervals and camera parameters are exported in
`PILOT_V3_VISUAL_ACTIVITY_DIAGNOSTICS.json`; exact motion JSON and manifest exports
are supplied under `scene_motion/` and `PILOT_V3_RENDER_MANIFEST.json`.

## Idempotency

Second actual production invocation reused all 30 selected
downstream artifacts. Encoding was explicitly rejected by the audit wrapper on that
run, so no re-encoding occurred. Final render/manifest/media IDs and SHA match.
Every database table, selection and timestamp remained identical; all
191 existing artifact files remained byte-identical.
Evidence: `production-render-first.json`, `production-render-second.json`, and
both `production-integrity-*.json`. Failed development attempts retained immutable
intermediates instead of overwriting prior versions; the final successful run appended
only missing/revised downstream artifacts. Historical V2 export and source pack remain intact.

## Tests

`uv run pytest -q`: **562 passed** (531 existing, 31 new motion cases).
`uv run ruff check .`, `uv run mypy src` (67 source files), `git diff --check`: passed.
Tovi character lock: 48 artifacts, valid. Fresh minimal installation imports planning
and production modules without MoviePy. Video extra confirms MoviePy 2.2.1; FFmpeg
doctor confirms system FFmpeg/ffprobe 8.1. Tiny real 270×480/3-second dynamic encode
exercises multiple layers, a pose change, prop reaction, camera, lesson-slot pixels
and mux/decode QA. Existing 2-second production fixture verifies pinned dependencies,
idempotency and failed encode/mux/QA behavior. New cases cover generic props/vocabulary,
overscan, grounding, micro continuity, outro phases, budget and collision rejection.
The Windows PR CI workflow runs the same tests, Ruff, mypy and character lock.
Full production renders remain outside CI; the CI result is linked in the PR.

## Provider-call audit

NVIDIA NIM=0, Lyria=0, image generation=0, video-generation provider=0, YouTube=0.
The audit blocks parent provider entry points and outbound sockets; the renderer worker
rejects outbound connections/DNS through an audit hook. Forbidden attempts=0 on both
successful runs. Provider/music/curriculum/episode/source-character tables are unchanged.
GitHub branch/PR transport is separate from rendering and uses no generation provider.

## Rights/publication state

Music rights remain **unknown**. Publication remains **blocked**. Technical review
selection gives no publication or rights approval. No upload or release was attempted.

## Remaining limitations

Production storyboard vocabulary/prop schema is still Red-pilot-specific and must be generalized before autonomous multi-concept rendering.

The motion API accepts generic prop metadata, but production drawing registry expansion
remains a future automation task. Deterministic art uses a deliberately simple meadow;
no AI backgrounds or new character artwork were generated. Full-body crossfades do
not provide mouth articulation or a rig. Bounded activity QA measures technical behavior;
the child-entertainment judgment requires watching the full Pilot V3. No retention or
subjective numeric score is inferred. Stop adding effects and review this version.

PILOT_V3_READY_FOR_CHILD_ENGAGEMENT_REVIEW
