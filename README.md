# ToviTunes

Standalone children's educational musical-video production project. The current foundation provides versioned brand and curriculum data, character-pack identity, typed creative contracts, SQLite persistence, immutable file ingestion, review gates, and a dry continuation planner. The Tovi visual pack is a draft metadata shell; animation and external providers come later.

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

See [the architecture proposal](docs/ARCHITECTURE_PROPOSAL.md), [artifact store guide](docs/ARTIFACT_STORE.md), [continuation guide](docs/CONTINUATION.md), [creative draft guide](docs/CREATIVE.md), [offline music benchmark rubric](docs/MUSIC_BENCHMARK.md), and [development guide](docs/DEVELOPMENT.md) for scope and verification commands.


