"""Plan the next unmet artifact requirement from durable facts."""

import re
from collections import defaultdict
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tovitunes.artifacts.store import AssetStore

Goal = Literal["audio", "storyboard", "render", "release"]
Action = Literal[
    "produce", "select", "review", "repair", "stale", "reconcile", "rights", "complete"
]
Slot = tuple[str, str]
_SCENE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TimedStoryboardIndex(BaseModel):
    """Small stable index; richer scene fields are added in the storyboard phase."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    audio_master_artifact_id: str
    scene_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_safe_scenes(self) -> "TimedStoryboardIndex":
        if len(self.scene_ids) != len(set(self.scene_ids)):
            raise ValueError("scene IDs must be unique")
        if any(not _SCENE_ID.fullmatch(scene) for scene in self.scene_ids):
            raise ValueError("scene ID is unsafe")
        return self


@dataclass(frozen=True)
class CandidateFact:
    artifact_id: str
    eligible: bool
    problem: str | None
    approval: str | None
    rights: str | None
    input_ids: frozenset[str]


@dataclass(frozen=True)
class SlotFact:
    selected_id: str | None
    candidates: tuple[CandidateFact, ...]


@dataclass(frozen=True)
class PlanSnapshot:
    episode_id: str
    slots: Mapping[Slot, SlotFact]
    requests: Mapping[Slot, tuple[str, ...]]
    scene_ids: tuple[str, ...] = ()
    storyboard_problem: str | None = None
    uncleared_rights: tuple[str, ...] = ()


@dataclass(frozen=True)
class Requirement:
    key: str
    kind: str
    slot_key: str
    prerequisites: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanResult:
    action: Action
    requirement: str | None
    reason: str
    artifact_id: str | None = None


def requirements(goal: Goal, scene_ids: tuple[str, ...]) -> tuple[Requirement, ...]:
    nodes = [
        Requirement("episode_spec", "episode_spec", "main"),
        Requirement("lyrics", "lyrics", "main", ("episode_spec",)),
        Requirement("music_spec", "music_spec", "main", ("lyrics",)),
        Requirement("audio_master", "audio_master", "main", ("music_spec",)),
    ]
    if goal == "audio":
        return tuple(nodes)
    nodes.extend(
        (
            Requirement("audio_alignment", "audio_alignment", "main", ("audio_master",)),
            Requirement("beat_analysis", "beat_analysis", "main", ("audio_master",)),
            Requirement(
                "timed_storyboard",
                "timed_storyboard",
                "main",
                ("audio_master", "audio_alignment", "beat_analysis"),
            ),
        )
    )
    if goal == "storyboard":
        return tuple(nodes)
    scene_requirements: list[str] = []
    for scene_id in scene_ids:
        for kind in ("scene_image", "character_animation"):
            key = f"{kind}:{scene_id}"
            nodes.append(Requirement(key, kind, scene_id, ("timed_storyboard",)))
            scene_requirements.append(key)
    nodes.extend(
        (
            Requirement(
                "render_manifest",
                "render_manifest",
                "main",
                ("audio_master", *scene_requirements),
            ),
            Requirement("final_render", "final_render", "main", ("render_manifest",)),
            Requirement("media_qa", "media_qa", "main", ("final_render",)),
        )
    )
    return tuple(nodes)


def _classify(candidate: CandidateFact) -> Action:
    if candidate.approval == "rejected":
        return "stale"
    if candidate.approval in {"pending", "needs_review", None}:
        return "review"
    if candidate.rights == "blocked":
        return "rights"
    if candidate.problem and "invalid" in candidate.problem:
        return "repair"
    if candidate.problem and "dependency" in candidate.problem:
        return "stale"
    return "repair"


def plan(snapshot: PlanSnapshot, goal: Goal) -> PlanResult:
    """Pure decision over one snapshot; callers must recheck before side effects."""
    selected_by_key: dict[str, str] = {}
    for node in requirements(goal, snapshot.scene_ids):
        slot = (node.kind, node.slot_key)
        fact = snapshot.slots.get(slot, SlotFact(None, ()))
        required_ids = {selected_by_key[key] for key in node.prerequisites}
        if fact.selected_id is not None:
            selected = next((c for c in fact.candidates if c.artifact_id == fact.selected_id), None)
            if selected is None:
                return PlanResult("repair", node.key, "selected artifact record is missing")
            if not selected.eligible:
                return PlanResult(
                    _classify(selected),
                    node.key,
                    selected.problem or "not eligible",
                    selected.artifact_id,
                )
            if not required_ids.issubset(selected.input_ids):
                return PlanResult(
                    "stale",
                    node.key,
                    "selected artifact lacks pinned prerequisites",
                    selected.artifact_id,
                )
            if node.key == "timed_storyboard" and snapshot.storyboard_problem:
                return PlanResult(
                    "repair", node.key, snapshot.storyboard_problem, selected.artifact_id
                )
            selected_by_key[node.key] = selected.artifact_id
            continue
        reusable = next(
            (c for c in fact.candidates if c.eligible and required_ids.issubset(c.input_ids)), None
        )
        if reusable is not None:
            return PlanResult(
                "select", node.key, "accepted candidate is reusable", reusable.artifact_id
            )
        pending = next(
            (c for c in fact.candidates if c.approval in {"pending", "needs_review"}), None
        )
        if pending is not None:
            return PlanResult("review", node.key, "candidate awaits review", pending.artifact_id)
        if snapshot.requests.get(slot):
            return PlanResult(
                "reconcile", node.key, "generation request outcome needs reconciliation"
            )
        stale = next(
            (c for c in fact.candidates if c.eligible and not required_ids.issubset(c.input_ids)),
            None,
        )
        if stale is not None:
            return PlanResult(
                "stale", node.key, "candidate lacks pinned prerequisites", stale.artifact_id
            )
        invalid = next((c for c in fact.candidates if c.approval == "approved"), None)
        if invalid is not None:
            return PlanResult(
                _classify(invalid),
                node.key,
                invalid.problem or "candidate is stale",
                invalid.artifact_id,
            )
        return PlanResult("produce", node.key, "required artifact is missing")
    if goal in {"render", "release"} and not snapshot.scene_ids:
        return PlanResult("repair", "timed_storyboard", "selected storyboard has no scenes")
    if goal == "release":
        if snapshot.uncleared_rights:
            return PlanResult(
                "rights",
                "transitive_rights",
                "release requires commercial-use clearance",
                snapshot.uncleared_rights[0],
            )
    return PlanResult("complete", None, f"all {goal} requirements are satisfied")


def load_snapshot(store: AssetStore, episode_id: str) -> PlanSnapshot:
    """Read DB facts together, then revalidate immutable files for a dry plan."""
    slots: dict[Slot, SlotFact] = {}
    with closing(store.database.connect()) as connection:
        connection.execute("BEGIN")
        episode = connection.execute(
            "SELECT episode_id FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        if episode is None:
            raise KeyError(episode_id)
        rows = connection.execute(
            "SELECT artifact_id, kind, slot_key, created_at FROM artifact_versions "
            "WHERE episode_id = ? ORDER BY created_at DESC, rowid DESC",
            (episode_id,),
        ).fetchall()
        selected_rows = connection.execute(
            "SELECT kind, slot_key, artifact_id FROM artifact_selections "
            "WHERE owner_scope = 'episode' AND owner_id = ?",
            (episode_id,),
        ).fetchall()
        approval_rows = connection.execute(
            "SELECT artifact_id, status FROM approval_decisions WHERE artifact_id IS NOT NULL "
            "ORDER BY rowid"
        ).fetchall()
        rights_rows = connection.execute(
            "SELECT artifact_id, status FROM rights_decisions ORDER BY rowid"
        ).fetchall()
        dependency_rows = connection.execute(
            "SELECT consumer_artifact_id, input_artifact_id FROM artifact_dependencies"
        ).fetchall()
        request_rows = connection.execute(
            "SELECT kind, slot_key, status FROM generation_requests WHERE episode_id = ? "
            "AND status IN ('prepared', 'remote_started', 'ambiguous', 'succeeded')",
            (episode_id,),
        ).fetchall()
        connection.commit()
    selected = {(r["kind"], r["slot_key"]): r["artifact_id"] for r in selected_rows}
    approvals = {r["artifact_id"]: r["status"] for r in approval_rows}
    rights = {r["artifact_id"]: r["status"] for r in rights_rows}
    dependencies: dict[str, set[str]] = defaultdict(set)
    for row in dependency_rows:
        dependencies[row["consumer_artifact_id"]].add(row["input_artifact_id"])
    candidates: dict[Slot, list[CandidateFact]] = defaultdict(list)
    for row in rows:
        artifact_id = row["artifact_id"]
        eligible, problem = store.eligibility(artifact_id)
        candidates[(row["kind"], row["slot_key"])].append(
            CandidateFact(
                artifact_id,
                eligible,
                problem,
                approvals.get(artifact_id),
                rights.get(artifact_id),
                frozenset(dependencies[artifact_id]),
            )
        )
    for slot in set(selected) | set(candidates):
        slots[slot] = SlotFact(selected.get(slot), tuple(candidates.get(slot, ())))
    requests: dict[Slot, list[str]] = defaultdict(list)
    for row in request_rows:
        requests[(row["kind"], row["slot_key"])].append(row["status"])
    scene_ids: tuple[str, ...] = ()
    storyboard_problem: str | None = None
    storyboard_id = selected.get(("timed_storyboard", "main"))
    if storyboard_id is not None:
        try:
            index = TimedStoryboardIndex.model_validate(store.read_json(storyboard_id))
            master_id = selected.get(("audio_master", "main"))
            if index.audio_master_artifact_id != master_id:
                raise ValueError("storyboard points to a different audio master")
            scene_ids = index.scene_ids
        except (ValueError, KeyError) as exc:
            storyboard_problem = str(exc)
    required_roots = [
        selected[(node.kind, node.slot_key)]
        for node in requirements("release", scene_ids)
        if (node.kind, node.slot_key) in selected
    ]
    uncleared: set[str] = set()
    visited: set[str] = set()
    pending_ids = list(required_roots)
    while pending_ids:
        artifact_id = pending_ids.pop()
        if artifact_id in visited:
            continue
        visited.add(artifact_id)
        if rights.get(artifact_id) != "commercial_use_confirmed":
            uncleared.add(artifact_id)
        pending_ids.extend(dependencies[artifact_id])
    return PlanSnapshot(
        episode_id=episode_id,
        slots=slots,
        requests={key: tuple(value) for key, value in requests.items()},
        scene_ids=scene_ids,
        storyboard_problem=storyboard_problem,
        uncleared_rights=tuple(sorted(uncleared)),
    )


def plan_episode(store: AssetStore, episode_id: str, goal: Goal) -> PlanResult:
    return plan(load_snapshot(store, episode_id), goal)

