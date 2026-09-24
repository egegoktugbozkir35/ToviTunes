# Canonical character pack intake

Tovi's v1 visual direction is approved by the project owner. The pack is still
`readiness: draft`: commercial-use evidence has not been supplied, five mouth
sprites need a cleaner separated source, and the thinking expression touches
the source sheet edge. The render and release planner therefore remains held.
Visual approval never grants rights clearance.

## Reference and role contract

The owner supplied the original profile and channel banner plus eight new
reference and sprite images. The byte hashes, generation IDs, crop rectangles,
and role review decisions are in
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

Place copies of the ten supplied PNG files under a source directory using the
stable names in `intake.yaml`. Keep the originals untouched. The recipe checks
their SHA-256 hashes before doing any work. The preparation and asset roots
must be separate, trusted directories. A typical local arrangement is
`data/tovi-pack-v1/sources` and `data/tovi-pack-v1/prepared`; `data/` is ignored
by Git.

```powershell
uv sync --locked --extra dev
uv run python -m tovitunes.cli --config config.example.yaml character-pack prepare `
  --recipe brands/tovitunes/characters/tovi/packs/v1/intake.yaml `
  --source-dir data/tovi-pack-v1/sources `
  --prepared-dir data/tovi-pack-v1/prepared
uv run python -m tovitunes.cli --config config.example.yaml character-pack ingest `
  --recipe brands/tovitunes/characters/tovi/packs/v1/intake.yaml `
  --source-dir data/tovi-pack-v1/sources `
  --prepared-dir data/tovi-pack-v1/prepared
uv run python -m tovitunes.cli --config config.example.yaml character-pack assess
```

`prepare` uses Pillow only for cropping, connected alpha-component extraction,
transparent padding, and PNG writing. It never rescales, recolors, redraws, or
generates art. Every mouth bust comes from a fixed source window and goes onto
the same 320 × 480 canvas. The source row itself has touching seams and small
eye/head alignment variation, so those five files remain review candidates.
The source singing sprite has detached music notes; the connected-component
extraction removes only those separate decorations. The isolated thinking bust
reaches the right source boundary and also remains under review.

For each animation sprite, the intake decodes the PNG, requires real alpha and
transparent pixels, records the nontransparent bounding box, and rejects an
effectively opaque or contaminated corner. Antialiased edge alpha is retained.
The preparation report records output hashes and crop warnings. Running again
with the same bytes reuses the prepared files; a changed file is refused.

`ingest` registers the ten supplied source files, then the 26 individual role
files. Derived artifacts pin their source artifact IDs and source hashes as
immutable dependencies. It records human visual approval only for technically
accepted roles, selects those versions, and writes the registered role UUIDs
to `pack.yaml`. Source references and all derived files begin with rights
`unknown`. There is no automatic `commercial_use_confirmed` decision. A
repeated intake against the same database reuses existing immutable versions.
The local database and accepted media stay outside Git; a fresh machine must
ingest the source files again before a manifest can be assessed there.

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
evidence-backed rights. It reports exact blockers while the manifest is draft.
Only after clean mouth and thinking assets, owner-provided commercial-use
evidence, and a complete art/rights review may the pack be revised to
`readiness: approved` and reassessed. No fake rig data or legal evidence should
be entered to pass the gate.
