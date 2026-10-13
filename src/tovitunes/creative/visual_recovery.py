"""Explicit local authorization; old requests are never reset or resent."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig
from tovitunes.creative.provider import fingerprint
from tovitunes.domain.creative_drafts import parse_episode_spec, parse_lyrics, parse_music_spec
from tovitunes.domain.visual_draft import VISUAL_PROMPT_V2
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.persistence.db import Database
from tovitunes.pipeline.duration_source import admit_duration_source
from tovitunes.pipeline.music_adapter import creative_music_spec


def recovery_evidence(db: sqlite3.Connection, episode_id: str) -> tuple[str, list[str]] | None:
    rows = db.execute(
        "SELECT * FROM generation_requests WHERE episode_id=? "
        "AND prompt_version='episode_visual_plan_v1' ORDER BY rowid",
        (episode_id,),
    ).fetchall()
    if not rows:
        return None
    if any(r["status"] not in {"succeeded_response_invalid", "failed"} for r in rows):
        raise ValueError(
            "old visual requests are successful or unresolved; recovery is not admitted"
        )
    if not any(r["attempt"] == 2 for r in rows):
        raise ValueError("old visual contract has no completed repair failure")
    evidence = []
    for row in rows:
        content = row["response_content"]
        if row["status"] == "succeeded_response_invalid" and content is None:
            raise ValueError("old visual failure is missing response evidence")
        if content is not None and sha256(content.encode()).hexdigest() != row["response_sha256"]:
            raise ValueError("old visual response SHA differs")
        evidence.append(
            {
                k: row[k]
                for k in (
                    "request_id",
                    "prompt_version",
                    "input_fingerprint",
                    "response_sha256",
                    "status",
                    "error_kind",
                    "error_reason",
                    "parent_request_id",
                    "previous_attempt_id",
                )
            }
        )
    upstream = list(
        db.execute(
            "SELECT v.kind,v.artifact_id,v.sha256 FROM artifact_selections s "
            "JOIN artifact_versions v "
            "ON v.artifact_id=s.artifact_id WHERE s.owner_scope='episode' AND s.owner_id=? "
            "AND s.slot_key='main' "
            "AND s.kind IN ('episode_spec','lyrics','music_spec','audio_master') "
            "ORDER BY v.kind",
            (episode_id,),
        )
    )
    if {r["kind"] for r in upstream} != {"episode_spec", "lyrics", "music_spec", "audio_master"}:
        raise ValueError("visual recovery requires all retained upstream selections")
    return fingerprint(
        {
            "attempts": evidence,
            "upstream": [dict(r) for r in upstream],
            "new_contract": VISUAL_PROMPT_V2,
        }
    ), [r["request_id"] for r in rows]


def recovery_state(db: sqlite3.Connection, episode_id: str) -> dict[str, object] | None:
    evidence = recovery_evidence(db, episode_id)
    if evidence is None:
        return None
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    grant = (
        db.execute(
            "SELECT * FROM visual_contract_recoveries WHERE episode_id=? AND new_prompt_version=?",
            (episode_id, VISUAL_PROMPT_V2),
        ).fetchone()
        if "visual_contract_recoveries" in tables
        else None
    )
    if grant and grant["evidence_fingerprint"] != evidence[0]:
        raise ValueError("recovery authorization differs from immutable upstream/attempt evidence")
    return {
        "recovery_action": "authorize_visual_v2" if grant is None else "resume",
        "new_prompt_version": VISUAL_PROMPT_V2,
        "old_attempt_ids": evidence[1],
        "authorized": grant is not None,
    }


def authorize_visual_recovery(
    config: RuntimeConfig,
    episode_key: str,
    *,
    confirmed: bool,
    actor: str,
) -> dict[str, object]:
    if not confirmed or not actor.strip():
        raise PermissionError("visual contract recovery requires explicit operator confirmation")
    # Publication capability is not consent. This grant only admits a new visual
    # contract; it neither executes production nor changes any saved job target.
    # Keep automatic publication disabled for this explicitly local recovery path.
    if config.automation.auto_publish:
        raise PermissionError(
            "disable automatic publication before authorizing local visual recovery"
        )
    database = Database(config.database_path)
    database.migrate()
    with ProductionExecutionOwnership(
        database, operation="visual-contract-recovery", item_id=episode_key
    ) as owner:
        with closing(database.connect()) as db, db:
            episode = db.execute(
                "SELECT * FROM episodes WHERE external_key=?", (episode_key,)
            ).fetchone()
            if episode is None:
                raise KeyError(episode_key)
            eid = episode["episode_id"]
            if db.execute(
                "SELECT 1 FROM historical_production_episodes WHERE episode_id=?", (eid,)
            ).fetchone():
                raise ValueError("historical production is retained read-only")
            if db.execute(
                "SELECT 1 FROM artifact_selections WHERE owner_id=? AND kind IN "
                "('episode_visual_plan','timed_storyboard','final_render')",
                (eid,),
            ).fetchone():
                raise ValueError(
                    "recovery cannot replace a selected visual plan or completed render"
                )
            evidence = recovery_evidence(db, eid)
            if evidence is None:
                raise ValueError("no failed legacy visual contract to recover")
            store = AssetStore(config.data_root, database, initialize=False, local_preview=True)
            records = [
                store.selected("episode", eid, kind, "main")
                for kind in ("episode_spec", "lyrics", "music_spec", "audio_master")
            ]
            if any(record is None for record in records):
                raise ValueError("retained upstream artifacts are missing or ineligible")
            ids = tuple(record.identity.artifact_id for record in records if record)
            current_episode = database.get_episode(eid)
            binding = db.execute(
                "SELECT request_id FROM episode_music_bindings WHERE episode_id=?", (eid,)
            ).fetchone()
            if binding is None:
                raise ValueError("retained music binding is missing")
            canonical = creative_music_spec(
                current_episode,
                parse_episode_spec(store.read_json(ids[0])),
                parse_lyrics(store.read_json(ids[1])),
                parse_music_spec(store.read_json(ids[2])),
                (ids[0], ids[1], ids[2]),
            )
            admit_duration_source(
                config, current_episode, binding[0], canonical, ids[3], connection=db
            )
            state = recovery_state(db, eid)
            assert state is not None
            if state["authorized"]:
                return state
            owner.assert_owned()
            db.execute(
                "INSERT INTO visual_contract_recoveries VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    eid,
                    "episode_visual_plan_v1",
                    VISUAL_PROMPT_V2,
                    evidence[0],
                    actor.strip(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            database.assert_production_execution_owner(owner.owner_token)
        return {
            "episode_key": episode_key,
            "authorized": True,
            "new_prompt_version": VISUAL_PROMPT_V2,
            "provider_calls": 0,
            "old_attempt_ids": evidence[1],
        }
