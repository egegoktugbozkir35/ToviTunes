# Development

From the repository root on Windows or another supported Python host:

```text
uv sync --python 3.11 --locked --extra dev
uv run ruff check .
uv run mypy src
uv run pytest -q
```

The first migration creates the durable identity schema. For a local smoke check, load `brands/tovitunes`, create an `Episode` for concept `red`, then call `Database.migrate()` and `Database.create_episode()`. Reopening the database preserves the pinned brand, curriculum and character-pack revisions. `CharacterAssetPack` v1 is a draft metadata shell; no canonical art is approved yet.

Runtime paths in `config.example.yaml` are relative to that file. Copy it to a local config before running code that needs a database. Keep `data/`, `secrets/` and generated media out of Git. Publication is disabled and the expected YouTube channel ID is unset by default. No provider, renderer or uploader is active in this foundation PR.

