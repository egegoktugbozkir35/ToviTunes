# Canonical character pack intake

Tovi's current `v1` pack is **draft metadata**. It contains no original character art, approved palette or rights evidence. Do not set its `readiness` to `approved` or use it for visual production until the artist's files and ownership record have been reviewed. The pack revision is separate from the brand revision, so new art can be released without silently changing earlier episodes.

## Required reference set

An approved pack manifest (`schema_version: 2`) must name:

- Original front, three-quarter and profile views.
- Exact `#RRGGBB` palette entries and written rules for silhouette, proportions, eyes, beak and tuft.
- At least one expression, wing pose and reusable sprite layer, plus the five initial mouth states: `closed`, `small_open`, `wide_a`, `e_smile`, `o_round`.
- Forbidden identity changes and any allowed accessories.
- A distinct immutable brand artifact ID for every `view/<name>`, `sprite/<name>` and `mouth/<name>` role. `rig_data`, when used, is a separate JSON artifact ID.

The semantic role map lives in `asset_artifact_ids`. For example, `view/front` points to the selected front-reference artifact, and `mouth/o_round` points to that mouth sprite. Never put a local filename or a provider URL in place of an artifact ID. The checked-in draft intentionally leaves the map empty.

## Intake sequence

1. Obtain the original layered files and reference sheets from the authorized owner. Record artist/source identity, license or assignment, scope of commercial YouTube use, and any restrictions as evidence. Keep raw files outside Git.
2. Ingest each reference as a brand artifact of kind `character_reference`; ingest transparent sprite and mouth images as `character_sprite`. Use `owner_scope="brand"` and the catalog's pinned brand revision ID. The store captures a hash and starts approval `pending` and rights `unknown`.
3. Review the actual images for consistent silhouette, palette, tuft, eye/beak anatomy, layer alignment and usable transparency. Record a human artifact approval and an evidence-backed `commercial_use_confirmed` rights decision, then select each accepted version. Reject or replace weak files without deleting their history.
4. Add the selected artifact IDs, measured palette and written rules to a new pack manifest revision. Set `readiness: approved` only after reviewing the complete set. `CharacterAssetPack` refuses an approved manifest with missing roles, malformed palette, duplicate required images or missing identity rules.
5. Run `assess_pack_assets(pack, store, brand_revision_id)`. It checks that every listed artifact is registered under the correct brand, has the expected kind/media type, passes the store's hash/review gate, is the selected version, and has commercial-use evidence. Resolve every issue before visual work starts.

This readiness check does **not** decode every pixel, prove transparency, verify visual similarity or establish copyright ownership by itself. The human art and rights review remains required, and later visual QA will add image-level checks. No canonical images are bundled yet.

The current pack is expected to report `pack manifest remains draft`. That is an intentional gate, not a migration failure. Episodes may pin the draft pack for planning and creative work; after a storyboard is selected, the render planner returns `review character_pack` until its pinned revision is approved. Scene animation and final visual approval wait for the complete approved asset set.

