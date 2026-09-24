# Canonical character pack intake

Tovi's v1 visual direction is approved by the project owner. Five isolated
mouth components and a dedicated thinking sprite replace the held PR #10
candidates. The selected pack now passes `assess_pack_assets` with
`readiness: approved`. Visual approval and rights clearance remain separate
append-only decisions.

## Reference and role contract

The owner supplied the original profile and channel banner, eight earlier
generated references and sheets, and six replacement PNGs. Their byte hashes,
generation IDs, crop rectangles, supersession history, and role review decisions are in
`brands/tovitunes/characters/tovi/packs/v1/intake.yaml`. The profile and banner
remain identifiable as the original Tovi identity references. Generation IDs
are recorded as `generation://` source URIs; the provider model is unknown and
is not fabricated.

An approved `CharacterAssetPack` must have front, three-quarter, and profile
views; five mouth states; a palette; written identity rules; expressions; wing
poses; reusable sprites; and a distinct UUID for each required image role.
`asset_artifact_ids` contains only registered artifact IDs, never filenames or
generation IDs. `rig_data` is null because no pivot or skeletal topology has
been proven.

## Repeatable offline workflow

Place copies of all sixteen supplied PNG files under a source directory using the
stable names in `intake.yaml`. Keep the originals untouched. The recipe checks
their SHA-256 hashes before doing any work. The preparation and asset roots
must be separate, trusted directories. A typical local arrangement is
`data/tovi-pack-v1/sources` and `data/tovi-pack-v1/prepared-replacements`;
`data/` is ignored by Git. Keep the original preparation directory and asset
store intact so earlier artifact versions remain retrievable.

```powershell
uv sync --locked --extra dev
uv run python -m tovitunes.cli --config config.example.yaml character-pack prepare `
  --recipe brands/tovitunes/characters/tovi/packs/v1/intake.yaml `
  --source-dir data/tovi-pack-v1/sources `
  --prepared-dir data/tovi-pack-v1/prepared-replacements
uv run python -m tovitunes.cli --config config.example.yaml character-pack ingest `
  --recipe brands/tovitunes/characters/tovi/packs/v1/intake.yaml `
  --source-dir data/tovi-pack-v1/sources `
  --prepared-dir data/tovi-pack-v1/prepared-replacements
uv run python -m tovitunes.cli --config config.example.yaml character-pack assess
```

`prepare` uses Pillow only for cropping, connected alpha-component extraction,
transparent padding, translation, and PNG writing. It never rescales,
recolors, redraws, or generates art. The five new beaks are isolated mouth
components, not whole Tovi busts. Each complete 1254 × 1254 source frame is
translated into a 1536 × 1664 transparent canvas. The center of its visible
upper beak is placed at shared anchor `(768, 600)`, using alpha greater than 8
to ignore subvisual alpha-1 fringe. No source pixels are clipped. The prepared
report records each source anchor, paste offset, and visible bounding box.
CharacterAnimator still needs a later calibrated anchor on the canonical
face; that placement is not guessed here. The thinking source receives 32 px
transparent padding on all sides. The prior five bust crops and clipped
thinking crop remain documented under `superseded_assets` and remain in the
asset store with their former review history.

The source singing sprite has detached music notes; connected-component
extraction removes only those separate decorations. Visual inspection of the
six replacements found no head or body baked into the mouth components, no
text or unrelated props, and an intact thinking silhouette, tuft, eyes, cream
face, and wing gesture. Closed, small open, wide A, smile, and round openings
have distinct aperture geometry. Their source hashes and exact generation IDs
are in `intake.yaml`; provider model/version are not asserted.

For each animation sprite, the intake decodes the PNG, requires real alpha and
transparent pixels, records the nontransparent bounding box, and rejects an
effectively opaque or contaminated corner. Antialiased edge alpha is retained.
The preparation report records output hashes and crop warnings. Running again
with the same bytes reuses the prepared files; a changed file is refused.

`ingest` registers all sixteen source files, then the 26 current role files.
Derived artifacts pin their source artifact IDs and hashes as immutable
dependencies. It records artifact-level visual approval only after technical
checks, selects the accepted versions, and writes their UUIDs to `pack.yaml`.
The other 20 role UUIDs are unchanged. Repeating intake against the same
database reuses existing immutable versions. The local database and accepted
media stay outside Git; a fresh machine must ingest the supplied files before
the manifest can be assessed there.

Sources explicitly marked `rights_basis: openai_chatgpt_output` carry known
`generation://` IDs and are identified by the owner as ChatGPT image outputs.
Intake records evidence-backed `commercial_use_confirmed` decisions for those
sources and their direct deterministic role derivatives, including historical
PR #10 candidates. Each decision has an actor, timestamp, policy version,
official evidence URI, and rationale. The evidence is the [OpenAI Terms of Use
effective January 1, 2026](https://openai.com/policies/row-terms-of-use/): as
between user and OpenAI, OpenAI assigns its interest, if any, in Output to the
user, to the extent permitted by law. This supports the project's provider
output use gate. It does not establish copyrightability, uniqueness, trademark
clearance, or rights in any input reference.

`original_profile` and `original_banner` remain `unknown`: their OpenAI
generation provenance is not documented. They are provenance/reference inputs,
not selected production role artifacts. The existing pack readiness policy
checks evidence-backed rights for selected role artifacts; it does not require
commercial clearance of every upstream reference. Dependency IDs and hashes
remain pinned, and a separately blocked upstream reference would still fail
asset eligibility. No rights policy was changed to approve this pack.

## Palette measurement

Palette values in `pack.yaml` are median RGB samples from small, visually
identified regions of the approved transparent neutral master where alpha is
above 200. The sample rectangles, in source pixel coordinates, are:

| Name | Rectangle `(left, top, right, bottom)` |
| --- | --- |
| `body_sky_blue` | `(620, 430, 680, 480)` |
| `body_shadow_blue` | `(250, 900, 300, 950)` |
| `face_cream` | `(340, 730, 390, 780)` |
| `belly_cream` | `(540, 1000, 600, 1050)` |
| `beak_orange` | `(610, 600, 650, 640)` |
| `feet_orange` | `(450, 1180, 490, 1210)` |
| `cheek_blush` | `(350, 660, 400, 700)` |
| `eye_blue_light` | `(440, 615, 470, 635)` |
| `eye_blue_dark` | `(440, 535, 465, 560)` |

These are identity guide colors across gradients, not exact flat fills. The
hex labels printed on the generated turnaround were not used as measurements.

## Current exclusions and release gate

The rig sheet's combined head/body already has eyes and mouth, so it is not a
clean replaceable `body_base`. The apparent tuft includes forehead feathers;
the belly patch carries blue edge remnants. Eye and pupil fragments lack a
proven coordinate and layer topology. The open beak pieces do not establish
separate upper and lower layers. Those pieces remain source references only.
Six isolated wing candidates and two closed-beak components were retained.

`assess_pack_assets` checks the pinned brand, kind, MIME type, file hash,
transparent sprite pixels, current human approval, selected version, and
evidence-backed rights. A structural approved-pack candidate passed with no
issues before the manifest transition. The approved manifest was then
revalidated and reassessed with no issues. Episodes pinned to an older draft
pack revision still retain their hold; new episodes pin the approved revision
and render/release planning moves to the next actual unmet requirement. `rig_data`
remains null pending a later rig calibration task.
