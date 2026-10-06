# ToviTunes

ToviTunes is a local production studio for preschool learning videos. The **local WebUI** is the recommended operator entry point. It reads the same episodes, selected immutable assets, approvals, rights decisions, render manifests, and SQLite database as the Python pipeline.

## Start the operator studio

Double-click **start-ui.bat** on Windows. Studio opens at **http://127.0.0.1:8766**.
The launcher uses `config.yaml`, falling back to `config.example.yaml`. It reuses healthy
local services, starts installed ACE-Step/ComfyUI when discoverable or configured, and
shows startup problems in **Settings**. Ollama is optional and only started when configured.

**Generate** offers exactly three actions: **Generate Draft**, **Generate + Render**,
and **Generate, Render & Publish**. They share one durable workflow with different
stopping targets. **Videos** lets you view a saved draft, render it later, watch a finished
video, or publish that same render. Task progress and recovery survive browser/server restarts.
The MPT sidebar and two-column production console put safe per-model outcomes in Queue Monitor.
**Dashboard** summarizes saved content; open editorial planning chooses the next lesson.

The sample config disables YouTube. Configure the intended channel and OAuth client once,
then use **Settings → Connect YouTube**. An explicit Publish click authorizes publication
without `automation.auto_publish`; configured release, rights, review and visibility policy
still apply. That flag continues to govern unattended commands.

See [Studio V1](docs/STUDIO_V1.md) for setup, target boundaries, service discovery, durable
library/progress rules and browser recovery. The existing CLI/API entry points remain
available for advanced operation with their previous publication semantics.

## Development and production reference

The first Colors Red pilot can be rendered locally from the selected production
artifact graph. See [production rendering](docs/PRODUCTION_RENDER.md) for the
MoviePy extra, system FFmpeg requirements, CLI, deterministic QA, and review exports.

Production handoff and renderer-facing storyboard contracts:
[Production storyboard V1](docs/PRODUCTION_STORYBOARD.md).

The selected pilot can be rendered and reviewed locally. Private YouTube test uploads require an explicit operator action and a passing release preflight. Public publication uses the existing gated same-video promotion service.

## Create the next creative episode

New autonomous episodes use an open Creative Director topic planner and persistent editorial
memory. The Director invents a preschool lesson and working title; deterministic policy and
history checks persist an immutable LearningBrief before expensive production. Future subjects
need no curriculum YAML additions. `creative generate-next --live` creates the next creative
episode. `creative doctor` and `creative history` inspect settings and memory without provider
calls. [Open editorial memory](docs/OPEN_EDITORIAL_MEMORY_V1.md) documents schemas, duplicate
rules, optional embeddings, donor adaptation, migrations and legacy Colors compatibility.

## Create a legacy Colors episode manually

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
