"""Domain service dependencies. Storage helpers contain no continuation decisions."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import uuid4

from tovitunes.artifacts.store import ArtifactRecord, AssetStore
from tovitunes.benchmark.providers import ImageProvider
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.creative.factory import creative_generator
from tovitunes.creative.provider import (
    StructuredGenerator,
)
from tovitunes.creative.service import CreativeService
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.music.providers import MusicProvider
from tovitunes.persistence.db import Database
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.render.episode_assets import persist_file


class StageContext:
    def __init__(
        self,
        config: RuntimeConfig,
        ownership: ProductionExecutionOwnership,
        *,
        creative_provider: StructuredGenerator | None = None,
        music_provider: MusicProvider | None = None,
        image_provider: ImageProvider | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config, self.ownership = config, ownership
        self.database = Database(config.database_path)
        self.catalog = load_brand(config.brand_root)
        self.working = config.data_root / ".short-production"
        self.working.mkdir(parents=True, exist_ok=True)
        music_root = config.data_root / "music-benchmark"
        music_root.mkdir(exist_ok=True)
        self.store = AssetStore(
            config.data_root,
            self.database,
            local_preview=True,
            generated_source_roots=[self.working, music_root],
        )
        self.creative_provider, self.music_provider, self.image_provider = (
            creative_provider,
            music_provider,
            image_provider,
        )
        self.progress = progress

    @contextmanager
    def _creative(self) -> Iterator[CreativeService]:
        if self.creative_provider is not None:
            yield CreativeService(
                self.config,
                self.creative_provider,
                progress=self.progress,
                assert_owner=self.ownership.assert_owned,
            )
        else:
            with creative_generator(self.database, self.config.creative_llm) as provider:
                yield CreativeService(
                    self.config,
                    provider,
                    progress=self.progress,
                    assert_owner=self.ownership.assert_owned,
                )

    def _selected(self, episode: Episode, kind: str, slot: str = "main") -> ArtifactRecord | None:
        self.ownership.assert_owned()
        return self.store.selected("episode", episode.episode_id, kind, slot)

    def _json(
        self,
        episode: Episode,
        kind: str,
        payload: object,
        deps: tuple[str, ...],
        provenance: Provenance | None = None,
    ) -> ArtifactRecord:
        self.ownership.assert_owned()
        path = self.working / f"{uuid4()}.json"
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        try:
            return persist_file(self.store, episode, kind, "main", path, deps, provenance)
        finally:
            path.unlink(missing_ok=True)

    def _creative_inputs(
        self, episode: Episode
    ) -> tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]]:
        records = [
            self._selected(episode, kind) for kind in ("episode_spec", "lyrics", "music_spec")
        ]
        if any(record is None for record in records):
            raise ValueError("creative stage did not retain selected artifacts")
        ids = tuple(record.identity.artifact_id for record in records if record)
        assert len(ids) == 3
        creative_ids = (ids[0], ids[1], ids[2])
        spec = EpisodeSpec.model_validate(self.store.read_json(ids[0]))
        lyrics = LyricsSpec.model_validate(self.store.read_json(ids[1]))
        music = MusicSpec.model_validate(self.store.read_json(ids[2]))
        creative_music_spec(episode, spec, lyrics, music, creative_ids)
        return spec, lyrics, music, creative_ids
