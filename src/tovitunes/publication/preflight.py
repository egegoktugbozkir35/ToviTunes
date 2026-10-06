"""Provider-free, read-only release gate over selected immutable evidence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from tovitunes.artifacts.store import ArtifactRecord, AssetStore
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.creative.models import EpisodePublicationMetadata
from tovitunes.domain.storyboard import TimedStoryboardV2, parse_storyboard
from tovitunes.persistence.db import Database
from tovitunes.publication.rights_policy import (
    evaluate_inherited_rights,
    is_direct_rights_root,
)
from tovitunes.render.models import RenderManifest


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    scope: str
    reason: str
    artifact_id: str | None = None


@dataclass(frozen=True)
class ReleasePreflight:
    episode_key: str
    render_ready: bool
    private_test_upload_allowed: bool
    public_release_allowed: bool
    checks: tuple[Check, ...]
    render_artifact_id: str | None = None
    render_sha256: str | None = None
    metadata_artifact_id: str | None = None
    metadata_fingerprint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _selected(
    db: sqlite3.Connection, episode_id: str, kind: str, slot: str | None = None
) -> str | None:
    args: tuple[str, ...]
    if slot is None:
        query = (
            "SELECT artifact_id FROM artifact_selections WHERE owner_scope='episode' "
            "AND owner_id=? AND kind=? AND slot_key IN ('main','main_v4') "
            "ORDER BY CASE slot_key WHEN 'main_v4' THEN 0 ELSE 1 END LIMIT 1"
        )
        args = (episode_id, kind)
    else:
        query = (
            "SELECT artifact_id FROM artifact_selections WHERE owner_scope='episode' "
            "AND owner_id=? AND kind=? AND slot_key=?"
        )
        args = (episode_id, kind, slot)
    row = db.execute(query, args).fetchone()
    return str(row[0]) if row else None


def _latest(db: sqlite3.Connection, table: str, artifact_id: str) -> str | None:
    # Only trusted table literals are supplied by this module.
    row = db.execute(
        f"SELECT status FROM {table} WHERE artifact_id=? ORDER BY rowid DESC LIMIT 1",
        (artifact_id,),
    ).fetchone()
    return str(row[0]) if row else None


def evaluate_release(config: RuntimeConfig, episode_key: str) -> ReleasePreflight:
    """Evaluate technical, approval and rights evidence without providers or writes."""
    checks: list[Check] = []

    def add(name: str, passed: bool, scope: str, reason: str, aid: str | None = None) -> None:
        checks.append(Check(name, passed, scope, reason, aid))

    if not config.database_path.is_file() or not config.data_root.is_dir():
        add("database", False, "technical", "Production database or data root is unavailable")
        return ReleasePreflight(episode_key, False, False, False, tuple(checks))
    database = Database(config.database_path)
    store = AssetStore(config.data_root, database, initialize=False)
    with closing(database.connect()) as db:
        row = db.execute("SELECT * FROM episodes WHERE external_key=?", (episode_key,)).fetchone()
        if row is None:
            raise KeyError(episode_key)
        eid = str(row["episode_id"])
        storyboard_v2 = None
        storyboard_id = _selected(db, eid, "timed_storyboard")
        if storyboard_id:
            try:
                storyboard = parse_storyboard(store.read_json(storyboard_id))
                if isinstance(storyboard, TimedStoryboardV2):
                    storyboard_v2 = storyboard
            except (KeyError, ValueError, OSError):
                pass
        render_id = _selected(db, eid, "final_render")
        manifest_id = _selected(db, eid, "render_manifest")
        qa_id = _selected(db, eid, "media_qa")
        metadata_id = _selected(db, eid, "publication_metadata", "main")
        add(
            "selected_final_render",
            bool(render_id),
            "technical",
            "Selected final render" if render_id else "No selected final render",
            render_id,
        )
        add(
            "render_manifest",
            bool(manifest_id),
            "technical",
            "Selected render manifest" if manifest_id else "No selected render manifest",
            manifest_id,
        )
        add(
            "media_qa",
            bool(qa_id),
            "technical",
            "Selected media QA" if qa_id else "No selected media QA",
            qa_id,
        )
        add(
            "publication_metadata",
            bool(metadata_id),
            "private",
            "Selected reviewed publication metadata"
            if metadata_id
            else "No selected publication metadata; create and review it before upload",
            metadata_id,
        )

        graph: set[str] = set()
        visiting: set[str] = set()
        records: dict[str, ArtifactRecord] = {}
        dependencies: dict[str, list[str]] = {}
        local_integrity: dict[str, bool] = {}

        def visit(aid: str, technical_scope: str = "technical") -> None:
            if aid in graph:
                return
            if aid in visiting:
                add("dependency_cycle", False, technical_scope, "Artifact dependency cycle", aid)
                local_integrity[aid] = False
                return
            visiting.add(aid)
            local_integrity[aid] = True
            try:
                rec = store.get(aid)
                records[aid] = rec
                valid = store.inspect(aid)
                local_integrity[aid] = local_integrity[aid] and valid.valid
                add(
                    "immutable_sha",
                    valid.valid,
                    technical_scope,
                    "File SHA, size and MIME match"
                    if valid.valid
                    else f"Invalid immutable artifact: {', '.join(valid.reasons)}",
                    aid,
                )
                selection = db.execute(
                    "SELECT artifact_id FROM artifact_selections "
                    "WHERE owner_scope=? AND owner_id=? AND kind=? AND slot_key=?",
                    (
                        rec.identity.owner_scope,
                        rec.identity.owner_id,
                        rec.identity.kind,
                        rec.identity.slot_key,
                    ),
                ).fetchone()
                is_selected = bool(selection and selection[0] == aid)
                local_integrity[aid] = local_integrity[aid] and is_selected
                add(
                    "dependency_selection",
                    is_selected,
                    technical_scope,
                    "Artifact remains selected"
                    if is_selected
                    else "Artifact is no longer selected",
                    aid,
                )
                approval = _latest(db, "approval_decisions", aid)
                preview_admitted = False
                content_review_root = aid in {render_id, metadata_id} or rec.identity.kind in {
                    "audio_master",
                    "visual_asset_source",
                    "environment_source_plate",
                }
                if storyboard_v2 is not None and (
                    not config.automation.require_human_review or not content_review_root
                ):
                    admission = db.execute(
                        "SELECT sha256 FROM preview_admissions WHERE artifact_id=?", (aid,)
                    ).fetchone()
                    preview_admitted = (
                        approval not in {"rejected", "needs_review"}
                        and admission is not None
                        and admission[0] == rec.sha256
                        and valid.valid
                    )
                add(
                    "approval",
                    approval == "approved" or preview_admitted,
                    "approval" if technical_scope == "technical" else "private",
                    (
                        "Technical/structural admission; content review is a separate gate"
                        if preview_admitted
                        else f"Current approval: {approval or 'missing'}"
                    ),
                    aid,
                )
                for dep in db.execute(
                    "SELECT input_artifact_id, input_sha256 FROM artifact_dependencies "
                    "WHERE consumer_artifact_id=?",
                    (aid,),
                ).fetchall():
                    dependency_id = str(dep[0])
                    dependencies.setdefault(aid, []).append(dependency_id)
                    try:
                        source = store.get(dependency_id)
                        pinned = source.sha256 == dep[1]
                        source_exists = True
                    except KeyError:
                        pinned = False
                        source_exists = False
                    local_integrity[aid] = local_integrity[aid] and pinned
                    add(
                        "dependency_sha",
                        pinned,
                        technical_scope,
                        "Pinned dependency SHA matches"
                        if pinned
                        else "Pinned dependency is missing or changed",
                        dependency_id,
                    )
                    if source_exists:
                        visit(dependency_id, technical_scope)
            except (KeyError, ValueError, OSError) as exc:
                local_integrity[aid] = False
                add(
                    "artifact_record",
                    False,
                    technical_scope,
                    f"Artifact cannot be inspected: {type(exc).__name__}",
                    aid,
                )
            finally:
                visiting.remove(aid)
                graph.add(aid)

        for aid in (render_id, manifest_id, qa_id):
            if aid:
                visit(aid)
        if metadata_id:
            visit(metadata_id, "private")

        render_sha: str | None = None
        fingerprint: str | None = None
        if render_id:
            try:
                render = store.get(render_id)
                render_sha = render.sha256
                add(
                    "render_media_type",
                    render.mime_type == "video/mp4",
                    "technical",
                    f"Render MIME: {render.mime_type}",
                    render_id,
                )
            except KeyError:
                pass
        if manifest_id and render_id:
            try:
                manifest = RenderManifest.model_validate(store.read_json(manifest_id))
                catalog = load_brand(config.brand_root)
                add(
                    "brand_revision",
                    catalog.version.revision_id == row["brand_revision_id"],
                    "technical",
                    "Current brand matches pinned episode"
                    if catalog.version.revision_id == row["brand_revision_id"]
                    else "Current brand differs from pinned episode",
                )
                pack_current = any(
                    pack.revision_id == manifest.character_pack_revision
                    and pack.readiness == "approved"
                    for pack in catalog.pack_revisions
                )
                add(
                    "character_pack",
                    pack_current,
                    "technical",
                    "Pinned character pack remains approved"
                    if pack_current
                    else "Pinned character pack is unavailable or unapproved",
                )
                manifest_ok = manifest.episode_id == eid and manifest_id in {
                    str(d[0])
                    for d in db.execute(
                        "SELECT input_artifact_id FROM artifact_dependencies "
                        "WHERE consumer_artifact_id=?",
                        (render_id,),
                    )
                }
                add(
                    "render_manifest_binding",
                    manifest_ok,
                    "technical",
                    "Manifest is bound to selected render"
                    if manifest_ok
                    else "Manifest does not bind selected render",
                    manifest_id,
                )
                manifest_inputs = {
                    str(d[0])
                    for d in db.execute(
                        "SELECT input_artifact_id FROM artifact_dependencies "
                        "WHERE consumer_artifact_id=?",
                        (manifest_id,),
                    )
                }
                add(
                    "manifest_dependency_set",
                    manifest_inputs == set(manifest.dependency_sha256),
                    "technical",
                    "Manifest dependency graph matches pinned SHA map"
                    if manifest_inputs == set(manifest.dependency_sha256)
                    else "Manifest dependency graph differs from SHA map",
                    manifest_id,
                )
                for aid, digest in manifest.dependency_sha256.items():
                    try:
                        match = store.get(aid).sha256 == digest
                    except KeyError:
                        match = False
                    add(
                        "manifest_dependency",
                        match,
                        "technical",
                        "Manifest SHA matches" if match else "Manifest dependency SHA differs",
                        aid,
                    )
                if manifest.environment_set_artifact_id:
                    add(
                        "environment_set",
                        manifest.environment_set_artifact_id in graph,
                        "technical",
                        "Selected environment set in render graph",
                        manifest.environment_set_artifact_id,
                    )
                if storyboard_v2 is not None:
                    expected_assets = set(storyboard_v2.asset_artifact_ids.values())
                    add(
                        "episode_visual_assets",
                        expected_assets <= graph,
                        "technical",
                        "Episode-scoped visual asset bindings are in the render graph",
                        storyboard_v2.visual_plan_artifact_id,
                    )
                    add(
                        "episode_environment_binding",
                        manifest.environment_set_artifact_id
                        == storyboard_v2.environment_set_artifact_id,
                        "technical",
                        "Renderer environment matches the V2 storyboard binding",
                    )
                elif manifest.renderer_version == "tovitunes_visual_story_render_v1":
                    lesson = db.execute(
                        "SELECT artifact_id FROM artifact_selections WHERE owner_scope='brand' "
                        "AND owner_id=? AND kind='lesson_object_manifest' LIMIT 1",
                        (row["brand_revision_id"],),
                    ).fetchone()
                    add(
                        "lesson_object_manifest",
                        bool(lesson),
                        "technical",
                        "Selected lesson-object manifest"
                        if lesson
                        else "Required lesson-object manifest is missing",
                        str(lesson[0]) if lesson else None,
                    )
                    if lesson:
                        visit(str(lesson[0]))
            except (KeyError, ValueError, OSError) as exc:
                add(
                    "render_manifest_binding",
                    False,
                    "technical",
                    f"Manifest invalid: {type(exc).__name__}",
                    manifest_id,
                )
        if qa_id and render_id:
            try:
                qa = store.read_json(qa_id)
                passed = (
                    isinstance(qa, dict)
                    and qa.get("passed") is True
                    and qa.get("render_artifact_id") == render_id
                    and qa.get("render_sha256") == render_sha
                )
                add(
                    "media_qa_passed",
                    passed,
                    "technical",
                    "Media QA passed for selected SHA"
                    if passed
                    else "Media QA failed or refers to another render",
                    qa_id,
                )
            except (KeyError, ValueError, OSError):
                add("media_qa_passed", False, "technical", "Media QA evidence invalid", qa_id)
        if metadata_id and render_id:
            try:
                metadata = EpisodePublicationMetadata.model_validate(store.read_json(metadata_id))
                matched = (
                    metadata.episode_id == eid
                    and metadata.final_render_artifact_id == render_id
                    and metadata.final_render_sha256 == render_sha
                )
                add(
                    "metadata_binding",
                    matched,
                    "private",
                    "Metadata binds selected render"
                    if matched
                    else "Metadata refers to a different render",
                    metadata_id,
                )
                fingerprint = (
                    sha256(
                        json.dumps(
                            metadata.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                    if matched
                    else None
                )
            except (KeyError, ValueError, OSError):
                add(
                    "metadata_binding",
                    False,
                    "private",
                    "Publication metadata invalid",
                    metadata_id,
                )

        latest_rights = {aid: _latest(db, "rights_decisions", aid) for aid in records}
        for aid in sorted(records):
            status = latest_rights[aid]
            add(
                "rights_not_blocked",
                status != "blocked",
                "rights_private",
                f"Current rights: {status or 'missing'}",
                aid,
            )
            if is_direct_rights_root(records[aid]):
                add(
                    "commercial_rights_direct",
                    status == "commercial_use_confirmed",
                    "rights_public",
                    f"Direct rights root; current rights: {status or 'missing'}",
                    aid,
                )
                continue
            inherited = evaluate_inherited_rights(
                aid,
                records=records,
                dependencies=dependencies,
                latest_rights=latest_rights,
                local_integrity=local_integrity,
            )
            if not inherited.dependency_graph_valid:
                reason = "Inherited rights blocked: dependency graph is invalid"
            elif inherited.blocked_artifacts:
                reason = "Inherited rights blocked by: " + ", ".join(inherited.blocked_artifacts)
            elif not inherited.commercially_cleared:
                uncleared = [
                    root
                    for root in inherited.direct_roots
                    if latest_rights.get(root) != "commercial_use_confirmed"
                ]
                reason = "Inherited rights await direct roots: " + ", ".join(uncleared)
            else:
                reason = "Inherited from cleared direct roots: " + (
                    ", ".join(inherited.direct_roots) or "none"
                )
            add(
                "commercial_rights_inherited",
                inherited.commercially_cleared,
                "rights_public",
                reason,
                aid,
            )

        if storyboard_v2 is not None and config.automation.require_human_review:
            required_reviews = {
                aid
                for aid, rec in records.items()
                if aid in {render_id, metadata_id}
                or rec.identity.kind
                in {"audio_master", "visual_asset_source", "environment_source_plate"}
            }
            for aid in required_reviews:
                decision = db.execute(
                    "SELECT status,actor FROM approval_decisions WHERE artifact_id=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (aid,),
                ).fetchone()
                human = bool(
                    decision
                    and decision[0] == "approved"
                    and not decision[1].startswith(("machine:", "system:"))
                )
                add(
                    "production_human_review",
                    human,
                    "approval",
                    "Configured human review is current"
                    if human
                    else "Human visual/copy review required",
                    aid,
                )

    technical = all(c.passed for c in checks if c.scope == "technical")
    approval = all(c.passed for c in checks if c.scope == "approval")
    private = (
        technical
        and approval
        and all(c.passed for c in checks if c.scope in {"private", "rights_private"})
    )
    public = private and all(c.passed for c in checks if c.scope == "rights_public")
    return ReleasePreflight(
        episode_key,
        technical and approval,
        private,
        public,
        tuple(checks),
        render_id,
        render_sha,
        metadata_id,
        fingerprint,
    )
