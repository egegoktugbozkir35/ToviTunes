"""ToviTunes render algorithms; application lifecycle belongs to Orchestrator."""

from contextlib import closing
from typing import Any

from tovitunes.creative.metadata import MetadataWriter
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.domain.storyboard import (
    LyricIntent,
    SceneIntent,
    StoryboardTemplate,
    TimedStoryboardV2,
    build_storyboard,
)
from tovitunes.domain.visual_plan import EpisodeVisualPlan
from tovitunes.errors import ProductionStop
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.pipeline.production import accept_episode_source, extract_alignment, extract_beats
from tovitunes.render.production import ProductionRenderer
from tovitunes.services.context import StageContext


class RenderService(StageContext):
    def _storyboard(
        self,
        episode: Episode,
        inputs: tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]],
        request_id: str,
        visual: EpisodeVisualPlan,
        plan_id: str,
        assets: dict[str, str],
        environment_id: str,
    ) -> None:
        spec, lyrics, music, ids = inputs
        source = accept_episode_source(
            self.config,
            episode,
            request_id,
            creative_music_spec(episode, spec, lyrics, music, ids),
            self.config.automation.analysis_version,
        )
        master = self._selected(episode, "audio_master")
        assert master is not None
        adapter = self._selected(episode, "creative_music_input")
        assert adapter is not None
        with closing(self.database.connect()) as db:
            binding = db.execute(
                "SELECT provider_endpoint FROM episode_music_bindings WHERE episode_id=?",
                (episode.episode_id,),
            ).fetchone()
        self._json(
            episode,
            "production_handoff",
            {
                **source.manifest,
                "creative_artifact_ids": ids,
                "adapter_artifact_id": adapter.identity.artifact_id,
                "provider_endpoint": binding[0] if binding else None,
            },
            (adapter.identity.artifact_id, master.identity.artifact_id),
        )
        alignment = extract_alignment(source, master.identity.artifact_id, generic=True)
        beats = extract_beats(source, master.identity.artifact_id)
        alignment_record = self._json(
            episode,
            "audio_alignment",
            alignment.model_dump(mode="json"),
            (master.identity.artifact_id,),
        )
        beat_record = self._json(
            episode, "beat_analysis", beats.model_dump(mode="json"), (master.identity.artifact_id,)
        )
        template = StoryboardTemplate(
            schema_version=2,
            template_id="episode_visual_plan_v1",
            concept_id=episode.concept_id,
            objective_id=episode.objective_id,
            intro=SceneIntent(visual_focus="Tovi enters the episode world", tovi_action="enter"),
            outro=SceneIntent(visual_focus="Tovi celebrates and settles", tovi_action="celebrate"),
            lyrics=tuple(
                LyricIntent(
                    lyric_text=scene.lyric_text,
                    visual_focus=scene.visual_focus,
                    tovi_action=scene.tovi_action,
                    required_props=scene.required_assets,
                    lesson_target=episode.concept_id,
                )
                for scene in visual.scenes
            ),
        )
        base = build_storyboard(
            episode,
            alignment,
            beats,
            alignment_record.identity.artifact_id,
            beat_record.identity.artifact_id,
            template,
            self.store.get(plan_id).sha256,
        )
        storyboard = TimedStoryboardV2(
            **base.model_dump(),
            lyrics_artifact_id=ids[1],
            visual_plan_artifact_id=plan_id,
            asset_artifact_ids=assets,
            environment_set_artifact_id=environment_id,
        )
        env_binding = self._selected(episode, "episode_environment")
        assert env_binding is not None
        self._json(
            episode,
            "timed_storyboard",
            storyboard.model_dump(mode="json"),
            (
                master.identity.artifact_id,
                alignment_record.identity.artifact_id,
                beat_record.identity.artifact_id,
                ids[1],
                plan_id,
                *assets.values(),
                env_binding.identity.artifact_id,
                environment_id,
            ),
        )

    def _render(self, episode: Episode) -> dict[str, Any]:
        if self.progress:
            self.progress("RENDER", "Encoding final video or validating retained render")
        return ProductionRenderer(
            self.config, local_preview=True, assert_owner=self.ownership.assert_owned
        ).render(episode.external_key, visual_story=True)

    def _media_qa(self, episode: Episode) -> dict[str, Any]:
        final = self._selected(episode, "final_render", "main_v4")
        qa_record = self._selected(episode, "media_qa", "main_v4")
        if not final or not qa_record:
            raise ValueError("renderer did not persist final render/media QA")
        qa = self.store.read_json(qa_record.identity.artifact_id)
        if (
            not isinstance(qa, dict)
            or qa.get("passed") is not True
            or qa.get("render_artifact_id") != final.identity.artifact_id
            or qa.get("render_sha256") != final.sha256
        ):
            raise ProductionStop("BLOCKED", "Media QA failed or differs from retained final MP4")
        return {"media_qa_artifact_id": qa_record.identity.artifact_id, "passed": True}

    def _metadata(self, episode: Episode) -> dict[str, Any]:
        with self._creative() as creative:
            creative.store.local_preview = True
            return MetadataWriter(creative).generate(episode.external_key)
