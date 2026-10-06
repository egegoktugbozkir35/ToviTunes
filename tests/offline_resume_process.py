"""Isolated offline restart worker; never opens live provider endpoints."""

import json
import sys
from pathlib import Path

import pytest
from test_short_production import connect_offline

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.service import committed_curriculum_digest, episode_by_key
from tovitunes.persistence.db import Database
from tovitunes.pipeline.targets import ProductionTarget

config = RuntimeConfig.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8"))
key, stop, output = sys.argv[2:]
patch = pytest.MonkeyPatch()
patch.setattr("socket.socket.connect", lambda *a: pytest.fail("live network in restart worker"))
committed_root = Path(__file__).parents[1] / "brands/tovitunes"
patch.setattr(
    "tovitunes.creative.service.committed_curriculum_digest",
    lambda root, catalog: committed_curriculum_digest(committed_root, catalog),
)
database = Database(config.database_path)
episode = episode_by_key(database, key)
store = AssetStore(config.data_root, database, local_preview=True)
ids = tuple(
    store.selected("episode", episode.episode_id, kind, "main").identity.artifact_id
    for kind in ("episode_spec", "lyrics", "music_spec")
)
fixture = connect_offline(config, episode, ids, FakeNIMTransport(), patch)


def crash(progress):
    if progress.stage == stop and (
        progress.detail.startswith("Running ") or progress.stage == "COMPLETED"
    ):
        raise SystemExit(75)


fixture["flow"]._reporter = crash
code = 0
result = {}
try:
    result = fixture["flow"].resume(key, ProductionTarget.RENDER)
except SystemExit as exc:
    code = exc.code
Path(output).write_text(
    json.dumps(
        {
            "result": result,
            "music_events": fixture["music_events"],
            "image_events": fixture["image_events"],
            "analysis_calls": len(fixture["analysis_calls"]),
        }
    ),
    encoding="utf-8",
)
patch.undo()
raise SystemExit(code)
