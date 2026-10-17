# Explicit dotted brushing cue

PR #48's bathroom and farmyard `environment_scene_v2` behavior is preserved. The
remaining pale Qwen circle is a decorated disc, and `qwen_white_exterior_v2`
correctly rejects its unsafe trim. Neither extraction nor the original source is
relaxed or repainted by this correction.

## Small opt-in contract

An optional `teaching_cue` enum is supported on `CreativeObject` and the compiled
`VisualRequirement`. Its only value is `dotted_circle_brushing_v1`. An explicitly
authored local proposal object can request it:

```json
{
  "name": "circular brushing path",
  "description": "A dotted clockwise path showing round-and-round brushing",
  "teaching_cue": "dotted_circle_brushing_v1"
}
```

The compiler carries the enum to the asset requirement and marks it ungrounded.
This is local, explicit metadata: it is deliberately omitted from model-facing
JSON schemas, keeping historical structured instructions and request fingerprints
unchanged. The creative provider does not automatically introduce the cue. Automatic
proposal of symbols would require a separately versioned future creative contract;
that work is outside this correction.
Direct requirements must be static, ungrounded, non-decorative `scene_element`s,
with no face or target-color override. Unknown cue values and conflicting
requirements fail validation. Text, names, asset keys and lesson topics never
select this route. A toothbrush, animal, character or environment continues
through its existing provider/approved-pack route. Other symbols remain ordinary
illustrations; this change does not introduce a generic symbol classifier.

`props.dotted_circle_brushing` uses the existing four-times supersampling approach:
ten teal dots, two clockwise tangent arrowheads, narrow white contrast contours,
and a fully transparent center and exterior. There is no filled disc, face,
shadow, white-background extraction or provider operation. The stage persists a
normal immutable RGBA visual asset with the pinned plan dependency and versioned
deterministic provenance. Existing prop loading, composition and motion are used.
It is a static direction cue; it does not implement tracing around another prop
or a new animation channel.

## Historical boundary

Absent/null cue fields are omitted from JSON, preserving historical proposal
hashes, compiler keys and visual-plan serialization. Model-facing schema and
structured-instruction hashes are frozen in regressions. An explicit cue changes the
object hash and therefore receives a distinct asset key. Existing selected assets
are checked and reused first, byte-for-byte, with the existing plan-dependency
guard. Any prior Qwen request for an unselected cue slot blocks for explicit
reconciliation in all five ledger states. It cannot be bypassed by local drawing.
Historical provider specs, fingerprints, receipts, source bytes and selected
videos are never rewritten. There is no automatic conversion of retained plans.

Using the cue in a future production requires an explicitly authored new plan and
the normal existing production workflow. This task only supplies an offline
unselected proof; it does not change the retained episode or select new media.

## Offline evidence and checks

`review/teaching_cue_preview.py` reads PR #48's existing evidence and approved Tovi
PNG and writes an output directory separate from those inputs. It opens no DB,
loads no config/provider and performs no network requests. It compares the failed
circle's **V1 diagnostic only** with the deterministic cue over the same accepted
bathroom image, using the same retained Tovi and cue bounds. Both frames are
360×640; the cue occupies the retained 108×108 slot. Checker, white, dark and warm
background tiles show clean transparency and contrasting dots at that actual size.
The source remains V2-blocked. PNG/input hashes and placement are recorded in
`preview-evidence.json`; `visual-proof.html` embeds the comparison images.

```powershell
uv run python review/teaching_cue_preview.py --evidence-root <pr48-qwen-validation> --sprite <approved-png> --output <separate-output-dir>
uv run pytest -q tests/test_teaching_cues.py tests/test_qwen_art_quality.py tests/test_composition.py tests/test_motion.py tests/test_lesson_objects.py tests/test_creative_production_v3.py tests/test_visual_story.py
uv run ruff check .
uv run mypy src
```

Focused regressions cover frozen circle identity/JSON, typed opt-in compilation,
conflicting/unknown values, provider-free drawing/reuse, ordinary free-text Qwen
routing, all historical request states, selected illustration preservation and
plan guards, 360×640 composition and existing motion-envelope validation. Existing
environment and prompt/extraction tests retain V1 fingerprints and V2 protections;
the setting regression includes bathroom and farmyard.

PASS applies to this explicit symbol and its offline readability/transparency.
It does not approve every illustrated prop, change the accepted environments or
authorize production, more Qwen calls, music, publication or merging PR #48.
