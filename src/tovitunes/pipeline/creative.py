"""Review-gated creative drafts through a replaceable typed generator boundary."""

from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Generic, Literal, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from tovitunes.artifacts.store import ArtifactRecord, AssetStore, InputDependency
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.creative import (
    EpisodeConcept,
    EpisodeSpec,
    LyricLine,
    LyricsSpec,
    MusicSection,
    MusicSpec,
    StoryBeat,
)
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision

T = TypeVar("T", bound=BaseModel)
CreativeKind = Literal["episode_spec", "lyrics", "music_spec"]


@dataclass(frozen=True)
class GeneratedDraft(Generic[T]):
    output: T
    provider: str
    model: str
    request_id: str
    prompt_version: str
    generated_at: datetime


class DraftGenerator(Protocol):
    def episode_spec(self, episode: Episode, *, variant: int) -> GeneratedDraft[EpisodeSpec]: ...

    def lyrics(
        self, episode: Episode, spec: EpisodeSpec, spec_artifact_id: str, *, variant: int
    ) -> GeneratedDraft[LyricsSpec]: ...

    def music_spec(
        self, episode: Episode, lyrics: LyricsSpec, lyrics_artifact_id: str, *, variant: int
    ) -> GeneratedDraft[MusicSpec]: ...


class FakeDraftGenerator:
    """Offline fixture only; its text is never an approved creative decision."""

    @staticmethod
    def _draft(output: T, prompt_version: str) -> GeneratedDraft[T]:
        return GeneratedDraft(
            output,
            "fake-llm",
            "fixture-v1",
            str(uuid4()),
            prompt_version,
            datetime.now(UTC),
        )

    def episode_spec(self, episode: Episode, *, variant: int) -> GeneratedDraft[EpisodeSpec]:
        words = episode.target_vocabulary
        subject = ", ".join(words)
        spec = EpisodeSpec(
            episode_id=episode.episode_id,
            concept_id=episode.concept_id,
            objective_id=episode.objective_id,
            concept=EpisodeConcept(
                premise=f"Tovi discovers {subject} in a familiar object (draft {variant}).",
                hook="A familiar object appears.",
                cast=(episode.character_packs[0].character_id,),
                setting="a simple playroom",
            ),
            teaching_vocabulary=words,
            story_beats=(
                StoryBeat(beat_id="hook", purpose="hook", action="Show the object."),
                StoryBeat(
                    beat_id="teach",
                    purpose="teach",
                    action=f"Name {subject} clearly.",
                    teaching_vocabulary=words,
                ),
                StoryBeat(
                    beat_id="practice",
                    purpose="practice",
                    action="Invite the child to repeat the name.",
                    teaching_vocabulary=words,
                ),
                StoryBeat(beat_id="payoff", purpose="payoff", action="Celebrate learning."),
            ),
            desired_structure="short hook, verse, repeated chorus, payoff",
        )
        return self._draft(spec, "episode-concept-v1")

    def lyrics(
        self, episode: Episode, spec: EpisodeSpec, spec_artifact_id: str, *, variant: int
    ) -> GeneratedDraft[LyricsSpec]:
        words = ", ".join(spec.teaching_vocabulary)
        lyrics = LyricsSpec(
            episode_id=episode.episode_id,
            objective_id=episode.objective_id,
            episode_spec_artifact_id=spec_artifact_id,
            target_vocabulary=spec.teaching_vocabulary,
            lines=(
                LyricLine(section="verse", text=f"Look around, what do we see? {words}!"),
                LyricLine(section="chorus", text=f"{words}, {words}, sing with me!"),
                LyricLine(section="outro", text=f"We found {words} today! Draft {variant}."),
            ),
        )
        return self._draft(lyrics, "lyrics-v1")

    def music_spec(
        self, episode: Episode, lyrics: LyricsSpec, lyrics_artifact_id: str, *, variant: int
    ) -> GeneratedDraft[MusicSpec]:
        duration = episode.target_duration_seconds
        music = MusicSpec(
            episode_id=episode.episode_id,
            objective_id=episode.objective_id,
            lyrics_artifact_id=lyrics_artifact_id,
            target_vocabulary=lyrics.target_vocabulary,
            target_duration_seconds=duration,
            mood="warm and playful",
            instrumentation=("hand percussion", "soft keyboard"),
            vocal_direction="clear, gentle preschool pronunciation",
            tempo_bpm=104 + variant,
            sections=(
                MusicSection(name="verse", target_seconds=duration * 0.4),
                MusicSection(name="chorus", target_seconds=duration * 0.4),
                MusicSection(name="outro", target_seconds=duration * 0.2),
            ),
        )
        return self._draft(music, "music-spec-v1")


class CreativeDraftService:
    def __init__(self, store: AssetStore, generated_root: Path, generator: DraftGenerator) -> None:
        resolved = generated_root.resolve(strict=True)
        if resolved not in store.generated_source_roots:
            raise ValueError("generated root must be configured as a trusted source")
        self.store = store
        self.generated_root = resolved
        self.generator = generator

    def _require_objective_approval(self, episode_id: str) -> None:
        with closing(self.store.database.connect()) as connection:
            decision = connection.execute(
                "SELECT status FROM approval_decisions WHERE episode_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        if decision is None or decision["status"] != "approved":
            raise PermissionError("pinned learning objective needs human approval")

    def review_objective(
        self,
        episode_id: str,
        status: Literal["approved", "rejected", "needs_review"],
        *,
        actor: str,
        reason: str | None = None,
        policy_version: str = "creative-v1",
    ) -> None:
        self.store.database.get_episode(episode_id)
        self.store.record_approval(
            ApprovalDecision(
                target_id=episode_id,
                target_kind="episode",
                status=status,
                actor=actor,
                reason=reason,
                policy_version=policy_version,
                decided_at=datetime.now(UTC),
            )
        )

    def review_candidate(
        self,
        artifact_id: str,
        status: Literal["approved", "rejected", "needs_review"],
        *,
        actor: str,
        reason: str | None = None,
        policy_version: str = "creative-v1",
    ) -> None:
        record = self.store.get(artifact_id)
        if record.identity.kind not in {"episode_spec", "lyrics", "music_spec"}:
            raise ValueError("artifact is not a creative draft")
        if status == "approved":
            self._require_objective_approval(record.identity.owner_id)
        self.store.record_approval(
            ApprovalDecision(
                target_id=artifact_id,
                target_kind="artifact",
                status=status,
                actor=actor,
                reason=reason,
                policy_version=policy_version,
                decided_at=datetime.now(UTC),
            )
        )
        if status == "approved":
            self.store.select(artifact_id)
        elif status == "rejected":
            self.store.deselect(artifact_id)

    def _persist(
        self,
        episode_id: str,
        kind: CreativeKind,
        draft: GeneratedDraft[T],
        dependencies: Sequence[str] = (),
    ) -> ArtifactRecord:
        source = self.generated_root / f"{uuid4()}.json"
        source.write_text(draft.output.model_dump_json(indent=2), encoding="utf-8")
        try:
            return self.store.ingest(
                source,
                owner_scope="episode",
                owner_id=episode_id,
                kind=kind,
                slot_key="main",
                provenance=Provenance(
                    source_kind="provider",
                    acquired_at=draft.generated_at,
                    provider=draft.provider,
                    model=draft.model,
                    request_id=draft.request_id,
                    prompt_version=draft.prompt_version,
                    input_artifact_ids=tuple(dependencies),
                ),
                dependencies=[InputDependency(value, "creative_input") for value in dependencies],
                expected_media_type="application/json",
            )
        finally:
            source.unlink(missing_ok=True)

    def draft_episode_spec(self, episode_id: str, *, variant: int = 1) -> ArtifactRecord:
        episode = self.store.database.get_episode(episode_id)
        self._require_objective_approval(episode_id)
        draft = self.generator.episode_spec(episode, variant=variant)
        spec = draft.output
        if (
            spec.episode_id != episode_id
            or spec.concept_id != episode.concept_id
            or spec.objective_id != episode.objective_id
            or spec.teaching_vocabulary != episode.target_vocabulary
            or not set(spec.concept.cast).issubset(
                pack.character_id for pack in episode.character_packs
            )
        ):
            raise ValueError("episode spec differs from pinned learning objective or cast")
        return self._persist(episode_id, "episode_spec", draft)

    def draft_lyrics(self, episode_id: str, *, variant: int = 1) -> ArtifactRecord:
        self._require_objective_approval(episode_id)
        episode = self.store.database.get_episode(episode_id)
        selected = self.store.selected("episode", episode_id, "episode_spec", "main")
        if selected is None:
            raise ValueError("approved episode spec must be selected before lyrics")
        spec = EpisodeSpec.model_validate(self.store.read_json(selected.identity.artifact_id))
        draft = self.generator.lyrics(episode, spec, selected.identity.artifact_id, variant=variant)
        lyrics = draft.output
        if (
            lyrics.episode_id != episode_id
            or lyrics.objective_id != episode.objective_id
            or lyrics.episode_spec_artifact_id != selected.identity.artifact_id
            or lyrics.target_vocabulary != episode.target_vocabulary
        ):
            raise ValueError("lyrics differ from selected objective or episode spec")
        return self._persist(episode_id, "lyrics", draft, (selected.identity.artifact_id,))

    def draft_music_spec(self, episode_id: str, *, variant: int = 1) -> ArtifactRecord:
        self._require_objective_approval(episode_id)
        episode = self.store.database.get_episode(episode_id)
        selected = self.store.selected("episode", episode_id, "lyrics", "main")
        if selected is None:
            raise ValueError("approved lyrics must be selected before music spec")
        lyrics = LyricsSpec.model_validate(self.store.read_json(selected.identity.artifact_id))
        draft = self.generator.music_spec(
            episode, lyrics, selected.identity.artifact_id, variant=variant
        )
        music = draft.output
        if (
            music.episode_id != episode_id
            or music.objective_id != episode.objective_id
            or music.lyrics_artifact_id != selected.identity.artifact_id
            or music.target_vocabulary != episode.target_vocabulary
            or music.target_duration_seconds != episode.target_duration_seconds
        ):
            raise ValueError("music spec differs from selected lyrics or objective")
        return self._persist(episode_id, "music_spec", draft, (selected.identity.artifact_id,))

