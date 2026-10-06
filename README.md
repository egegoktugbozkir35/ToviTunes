# ToviTunes

ToviTunes is a local production studio for preschool learning videos. Its application architecture specializes [ollama-mpt-youtube](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/tree/1b82232ed794ccf611cb939ff95a1a52f3b06f52).

Double-click **start-ui.bat** on Windows. Studio opens at **http://127.0.0.1:8766**, using `config.yaml` or `config.example.yaml`. Settings shows the installed ACE-Step/ComfyUI services and any startup problem. Ollama is optional.

Generate Draft stops after the durable topic, learning brief, episode, lyrics and music direction. Generate + Render continues through ACE-Step, audio analysis, Qwen assets, Storyboard V2, MP4, QA and metadata. Generate, Render & Publish also applies release gates and publishes to the configured channel. Videos resumes a saved episode through the same application API. A restart reconstructs work from SQLite facts; queue/progress history is observational.

The sample config disables YouTube. Configure the expected channel and OAuth client, then connect in Settings. A Publish target authorizes publication subject to configured visibility, rights and human-review gates. Creative ambiguity advances to the next model without resending. An uncertain YouTube upload remains blocked until its remote outcome is reconciled.

The complete [architecture migration report](docs/MPT_ARCHITECTURE_MIGRATION.md) includes the old-to-new map, table authority, deletion measurements, generate/resume sequences, compatibility and validation.

```python
from pathlib import Path
from tovitunes.config import load_config
from tovitunes.orchestrator import build_orchestrator
from tovitunes.pipeline.targets import ProductionTarget

# Resolve config once; production dependencies are built only when needed.
app = build_orchestrator(load_config(Path("config.yaml")))
draft = app.generate(ProductionTarget.DRAFT)
render = app.resume(draft["episode_key"], ProductionTarget.RENDER)
```

Advanced CLI aliases delegate to these same two operations. Provider-free commands include `creative doctor`, `creative history`, `creative request-status` and production dry-run planning. The former creative reconciliation command was removed; PR40 decisions remain read-only audit evidence.

Domain references: [creative contracts](docs/CREATIVE.md), [editorial memory](docs/OPEN_EDITORIAL_MEMORY_V1.md), [artifact storage](docs/ARTIFACT_STORE.md), [production rendering](docs/PRODUCTION_RENDER.md), [historical storyboard data](docs/PRODUCTION_STORYBOARD.md), [release gates](docs/PUBLIC_RELEASE_V1.md), [music benchmark](docs/MUSIC_BENCHMARK.md), [character intake](docs/CHARACTER_PACK_INTAKE.md), [visual benchmark](docs/VISUAL_BENCHMARK.md), and [development](docs/DEVELOPMENT.md).

Install `dev`, `web`, `youtube` and `video-render` extras with `uv sync --locked`. Validate with `uv run --locked ruff check .`, `uv run --locked mypy src` and `uv run --locked pytest -q`. Windows CI uses system FFmpeg and the same offline suite.
