# Production storyboard V1

The Colors — Red pilot promotes retained, accepted candidate-2 audio and measured
analysis version 3 into the existing production `AssetStore`. It makes no generation,
analysis, policy reevaluation, model-loading, or provider calls.

```powershell
uv run python -m tovitunes.cli --config <production-config.yaml> production prepare-storyboard --concept red --episode-key colors-red-001 --music-blind-id mb_3f657849e3d04060a0107940b098fb60 --analysis-version 3 --dry-run
uv run python -m tovitunes.cli --config <production-config.yaml> production prepare-storyboard --concept red --episode-key colors-red-001 --music-blind-id mb_3f657849e3d04060a0107940b098fb60 --analysis-version 3
```

The configuration must point to the authoritative existing database and retained data
root, and to a brand catalog containing `storyboards/colors_red_v1.yaml`. The pilot
checks the known source identity against persisted request, receipt, output, analysis,
timing, current machine QA v2, and timing-policy v1 records. QA and timing evidence must
bind the exact SHA and analysis version. Different source identity or evidence fails
closed; the expected constants never substitute for missing persisted evidence.

The read-only plan validates source and semantic inputs before initializing the store
or acquiring a lease. Execution uses the existing per-resource lease, repeats preflight,
and creates/reuses `Episode.create(...)` identity with catalog-derived revisions. An
existing external key with a different curriculum, objective, concept, brand, or Tovi
pack fails. Later rejected/needs-review decisions are respected.

Artifacts are append-only immutable versions with SHA-pinned dependencies:

```text
audio_master -> production_handoff
audio_alignment -> audio_master
beat_analysis -> audio_master
timed_storyboard -> audio_master, audio_alignment, beat_analysis
```

`production_handoff` is a small immutable source manifest retaining provider/model,
local/provider request IDs, prompt contract, blind ID, attempt, SHA, byte count and
duration. Audio provenance remains provider provenance in the existing fields. The
master is a byte-preserving AssetStore copy of the benchmark MP3; benchmark persistence
is never rewritten.

JSON serialization uses sorted keys, compact separators and finite numbers. Identical
content and pinned input IDs reuse immutable versions. Reruns skip existing approvals
and selection writes, preserving selection timestamps. Interruption after an ingest
can reuse that valid version on the next run. Changed content is never rewritten over
an old version. This frozen pilot rejects another source/version; future handoffs need
an explicitly versioned source pin. Existing different selections require explicit
replacement rather than silently overriding the selected graph.

`canonical_curriculum_v1` is a machine decision approving the pinned curriculum
objective **only for technical production planning**. `technical_production_master_v1`
approves validated production artifacts **only for storyboard/render development**.
Neither decision approves the benchmark music output, final video, publication, or
commercial rights. Music-output approval remains pending. Every new artifact records
unknown rights, so JSON artifacts retain the transitive music release blocker.

The renderer-facing models live in `domain/storyboard.py`:

- `AudioAlignment`: exact admitted words, lyric lines, sections and measured lyric edges;
  audio master/SHA, duration, source blind ID and analysis version. No CTC scores.
- `BeatAnalysis`: exact beats/downbeats/BPM, audio identity, source version, detector and
  checkpoint identity. No beat interpolation or quantization.
- `TimedStoryboard`: production artifact IDs, episode/audio identity, pinned Tovi pack,
  template identity/hash and ordered `TimedScene` records.
- `TimedScene`: measured start/end, kind/section, actual lyric interval, visual focus,
  required props, semantic Tovi action, target concept and beat/downbeat index ranges.

The builder reads creative intent from the version-controlled YAML template. Each lyric
position must match its canonical text exactly; each line is used once. The first scene
starts at zero, every adjacent boundary is equal, and the final scene ends at actual
duration. Intro/outro are omitted only when their measured intervals are zero. A lyric
scene ends at the next lyric start, except the final lyric ends at its actual lyric end.
The sung `lyric_start` and `lyric_end` remain unchanged even when visuals span a gap.

Beat index ranges are `[start_index, end_index)` slices of the selected global arrays.
Scene temporal ranges are half-open; the final scene includes an event exactly at audio
duration. Auxiliary beats never move a lyric boundary. Empty scene beat ranges are valid.

V1 prop IDs declare exactly one object each: `red_swatch`, `red_apple`, `red_ball`.
Apple/ball lines require their corresponding red prop. Teaching target is red, cast is
only pinned Tovi, and actions are `enter`, `idle`, `point`, `present`, `question`, `sing`,
`celebrate`. Intro/outro contain no lyric or teaching claim. No images, animation, or
renderer implementation are included.

`load_snapshot()` reads the real selected storyboard, validates its references, lyric
intervals, character pin and beat associations, and discovers scene IDs for render
requirements. The historical tiny `TimedStoryboardIndex` remains only as a compatibility
reader for old persisted artifacts. Selected provider audio with its matching pinned
handoff manifest satisfies the existing planner's music entry point, without generating
new pre-music creative drafts. Release planning still traverses unknown transitive rights.

The production closeout and real artifact identities are in
[`TIMED_STORYBOARD_V1_PRODUCTION_HANDOFF.md`](../TIMED_STORYBOARD_V1_PRODUCTION_HANDOFF.md).
