"""Write publication metadata from selected final-render evidence; never upload."""

from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tovitunes.artifacts.store import ArtifactRecord, InputDependency
from tovitunes.creative.director import pinned_facts, validate_pins
from tovitunes.creative.models import EpisodePublicationMetadata
from tovitunes.creative.prompts import METADATA_PROMPT, metadata_messages
from tovitunes.creative.provider import GenerationContext, canonical
from tovitunes.creative.validation import validate_episode_spec, validate_lyrics, validate_metadata
from tovitunes.creative.workflow import CreativeWorkflow, call_report, call_snapshot, episode_by_key
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec
from tovitunes.domain.review import ApprovalDecision
from tovitunes.domain.storyboard import TimedStoryboard
from tovitunes.render.models import RenderManifest


class MetadataWriter:
    def __init__(self, workflow: CreativeWorkflow) -> None:
        self.workflow = workflow
        self.store = workflow.store

    def _selected(self, episode_id: str, kind: str) -> ArtifactRecord:
        record = (
            self.store.selected("episode", episode_id, kind, "main_v4")
            if kind in {"final_render", "render_manifest"}
            else None
        ) or self.store.selected("episode", episode_id, kind, "main")
        if record is None:
            raise ValueError(f"metadata requires selected {kind}")
        return record

    def _facts(self, episode_id: str) -> tuple[dict[str, Any], tuple[str, ...]]:
        episode = self.store.database.get_episode(episode_id)
        validate_pins(episode, self.workflow.catalog)
        final = self._selected(episode_id, "final_render")
        spec_record = self.store.selected("episode", episode_id, "episode_spec", "main")
        lyrics_record = self.store.selected("episode", episode_id, "lyrics", "main")
        storyboard_record = self._selected(episode_id, "timed_storyboard")
        manifest_record = self._selected(episode_id, "render_manifest")
        if (spec_record is None) != (lyrics_record is None):
            raise ValueError("metadata requires a complete selected creative pair")
        storyboard = TimedStoryboard.model_validate(
            self.store.read_json(storyboard_record.identity.artifact_id)
        )
        manifest = RenderManifest.model_validate(
            self.store.read_json(manifest_record.identity.artifact_id)
        )
        if spec_record and lyrics_record:
            spec = EpisodeSpec.model_validate(
                self.store.read_json(spec_record.identity.artifact_id)
            )
            lyrics = LyricsSpec.model_validate(
                self.store.read_json(lyrics_record.identity.artifact_id)
            )
            validate_episode_spec(episode, spec)
            validate_lyrics(episode, lyrics, spec_record.identity.artifact_id)
            spec_facts: dict[str, Any] = spec.model_dump(mode="json")
            lyrics_facts: dict[str, Any] = lyrics.model_dump(mode="json")
        else:
            # The frozen production-handoff pilot predates CreativeWorkflow specs.
            # Its selected timed storyboard is the approved final creative evidence.
            spec_facts = {"basis": "selected_timed_storyboard"}
            lyrics_facts = {
                "lines": [
                    {"text": scene.lyric_text}
                    for scene in storyboard.scenes
                    if scene.kind == "lyric"
                ]
            }
        with closing(self.store.database.connect()) as db:
            approval = db.execute(
                "SELECT status FROM approval_decisions WHERE episode_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
            if approval is None or approval[0] != "approved":
                raise PermissionError("metadata requires current pinned objective approval")
            deps = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                    "WHERE consumer_artifact_id=?",
                    (final.identity.artifact_id,),
                )
            }
        if (
            deps.get(manifest_record.identity.artifact_id) != manifest_record.sha256
            or manifest.episode_id != episode_id
            or storyboard.episode_id != episode_id
            or storyboard.concept_id != episode.concept_id
            or storyboard.objective_id != episode.objective_id
            or storyboard.character_pack not in episode.character_packs
            or manifest.timed_storyboard_artifact_id != storyboard_record.identity.artifact_id
            or manifest.dependency_sha256.get(storyboard_record.identity.artifact_id)
            != storyboard_record.sha256
            or manifest.duration_seconds != storyboard.duration_seconds
            or (
                lyrics_record is not None
                and tuple(s.lyric_text for s in storyboard.scenes if s.kind == "lyric")
                != tuple(line["text"] for line in lyrics_facts["lines"])
            )
        ):
            raise ValueError("selected final render, storyboard and creative facts differ")
        facts = {
            **pinned_facts(self.workflow.catalog),
            "episode": episode.model_dump(mode="json"),
            "episode_spec": spec_facts,
            "lyrics": lyrics_facts,
            "timed_storyboard": storyboard.model_dump(mode="json"),
            "known_lesson_props_and_scene_intents": [
                s.model_dump(mode="json") for s in storyboard.scenes
            ],
            "final_render": {
                "artifact_id": final.identity.artifact_id,
                "sha256": final.sha256,
                "duration_seconds": manifest.duration_seconds,
                "manifest_artifact_id": manifest_record.identity.artifact_id,
            },
            "language": "en",
            "made_for_kids": True,
        }
        input_ids = [
            final.identity.artifact_id,
            storyboard_record.identity.artifact_id,
            manifest_record.identity.artifact_id,
        ]
        if spec_record and lyrics_record:
            input_ids.extend((spec_record.identity.artifact_id, lyrics_record.identity.artifact_id))
        return facts, tuple(input_ids)

    def generate(self, episode_key: str) -> dict[str, Any]:
        workflow = self.workflow
        before = call_snapshot(workflow.database)
        episode = episode_by_key(workflow.database, episode_key)
        lease = workflow.leases.acquire(
            f"creative-planning:{workflow.catalog.definition.brand_id}",
            duration_seconds=workflow.config.creative_llm.timeout_seconds * 2 + 600,
        )
        try:
            workflow.leases.assert_owner(lease)
            facts, deps = self._facts(episode.episode_id)
            final = facts["final_render"]
            authoritative = canonical(
                {"storyboard": facts["timed_storyboard"], "lyrics": facts["lyrics"]}
            )

            def validate(output: EpisodePublicationMetadata) -> None:
                validate_metadata(
                    episode,
                    output,
                    final["artifact_id"],
                    final["sha256"],
                    [c.concept_id for c in workflow.catalog.curriculum.concepts],
                    authoritative,
                )

            with closing(workflow.database.connect()) as db:
                prior_selection = db.execute(
                    "SELECT artifact_id FROM artifact_selections WHERE owner_scope='episode' "
                    "AND owner_id=? AND kind='publication_metadata' AND slot_key='main'",
                    (episode.episode_id,),
                ).fetchone()
                prior_deps = (
                    dict(
                        db.execute(
                            "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                            "WHERE consumer_artifact_id=?",
                            (prior_selection[0],),
                        ).fetchall()
                    )
                    if prior_selection
                    else {}
                )
            if prior_selection and prior_deps == {d: self.store.get(d).sha256 for d in deps}:
                existing = self._selected(episode.episode_id, "publication_metadata")
                output = EpisodePublicationMetadata.model_validate(
                    self.store.read_json(existing.identity.artifact_id)
                )
                validate(output)
                with closing(workflow.database.connect()) as db:
                    approval = db.execute(
                        "SELECT status FROM approval_decisions WHERE artifact_id=? "
                        "ORDER BY rowid DESC LIMIT 1",
                        (existing.identity.artifact_id,),
                    ).fetchone()
                if approval is None or approval[0] != "approved":
                    raise PermissionError("selected publication metadata needs approval")
                workflow.leases.assert_owner(lease)
                return {
                    "episode_id": episode.episode_id,
                    "episode_key": episode.external_key,
                    "publication_metadata_artifact_id": existing.identity.artifact_id,
                    "final_render_artifact_id": final["artifact_id"],
                    "final_render_sha256": final["sha256"],
                    "metadata": output.model_dump(mode="json"),
                    "provider_calls": call_report(workflow.database, before),
                }
            draft = workflow.provider.generate(
                EpisodePublicationMetadata,
                metadata_messages(facts),
                context=GenerationContext(
                    "publication_metadata",
                    METADATA_PROMPT,
                    episode_id=episode.episode_id,
                    assert_owner=lambda: workflow.leases.assert_owner(lease),
                ),
                validate=validate,
            )
            workflow.leases.assert_owner(lease)
            # Defend against a render selection changing during the remote call.
            if self._facts(episode.episode_id) != (facts, deps):
                raise ValueError("final render facts changed while metadata was generated")
            validate(draft.output)
            with closing(workflow.database.connect()) as db:
                saved = (
                    db.execute(
                        "SELECT artifact_id FROM artifact_versions WHERE episode_id=? "
                        "AND kind='publication_metadata' "
                        "AND json_extract(provenance_json,'$.local_request_id')=?",
                        (episode.episode_id, draft.local_request_id),
                    ).fetchone()
                    if draft.local_request_id
                    else None
                )
            if saved:
                record = self.store.get(saved[0])
            else:
                path = workflow.generated / f"{uuid4()}.json"
                path.write_text(draft.output.model_dump_json(indent=2), encoding="utf-8")
                try:
                    record = self.store.ingest(
                        path,
                        owner_scope="episode",
                        owner_id=episode.episode_id,
                        kind="publication_metadata",
                        slot_key="main",
                        provenance=Provenance(
                            source_kind="provider",
                            acquired_at=draft.generated_at,
                            provider=draft.provider,
                            model=draft.model,
                            request_id=draft.request_id,
                            local_request_id=draft.local_request_id,
                            prompt_version=draft.prompt_version,
                            input_artifact_ids=deps,
                        ),
                        dependencies=[InputDependency(d, "metadata_input") for d in deps],
                        expected_media_type="application/json",
                    )
                finally:
                    path.unlink(missing_ok=True)
            with closing(workflow.database.connect()) as db:
                prior = db.execute(
                    "SELECT status FROM approval_decisions WHERE artifact_id=? ORDER BY "
                    "rowid DESC LIMIT 1",
                    (record.identity.artifact_id,),
                ).fetchone()
            if prior and prior[0] in {"rejected", "needs_review"}:
                raise PermissionError("existing metadata review requires human escalation")
            workflow.leases.assert_owner(lease)
            self.store.record_approval(
                ApprovalDecision(
                    target_id=record.identity.artifact_id,
                    target_kind="artifact",
                    status="approved",
                    actor="machine:metadata_policy",
                    reason="Accurate pinned render facts and bounded schema.",
                    policy_version="metadata_structural_v1",
                    decided_at=datetime.now(UTC),
                )
            )
            self.store.select(record.identity.artifact_id)
            return {
                "episode_id": episode.episode_id,
                "episode_key": episode.external_key,
                "publication_metadata_artifact_id": record.identity.artifact_id,
                "final_render_artifact_id": final["artifact_id"],
                "final_render_sha256": final["sha256"],
                "metadata": draft.output.model_dump(mode="json"),
                "provider_calls": call_report(workflow.database, before),
            }
        finally:
            workflow.leases.release(lease)
