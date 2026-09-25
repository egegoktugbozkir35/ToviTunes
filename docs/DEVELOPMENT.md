# Development

From the repository root on Windows or another supported Python host:

```text
uv sync --python 3.11 --locked --extra dev
uv run ruff check .
uv run mypy src
uv run pytest -q
```

The numbered migrations create the durable identity, artifact-validation, continuation, and visual-benchmark schemas. For a local smoke check, load `brands/tovitunes`, create an `Episode` for concept `red`, then call `Database.migrate()` and `Database.create_episode()`. Reopening the database preserves the pinned brand, curriculum and character-pack revisions. The [artifact store guide](ARTIFACT_STORE.md) covers file ingestion, decisions, selection and recovery. The [continuation guide](CONTINUATION.md) covers dry planning, request identity and leases. The [creative draft guide](CREATIVE.md) covers the objective, premise and lyrics review gates with an offline fake. The [character pack intake guide](CHARACTER_PACK_INTAKE.md) documents offline asset preparation, approval, and clean-machine rehydration of Tovi's approved v1 pack. The [visual benchmark guide](VISUAL_BENCHMARK.md) covers provider setup, dry runs, blind review, resumption, rights, cost, and reporting.

Runtime paths in `config.example.yaml` are relative to that file. Copy it to a local config before running code that needs a database. Keep `data/`, `secrets/` and generated media out of Git. Publication is disabled and the expected YouTube channel ID is unset by default. Visual providers run only through an explicit live `visual-benchmark run`; dry runs and the test suite never call external services. No renderer or uploader is active yet.
