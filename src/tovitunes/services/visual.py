"""ToviTunes visual algorithms; application lifecycle belongs to Orchestrator."""

import json
from contextlib import closing

from tovitunes.benchmark.providers import ProviderFailure
from tovitunes.creative.director import pinned_facts
from tovitunes.creative.provider import (
    GenerationContext,
)
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.domain.visual_plan import EpisodeVisualPlan, validate_visual_plan
from tovitunes.errors import ProductionStop
from tovitunes.render.environment_sets import EnvironmentSet, generate_set, selected_set
from tovitunes.services.context import StageContext


class VisualService(StageContext):
    def _visual(
        self,
        episode: Episode,
        inputs: tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]],
    ) -> tuple[EpisodeVisualPlan, str]:
        spec, lyrics, music, ids = inputs
        selected = self._selected(episode, "episode_visual_plan")

        def validate(plan: EpisodeVisualPlan) -> None:
            validate_visual_plan(plan, episode, spec, lyrics, ids)

        if selected:
            plan = EpisodeVisualPlan.model_validate(
                self.store.read_json(selected.identity.artifact_id)
            )
            validate(plan)
            return plan, selected.identity.artifact_id
        facts = {
            **pinned_facts(self.catalog),
            "episode": episode.model_dump(mode="json"),
            "episode_spec": spec.model_dump(mode="json"),
            "lyrics": lyrics.model_dump(mode="json"),
            "music_spec": music.model_dump(mode="json"),
            "creative_artifact_ids": ids,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Plan preschool visuals for the exact selected episode and every exact "
                    "lyric line. "
                    "Keep Tovi as the sole character. Invent no lyrics, timing, curriculum claims, "
                    "rights or approval facts. Stable asset keys are data. Use concrete "
                    "entities supported "
                    "by the lyrics/story. Include a deterministic color_swatch for Colors lessons. "
                    "Provide all four environment roles in order: meadow_wide, lesson_garden, "
                    "play_path, "
                    "celebration_meadow. Reuse the shared textual preschool-world-v1 meadow "
                    "when compatible. "
                    "Qwen supports no reference images. Every required asset must appear in a "
                    "scene. "
                    "No artist/franchise imitation, text, logos, unsafe content or new characters. "
                    "Return the EpisodeVisualPlan schema; copy all three supplied creative "
                    "artifact IDs."
                ),
            },
            {"role": "user", "content": json.dumps(facts, sort_keys=True)},
        ]
        with self._creative() as creative:
            draft = creative.provider.generate(
                EpisodeVisualPlan,
                messages,
                context=GenerationContext(
                    "episode_visual_plan",
                    "episode_visual_plan_v1",
                    episode_id=episode.episode_id,
                    assert_owner=self.ownership.assert_owned,
                ),
                validate=validate,
            )
        validate(draft.output)
        record = self._json(
            episode,
            "episode_visual_plan",
            draft.output.model_dump(mode="json"),
            ids,
            Provenance(
                source_kind="provider",
                acquired_at=draft.generated_at,
                provider=draft.provider,
                model=draft.model,
                request_id=draft.request_id,
                local_request_id=draft.local_request_id,
                prompt_version=draft.prompt_version,
                input_artifact_ids=ids,
            ),
        )
        return draft.output, record.identity.artifact_id

    def _environment(self, episode: Episode, visual: EpisodeVisualPlan, plan_id: str) -> str:
        selected = self._selected(episode, "episode_environment")
        if selected:
            payload = self.store.read_json(selected.identity.artifact_id)
            if not isinstance(payload, dict) or payload["visual_plan_artifact_id"] != plan_id:
                raise ValueError("episode environment binding differs from current visual plan")
            aid = str(payload["environment_set_artifact_id"])
        else:
            with closing(self.database.connect()) as db:
                unresolved = db.execute(
                    "SELECT status,request_id FROM environment_requests "
                    "WHERE episode_id=? "
                    "AND status NOT IN ('prepared','succeeded') ORDER BY rowid LIMIT 1",
                    (episode.episode_id,),
                ).fetchone()
            if unresolved:
                raise ProductionStop(
                    "AMBIGUOUS"
                    if unresolved["status"] in {"remote_started", "ambiguous"}
                    else "FAILED",
                    "Environment interaction requires explicit recovery; no automatic resend",
                    {"request_id": unresolved["request_id"]},
                )
            shared = None
            if visual.reuse_shared_environment:
                try:
                    shared = selected_set(self.config)
                except ValueError as exc:
                    if "no selected approved environment set" not in str(exc):
                        raise
            if shared is not None and shared[0].theme == visual.world_contract:
                aid = shared[1]
            else:
                if self.config.environment_generation.provider != "qwen_comfyui":
                    raise ProductionStop(
                        "BLOCKED", "Production environments require configured qwen_comfyui"
                    )
                try:
                    result = generate_set(
                        self.config,
                        confirmed=True,
                        episode_id=episode.episode_id,
                        role_briefs={env.role: env.description for env in visual.environments},
                    )
                except ProviderFailure as exc:
                    raise ProductionStop(
                        "AMBIGUOUS" if exc.outcome == "ambiguous" else "FAILED",
                        "Qwen environment generation stopped; inspect the durable request",
                    ) from exc
                aid = str(result["manifest_artifact_id"])
            environment = EnvironmentSet.model_validate(self.store.read_json(aid))
            for plate in environment.plates:
                if plate.source_artifact_id:
                    self.store.admit_preview(plate.source_artifact_id)
                self.store.admit_preview(plate.artifact_id)
            self.store.admit_preview(aid)
            self._json(
                episode,
                "episode_environment",
                {
                    "visual_plan_artifact_id": plan_id,
                    "environment_set_artifact_id": aid,
                    "ownership": "shared_selected" if shared else "episode",
                },
                (plan_id, aid),
            )
        environment = EnvironmentSet.model_validate(self.store.read_json(aid))
        if environment.theme != visual.world_contract:
            raise ValueError("environment world differs from admitted visual plan")
        for plate in environment.plates:
            record = self.store.get(plate.artifact_id)
            if record.sha256 != plate.sha256 or not self.store.inspect(plate.artifact_id).valid:
                raise ValueError("environment plate failed immutable validation")
        return aid
