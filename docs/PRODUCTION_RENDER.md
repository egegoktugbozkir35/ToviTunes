# Colors Red pilot render

Install `uv sync --extra dev --extra video-render` and system FFmpeg/ffprobe.
MoviePy is pinned to 2.2.1 and is not required for minimal installation.

```powershell
uv run python -m tovitunes.cli --config <CONFIG> production render --episode-key colors-red-001
```

The config must point to the existing production database, AssetStore, and the
matching versioned brand catalog containing the approved pack. PR #26 must be
merged and its production handoff selected. Rendering reads selected production
artifacts exclusively; it does not query benchmark tables, regenerate music,
run analysis, or contact providers.

Discovery prefers `TOVITUNES_FFMPEG_BIN` / `TOVITUNES_FFPROBE_BIN` file overrides,
then each system executable on PATH. A bad explicit override fails clearly.
Doctor: `uv run python -c "from tovitunes.render.ffmpeg import doctor; print(doctor())"`.
MoviePy is forced to this resolved FFmpeg before import. The transitive
imageio-ffmpeg dependency is not used to discover or download a bundled binary.

The command ensures immutable `scene_image/<scene_id>` PNGs (composition metadata
embedded in PNG text), `character_animation/<scene_id>` JSON, `render_manifest/main`,
`final_render/main`, and `media_qa/main`. Dependencies pin exact IDs and SHA-256s.
The same unchanged inputs reuse these identities and decisions and do not encode
again. Changed inputs require new immutable downstream artifacts; old bytes and
decisions are retained. A rejected/needs-review artifact is not auto-approved.

MoviePy performs image/sprite/rolling-ball composition and direct scene cuts.
The encode worker has a 1,800-second process-tree deadline. FFmpeg muxes with
explicit video/audio maps, video stream copy, AAC 192k, and `+faststart` (120-second
deadline). ffprobe has a 30-second deadline. A temporary mux file must pass stream,
format, duration, and full decode QA before atomic finalization and registration.
Failed encode, mux, or QA cannot select a final render.

Production output is 1080×1920, 30 fps, libx264 CRF 18/medium, yuv420p, AAC, MP4.
Container and both stream durations must match selected source duration within
two frames plus 5 ms: `2/30 + 0.005` seconds. Sample/display aspect ratio is
checked, as are full scene coverage, safe uniform character layout, visible alpha,
exact prop counts, lesson red `#E53935`, and clear educational-object bounds.

Motion uses selected Beat This timestamps without changing scene/vocal timing.
The zero-downbeat final lyric receives no invented hop. Existing mouth components
have a common component normalization anchor, but no registered anchor/scale
relative to the singing sprite. V1 records `mouth_animation_supported=false` and
uses the approved singing pose as-is; no offsets or speech inference are added.

Determinism means equivalent scene composition, timeline, sprite choices, motion,
audio input, and codec configuration. MP4 bytes need not be identical across
FFmpeg builds/machines. Reuse on the same artifact graph is byte-identical.

Exports under the project's `outputs/` include the MP4, media QA JSON, and a PNG
frame at each scene midpoint. The AssetStore is authoritative. Each export copies
the registered bytes. `technical_render_v1` means technically suitable for local
visual review. Music rights remain unknown and publication remains blocked.
No upload, commercial-rights decision, subtitles, or external generation is run.

CI installs the optional extra and system FFmpeg, then runs a 270×480 two-second
three-scene fixture. The full production pilot is rendered only locally.
See [donor provenance/license notice](RENDER_DONOR_NOTICE.md).
