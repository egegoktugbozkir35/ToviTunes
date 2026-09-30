# Visual storytelling V1 / Colors Red Pilot V4 preparation

## Starting main SHA

`d61ab25828d34679b4b8d6d7950d4d8f496c3ae5` (`origin/main`, merged PR #30).
Branch: `codex/visual-storytelling-v1`. This change is for review; it is not merged.

## Why V3 still felt like animated slides

Pilot V3 introduced separate prop tracks, approved pose changes, parallax, exact
keyword reactions and gentle camera motion. The visual action was still usually
Tovi beside one teaching object in the deterministic meadow. The question scene
stacked objects, and the last 8.846 seconds settled too early. Motion activity
was present, but the child could not always identify a meaningful event in the lyric.

## VisualStoryPlan architecture

`TimedStoryboard → SceneComposition → VisualStoryPlan → EnvironmentSet →
SceneMotionPlan → MoviePy → FFmpeg`. `visual_story_v1` is typed, immutable JSON
per episode (`kind=visual_story_plan`, `slot=main_v4`). It records story action,
four contiguous sub-phases, environment role, camera/character/prop intent,
primary focus and continuity source. It depends on the selected storyboard,
environment set, beats, alignment and approved character sprites. The generic
planner branches on action, prop capabilities, scene kind, duration and
previously introduced identities; it does not inspect `red` or lyric scene IDs.

## Existing V3 components retained

V3 `SceneMotionPlan`, deterministic prop tracks, Tovi pose sequencing, camera
tracks, word timing, parallax, activity budget, motion QA, AssetStore, immutable
artifacts, idempotent render, MoviePy 2.2.1 and FFmpeg remain in use. The V3
`production render` command and its artifact slots remain available. V4 uses
`production render-v4` and `_v4` slots, with renderer identity
`tovitunes_visual_story_render_v1`. No new animation framework was added.

## Environment provider reuse

The separate `environment` service calls the existing
`benchmark.providers.GeminiImageProvider` Vertex AI adapter. It uses the
adapter's 9:16 image and reference-image support, remote-start callback and
provider failure outcomes. No second image API client or registry was created.
The renderer imports only the reviewed environment-set service and makes no
image-provider call. `generate-set` fails before provider/network activity unless
`--confirm-provider-generation` is supplied. Vertex project and ADC preflight
run before a durable request is prepared.

## Environment set design

Four 1080×1920 opaque PNG plates are held as immutable brand
`environment_plate` artifacts. A deterministic `environment_set` manifest pins
their IDs, SHA-256s, dimensions, provider/model/request provenance, generation
times, shared theme and staging zones. AssetStore records rights as `unknown`.
The manifest begins `pending`; its current human review decision lives in the
approval table. Human `approve` or `reject` records an explicit actor and reason;
`select` requires approved plate and manifest dependencies. A contact sheet is
exported for inspection. Automatic art judgment is intentionally absent.
Technical validation checks readable portrait source size/aspect, opacity,
nonblank content, and exact normalized output dimensions. Provider text metadata
is not copied into render plates.

## Environment generation requests

`meadow_wide`, `lesson_garden`, `play_path`, `celebration_meadow`. The master
meadow is the provider reference for the other three. Prompts require a shared
premium preschool 2D world, rounded shapes, controlled color, open Tovi/object
staging zones and a visible path/tree where useful. They explicitly forbid
Tovi, birds, people, other characters, prominent apples/balls/swatches, text,
logos and watermarks. Human review must reject a plate that violates these art
requirements. The image provider does not create Tovi or lesson objects.

Offline preview:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml environment plan
```

The preview reports four requests, exact prompts, model and master-reference
plan with **zero provider calls**. A real operator with the admitted production
config may run `environment generate-set --confirm-provider-generation`, inspect
its contact sheet, then use `environment inspect`, `approve`/`reject`, and
`select` with the returned set artifact ID. Generation never occurs in render.
An ambiguous remote request remains durable and is never blindly resent; a new
explicit attempt is required after failure or rejection.

## Provider/model

Planned production provider: Vertex AI Gemini Image (`google`,
`gemini-3.1-flash-image`, 1K portrait 9:16). Returned images must be at least
768×1376 before controlled normalization to 1080×1920. This model was not
called during the initial implementation phase; see the live-attempt report below.

## Provider call count

Actual calls during the initial implementation phase: prepared **0**,
remote_started **0**, succeeded **0**, failed **0**, ambiguous **0**. The later
live Attempt 1 is audited below. NVIDIA Kimi **0**, Lyria **0**, video generation
**0**, YouTube **0**. Fake provider invocations in tests are offline and excluded.

## Environment artifact IDs

None: no live plates or set were generated. The next real set will report its
manifest and per-role artifact IDs. No V3 meadow is used as a V4 fallback.

## Environment review status

No production set exists or is selected. The generation workflow creates a
pending set and cannot approve it artistically. A V4 production render fails
clearly until an explicitly approved set is selected.

## Story actions

The V1 vocabulary is `introduce`, `reveal`, `drop_and_settle`, `roll_through`,
`present`, `compare`, `performance`, `celebrate` and `settle`, with `question` and
`recap` reserved in the typed vocabulary. Normal scenes have setup, action,
reaction and resolution phases driven by the admitted duration and target-word
cue when available. The current pilot resolves intro → swatch reveal/present →
apple drop → ball roll → comparison → performance → micro celebration → outro.

## Prop continuity

Introduced identities and resting positions are tracked independently of
concept name. Existing objects do not repeat their first entrance. Comparison
and performance use bounded `slide_to_focus` tracks. Objects absent in the
immediately previous scene enter from an appropriate edge instead of appearing
at an unrelated on-screen location. The 0.24-second micro scene still inherits
the prior motion clock and composition.

## Apple drop implementation

Any prop with `supports_drop` can use pseudo-gravity `fall_in`, a small
`bounce_settle`, then a ground-plane rest. The deterministic renderer still
draws the apple; the `lesson_garden` plate may contain a simple tree but no
apple. The fall is eased, bounded and has no simulation dependency.

## Ball sequence

Any `supports_roll` prop retains V3's nearest-edge `roll_in` and ground-contact
QA. V4 positions the resting object along the `play_path` role, moves the camera
slightly with it, then presents the object near Tovi. No rotation breaks ground
contact.

## Swatch reveal

Any floating introductory prop may use a bounded scale reveal and soft halo.
Exact target-word reactions continue through the admitted alignment; the swatch
remains a deterministic lesson layer, not a generated image or UI card.

## Question scene

`compare` places two objects on opposite upper sides with Tovi between/below.
Objects enter or slide toward those positions and receive staggered reactions.
The generic staging and sampled motion tests check the comparison does not
obscure Tovi.

## Performance scene

`performance` uses the celebration plate, approved singing pose, upper focal
swatch and two side objects in a depth arc, restrained camera push, musical note,
sequential object motion and keyword events. The upper object and side objects
remain separate from Tovi's checked bounds.

## Outro

For the admitted approximately 8.846-second tail, V4 derives about 2.9 seconds
celebrate, recap through the last 1.8 seconds, then a short settle. The three
known props react in sequence before the stable approved goodbye pose. Audio
and storyboard boundaries are unchanged. No new lesson content appears.

## Frame occupancy diagnostics

Per scene QA records story action, environment role, focus, primary visual
action box, combined active visual box, normalized vertical occupancy, event
count, camera behavior, prop actions, pose changes, keyword count and
`excessive_dead_visual_space` where the active region starts below 39% of frame
height. This is a geometric diagnostic, not an engagement score. V4 stages Tovi
up to 48% frame height at a 0.86 ground plane, with question/performance props
using upper space. The tiny V4 fixture has no dead-space warnings; the real
pilot requires frame-by-frame human review after approved environments exist.

## V3 → V4 comparison

The V3 column describes the reviewed V3 report and the task's visual critique.
The V4 column describes implemented behavior; **no real V4 visual outcome is
claimed**.

| Dimension | V3 | V4 implementation awaiting real review |
| --- | --- | --- |
| Background quality | Deterministic meadow | Four provider-backed, human-reviewed plates |
| World coherence | One simple meadow | Master-referenced plate family |
| Story action | Mostly object presentation | Typed mini-actions and four phases |
| Object interaction | Entry/pulse/roll | Drop/bounce, reveal, slide, staggered reactions |
| Frame occupancy | Tovi capped at 38% | Up to 48% plus action-region diagnostic |
| Prop continuity | Adjacent/micro inheritance | Identity and rest-position tracking |
| Camera storytelling | Gentle generic tracks | Fall focus, roll follow, performance push |
| Question scene | Stacked options | Opposite options with Tovi between/below |
| Performance scene | Singing with objects | Depth arc, notes, staged reactions |
| Outro | Early long settle | About 2.9s celebrate, active recap, 1.8s settle |

## V4 render ID

None. A real approved environment set is unavailable because live Attempt 1
failed technical validation before any plate artifact was persisted. The code
exports `outputs/TOVITUNES_COLORS_RED_001_PILOT_V4.mp4` only after a successful
real render. Existing V3 render ID in the V3 report:
`6fe77b2c-e285-42b4-90e4-fd8769d9c7fc`.

## V4 SHA

Not available: no real Pilot V4 MP4 was produced. Existing V3 SHA in its report:
`f753990b3ebce60940177896818f8ddf22374e5a7d171f00648942bdc737793b`.

## Audio integrity

V4 pins the same selected audio master and uses the unchanged FFmpeg mux
command and admitted scene/word intervals. The tiny real fixture passed media
QA. Bitstream/PCM equivalence against the actual V3 pilot cannot be verified
until a real V4 is rendered; no claim is made here.

## Media QA

The V4 path retains V3's format, decode, timing, character, prop, camera and
activity checks. It adds geometric visual-story diagnostics to media QA and
exports a V4 scene contact sheet; where V3 scene frames are present it also
exports a V3/V4 comparison sheet. The full MP4 remains authoritative. No real
V4 media QA artifact exists yet.

## Idempotency

The offline 270×480 V4 production fixture rendered from a selected,
human-approved fake-provider set. A second invocation reused every downstream
artifact, did not invoke the encoder, did not call the provider and left all
database rows unchanged. The real pilot must be checked again after selection.

## Tests

Locked CI-style environment: `uv run ruff check .` and `uv run mypy src` pass.
`uv run pytest -q`: **571 passed**. This includes the existing regression suite plus new tests for
confirmation/preflight, pending review, selection, provenance, idempotency,
ambiguous requests, generic actions, drop/bounce/grounding, comparison,
performance/outro phases, occupancy, a real tiny drop MoviePy/FFmpeg encode and
a real tiny V4 fixture render. The selected Tovi character lock validates all
48 registered artifacts.

## CI

The existing Windows public GitHub runner uses the locked development/video
extras, Ruff, mypy, FFmpeg and pytest. Tests use a fake image provider and do
not render the full Pilot V4. CI has no live image-provider path or credentials.

## Rights

Music rights remain **unknown**. Generated environment rights will be
**unknown** unless actual evidence changes them. Publication remains
**blocked**. Technical review approval is not a publication approval.

## Remaining limitations

Storyboard schema and deterministic prop drawing remain Red-pilot-specific;
their generalization is still required before autonomous multi-concept
production. Human review is needed to reject accidental characters, target
objects or inconsistent art. Live Attempt 1 did not produce a plate artifact,
real V4 MP4, audio-equivalence comparison or artistic approval. No retention or
engagement improvement is inferred.

## Live Environment Generation — Attempt 1

Date: 2026-09-30. `GOOGLE_CLOUD_LOCATION` resolved to `global`,
`GOOGLE_CLOUD_PROJECT` was present, and Vertex ADC preflight succeeded without
printing credentials or tokens. The provider-free plan completed successfully
with provider `google`, model `gemini-3.1-flash-image`, four planned requests and
zero provider calls. `meadow_wide` had no reference; `lesson_garden`, `play_path`
and `celebration_meadow` each referenced `meadow_wide`. Every planned prompt
forbade Tovi, birds, people, other characters, text, logos, watermarks and
prominent apples, balls, swatches or other lesson objects.

The explicitly authorized live command made one request for `meadow_wide`.
Vertex returned one `image/png`, but local technical validation rejected it as
too small because the implementation required 900×1600. The official
`gemini-3.1-flash-image` 1K 9:16 contract is 768×1376, so this was an
implementation defect rather than evidence that the provider violated its size
contract. The durable request record is:

- role: `meadow_wide`
- local request ID: `2f33f3cb-c5fe-4d1e-b198-8fb1a979fa56`
- provider request ID: `pS69aqv_GfLnusEP5bapsAg`
- status: `terminal_failure`
- response MIME: `image/png`
- response byte count: `1039245`
- response SHA-256: `28bad4330d129e3202f9478cf8eb4a389ccbb30e15d62a25351e2f7c89a1b4d8`
- response metadata: backend `Vertex AI`, location `global`, model version
  `gemini-3.1-flash-image`, image output count `1`
- technical validation: failed the incorrect pre-fix minimum-size gate

The failed response bytes were not retained after validation, so the actual
source dimensions and any returned usage metadata cannot be recovered or
invented. No normalized output, plate artifact, reference relationship,
environment-set artifact, pending review record, individual review plate or
contact sheet exists. Accordingly, `environment inspect` was not applicable.

The exact image-request audit is: prepared **1**, remote_started **1**,
succeeded **0**, terminal_failure **1**, retryable_failure **0**, ambiguous
**0**; actual live image-provider requests **1**. NVIDIA Kimi **0**, Lyria **0**,
video-generation provider **0**, YouTube **0**. No cost metadata is available;
Google Cloud billing remains authoritative.

The validator was corrected to accept the documented 768×1376 minimum and still
normalize valid inputs to 1080×1920. Regression coverage proves the documented
size succeeds and a one-pixel-under-width image fails. Focused tests pass
(`7 passed, 1 skipped`), strict mypy passes for all 69 source files, and Ruff
passes for the changed files. Attempt 1 was not resent, Attempt 2 was not
started, and no environment approval, rejection, selection or Pilot V4 render
was performed.

ENVIRONMENT_GENERATION_INCOMPLETE
