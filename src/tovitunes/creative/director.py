"""Provider-neutral director satisfying the existing DraftGenerator boundary."""

import json
from collections.abc import Callable
from contextlib import closing
from typing import Any

from tovitunes.catalog import BrandCatalog
from tovitunes.creative import prompts
from tovitunes.creative.provider import GenerationContext, StructuredGenerator
from tovitunes.creative.validation import validate_episode_spec, validate_lyrics, validate_music
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.persistence.db import Database
from tovitunes.pipeline.creative import GeneratedDraft


def pinned_facts(catalog: BrandCatalog) -> dict[str, Any]:
    return {
        "brand": catalog.definition.model_dump(mode="json"),
        "brand_revision": catalog.version.model_dump(mode="json"),
        "curriculum_revision": catalog.curriculum_revision.model_dump(mode="json"),
        "creative_bible": catalog.creative_bible.model_dump(mode="json"),
        "safety_policy": catalog.safety_policy.model_dump(mode="json"),
        "characters": [c.model_dump(mode="json") for c in catalog.characters],
        "character_packs": [p.model_dump(mode="json") for p in catalog.pack_revisions],
    }


def validate_pins(episode: Episode, catalog: BrandCatalog) -> None:
    expected = Episode.create(catalog, episode.concept_id, episode.external_key)
    fields = {
        "brand_revision_id",
        "curriculum_revision_id",
        "concept_id",
        "objective_id",
        "objective",
        "target_vocabulary",
        "language",
        "target_duration_seconds",
        "character_packs",
    }
    if episode.model_dump(include=fields) != expected.model_dump(include=fields):
        raise ValueError(
            "episode differs from exact pinned catalog identity, objective or vocabulary"
        )


class CreativeDirector:
    def __init__(
        self,
        catalog: BrandCatalog,
        database: Database,
        provider: StructuredGenerator,
        *,
        assert_owner: Callable[[], None] = lambda: None,
    ) -> None:
        self.catalog, self.database, self.provider = catalog, database, provider
        self.assert_owner = assert_owner

    def _facts(self, episode: Episode, variant: int) -> dict[str, Any]:
        validate_pins(episode, self.catalog)
        with closing(self.database.connect()) as db:
            row = db.execute(
                "SELECT selected_subject_json FROM creative_runs WHERE episode_id=?",
                (episode.episode_id,),
            ).fetchone()
        return {
            **pinned_facts(self.catalog),
            "episode": episode.model_dump(mode="json"),
            "selected_subject": json.loads(row[0]) if row and row[0] else None,
            "variant": variant,
        }

    def _context(self, episode: Episode, kind: str, version: str) -> GenerationContext:
        return GenerationContext(
            kind, version, episode_id=episode.episode_id, assert_owner=self.assert_owner
        )

    def episode_spec(self, episode: Episode, *, variant: int) -> GeneratedDraft[EpisodeSpec]:
        return self.provider.generate(
            EpisodeSpec,
            prompts.episode_messages(self._facts(episode, variant)),
            context=self._context(episode, "episode_spec", prompts.EPISODE_PROMPT),
            validate=lambda spec: validate_episode_spec(episode, spec),
        )

    def lyrics(
        self,
        episode: Episode,
        spec: EpisodeSpec,
        spec_artifact_id: str,
        *,
        variant: int,
    ) -> GeneratedDraft[LyricsSpec]:
        validate_episode_spec(episode, spec)
        facts = {
            **self._facts(episode, variant),
            "episode_spec": spec.model_dump(mode="json"),
            "episode_spec_artifact_id": spec_artifact_id,
        }
        return self.provider.generate(
            LyricsSpec,
            prompts.lyrics_messages(facts),
            context=self._context(episode, "lyrics", prompts.LYRICS_PROMPT),
            validate=lambda lyrics: validate_lyrics(episode, lyrics, spec_artifact_id),
        )

    def music_spec(
        self,
        episode: Episode,
        lyrics: LyricsSpec,
        lyrics_artifact_id: str,
        *,
        variant: int,
    ) -> GeneratedDraft[MusicSpec]:
        validate_lyrics(episode, lyrics, lyrics.episode_spec_artifact_id)
        facts = {
            **self._facts(episode, variant),
            "lyrics": lyrics.model_dump(mode="json"),
            "lyrics_artifact_id": lyrics_artifact_id,
        }
        return self.provider.generate(
            MusicSpec,
            prompts.music_messages(facts),
            context=self._context(episode, "music_spec", prompts.MUSIC_PROMPT),
            validate=lambda music: validate_music(episode, music, lyrics_artifact_id),
        )


# Compatibility for callers of the original director boundary.
NvidiaCreativeDirector = CreativeDirector
