# Gate 2 image and asset contracts — independent work

Decision: **BLOCKED**. Independent Gate 2 implementation is reviewable. The
whole-file release of `render/episode_assets.py` from Gate 1 is still pending.
New-Qwen quality proof is unproven and no live inference is authorized. This is
not a Gate 2 acceptance or a Gate 3 interface freeze.

Base: `ea3495ae44f2f21018e06db19c90ab49f94c70ca`; branch
`codex/v2-gate2-art-assets`; draft PR targets `codex/v2-gate0-baseline`.
The Gate 0 owner approval is recorded at
[PR #50](https://github.com/egegoktugbozkir35/ToviTunes/pull/50#issuecomment-6270140245).
The remote head matched the approved exact SHA before branching; its diff from
`790da5502a966675b94a32838e303a97fb16503e` contains Gate 0 documentation only.
The separate Gate 2 worktree was created directly from that SHA. Gate 1 files
and uncommitted work are not incorporated.

## Implemented behavior

- Fresh episode environments use `environment_scene_v3`. Each role's setting is
  authoritative. Role identifiers never become biome instructions. The episode's
  catalog art direction and shared rounded/tonal/light/palette language are supplied
  consistently without forcing a location, palette, subject inventory or composition.
  The original four-role architecture stays intact. Classroom, bathroom, outdoor,
  underwater and other plan descriptions remain data. The creative planner is unchanged.
- `environment_opaque_noise_v2` retains the existing mean-opacity requirement and
  also refuses any alpha below 200 before flattening a fresh scene-v3 environment.
  The retained Qwen environment inventory has pixel minima of 207 or higher, while
  a localized transparent hole can pass the previous frame-average check. This is
  a conservative technical opacity guard, not a new aesthetic acceptance score.
  RGB/palette transparency and static-image restrictions cannot bypass the check.
  Raw sources remain byte-identical and available for inspection.
  Final scene-v3 admission rechecks the raw source contract, so a derivative from
  explicit recovery using an older normalizer cannot hide an unsafe alpha hole.
- `qwen_white_exterior_v3` is an explicit independent extraction primitive. Like V2,
  it removes only border-connected backdrop colors, preserves RGB and retained alpha,
  and never grows masks, paints contours or deletes specks. V2's neighbor check can
  mistake two-pixel streaks and 2x2 specks for useful contour support. V3 checks full
  eight-connected components: an outer crop bound supported only by detached
  components of at most four source pixels is rejected. Attached diagonal tips and
  meaningful detached parts survive. Four pixels is a noise guard, not a mobile
  recognition score. Sources inseparable from white padding are still rejected.
- New image artifacts require container verification and full pixel decode before
  registration. Animated artwork is refused as unsupported static art. Validation
  facts are recorded as `file-image-decode-v2`. Old validation records are not rewritten;
  reinspection verifies bytes without re-normalizing them. Corrupt source files are
  not selected or described as usable merely because their PNG header is valid.
  Rejected provider source bytes are retained in the existing quarantine directory,
  with their path in the rejection diagnostic, without registration or selection.
- Environment admission checks manifest owner, exact ordered roles, each plate's
  owner/kind/slot/hash and its manifest dependency, and each retained source's
  owner/kind/slot/hash and derivative dependency. Episode request specs must still
  match their original planned setting. The episode binding records the visual-plan
  hash as well as its artifact ID. Existing historical bindings without that added
  hash remain readable. Shared approved sets retain their distinct brand ownership.

## Version and request identity

`environment_world_v1`, `environment_scene_v2`, illustration V2/V3, extraction
V1/V2, explicit cue enums and historical compiler/provider schemas remain available
with their old meanings. `EnvironmentPlate.normalization_version` is optional and
omitted when absent, so old serialized manifests do not gain a null field.

Scene-v3 set identity includes the exact episode art direction and normalization
version, alongside episode, role descriptions and existing provider/workflow profile.
Canonical scene specs include the actual prompt. Changing those inputs cannot
silently reinterpret a prepared, ambiguous, failed or succeeded old request: existing
same-episode mismatch checks require explicit reconciliation. Successful admitted
foregrounds continue through their existing reuse path. No historical source is
renormalized in production, reselected, reconciled or retried by this work.

Frozen scene-v2 sample fingerprint:
`70fdff48746ef3648e69fde33f76787dbdf9df20237b08e9f7f60c84f2ff8d33`
for role `meadow_wide`, brand `brand`, attempt 1, no reference and scene
`A cozy indoor playroom`. Existing frozen world/foreground/extraction tests remain.

## Shared-file dependency and Gate 3 ownership

No edits to `render/episode_assets.py`, Gate 1 reliability modules, creative
planner/lyrics, domain schemas, configuration, migrations, workflow/model stack,
renderer defaults or publication code are included.

`episode_assets.py` is byte-identical to the approved Gate 0 base. Its foreground
prompt, receipt profile and extraction-version dispatch remain unchanged, so V3
extraction is **not yet used by fresh production foregrounds**. No replacement
entry point or alternate asset pipeline is introduced. After an explicit Gate 1
release: pin and incorporate its exact commit, verify durability/restart checks,
then make only the authorized foreground prompt/admission changes and preserve
historical profile/version routing. Do not copy uncommitted files or guess a SHA.

`services/visual.py`, `artifacts/media.py`, `artifacts/store.py` remain held by Gate 2.
No domain-schema changes were needed for independent work. The Gate 2-to-Gate 3
schema/compiler handoff stays pending until the complete Gate 2 contracts are frozen;
this report does not authorize later-gate edits.

## Offline visual evidence and limits

Private raw media and comparisons are retained in the task's `outputs/comparisons/`
with an HTML index, per-case source/derivative/mobile PNGs, immutable historical
receipts/derivatives where present, and a SHA-256 manifest. The reproducible builder
is retained outside Git. No private source media or database is committed.

The 14 cases cover retained indoor bathroom and outdoor farmyard sources; pale
teeth, cup, dog, duck and barn illustrations; an unsupported pale disc; closed pale
contours, two-pixel specks, invisible-white and colored-border fixtures; an explicit
geometric cue; and a stacked raw environment failure. Review compares raw,
V1/V2/V3 derivatives or rejection diagnostics, plus 360x640 static staging proxies
using the approved retained sprite and a 108px foreground box. Proxies are not
complete renders, animation evidence or production selections.

Observed: the cup, duck and barn remain recognizable in the static proxies; the
white closed-contour fixture retains its interior where V1 loses the object. Teeth,
dog and the unusable pale disc remain refused by conservative extraction because
backdrop specks control their crop bounds. They are not silently repaired. Existing
stacked landscapes and unwanted subject details originate in raw source pixels;
technical decode/alpha checks do not certify their editorial correctness. A static
proxy also cannot establish final grounding, transitions or performance quality.
The retained examples do not prove that the new prompts improve future Qwen output.

Read-only compatibility rechecks parse all 20 retained storyboard/render manifests,
recompute all 85 stored illustration profiles and 40 environment fingerprints, and
fully decode 606 registered images across three databases. Fifteen older environment
snapshot rows lack a retained set fingerprint; they remain explicitly unproven.
All 30 configured model/config files still match the Gate 0 inventory at the initial
Gate 2 read. Before/after protected-state and final exact-head validation results
are retained with the local gate report and PR checks.

## Validation and live-test boundary

Run the locked Python 3.11 environment, Ruff, strict Mypy, approved pack validator,
focused image/asset/cue/provider tests, unchanged native-crash regressions, and full
offline suite. Hosted Windows CI must check the exact delivery head; a passing base
or earlier commit is insufficient. No coverage is disabled or historical fixture
bytes changed. Final validation logs and hosted run identity accompany delivery.

After the Gate 1 release and foreground contract freeze, the proposed small Qwen
comparison is **10 local requests maximum**, one A/B pair for each: indoor classroom
environment, outdoor farm environment, white/pale cup, animal illustration, and an
everyday teaching object. Environments compare scene-v2/v3; foreground B must first
be defined in the released shared file. Add the explicit geometric cue with zero
calls. Freeze exact canonical specs, translated workflow hashes and their naturally
derived seeds before seeking authorization; seeds change with prompt identity, so
this is not a same-seed causal experiment. One output per spec, no best-of-N,
automatic retry, replacement grant, music, creative call, complete episode or paid
inference. Retain all failures and stop. **This proposal is not authorization.**
The final request set cannot be approved as executable until the shared-file work
and exact request inventory are concrete. No requests were made here.
