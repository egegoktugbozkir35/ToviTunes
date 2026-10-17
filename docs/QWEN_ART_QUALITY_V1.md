# Qwen artwork quality V1: source diagnosis and targeted repair

Baseline: draft PR #47, verified head `2ff081c03668aaba2d434546f97febd084dfb6c9`.
Episode: `brush-teeth-up-down-and-all-around-16894028-001`,
ID `909efb22-9e51-4c50-9839-475fc13190fe`.

## Scope and evidence

Read all eleven retained raw Qwen PNGs, their eleven admitted PNGs, all four
raw/admitted environment plates, the creative proposal, compiled visual plan,
storyboard, PNG composition metadata, request specs/receipts and final video.
Eight midpoint frames were decoded from the existing normal MP4. The raw/admitted/
video triptychs and eleven detail comparisons are local deliverables; generated
media, configuration and databases are not added to this repository.

Deterministic replay of historical extraction + normalization reproduces all eleven
admitted foreground images pixel for pixel. Historical environment validation,
bounded alpha flattening and resizing reproduce all four admitted plates pixel for
pixel. This establishes which defects precede compositing, without inferring their
origin solely from the MP4. Environment dimensions are 768x1376 raw and 1080x1920
admitted; foreground images are 1024x1024.

All eleven retained illustration fingerprints were independently recomputed from
their stored provider profiles. All four environment request fingerprints were
recomputed from stored canonical specs and the retained set generation fingerprint.
The exact translated workflows for the eleven illustration requests were also
checked against their stored prompts/seeds. The four environment request bodies
were reconstructed locally from their canonical specs and the matching retained
workflow SHA-256/seed. The adapter sends `spec.prompt()` unchanged in node
`459:452`; its workflow's separate negative-prompt input is empty. No transport was
invoked during this reconstruction.
No receipt, fingerprint, source or selection was rewritten. Read-only table/file
snapshots cover the retained acceptance workspace and authoritative desktop
production workspace, including real config, original music and approved character
files. The local preservation report records the before/after comparison.

## Findings by retained image

The approximate visible dimensions below are measured from admitted alpha bounds
and the existing composition's square at 360x640 phone size, before camera motion.
They are evidence of readability limits, not a perceptual pass threshold.

| Image / stable asset key suffix | Concrete defect and origin | Visible size at 360px |
| --- | --- | --- |
| big mirror / `ad5a54255507bd5e` | Raw includes a sink as requested by the description; composition duplicates fixtures already painted into the background. Pale sink edge is mostly retained. Planning/composition duplication, not a new render defect. | 58x91 |
| shiny smile / `da1d67fe33bab615` | Raw has two disconnected tooth arcs, very faint sparkles and isolated cyan backdrop artifacts. V1 clips faint contours/sparkles; enclosed solid white interiors survive. A narrower key also retains backdrop specks that would shift the normalized subject: V2 explicitly blocks this unsuitable source. Planning chooses a conceptual smile rather than literal brushing. | 73x45 |
| toothbrush / `7ec27c00e347042d` | Face and tooth-like blue head are present in raw despite `allow_face=false`. Raw semantic/style variability; extraction cannot remove the face or invent bristles. Thin subject occupies little of its square. | 22x91 |
| up arrows / `d31653ad1a3bf5b8` | Raw adds a tooth/bone-like central body and hands to the arrows. Prompt supplies the full brushing objective, inviting unrelated subjects; raw also violates no-hands/no-anthropomorphism instructions. Not caused by alpha processing. | 79x91 |
| down arrows / `00e7c284e49e4714` | Raw is one recognizable blue arrow. Mostly intact after extraction. Different treatment from the up-arrow collage is already present at source. | 35x91 |
| circle swirl / `4afa8ebb34209b2a` | Raw resembles a pale ornamental disc with tooth-like beads, not the requested simple dotted path. Its rim exists in raw but the broad white key erases portions. Both raw semantic failure and confirmed extraction damage. | 90x91 |
| soap bubbles / `0f18045f5f2161b0` | Raw invents a large mouth/body/limbs brushing teeth among the bubbles. Full lesson objective contaminates an isolated-overlay request; raw disobeys subject/character constraints. Normalization merely preserves the unwanted anatomy. | 66x73 |
| cup of water / `f58a1b77e9ff13a3` | Raw contains an ambiguous glass-in-mug depiction and cast shadow. No extraction repair can clarify the vessel; residual shadow remains in both derivatives. Too much raw detail for phone scale. | 83x91 |
| water drops / `2531cdc3741fda6d` | One glossy drop with bubbles and cast shadow is already in raw; keying leaves a pale shadow island. This is source lighting/background suitability, not an internal detail erased by resizing. | 39x73 |
| toothbrush in cup / `5565beed1649551c` | Raw adds a face and uses different brush colors/materials from the separate toothbrush. Both were requested independently; shared text does not guarantee object identity. Thin bristles are small on a phone. | 29x91 |
| party bubbles / `0f39e7b0bb85616f` | Faint tiny bubbles are present in raw. Some pale rim pixels disappear under V1; microdetails are also intrinsically too small/low contrast. Extraction cannot make a weak teaching concept stronger. | 60x73 |
| `meadow_wide` | Raw already contains about five stacked meadow views and a bathroom vignette. Admitted version preserves them; used by scenes 1-7. | Full frame |
| `lesson_garden` | Raw has a more continuous valley but bathroom fixtures embedded in outdoor scenery; used by scene 8. The source, not the renderer, mixes incompatible settings. | Full frame |
| `play_path` | Raw contains about seven narrow landscape strips and bathroom furniture. Not used by the eight-scene final video, but retained and inspected. | Not used |
| `celebration_meadow` | Raw contains about six repeated landscape views and a corner mirror/sink. Not used by the final video. | Not used |

## Exact environment conflict and corrected construction

The retained compiled plan specifies:

```text
world_contract = preschool-world-v1
reuse_shared_environment = false
meadow_wide / play_path / celebration_meadow:
  A bright, cozy bathroom with a big mirror above the sink
lesson_garden:
  A bright, cozy bathroom with a big mirror above the sink, softly lit
```

The world contract is a fixed compatibility identifier in both plan schemas. The
compiler assigns setting strings to the four role identifiers; those identifiers
must not be interpreted as literal biomes. A lighting-word change caused the
closing bathroom to occupy a second role. The fresh episode did not select a
shared meadow: it explicitly requested its own plates.

Previously `VisualService._environment` supplied those descriptions to
`generate_set`, which first called the fixed meadow `_spec`, then replaced only
`scene_brief` and two constraint fields. The retained prompt still included:

```text
COMMON BRIEF: ... One coherent friendly meadow world across every plate ...
layered sky/hills/vegetation ... One continuous landscape and one coherent horizon ...
REFERENCE INSTRUCTIONS: No reference image. Use the shared textual meadow world ...
PALETTE: grass=#92C970, sky=#B8E9F8, sunlight=#FFF3C4
SCENE: A bright, cozy bathroom with a big mirror above the sink
```

The same raw requests already prohibited stacked views. Thus absence of a
prohibition was not the cause. Contradictory setting instructions are confirmed;
their exact contribution to Qwen's tendency to produce strips is a hypothesis
until approved new output is inspected.

New episode construction uses `environment_scene_v2` and supplies the plan's
description directly. Its common art direction is:

```text
Preschool 2D illustration: rounded forms, soft tonal contours, gentle dimensional
shading, warm even light and a bright controlled palette. Depict the described
setting as one continuous physical space from a single viewpoint. Keep the middle
and lower stage quiet and open for a separate character and educational
illustrations, with depth behind them.
```

It asks for one frame-filling portrait scene with consistent perspective. It has
no grass/sky palette, meadow reference contract or outdoor horizon requirement.
The retained bathroom prompt falls from 1,584 to 1,032 characters. The same builder
is tested with an underwater coral cove. No topic/room keyword special case is
introduced, and no settings are rewritten. Distinct planned settings remain valid.
The four role IDs remain renderer data. The shared brand meadow V1 builder and
fingerprint remain available and unchanged.

Production explicitly selects the new episode prompt version. Its generation/set
and per-request fingerprints change. If this episode already has a different set
of requests, generation stops before a new request is inserted or sent, regardless
of whether the old status is prepared, complete, failed or ambiguous. Admitted
episode bindings continue to reuse their existing artifacts. Explicit legacy
generation remains available for historical recovery; old rows are never recast as
new-version requests.

## Foreground extraction correction

V1 flood-fills exterior-connected pixels with alpha >=250, every RGB channel >=240
and channel spread <=14. This does preserve enclosed white interiors when the
contour is dark enough. A pale contour inside that broad threshold ceases to be a
barrier: the flood erases rim details and may enter white interiors. Normalization
then trims and fits the already damaged result to 84% of a square; it is not the
source of those missing rim pixels.

V2 retains the same exterior-only flood. After validating white padding, it
measures each RGB channel's range along the border, permits one channel level of
tolerance and rejects borders whose per-channel range exceeds 12. It keys that
measured backdrop range rather than the entire near-white range. Qwen's minor
alpha noise is not used as a false barrier in V2; retained pixels keep their
original RGB and alpha. No dilation, mask enlargement, hole filling, blur or
repainting is used. Coverage remains bounded to 15%-95%; nonwhite/touching borders,
uneven backgrounds and indistinguishable white-on-white sources produce actionable
errors. This is not semantic segmentation: a contour identical to the backdrop, a
gap or a barely distinguishable subject can still be unsuitable. Such artwork
requires a new source with a distinct continuous contour and plain white padding.

If isolated pixels alone determine an extreme trim boundary, V2 blocks with
`isolated pixels control trim bounds`. It does not silently delete them. This
was found by inspecting identical-placement comparisons: the smile raw source
contains single cyan pixels `(245,254,255,255)` at (223,923) and (239,923), well
below the visible tooth arcs. Keeping them would extend the trim bottom from 667
to 924 and move/shrink the subject. A new clean source is required. A connected
single-pixel tip remains valid; a synthetic regression distinguishes the two.
Four retained sources (smile, soap bubbles, water drops and toothbrush in a cup)
produce this explicit diagnostic. Seven produce reproducible offline derivatives.
The guard is intentionally conservative; it does not infer which small marks are
artistic detail. The complete historical admitted versions remain untouched.

The retained circle source is
`d8876060edda376fa7568300427134cb9b8163e18f13e2864bebddd4e880daca`;
its original admitted PNG is
`2c774145573efb4e5549321a845fef8f55e6677ec806153c8b67565373e6a46f`.
At raw coordinate (277,161), RGBA `(252,246,240,255)` is visible source artwork:
V1 sets alpha to zero, V2 retains 255. Across that source V2 retains 49,047 pixels
that V1 removed, with no newly removed pixels. Visual comparison confirms the
restored outer rim; a pixel count alone is not a quality score. All eleven sources
were processed twice, requiring identical pixel hashes for successful derivatives
or identical explicit diagnostics for rejected sources. They are unselected local
derivatives or blocked diagnostics, not replacement production artwork. Residual
source shadows and semantic errors remain visible.

The extraction helper defaults to `qwen_white_exterior_v1`. Existing reviewed
lesson-object paths retain V1. Only new `episode_illustration_v3` requests explicitly
select `qwen_white_exterior_v2`, which is included in their fingerprinted provider
profile. Explicit `episode_illustration_v2` recovery retains the historical prompt,
profile and V1 normalization. Tests simulate a restart with retained V2-prompt raw
art and verify that changing to the V3 prompt blocks, while explicit legacy recovery
reuses the raw bytes without resending. Already admitted legacy assets continue to
reuse their exact bytes under the new default.

## Compact foreground direction and readability

`episode_illustration_v3` shares the environment's short style sentence. The
isolated asset's teaching check names only that subject; it no longer repeats the
entire episode objective that introduced teeth/toothbrush context into arrow and
bubble requests. There is no default white palette when target color is unspecified.
The visible continuous-contour instruction applies to all pale subjects, including
the retained V2 plan's assets whose `target_color` is null. Existing no-added-face
policy is retained in compact explicit language, without anatomy/character reference
boilerplate that mentions extra Tovi anatomy in a prop request. No unsupported image
reference capability is added.

Shared style was already fairly specific. The dominant environment issue is the
contradiction, while independent object generation and objective contamination
explain plausible consistency failures. Whether compact wording reduces faces,
improves subject accuracy or matches Tovi better is unverified until real outputs
are reviewed. Cross-asset identity is still not guaranteed by text-only Qwen.

The 22px-wide primary toothbrush and 29px-wide toothbrush/cup at phone size are
weak teaching illustrations; the smile is only about 45px tall. The semantic and
contrast problems remain more severe than output dimensions. Existing R3 composition
already provides an opt-in scale treatment. This repair does not globally enlarge
props, change safe character placement, change the renderer/compiler or make R3 the
default. Review the three planned candidates at the existing placements and at
360x640 before accepting them. A clear, accurate brush silhouette and a simple
readable tooth graphic matter more than added decorative detail.

## Provider-free validation and remaining gate

`tests/test_qwen_art_quality.py` covers pale contours, enclosed white interiors,
tinted near-white backgrounds, antialiased source edges, invisible white-on-white
subjects, touching/near-border objects, uneven padding, isolated trim outliers,
frozen historical hashes,
scene generalization, changed-request blocking, immutable receipts and retained raw/
admitted foreground reuse. Existing lesson-object, Qwen, environment, rendering and
source-reuse tests remain authoritative. Run Ruff, strict Mypy, focused/relevant
tests and the complete offline suite; the PR's Windows CI must succeed at its exact
head. Local logs and final totals accompany the handoff; GitHub checks are the CI
authority.

No new provider output has been generated. A coherent next environment is a target,
not an achieved perceptual acceptance claim. Artistic approval remains on hold.

## Limited Qwen A/B plan — separate operator approval required

Propose **three new requests total**, each compared against a frozen retained A
source, using the existing local Qwen model/workflow. No full episode or extra model.

1. Bathroom B: same retained bathroom description and model/sampling profile as the raw
   `meadow_wide` A, using the versioned prompt construction. Inspect raw B
   for one coherent indoor room, consistent perspective and no repeated horizons.
2. General environment B: use the retained Qwen farmyard A, request
   `ee18bc3f-b97c-4700-aa85-36e62966501d`, raw artifact
   `cdadc167-282d-4fae-b6ea-38b23915c948`, SHA-256
   `ae9b4b585d3fd532472759cf3ebb9efdf3f184e32b785d33346f92f672ad5a38`.
   It already shows a coherent scene. Pin its model/sampling profile and use its
   setting, “A cheerful farm yard with soft green grass and a sunny blue sky”,
   with the new builder. Confirm an outdoor setting still works and is not forced
   into a bathroom.
3. Pale subject B: same retained circle-swirl requirement and model/sampling profile as A,
   with `episode_illustration_v3`. Compare raw A/B for a simple dotted circle,
   contour visibility and subject isolation. Run both extraction versions on B;
   inspect the rim and interior at full scale and in the current placement.

The current adapter derives its seed from the canonical spec fingerprint. Changed
prompts therefore also change the seed: this small comparison is qualitative, not a
controlled prompt-only causal experiment. Record both seeds; do not add a seed
override or silently mutate the adapter's translated request.

Freeze exact canonical specs, translated workflows, seeds, settings and source
hashes in an isolated candidate directory before approved submission. Use one
request per candidate and record returned task IDs, raw bytes, hashes and receipts.
Do not retry completed, failed or ambiguous requests automatically. No fourth
sample unless the operator approves a specific remaining ambiguity. No selected
production artifacts, database, music, normal/R3 videos or real config are changed.
Assess raw and composited results side by side at full size and 360x640. A human
must reject faces/invented anatomy, unclear teaching subjects, mixed settings,
repeated panels and lost pale details. Do not accept dimensions or prompt adherence
as evidence of visual quality. Approval of this plan is not included in the current
authorization; no Qwen call has been made for this repair.
