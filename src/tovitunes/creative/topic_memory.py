"""Editorial projections and reservations in the existing ToviTunes Database."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from tovitunes.catalog import BrandCatalog
from tovitunes.config import CreativeTopicsConfig
from tovitunes.creative.learning import LEARNING_POLICY, LearningBrief, TopicCandidate
from tovitunes.creative.provider import fingerprint
from tovitunes.creative.similarity import cosine_similarity, lexical_similarity, normalize_topic
from tovitunes.creative.validation import contains, safe_text, treatment_signature
from tovitunes.persistence.db import Database
from tovitunes.pipeline.creative import GeneratedDraft

# These are grammatical framing words, never a subject whitelist.
FRAMING = frozenset(
    "a an the tovi learn learns learning can with things objects object thing "
    "identify identifies name names recognize recognise find finds compare "
    "using use show shows point at by in on and or of to child children "
    "color colour colors colours is are vs versus understand distinguishes "
    "distinguish match matching sort sorting see sing song preschool".split()
)


class DuplicateSelection(ValueError):
    """Only a conclusive duplicate permits considering another candidate."""


def concept_signature(text: str) -> str:
    words = normalize_topic(text).split()
    return " ".join(sorted({w for w in words if w not in FRAMING}))


def educational_identity(candidate: TopicCandidate) -> str:
    return fingerprint(
        {
            "objective": concept_signature(candidate.objective),
            "vocabulary": sorted(normalize_topic(v) for v in candidate.target_vocabulary),
        }
    )


def validate_candidate(candidate: TopicCandidate, config: CreativeTopicsConfig) -> None:
    safe_text(" ".join(str(v) for v in candidate.model_dump().values()))
    for phrase in (
        *config.banned_topics,
        "in the style of",
        "sounds like",
        "philosophy",
        "existential",
        "metaphysics",
        "investment",
        "sexuality",
        "explosives",
        "touch fire",
        "taste chemicals",
        "taylor swift",
        "ed sheeran",
    ):
        if phrase and contains(
            normalize_topic(candidate.model_dump_json()), normalize_topic(phrase)
        ):
            raise ValueError("editorial policy rejects unsafe or abstract content")
    if not normalize_topic(candidate.subject):
        raise ValueError("empty normalized subject")
    if len(candidate.objective.split()) > 32 or len(candidate.target_vocabulary) > 4:
        raise ValueError("learning complexity exceeds one Short")
    if not re.search(
        r"\b(identify|name|count|compare|match|sort|point|show|find|recognize|"
        r"recognise|demonstrate|distinguish|say|move|imitate)\b",
        candidate.objective,
        re.IGNORECASE,
    ):
        raise ValueError("objective requires a concrete visually demonstrable learning action")
    if not all(contains(candidate.objective, word) for word in candidate.target_vocabulary):
        raise ValueError("objective must explicitly teach every selected vocabulary term")
    if re.search(r"\b(also|additionally|as well as)\b|;", candidate.objective, re.IGNORECASE):
        raise ValueError("only one primary objective is allowed")
    for example in candidate.example_objects:
        if any(contains(example, word) for word in ("concept", "theory", "justice", "infinity")):
            raise ValueError("examples must be concrete and visualizable")


def duplicate_reason(
    candidate: TopicCandidate,
    history: list[dict[str, Any]],
    config: CreativeTopicsConfig,
    vector: list[float] | None = None,
) -> str | None:
    subject = normalize_topic(candidate.subject)
    concept = concept_signature(
        " ".join((candidate.subject, candidate.objective, *candidate.target_vocabulary))
    )
    vocabulary = " ".join(sorted(candidate.target_vocabulary))
    treatment = treatment_signature(
        " ".join((candidate.premise, candidate.hook, *candidate.example_objects))
    )
    for prior in history:
        if subject == normalize_topic(prior["subject"]):
            return "exact normalized subject"
        if educational_identity(candidate) == prior.get("idea_fingerprint"):
            return "exact canonical educational idea"
        prior_concept = concept_signature(
            " ".join((prior["subject"], prior["objective"], *prior["target_vocabulary"]))
        )
        if (
            max(
                lexical_similarity(concept, prior_concept),
                lexical_similarity(vocabulary, " ".join(sorted(prior["target_vocabulary"]))),
                lexical_similarity(
                    concept_signature(candidate.subject), concept_signature(prior["subject"])
                ),
            )
            >= config.lexical_similarity_threshold
        ):
            return "lexical educational concept"
        prior_treatment = treatment_signature(
            " ".join(
                (prior.get("premise", ""), prior.get("hook", ""), *prior.get("example_objects", []))
            )
        )
        if lexical_similarity(treatment, prior_treatment) >= config.treatment_similarity_threshold:
            return "lexical creative treatment"
        if vector is not None and prior.get("embedding") is not None:
            if (
                cosine_similarity(vector, prior["embedding"])
                >= config.semantic_similarity_threshold
            ):
                return "semantic educational idea"
    return None


class TopicMemory:
    def __init__(self, database: Database, catalog: BrandCatalog) -> None:
        self.database, self.catalog = database, catalog

    def history(
        self, *, embedding_identity: str | None = None, connection: sqlite3.Connection | None = None
    ) -> list[dict[str, Any]]:
        if connection is None:
            with closing(self.database.connect()) as db:
                return self.history(embedding_identity=embedding_identity, connection=db)
        db = connection
        result: list[dict[str, Any]] = []
        for row in db.execute(
            "SELECT * FROM learning_briefs WHERE brand_id=?", (self.catalog.definition.brand_id,)
        ):
            brief = LearningBrief.model_validate_json(row["brief_json"])
            item = brief.model_dump(mode="json")
            item.update(
                memory_id=brief.brief_id,
                episode_id=row["reserved_episode_id"],
                run_id=row["run_id"],
                production_status="reserved",
            )
            result.append(item)
        for row in db.execute(
            "SELECT e.* FROM episodes e JOIN brand_revisions b "
            "ON b.revision_id=e.brand_revision_id "
            "WHERE b.brand_id=? AND e.learning_source='legacy_curriculum'",
            (self.catalog.definition.brand_id,),
        ):
            selected = db.execute(
                "SELECT selected_subject_json FROM creative_runs WHERE episode_id=?",
                (row["episode_id"],),
            ).fetchone()
            treatment = json.loads(selected[0]) if selected and selected[0] else {}
            result.append(
                {
                    "memory_id": "legacy:" + row["episode_id"],
                    "episode_id": row["episode_id"],
                    "run_id": None,
                    "subject": row["objective"],
                    "normalized_subject": normalize_topic(row["objective"]),
                    "domain": row["objective_id"].split(".")[0],
                    "objective": row["objective"],
                    "target_vocabulary": json.loads(row["target_vocabulary_json"]),
                    "premise": treatment.get("premise", ""),
                    "hook": treatment.get("hook", ""),
                    "example_objects": treatment.get("example_objects", []),
                    "working_title": row["external_key"],
                    "created_at": row["created_at"],
                    "production_status": row["lifecycle"],
                }
            )
        for item in result:
            item["embedding"] = None
            if embedding_identity:
                embedding = db.execute(
                    "SELECT vector_json FROM editorial_embeddings WHERE memory_id=? "
                    "AND embedding_identity=?",
                    (item["memory_id"], embedding_identity),
                ).fetchone()
                if embedding:
                    item["embedding"] = json.loads(embedding[0])
            if item["episode_id"]:
                publication = db.execute(
                    "SELECT outcome,privacy_status FROM publication_attempts "
                    "WHERE episode_id=? ORDER BY rowid DESC LIMIT 1",
                    (item["episode_id"],),
                ).fetchone()
                if publication:
                    item["publication_status"] = publication[0] + ":" + publication[1]
        return sorted(result, key=lambda v: (v["created_at"], v["memory_id"]), reverse=True)

    def selected(self, run_id: str) -> list[LearningBrief]:
        with closing(self.database.connect()) as db:
            return [
                LearningBrief.model_validate_json(r[0])
                for r in db.execute(
                    "SELECT brief_json FROM learning_briefs WHERE run_id=? ORDER BY ordinal",
                    (run_id,),
                )
            ]

    def get(self, brief_id: str) -> LearningBrief:
        with closing(self.database.connect()) as db:
            row = db.execute(
                "SELECT brief_json FROM learning_briefs WHERE brief_id=?", (brief_id,)
            ).fetchone()
        if row is None:
            raise KeyError(brief_id)
        brief = LearningBrief.model_validate_json(row[0])
        identity = educational_identity(brief)
        if (
            brief.brief_id != "learning:" + identity
            or brief.idea_fingerprint != identity
            or brief.normalized_subject != normalize_topic(brief.subject)
        ):
            raise ValueError("persisted learning brief differs from canonical application identity")
        return brief

    def reserve(
        self,
        candidate: TopicCandidate,
        run_id: str,
        ordinal: int,
        draft: GeneratedDraft[Any],
        config: CreativeTopicsConfig,
        vector: list[float] | None = None,
        embedding_identity: str | None = None,
    ) -> LearningBrief:
        validate_candidate(candidate, config)
        identity = educational_identity(candidate)
        brief = LearningBrief(
            **candidate.model_dump(),
            brief_id="learning:" + identity,
            idea_fingerprint=identity,
            normalized_subject=normalize_topic(candidate.subject),
            learning_policy_revision_id=LEARNING_POLICY.revision_id,
            target_duration_seconds=(
                self.catalog.definition.duration_min_seconds
                + self.catalog.definition.duration_max_seconds
            )
            // 2,
            created_at=datetime.now(UTC),
        )
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # Re-read under a write lock: even concurrent near-duplicates cannot reserve.
                reason = duplicate_reason(
                    candidate,
                    self.history(connection=db, embedding_identity=embedding_identity),
                    config,
                    vector,
                )
                if reason:
                    raise DuplicateSelection("duplicate: " + reason)
                db.execute(
                    "INSERT OR IGNORE INTO learning_policy_revisions VALUES (?,?)",
                    (LEARNING_POLICY.revision_id, LEARNING_POLICY.model_dump_json()),
                )
                db.execute(
                    "INSERT INTO learning_briefs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        brief.brief_id,
                        self.catalog.definition.brand_id,
                        brief.normalized_subject,
                        identity,
                        brief.model_dump_json(),
                        brief.learning_policy_revision_id,
                        run_id,
                        ordinal,
                        None,
                        draft.local_request_id,
                        draft.provider,
                        draft.model,
                        brief.created_at.isoformat(),
                    ),
                )
                if vector is not None and embedding_identity:
                    db.execute(
                        "INSERT INTO editorial_embeddings VALUES (?,?,?)",
                        (brief.brief_id, embedding_identity, json.dumps(vector)),
                    )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                if any(
                    key in str(exc)
                    for key in (
                        "learning_briefs.brand_id, learning_briefs.normalized_subject",
                        "learning_briefs.brand_id, learning_briefs.idea_fingerprint",
                        "learning_briefs.brief_id",
                    )
                ):
                    raise DuplicateSelection("duplicate exact persisted identity") from exc
                raise
            except Exception:
                db.rollback()
                raise
        return brief

    def store_embedding(self, memory_id: str, identity: str, vector: list[float]) -> None:
        with closing(self.database.connect()) as db:
            db.execute(
                "INSERT OR REPLACE INTO editorial_embeddings VALUES (?,?,?)",
                (memory_id, identity, json.dumps(vector)),
            )
            db.commit()


def compact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "subject",
        "domain",
        "objective",
        "target_vocabulary",
        "premise",
        "hook",
        "example_objects",
        "working_title",
        "production_status",
        "publication_status",
    )
    return [{k: item[k] for k in fields if k in item} for item in history]
