# Development

From the repository root on Windows or another supported Python host:

```text
uv sync --python 3.11 --locked --extra dev --extra video-render --extra web --extra youtube
uv run ruff check .
uv run mypy src
uv run pytest -q
```

The numbered migrations create the durable identity, artifact-validation, continuation, and visual-benchmark schemas. For a local smoke check, load `brands/tovitunes`, create an `Episode` for concept `red`, then call `Database.migrate()` and `Database.create_episode()`. Reopening the database preserves the pinned brand, curriculum and character-pack revisions. The [artifact store guide](ARTIFACT_STORE.md) covers file ingestion, decisions, selection and recovery. The [MPT architecture migration](MPT_ARCHITECTURE_MIGRATION.md) covers the single read-only planner, request identity and singleton production lease. The [creative draft guide](CREATIVE.md) covers the objective, premise and lyrics review gates with an offline fake. The [character pack intake guide](CHARACTER_PACK_INTAKE.md) documents offline asset preparation, approval, and clean-machine rehydration of Tovi's approved v1 pack. The [visual benchmark guide](VISUAL_BENCHMARK.md) covers provider setup, dry runs, blind review, resumption, rights, cost, and reporting.

Runtime paths in `config.example.yaml` are relative to that file. Copy it to a local config before running code that needs a database. Keep `data/`, `secrets/` and generated media out of Git. Publication is disabled and the expected YouTube channel ID is unset by default. Normal production uses the shared application orchestrator. Benchmark commands remain explicit domain tools. Dry plans and the offline test suite never call external services; rendering tests use real FFmpeg with mocked provider transports.

Music ML dependencies remain separate optional extras: `audio-analysis`,
`audio-asr`, and `audio-timing` (`beat-this==1.1.0`). Ordinary development and CI
use the installation above, mock inference and require no CUDA or
model downloads. The [music benchmark guide](MUSIC_BENCHMARK.md#production-beatdownbeat-timing)
documents explicit `analysis-models prepare-timing`, verified local `final0`,
offline inference, detector provenance and measured pre/post-lyric edges.
Use a dedicated production environment; preserve the verified Windows CUDA/ASR
versions rather than running ordinary `uv sync` over its cu128 wheels.
Changed analyzer source or configuration requires the next unused immutable
analysis/timing version. The compatible `artifact_free` gate evaluates configured
objective digital defects only; broad perceptual perfection is not claimed.
