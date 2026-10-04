# ToviTunes

ToviTunes is a local production studio for preschool learning videos. The **local WebUI** is the recommended operator entry point. It reads the same episodes, selected immutable assets, approvals, rights decisions, render manifests, and SQLite database as the Python pipeline.

## Start the operator studio

From the repository root on Windows:

```powershell
uv sync --python 3.11 --locked --extra web --extra youtube --extra video-render
Copy-Item config.example.yaml config.yaml
.\start-ui.bat
```

Open **http://127.0.0.1:8765**. The batch script uses `config.yaml` when present, and `config.example.yaml` otherwise. The Python equivalent is:

```powershell
uv run --locked --extra web --extra youtube --extra video-render python -m tovitunes.web --config config.yaml
```

The sample config keeps YouTube disabled. Set `expected_youtube_channel_id`, enable `publication.youtube.enabled`, and supply a local installed-app OAuth client secret only when you are ready to connect. The UI has a private-test upload action; it has no public publication action. See [WebUI and YouTube V1](docs/WEBUI_YOUTUBE_V1.md) for setup, routes, preflight policy, and recovery behavior.

The current WebUI operates existing selected production assets. Creative eligibility is read-only. Generating a complete new Short from concept through music, storyboard, render, and publication is the next orchestration milestone.

## Development and production reference

The first Colors Red pilot can be rendered locally from the selected production
artifact graph. See [production rendering](docs/PRODUCTION_RENDER.md) for the
MoviePy extra, system FFmpeg requirements, CLI, deterministic QA, and review exports.

Production handoff and renderer-facing storyboard contracts:
[Production storyboard V1](docs/PRODUCTION_STORYBOARD.md).

The selected pilot can be rendered and reviewed locally. Private YouTube test uploads require an explicit operator action and a passing release preflight. Public publication remains a future step.

## Create a Colors episode

After `uv sync --python 3.11 --locked --extra dev`, run this from the repository root in Python:

```python
from pathlib import Path

from tovitunes.catalog import load_brand
from tovitunes.domain.episode import Episode
from tovitunes.persistence.db import Database

catalog = load_brand(Path("brands/tovitunes"))
episode = Episode.create(catalog, "red", "colors-red")
db = Database(Path("data/tovitunes.db"))
db.migrate()
db.create_episode(catalog, episode)
print(db.get_episode(episode.episode_id).model_dump_json(indent=2))
```

See [the architecture proposal](docs/ARCHITECTURE_PROPOSAL.md), [artifact store guide](docs/ARTIFACT_STORE.md), [continuation guide](docs/CONTINUATION.md), [creative draft guide](docs/CREATIVE.md), [offline music benchmark rubric](docs/MUSIC_BENCHMARK.md), [character pack intake](docs/CHARACTER_PACK_INTAKE.md), [offline visual benchmark protocol](docs/VISUAL_BENCHMARK.md), and [development guide](docs/DEVELOPMENT.md) for scope and verification commands.

For local validation, install the `dev`, `web`, `youtube`, and `video-render` extras, then run `uv run ruff check .`, `uv run mypy src`, and `uv run pytest -q`.

