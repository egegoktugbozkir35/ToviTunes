# Production sprite render

Install `uv sync --extra dev --extra video-render` and system FFmpeg/ffprobe.
MoviePy remains pinned to 2.2.1 and optional for minimal installation.

```powershell
uv run python -m tovitunes.cli --config <CONFIG> production render --episode-key <EPISODE_KEY>
```

The config must point to the production database, AssetStore, and matching approved
brand catalog. Selected audio, alignment, measured beats, and TimedStoryboard must
already be admitted. The render command never generates or analyzes music, changes
storyboard times, modifies the canonical Tovi pack, or contacts providers.

The adapter resolves `SceneComposition` before drawing. Nine normalized semantic
screen slots define character/object regions; the approved pointing pose's renderer
metadata declares its gesture direction. Directional targets occupy the facing side.
Presentation alternates sides for new targets and preserves an existing target.
Recall groups objects together; singing uses a central character with a primary and
smaller secondary object arc. Objects rest/move on the meadow's ground plane.
Recall/performance deliberately displays recalled objects above the character; the
plan records this semantic display exception to grounded placement.

Scenes under 0.6 seconds (18 frames at 30 fps) retain an existing arrangement/pose
when all required targets are already present, continue the original motion clock,
and add a small vertical pulse with zero displacement at each boundary. New required
content still appears. A renderer-level explicit reset can opt out; the current
admitted storyboard schema has no reset field and is not changed by this revision.
Empty scenes inherit only established visual elements. State IDs, source scenes,
placements, sprite role, persistence, and emphasis are persisted in PNG metadata.

Post-lyric tail duration comes from the canonical lyric end and storyboard end.
Tails longer than three seconds have celebration, recap, and settle phases inside
the existing scene. Hops use measured downbeats only. Gentle non-rhythmic drift
provides activity without inventing evidence; settling begins after available
rhythm and ends in a stable closing hold. Animation JSON records phase boundaries,
actual measured events, and continued clocks. Mouth animation remains unsupported.

Every supported prop shares `preschool_soft_v1`: 4x internal drawing and LANCZOS
downsampling, soft cast shadow, rounded silhouettes, tonal shading/contour, and
soft highlight. The supported prop registry supplies lesson colors and grounded/
rolling semantics. Layout does not inspect curriculum IDs, lesson words or scene IDs.

The renderer identity is `tovitunes_sprite_render_v2`. Immutable scene images,
animation plans, manifest, final MP4 and media QA pin exact IDs/SHA-256s. Existing
artifacts/decisions remain; selections may move to passing new versions. Unchanged
invocations reuse the same 21 artifacts without encoding or database changes.
V1 remains reproducible using its pinned commit and immutable inputs/outputs.

The existing architecture is retained: isolated MoviePy video worker, two-stage
FFmpeg mux with explicit stream maps, deadlines/process-tree cleanup, full probe/
decode QA, atomic finalization, and AssetStore registration. System binary discovery
prefers explicit `TOVITUNES_FFMPEG_BIN` / `TOVITUNES_FFPROBE_BIN`, then PATH; invalid
overrides fail. No bundled binary download or new generation dependency is added.

Production media stays 1080x1920, 30 fps, libx264 CRF 18/medium, yuv420p, AAC 192k,
MP4 faststart. Duration tolerance remains `2/30 + 0.005` seconds. QA checks stream
format/decode/coverage plus uniform sprite bounds, alpha visibility, prop count,
separation, directional coherence, ground-plane rolling, micro continuity, long
scene activity and outro phases. Repetition is diagnostic, not aesthetic scoring.

Exports derive from episode key: `TOVITUNES_<EPISODE_KEY>_PILOT_V2.mp4`, media QA,
all scene midpoint frames, and early/late outro frames. The AssetStore is authoritative.
Technical acceptance means local visual review only. Rights remain unknown and
publication blocked. No upload or release authorization is inferred.

CI remains provider-free and uses the tiny 270x480, two-second fixture. The full
pilot runs locally. See [donor provenance/license notice](RENDER_DONOR_NOTICE.md)
and [composition review](../GENERIC_VISUAL_COMPOSITION_V1_REVIEW.md).
