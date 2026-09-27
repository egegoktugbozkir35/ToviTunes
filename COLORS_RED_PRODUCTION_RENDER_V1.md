# ToviTunes Colors — Red Pilot V1

## Starting main SHA

`621c7205f1239d12451a104ea128c40f75c5c7ab`, latest fetched `origin/main`.
PR #26 was merged at `2026-09-27T14:59:49Z` before implementation.
Fresh clone and branch `codex/colors-red-production-render-v1`; no unmerged
storyboard branch was used. No automatic merge of this render PR.

## Source TimedStoryboard

Selected artifact `25937e67-b373-42e5-a816-9d21a732d96c`, episode
`e9751591-906e-4404-88a2-31e7a41071d6`, key `colors-red-001`.
Nine scenes, seven lyric scenes, zero gaps/overlaps; frozen duration
`38.164897959183676` seconds. Production input IDs:

| Kind | Artifact ID |
| --- | --- |
| Audio master | `ace1ea8d-6a31-47a2-bb2a-2a895ecd9604` |
| Audio alignment | `9edd8b5e-7554-4ca7-a945-786d9dfa30f1` |
| Beat analysis | `705c82aa-0fd0-47ca-ba22-1265e6c76f2d` |
| TimedStoryboard | `25937e67-b373-42e5-a816-9d21a732d96c` |

Selected production artifacts are consumed through AssetStore. Renderer code
does not query music benchmark tables or alter lyrics, timing, policy, curriculum,
identity, pack assets, or upstream music implementation. Original candidate-2
audio master remains 923624 bytes with SHA
`06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991`.

## First-party donor audit

Authorized donor `egegoktugbozkir35/mpt-movie-narrator`, pinned audit commit
`32b5cd33776881f17b1594cce8716443c61e0bb4`. Inspected all requested modules:
`pipeline/render.py`, `utils/ffmpeg_bin.py`, `utils/video_layout.py`, and
`utils/video_qa.py`, plus `utils/process.py`. Inspected `test_ffmpeg_bin.py`,
`test_render.py`, `test_render_coverage.py`, `test_render_real.py`,
`test_render_template.py`, `test_video_layout.py`, and `test_v120_render_process.py`.

## Donor modules reused

Copied/adapted the pure contain/cover geometry helper. Adapted binary discovery,
ffprobe stream/fraction parsing and encoding QA, bounded process-tree execution,
two-stage video/audio mux, explicit stream mapping, faststart, temporary outputs,
atomic finalization, and resource cleanup. No Movie Narrator runtime dependency.

## Third-party provenance check

The relevant donor files credit `zcbacxc` and use AGPL-3.0-or-later. Git history
credits 早川, including layout introduction `370dc92` and SPDX work `e7bcc48`.
Repository ownership is not treated as proof of owner authorship. Retained SPDX
notices, included full license in `LICENSES/AGPL-3.0-or-later.txt`, documented the
adaptations in `docs/RENDER_DONOR_NOTICE.md`, and declared the integrated derivative
AGPL-3.0-or-later in package metadata. No unilateral relicensing is claimed.
No additional vendored implementation was identified in the adopted pieces.
MoviePy remains an external MIT dependency.

## ToviTunes adaptations

The renderer remains a small `src/tovitunes/render/` adapter: typed immutable
manifest/plans, Pillow props/scenes, approved sprite mapping, MoviePy composition,
system FFmpeg utilities, deterministic QA, and one production CLI.
No subtitle/template/HDR/4K/GPU/long-form infrastructure was transplanted.
`video-render = ["moviepy==2.2.1"]` is optional. MoviePy classes never enter domain data.

## MoviePy version

Distribution version **2.2.1**, verified by import and `importlib.metadata`.
MoviePy requires Pillow below 12; the render extra resolves Pillow 11.3.0.
Minimal installation smoke passed with MoviePy absent (Pillow 12.3.0).
The transitive imageio-ffmpeg package is installed by MoviePy, but its bundled
binary is not discovered, required, or downloaded by this renderer.

## FFmpeg version/path

`ffmpeg version 8.1-full_build-www.gyan.dev`.
Resolved system path: `C:\Users\Victus\AppData\Local\Microsoft\WinGet\Links\ffmpeg.EXE`.
Explicit `TOVITUNES_FFMPEG_BIN` overrides system lookup; invalid overrides fail.

## ffprobe version/path

`ffprobe version 8.1-full_build-www.gyan.dev`.
Resolved system path: `C:\Users\Victus\AppData\Local\Microsoft\WinGet\Links\ffprobe.EXE`.
Explicit `TOVITUNES_FFPROBE_BIN` overrides system lookup. Doctor passed.

## Scene visual generation

Nine immutable `scene_image/<scene_id>` PNGs, 1080×1920, contain gradient sky,
soft green ground, quiet rounded decoration, and requested educational props.
They contain no Tovi. Background colors derive from the approved blue/cream pack
palette. Lesson red is the deliberate constant `#E53935`.
Pillow geometry draws one red apple with a green leaf/brown stem, one red ball,
and a rounded red swatch. The ball-only teaching scene animates the ball from
offscreen-right to its recorded lesson position over 0.95 seconds.
PNG text retains canvas, scene ID, storyboard dependency, palette, prop counts,
red constant, bounding boxes, and prop motion. No vision detector is used.

## Tovi sprite mapping

Only approved `tovi-pack-v1-8f7e487b5ac5279b` sprites are resolved through AssetStore:
`sprite/hello`, `sprite/pointing`, `sprite/neutral_full_body`, `sprite/singing`,
and `sprite/hopping`. Visible alpha is cropped without changing source assets;
one scalar preserves aspect ratio. Resting character bounds and conservative
motion envelopes remain safe and clear of all lesson prop bounding boxes.

## Mouth-animation decision

**`mouth_animation_supported=false`**. Actual sprite/component geometry and intake
normalization were inspected. Components normalize to a 1536×1664 canvas and
anchor `[768,600]`, but no registered mouth anchor/scale exists for the separately
extracted singing pose. No arbitrary offsets, new ASR, speech inference, Rhubarb,
or phoneme model were introduced. The approved singing pose is used as-is.

## Per-scene animation table

| Scene | Start–end (s) | Sprite | Motion | Props | Beats / downbeats |
| --- | --- | --- | --- | --- | --- |
| intro | 0–2.462 | hello | enter | — | 5 / 2 |
| lyric_01 | 2.462–6.925 | pointing | point/bob | swatch | 8 / 2 |
| lyric_02 | 6.925–11.147 | neutral_full_body | present/bob | swatch | 7 / 1 |
| lyric_03 | 11.147–15.57 | neutral_full_body | present/bob | apple | 8 / 2 |
| lyric_04 | 15.57–20.193 | pointing | point/bob | rolling ball | 8 / 2 |
| lyric_05 | 20.193–24.636 | pointing | question/shift | apple, ball | 9 / 3 |
| lyric_06 | 24.636–29.079 | singing | beat bob | swatch, apple, ball | 8 / 2 |
| lyric_07 | 29.079–29.319 | hopping | celebrate | swatch | 0 / 0 |
| outro | 29.319–38.164897959183676 | hopping | downbeat hop, settle | — | 8 / 2 |

Nine immutable `character_animation/<scene_id>` JSONs pin sprite ID/role, crop,
alpha area, uniform scale, size, positions, motion amplitude, entrance interval,
measured beat/downbeat index slices and relative timestamps, and mouth fallback.

## Beat/downbeat usage

Selected evidence contains 61 beats and 16 downbeats. Singing bobs use measured
beats; celebration hops use measured downbeats. No scene/lyric times are changed.
The 0.24-second final red scene has no measured beat/downbeat and receives no
invented hop. Outro settles after the final measured downbeat; no tail beats are
invented. Existing global beat/index evidence is preserved exactly.

## Render manifest

`d8fab798-1218-4a65-aa7d-e3cf5bcf2c76`, `render_manifest/main`.
Pins exact audio, alignment, storyboard, beat, all nine scene-image/animation IDs,
used sprite IDs, dependency SHAs, character-pack revision, renderer version
`colors_red_render_v1`, MoviePy/FFmpeg/ffprobe versions, canvas, FPS, and codec settings.
Complete ordered scene boundaries are validated against the selected storyboard.
Stale input SHA/selection or missing coverage is rejected.

Determinism means identical intended composition, timing, movement, sprite choice,
audio input, and encode configuration. Cross-machine MP4 bytes need not be
identical across encoder builds. Same-machine artifact reuse is byte-identical.

## Encode/mux architecture

MoviePy builds transparent image composites and concatenates exact scene intervals
with direct cuts. Video-only encoding runs in a separate process with a 1800-second
deadline and owned-tree termination. FFmpeg then muxes with `-map 0:v:0`,
`-map 1:a:0`, `-c:v copy`, `-c:a aac`, `-b:a 192k`, and `-movflags +faststart`.
Mux/decode deadlines are 120 seconds; probe deadline is 30 seconds. Commands use
argv lists, support spaces, capture stderr, and expose at most 4000 diagnostic
characters on failure. Temporary MP4s are cleaned on failure. Probe/decode and
deterministic QA precede final atomic staging and AssetStore registration.

## Final MP4 artifact

`d4244280-672b-4a2f-b9ef-86643c1b8fa9`, `final_render/main`, `video/mp4`,
**2505634 bytes**. Pins the render manifest and unchanged production audio.
Local byte-identical review export: `outputs/TOVITUNES_COLORS_RED_PILOT_V1.mp4`.
The current chat also retains the export in its user-facing `outputs/` directory.
The AssetStore remains authoritative; videos and local configs are not committed.

## Final MP4 SHA

`b138e3a30a19b293ba292c44a9dd64236fffd737757083811f9a079627fede67`.

## Resolution

**1080×1920**, display aspect ratio **9:16**, square pixels.

## FPS

**30.0**.

## Video codec

**H.264**, `libx264`, CRF 18, medium CPU preset, MP4 faststart.

## Pixel format

**yuv420p**, 8-bit SDR.

## Audio codec

**AAC**, 192 kbps target, **44100 Hz**, **2 channels**. The final AAC encode is a
derived mux stream; the retained original candidate-2 master is byte-unchanged.

## Duration

Container/audio: **38.165011 s**. Video stream: **38.133333 s**.
Selected source/storyboard: **38.164897959183676 s**. All pass the documented
`2/30 + 0.005 = 0.07166666666666667 s` frame/container tolerance; the video
stream differs by less than one video frame. No meaningful timeline drift.

## Media QA

**Passed**. Artifact `b5cc8b5b-6d1c-4840-9f29-acf56094eb19`, `media_qa/main`,
pinned to final render ID/SHA. Video/audio stream existence, codec, resolution,
frame rate, pixel format, sample rate, channels, display aspect, container and
both stream durations pass. Full FFmpeg decode succeeds. Deterministic QA confirms
all nine ordered scenes, exact coverage, visible sprite alpha, safe uniform layout,
no lesson-prop occlusion, exact prop presence/counts, and lesson red.
Machine policy **`technical_render_v1`** selects final render/media QA solely for
local visual review, without commercial-rights or publication approval.

Representative final-video frames were exported and inspected. Scene filenames:
`intro.png`, `lyric_01.png` (red swatch), `lyric_03.png` (red apple),
`lyric_04.png` (red ball), `lyric_05.png` (question), `lyric_06.png` (sing),
`lyric_07.png` (final red), and `outro.png`; `lyric_02.png` is also included.
All are under `outputs/colors_red_v1_frames/`. A contact sheet is retained in
the current chat's outputs for quick review. Visual inspection is still the
user's next gate; technical acceptance is not a claim of creative perfection.

Validation completed:

- `uv run pytest -q`: **404 passed**, including **37 focused renderer cases**.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed, **53 source files**.
- `git diff --check`: passed.
- Tovi character lock: **48 artifacts, valid**.
- Minimal installation smoke: passed with MoviePy absent.
- `video-render` extra install and MoviePy distribution/import: passed.
- FFmpeg/ffprobe doctor: passed.
- Tiny real MoviePy transparent composition/render/mux: passed.
- Full real production render and second invocation: passed.

Tests cover explicit/system binary discovery, missing overrides/binaries, paths
with spaces, real bounded timeout, bounded diagnostics, donor cover/contain math,
stable PNG SHA and red constants/count metadata, approved role/alpha/uniform scale,
safe motion/mouth fallback, ball entrance, stream maps/faststart, manifest pins and
staleness, wrong codecs/resolution/FPS/pixel format/aspect/duration/audio, and encode,
mux, or QA failure leaving no authoritative selected final artifact. CI uses only
the CPU/network-free two-second 270×480 three-scene fixture, not the full pilot.

## Idempotency proof

The real production CLI ran twice with unchanged inputs. Second invocation reused
all **21** artifacts: nine scene images, nine animation plans, one manifest, one
final render, and one media QA. Same artifact IDs/SHA, no encode call, no duplicate
decisions, and exact unchanged row digests for **every database table**, including
selections/timestamps. Local `production-render-first.json`,
`production-render-second.json`, and both integrity audits retain the evidence.

## Historical immutability

Every preexisting canonical row-digest multiset is preserved. All music tables
are exactly unchanged, including request/receipt/output, all eight analysis and
eight timing rows, policy evidence and decisions. Character/brand/catalog/episode
rows and original asset bytes remain unchanged. Only downstream technical artifacts
and their required ingestion/approval/dependency/selection rows were appended:

| Table | Before | After first run | After second run |
| --- | ---: | ---: | ---: |
| artifact_versions | 73 | 94 | 94 |
| artifact_selections | 47 | 68 | 68 |
| artifact_dependencies | 114 | 180 | 180 |
| artifact_validation | 73 | 94 | 94 |
| rights_decisions | 119 | 140 | 140 |
| approval_decisions | 127 | 169 | 169 |
| execution_leases | 0 | 0 | 0 |

New rights rows record unknown; all earlier rights rows are unchanged. Approval
additions consist of 21 ingestion-pending and 21 technical-review decisions.

## Provider-call audit

Lyria POSTs **0**; provider-resume GETs **0**; image-generation calls **0**;
video-generation calls **0**; LLM planning calls **0**; YouTube uploads **0**.
Parent CLI generation/analysis/provider/socket entry points were patched to raise
for both runs. Child composition Python also has an audit hook blocking outbound
socket connections/address resolution. Forbidden-entry-point attempts **0**.
Unchanged generation/music/provider row digests corroborate the audit. No candidate
3, new music, timing inference, or external scene generation occurred.

## Rights state

Music rights remain **unknown**. Existing character/brand evidence is retained.
No music-output or commercial-rights approvals were altered.

## Publication state

**Blocked** pending later release gates, including rights clearance. No upload
or publication authorization is inferred from technical rendering approval.

## Next required action

Watch the actual local pilot and inspect the frame exports. Identify concrete
visible defects before any further renderer work or Production V1 release work.
No further renderer improvements are included after this milestone.

PILOT_RENDER_READY_FOR_VISUAL_REVIEW
