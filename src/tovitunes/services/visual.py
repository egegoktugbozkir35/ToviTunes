"""ToviTunes visual algorithms; application lifecycle belongs to Orchestrator."""

import json
from contextlib import closing

from tovitunes.benchmark.providers import ProviderFailure
from tovitunes.creative.director import pinned_facts
from tovitunes.creative.prompts import freedom_messages
from tovitunes.creative.provider import (
    GenerationContext,
)
from tovitunes.creative.visual_recovery import recovery_state
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.domain.production_v3 import AudioClock
from tovitunes.domain.visual_draft import (
    VISUAL_PROMPT_V2,
    CreativeVisualDraft,
    EpisodeVisualPlanV2,
    compile_visual_draft,
    parse_visual_plan,
    validate_compiled_visual,
)
from tovitunes.domain.visual_plan import EpisodeVisualPlan, validate_visual_plan
from tovitunes.errors import ProductionStop
from tovitunes.render.environment_sets import (
    EPISODE_PROMPT_VERSION,
    generate_set,
    selected_set,
)
from tovitunes.render.visual_evidence import validate_environment_assets
from tovitunes.services.context import StageContext


class VisualService(StageContext):
    def _visual(
        self,
        episode: Episode,
        inputs: tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]],
    ) -> tuple[EpisodeVisualPlan | EpisodeVisualPlanV2, str]:
        spec, lyrics, music, ids = inputs
        selected = self._selected(episode, "episode_visual_plan")
        if selected:
            plan = parse_visual_plan(self.store.read_json(selected.identity.artifact_id))
            if isinstance(plan, EpisodeVisualPlan):
                validate_visual_plan(plan, episode, spec, lyrics, ids)
            else:
                validate_compiled_visual(plan, episode, ids)
                self._visual_receipt(plan)
            return plan, selected.identity.artifact_id
        if episode.learning_source == "legacy_curriculum":
            return self._visual_legacy(episode, inputs)
        with closing(self.database.connect()) as db:
            success = db.execute(
                "SELECT 1 FROM generation_requests WHERE episode_id=? "
                "AND prompt_version='episode_visual_plan_v1' AND status='succeeded'",
                (episode.episode_id,),
            ).fetchone()
            if success:
                return self._visual_legacy(episode, inputs)
            recovery = recovery_state(db, episode.episode_id)
        if recovery and not recovery["authorized"]:
            raise ProductionStop(
                "BLOCKED", "Authorize the new visual contract explicitly", recovery
            )
        clock_record = self._selected(episode, "audio_clock")
        if clock_record is None:
            raise ValueError("visual draft requires an admitted retained audio clock")
        clock = AudioClock.model_validate(self.store.read_json(clock_record.identity.artifact_id))
        raw = self._selected(episode, "creative_visual_draft")
        if raw is None:
            facts = {
                "topic": episode.subject or episode.objective,
                "prior_story": spec.concept.model_dump(mode="json"),
                "song_mood": music.mood,
                "optional_lyrics_context": [line.text for line in lyrics.lines],
                "duration_seconds": clock.duration_seconds,
                "visual_direction": self.catalog.creative_bible.visual_direction,
            }
            with self._creative() as creative:
                draft = creative.provider.generate(
                    CreativeVisualDraft,
                    freedom_messages(VISUAL_PROMPT_V2, facts),
                    context=GenerationContext(
                        "creative_visual_draft",
                        VISUAL_PROMPT_V2,
                        episode_id=episode.episode_id,
                        assert_owner=self.ownership.assert_owned,
                    ),
                )
            raw = self._json(
                episode,
                "creative_visual_draft",
                draft.output.model_dump(mode="json"),
                (*ids, clock_record.identity.artifact_id),
                Provenance(
                    source_kind="provider",
                    acquired_at=draft.generated_at,
                    provider=draft.provider,
                    model=draft.model,
                    request_id=draft.request_id,
                    local_request_id=draft.local_request_id,
                    prompt_version=draft.prompt_version,
                    input_artifact_ids=(*ids, clock_record.identity.artifact_id),
                ),
            )
        proposal = CreativeVisualDraft.model_validate(
            self.store.read_json(raw.identity.artifact_id)
        )
        plan_v2 = compile_visual_draft(proposal, episode, ids, raw.identity.artifact_id, raw.sha256)
        self._visual_receipt(plan_v2)
        record = self._json(
            episode,
            "episode_visual_plan",
            plan_v2.model_dump(mode="json"),
            (*ids, raw.identity.artifact_id),
        )
        return plan_v2, record.identity.artifact_id

    def _visual_receipt(self, plan: EpisodeVisualPlanV2) -> None:
        from tovitunes.render.visual_evidence import validate_visual_receipt

        validate_visual_receipt(self.store, plan)

    def _visual_legacy(
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

    def _environment(
        self, episode: Episode, visual: EpisodeVisualPlan | EpisodeVisualPlanV2, plan_id: str
    ) -> str:
        selected = self._selected(episode, "episode_environment")
        if selected:
            payload = self.store.read_json(selected.identity.artifact_id)
            if not isinstance(payload, dict) or payload["visual_plan_artifact_id"] != plan_id:
                raise ValueError("episode environment binding differs from current visual plan")
            if payload.get("visual_plan_sha256", self.store.get(plan_id).sha256) != (
                self.store.get(plan_id).sha256
            ):
                raise ValueError("episode environment visual plan bytes differ")
            aid = str(payload["environment_set_artifact_id"])
            shared_binding = payload.get("ownership") == "shared_selected"
        else:
            with closing(self.database.connect()) as db:
                unresolved = db.execute(
                    "SELECT status,request_id FROM environment_requests "
                    "WHERE episode_id=? "
                    "AND status NOT IN ('prepared','succeeded') "
                    "AND request_id NOT IN (SELECT request_id FROM "
                    "environment_source_reconciliations) ORDER BY rowid LIMIT 1",
                    (episode.episode_id,),
                ).fetchone()
            if unresolved:
                raise ProductionStop(
                    "AMBIGUOUS"
                    if unresolved["status"] in {"remote_started", "ambiguous"}
                    else "FAILED",
                    "Environment interaction requires explicit recovery; no automatic resend",
                    {
                        "request_id": unresolved["request_id"],
                        "subsystem": "environment",
                        "error_kind": "environment_provider_failure",
                        "retry_state": "ambiguous"
                        if unresolved["status"] in {"remote_started", "ambiguous"}
                        else "requires_recovery",
                    },
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
                        prompt_version=EPISODE_PROMPT_VERSION,
                        visual_direction=self.catalog.creative_bible.visual_direction,
                        assert_owner=self.ownership.assert_owned,
                    )
                except ProviderFailure as exc:
                    with closing(self.database.connect()) as db:
                        failed = db.execute(
                            "SELECT request_id FROM environment_requests WHERE episode_id=? "
                            "ORDER BY rowid DESC LIMIT 1",
                            (episode.episode_id,),
                        ).fetchone()
                    raise ProductionStop(
                        "AMBIGUOUS" if exc.outcome == "ambiguous" else "FAILED",
                        "Qwen environment generation stopped; inspect the durable request",
                        {
                            "request_id": failed[0] if failed else None,
                            "subsystem": "environment",
                            "error_kind": "environment_provider_failure",
                            "retry_state": "ambiguous"
                            if exc.outcome == "ambiguous"
                            else "requires_recovery",
                        },
                    ) from exc
                aid = str(result["manifest_artifact_id"])
            self.ownership.assert_owned()
            shared_binding = shared is not None
            environment = validate_environment_assets(
                self.store, aid, episode.episode_id, shared=shared_binding,
                role_briefs={env.role: env.description for env in visual.environments},
            )
            for plate in environment.plates:
                if plate.source_artifact_id:
                    self.ownership.assert_owned()
                    self.store.admit_preview(plate.source_artifact_id)
                self.ownership.assert_owned()
                self.store.admit_preview(plate.artifact_id)
            self.ownership.assert_owned()
            self.store.admit_preview(aid)
            self._json(
                episode,
                "episode_environment",
                {
                    "visual_plan_artifact_id": plan_id,
                    "visual_plan_sha256": self.store.get(plan_id).sha256,
                    "environment_set_artifact_id": aid,
                    "ownership": "shared_selected" if shared else "episode",
                },
                (plan_id, aid),
            )
        environment = validate_environment_assets(
            self.store, aid, episode.episode_id, shared=shared_binding,
            role_briefs={env.role: env.description for env in visual.environments},
        )
        if environment.theme != visual.world_contract:
            raise ValueError("environment world differs from admitted visual plan")
        return aid
