"""Bounded durable editorial pools; downstream production stays in ShortProductionWorkflow."""

import json
import math
from collections.abc import Callable, Sequence
from contextlib import closing
from typing import Any, Protocol

import httpx

from tovitunes.config import CreativeTopicsConfig, TopicEmbeddingConfig
from tovitunes.creative.learning import LearningBrief, TopicPool
from tovitunes.creative.prompts import OPEN_TOPIC_PROMPT, topic_messages
from tovitunes.creative.provider import GenerationContext, StructuredGenerator, canonical
from tovitunes.creative.topic_memory import (
    DuplicateSelection,
    TopicMemory,
    compact_history,
    duplicate_reason,
    validate_candidate,
)


class EmbeddingProvider(Protocol):
    @property
    def identity(self) -> str: ...
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OllamaTopicEmbedding:
    def __init__(self, config: TopicEmbeddingConfig) -> None:
        self.config = config

    @property
    def identity(self) -> str:
        return "ollama:" + self.config.base_url + ":" + str(self.config.model)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # No pull/download API, no coupling to the Creative Director provider.
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.post(
                self.config.base_url + "/api/embed",
                json={
                    "model": self.config.model,
                    "input": list(texts),
                },
            )
            response.raise_for_status()
            vectors: list[list[float]] = response.json()["embeddings"]
            return vectors


class TopicPlanner:
    def __init__(
        self,
        provider: StructuredGenerator,
        memory: TopicMemory,
        config: CreativeTopicsConfig,
        *,
        embedding: EmbeddingProvider | None = None,
        assert_owner: Callable[[], None] = lambda: None,
    ) -> None:
        self.provider, self.memory, self.config = provider, memory, config
        self.assert_owner = assert_owner
        self.embedding = (
            (embedding or OllamaTopicEmbedding(config.embedding))
            if config.embedding.enabled
            else None
        )

    def _vectors(self, texts: Sequence[str]) -> list[list[float] | None]:
        if self.embedding is None or not texts:
            return [None] * len(texts)
        self.assert_owner()
        try:
            vectors = self.embedding.embed(texts)
            if (
                len(vectors) != len(texts)
                or any(
                    not vector
                    or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector)
                    or not any(vector)
                    for vector in vectors
                )
                or len({len(v) for v in vectors}) != 1
            ):
                raise ValueError("invalid optional embedding response")
        except Exception:
            # Provider failures never replace authoritative facts or abort lexical planning.
            self.assert_owner()
            return [None] * len(texts)
        self.assert_owner()
        return list(vectors)

    @staticmethod
    def _embedding_text(item: dict[str, Any]) -> str:
        return " ".join((item["subject"], item["objective"], *item["target_vocabulary"]))

    def select(self, run_id: str, facts: dict[str, Any], count: int = 1) -> list[LearningBrief]:
        if not 1 <= count <= 30:
            raise ValueError("topic selection count must be between 1 and 30")
        selected = self.memory.selected(run_id)
        if len(selected) >= count:
            return selected[:count]
        identity = self.embedding.identity if self.embedding else None
        history = self.memory.history(embedding_identity=identity)
        missing = [item for item in history if item["embedding"] is None]
        for item, vector in zip(
            missing, self._vectors([self._embedding_text(i) for i in missing]), strict=True
        ):
            if vector is not None and identity:
                self.assert_owner()
                self.memory.store_embedding(item["memory_id"], identity, vector)
        for ordinal in range(1, self.config.max_generation_rounds + 1):
            self.assert_owner()
            with closing(self.memory.database.connect()) as db:
                row = db.execute(
                    "SELECT * FROM topic_rounds WHERE run_id=? AND ordinal=?", (run_id, ordinal)
                ).fetchone()
                if row and row["completed"]:
                    continue
                if row is None:
                    round_facts = {
                        **facts,
                        "round": ordinal,
                        "candidate_batch_size": self.config.candidate_batch_size,
                        "history": compact_history(
                            self.memory.history()[: self.config.recent_history_count]
                        ),
                        "same_run_exclusions": [b.subject for b in selected],
                    }
                    db.execute(
                        "INSERT INTO topic_rounds(run_id,ordinal,input_json) VALUES (?,?,?)",
                        (run_id, ordinal, canonical(round_facts)),
                    )
                    db.commit()
                else:
                    round_facts = json.loads(row["input_json"])

            def validate_pool(pool: TopicPool) -> None:
                if len(pool.candidates) != self.config.candidate_batch_size:
                    raise ValueError("topic pool must match the configured candidate batch size")

            # Domain rejection is a completed pool, not a hidden provider retry. Schema/size
            # repair and PR #37 model fallback remain owned by the existing generator.
            draft = self.provider.generate(
                TopicPool,
                topic_messages(round_facts),
                context=GenerationContext(
                    "subject_pool", OPEN_TOPIC_PROMPT, run_id=run_id, assert_owner=self.assert_owner
                ),
                validate=validate_pool,
            )
            self.assert_owner()
            ranked = sorted(draft.output.candidates, key=lambda c: -c.score)
            vectors = self._vectors([self._embedding_text(c.model_dump()) for c in ranked])
            rejections: list[str] = []
            for candidate, vector in zip(ranked, vectors, strict=True):
                self.assert_owner()
                try:
                    validate_candidate(candidate, self.config)
                except ValueError as exc:
                    rejections.append(str(exc))
                    continue
                reason = duplicate_reason(
                    candidate,
                    self.memory.history(embedding_identity=identity),
                    self.config,
                    vector,
                )
                if reason:
                    rejections.append(reason)
                    continue
                try:
                    brief = self.memory.reserve(
                        candidate, run_id, len(selected) + 1, draft, self.config, vector, identity
                    )
                except DuplicateSelection as exc:
                    rejections.append("candidate rejected: " + str(exc))
                    continue
                selected.append(brief)
                if len(selected) >= count:
                    break
            self.assert_owner()
            with closing(self.memory.database.connect()) as db:
                db.execute(
                    "UPDATE topic_rounds SET completed=1,rejections_json=? "
                    "WHERE run_id=? AND ordinal=?",
                    (canonical(rejections), run_id, ordinal),
                )
                db.commit()
            if len(selected) >= count:
                return selected
        raise ValueError(
            f"TOPIC_POOLS_EXHAUSTED: selected {len(selected)} of {count} novel ideas "
            f"after {self.config.max_generation_rounds} bounded generation rounds"
        )
