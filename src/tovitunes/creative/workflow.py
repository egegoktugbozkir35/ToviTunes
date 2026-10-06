"""A bounded durable planning run and resumable creative-only production path."""

import json
import subprocess
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from sqlite3 import Row
from typing import Any, cast
from uuid import uuid4

from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import BrandCatalog, load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.creative.director import CreativeDirector
from tovitunes.creative.learning import LEARNING_POLICY, LearningBrief, subject_slug
from tovitunes.creative.models import CreativeSubjectCandidate, CreativeSubjectPool
from tovitunes.creative.prompts import OPEN_TOPIC_PROMPT, SUBJECT_PROMPT, subject_messages
from tovitunes.creative.provider import (
    GenerationContext,
    StructuredGenerator,
    canonical,
    fingerprint,
)
from tovitunes.creative.resilience import generation_audit
from tovitunes.creative.topic_memory import TopicMemory
from tovitunes.creative.topics import TopicPlanner
from tovitunes.creative.validation import treatment, validate_subject
from tovitunes.domain.episode import Episode
from tovitunes.persistence.db import Database
from tovitunes.persistence.leases import LeaseStore
from tovitunes.pipeline.creative import CreativeDraftService
from tovitunes.pipeline.execution import production_execution


def committed_curriculum_digest(root: Path, catalog: BrandCatalog) -> str:
    """Read trusted curriculum bytes from Git HEAD; uncommitted lessons cannot be auto-approved."""
    try:
        repository = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=root,
                stderr=subprocess.PIPE,
            )
            .decode()
            .strip()
        )
        relative = (
            (root / catalog.definition.curriculum_file)
            .resolve()
            .relative_to(repository.resolve())
            .as_posix()
        )
        committed = subprocess.check_output(
            ["git", "show", f"HEAD:{relative}"],
            cwd=repository,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise PermissionError(
            "machine curriculum approval requires a committed Git revision"
        ) from exc
    digest = sha256(committed).hexdigest()
    if digest != catalog.curriculum_revision.sha256:
        raise PermissionError("curriculum differs from committed Git bytes")
    return digest


def episode_by_key(database: Database, key: str) -> Episode:
    with closing(database.connect()) as db:
        row = db.execute("SELECT episode_id FROM episodes WHERE external_key=?", (key,)).fetchone()
    if row is None:
        raise KeyError(f"unknown episode key: {key}")
    return database.get_episode(row[0])


def eligibility(database: Database, catalog: BrandCatalog) -> dict[str, Any]:
    with closing(database.connect()) as db:
        # Reserve draft/held episodes too. Archival is not an implicit repeat authorization.
        rows = db.execute(
            "SELECT e.episode_id,e.concept_id,e.external_key,e.lifecycle FROM episodes e "
            "JOIN brand_revisions b ON b.revision_id=e.brand_revision_id "
            "JOIN curriculum_revisions c ON c.revision_id=e.curriculum_revision_id "
            "WHERE b.brand_id=? AND c.curriculum_id=? ORDER BY e.created_at,e.episode_id",
            (catalog.definition.brand_id, catalog.curriculum.curriculum_id),
        ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            subject = db.execute(
                "SELECT selected_subject_json FROM creative_runs WHERE episode_id=?",
                (row["episode_id"],),
            ).fetchone()
            if subject and subject[0]:
                item["treatment"] = treatment(
                    CreativeSubjectCandidate.model_validate_json(subject[0])
                )
            else:
                spec = db.execute(
                    "SELECT artifact_id FROM artifact_versions WHERE episode_id=? "
                    "AND kind='episode_spec' ORDER BY rowid DESC LIMIT 1",
                    (row["episode_id"],),
                ).fetchone()
                if spec:
                    item["episode_spec_artifact_id"] = spec[0]
            history.append(item)
    used = {row["concept_id"] for row in rows}
    return {
        "brand_revision_id": catalog.version.revision_id,
        "curriculum_revision_id": catalog.curriculum_revision.revision_id,
        "curriculum_id": catalog.curriculum.curriculum_id,
        "used_concepts": sorted(used),
        "eligible_concepts": [
            c.model_dump(mode="json")
            for c in catalog.curriculum.concepts
            if c.concept_id not in used
        ],
        "target_duration_seconds": (
            catalog.definition.duration_min_seconds + catalog.definition.duration_max_seconds
        )
        // 2,
        "character_packs": [p.model_dump(mode="json") for p in catalog.pack_revisions],
        "history": history,
    }


def call_snapshot(database: Database) -> set[str]:
    with closing(database.connect()) as db:
        return {
            row[0]
            for row in db.execute(
                "SELECT request_id FROM generation_requests WHERE prompt_version IS NOT NULL "
                "AND remote_started_at IS NOT NULL"
            )
        }


def call_report(database: Database, before: set[str]) -> dict[str, int]:
    result = dict.fromkeys(
        ("subject", "episode_spec", "lyrics", "music_spec", "metadata", "repair"), 0
    )
    with closing(database.connect()) as db:
        rows = db.execute(
            "SELECT request_id,kind,attempt FROM generation_requests WHERE "
            "prompt_version IS NOT NULL "
            "AND remote_started_at IS NOT NULL"
        ).fetchall()
    for row in rows:
        if row[0] in before:
            continue
        if row[2] == 2:
            result["repair"] += 1
        else:
            key = {"subject_pool": "subject", "publication_metadata": "metadata"}.get(
                row[1], row[1]
            )
            if key in result:
                result[key] += 1
    return result


class CreativeWorkflow:
    def __init__(
        self,
        config: RuntimeConfig,
        provider: StructuredGenerator,
        *,
        catalog: BrandCatalog | None = None,
    ) -> None:
        self.config, self.provider = config, provider
        self.catalog = catalog or load_brand(config.brand_root)
        self.database = Database(config.database_path)
        self.database.migrate()
        self.database.register_catalog(self.catalog)
        self.generated = config.data_root / ".creative-working"
        self.generated.mkdir(parents=True, exist_ok=True)
        self.store = AssetStore(
            config.data_root, self.database, generated_source_roots=[self.generated]
        )
        self.leases = LeaseStore(self.database)

    def _run(self, run_id: str | None) -> Row | None:
        with closing(self.database.connect()) as db:
            if run_id:
                row = db.execute("SELECT * FROM creative_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise KeyError(run_id)
                return cast(Row, row)
            return cast(
                Row | None,
                db.execute(
                    "SELECT r.* FROM creative_runs r JOIN brand_revisions b "
                    "ON b.revision_id=r.brand_revision_id WHERE b.brand_id=? "
                    "AND r.status<>'complete' "
                    "ORDER BY r.created_at LIMIT 1",
                    (self.catalog.definition.brand_id,),
                ).fetchone(),
            )

    def _new_run(self) -> Row:
        facts = {
            "brand": self.catalog.definition.model_dump(mode="json"),
            "creative_bible": self.catalog.creative_bible.model_dump(mode="json"),
            "safety_policy": self.catalog.safety_policy.model_dump(mode="json"),
            "learning_policy": LEARNING_POLICY.model_dump(mode="json"),
            "learning_policy_revision_id": LEARNING_POLICY.revision_id,
            "creative_topics": self.config.creative_topics.model_dump(mode="json"),
        }
        run_id, now = str(uuid4()), datetime.now(UTC).isoformat()
        with closing(self.database.connect()) as db:
            db.execute(
                "INSERT INTO creative_runs "
                "(run_id,brand_revision_id,curriculum_revision_id,provider,"
                "model,prompt_version,input_fingerprint,input_json,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    self.catalog.version.revision_id,
                    self.catalog.curriculum_revision.revision_id,
                    self.config.creative_llm.provider,
                    self.config.creative_llm.model,
                    OPEN_TOPIC_PROMPT,
                    fingerprint(facts),
                    canonical(facts),
                    "planning",
                    now,
                    now,
                ),
            )
            db.commit()
        row = self._run(run_id)
        assert row is not None
        return row

    def _reserve_brief(self, row: Row, brief: LearningBrief) -> Episode:
        stem = subject_slug(brief.subject) + "-" + brief.idea_fingerprint[:8]
        with closing(self.database.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT reserved_episode_json FROM creative_runs WHERE run_id=?", (row["run_id"],)
            ).fetchone()
            if existing and existing[0]:
                return Episode.model_validate_json(existing[0])
            ordinal = 1
            while db.execute(
                "SELECT 1 FROM episodes WHERE external_key=? UNION ALL "
                "SELECT 1 FROM creative_runs "
                "WHERE json_extract(reserved_episode_json,'$.external_key')=?",
                (f"{stem}-{ordinal:03d}", f"{stem}-{ordinal:03d}"),
            ).fetchone():
                ordinal += 1
            episode = Episode.from_learning_brief(self.catalog, brief, f"{stem}-{ordinal:03d}")
            db.execute(
                "UPDATE learning_briefs SET reserved_episode_id=? WHERE brief_id=?",
                (episode.episode_id, brief.brief_id),
            )
            db.execute(
                "UPDATE creative_runs SET status='selected',selected_concept_id=?,"
                "selected_subject_json=?,reserved_episode_json=?,updated_at=? WHERE run_id=?",
                (
                    episode.concept_id,
                    brief.model_dump_json(),
                    episode.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                    row["run_id"],
                ),
            )
            db.commit()
        return episode

    @staticmethod
    def _choose(pool: CreativeSubjectPool, facts: dict[str, Any]) -> CreativeSubjectCandidate:
        eligible = {c["concept_id"] for c in facts["eligible_concepts"]}
        history = [h["treatment"] for h in facts["history"] if h.get("treatment")]
        reasons: list[str] = []
        for candidate in pool.candidates:
            try:
                allowed_examples = next(
                    (
                        concept.get("example_entities", ())
                        for concept in facts["eligible_concepts"]
                        if concept["concept_id"] == candidate.concept_id
                    ),
                    (),
                )
                if allowed_examples:
                    validate_subject(candidate, eligible, history, allowed_examples)
                else:
                    validate_subject(candidate, eligible, history)
            except ValueError as exc:
                reasons.append(str(exc))
                continue
            return candidate
        raise ValueError("no acceptable subject in ordered pool: " + "; ".join(reasons))

    def _reserve(self, row: Row, subject: CreativeSubjectCandidate) -> Episode:
        current = eligibility(self.database, self.catalog)
        if subject.concept_id not in {c["concept_id"] for c in current["eligible_concepts"]}:
            raise ValueError("selected concept is no longer eligible; operator recovery required")
        stem = f"{self.catalog.curriculum.curriculum_id}-{subject.concept_id}"
        # Keep a hash of the full stem when truncating to the Episode external-key limit.
        if len(stem) > 54:
            stem = stem[:41] + "-" + sha256(stem.encode()).hexdigest()[:12]
        with closing(self.database.connect()) as db:
            keys = {r[0] for r in db.execute("SELECT external_key FROM episodes")}
        ordinal = 1
        while f"{stem}-{ordinal:03d}" in keys:
            ordinal += 1
        episode = Episode.create(self.catalog, subject.concept_id, f"{stem}-{ordinal:03d}")
        with closing(self.database.connect()) as db:
            db.execute(
                "UPDATE creative_runs SET status='selected',selected_concept_id=?,"
                "selected_subject_json=?,reserved_episode_json=?,updated_at=? WHERE run_id=?",
                (
                    subject.concept_id,
                    subject.model_dump_json(),
                    episode.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                    row["run_id"],
                ),
            )
            db.commit()
        return episode

    def reserve_next_run(self) -> str:
        """Expose durable planning identity so callers survive a create-next process crash."""
        execution = production_execution(
            self.database,
            f"creative-planning:{self.catalog.definition.brand_id}",
            duration_seconds=60,
        )
        self.leases, lease = execution.__enter__()
        try:
            row = self._run(None)
            return str((row if row is not None else self._new_run())["run_id"])
        finally:
            execution.__exit__(None, None, None)

    def generate_next(
        self,
        *,
        run_id: str | None = None,
        episode_key: str | None = None,
    ) -> dict[str, Any]:
        before = call_snapshot(self.database)
        if self.catalog.definition.language != "en" or self.catalog.definition.characters != (
            "tovi",
        ):
            raise ValueError("Production V1 requires English and the selected Tovi-only brand")
        execution = production_execution(
            self.database,
            f"creative-planning:{self.catalog.definition.brand_id}",
            duration_seconds=self.config.creative_llm.generation_budget_seconds(
                self.config.creative_topics.max_generation_rounds + 3
            ),
        )
        self.leases, lease = execution.__enter__()

        def assert_owner() -> None:
            self.leases.assert_owner(lease)

        try:
            row = self._run(run_id) if not episode_key else None
            if episode_key:
                episode = episode_by_key(self.database, episode_key)
            else:
                row = row if row is not None else self._new_run()
                if row["brand_revision_id"] != self.catalog.version.revision_id or (
                    row["prompt_version"] == SUBJECT_PROMPT
                    and row["curriculum_revision_id"]
                    != self.catalog.curriculum_revision.revision_id
                ):
                    raise ValueError(
                        "pending run pins an older catalog; explicit recovery required"
                    )
                if row["reserved_episode_json"]:
                    episode = Episode.model_validate_json(row["reserved_episode_json"])
                elif row["prompt_version"] == OPEN_TOPIC_PROMPT:
                    facts = json.loads(row["input_json"])
                    if facts["creative_topics"] != self.config.creative_topics.model_dump(
                        mode="json"
                    ):
                        raise ValueError(
                            "pending topic run pins older configuration; restore original config"
                        )
                    planner = TopicPlanner(
                        self.provider,
                        TopicMemory(self.database, self.catalog),
                        self.config.creative_topics,
                        assert_owner=assert_owner,
                    )
                    brief = planner.select(row["run_id"], facts)[0]
                    assert_owner()
                    episode = self._reserve_brief(row, brief)
                else:
                    facts = json.loads(row["input_json"])

                    def validate_pool(pool: CreativeSubjectPool) -> None:
                        self._choose(pool, facts)

                    pool = self.provider.generate(
                        CreativeSubjectPool,
                        subject_messages(facts),
                        context=GenerationContext(
                            "subject_pool",
                            SUBJECT_PROMPT,
                            run_id=row["run_id"],
                            assert_owner=assert_owner,
                        ),
                        validate=validate_pool,
                    )
                    assert_owner()
                    episode = self._reserve(row, self._choose(pool.output, facts))
                try:
                    existing = self.database.get_episode(episode.episode_id)
                    if existing.model_dump(exclude={"lifecycle"}) != episode.model_dump(
                        exclude={"lifecycle"}
                    ):
                        raise ValueError("reserved episode differs from persisted identity")
                    episode = existing
                except KeyError:
                    assert_owner()
                    self.database.create_episode(self.catalog, episode)
                assert_owner()
                with closing(self.database.connect()) as db:
                    db.execute(
                        "UPDATE creative_runs SET episode_id=?,updated_at=? WHERE run_id=?",
                        (episode.episode_id, datetime.now(UTC).isoformat(), row["run_id"]),
                    )
                    db.commit()
            digest = (
                committed_curriculum_digest(self.config.brand_root, self.catalog)
                if episode.learning_source == "legacy_curriculum"
                else ""
            )
            result = self._resume(episode, digest, assert_owner)
            if row is not None:
                assert_owner()
                with closing(self.database.connect()) as db:
                    db.execute(
                        "UPDATE creative_runs SET status='complete',updated_at=? WHERE run_id=?",
                        (datetime.now(UTC).isoformat(), row["run_id"]),
                    )
                    db.commit()
                result["run_id"] = row["run_id"]
            result["provider_calls"] = call_report(self.database, before)
            result["generation_attempts"] = generation_audit(self.database, episode.episode_id)
            return result
        finally:
            execution.__exit__(None, None, None)

    def _resume(
        self,
        episode: Episode,
        digest: str,
        assert_owner: Callable[[], None],
    ) -> dict[str, Any]:
        director = CreativeDirector(
            self.catalog, self.database, self.provider, assert_owner=assert_owner
        )
        service = CreativeDraftService(self.store, self.generated, director)
        assert_owner()
        if episode.learning_source == "generated_learning_brief":
            service.approve_learning_brief(
                episode.episode_id, self.catalog, self.config.creative_topics
            )
        else:
            service.approve_curriculum_objective(
                episode.episode_id, self.catalog, committed_curriculum_sha256=digest
            )
        result: dict[str, Any] = {
            "episode_id": episode.episode_id,
            "episode_key": episode.external_key,
        }
        for kind, draft in (
            ("episode_spec", service.draft_episode_spec),
            ("lyrics", service.draft_lyrics),
            ("music_spec", service.draft_music_spec),
        ):
            assert_owner()
            # Calling the generator is safe even for a selected stage: exact fingerprints reuse
            # its receipt, and a crash after ingest reuses its local-request artifact identity.
            record = self.store.selected("episode", episode.episode_id, kind, "main")
            if record is None:
                record = draft(episode.episode_id)
            assert_owner()
            selected = self.store.selected("episode", episode.episode_id, kind, "main")
            if selected is None or selected.identity != record.identity:
                service.select_structural(record.identity.artifact_id)
            result[f"{kind}_artifact_id"] = record.identity.artifact_id
        return result
