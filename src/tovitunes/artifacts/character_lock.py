"""Portable bootstrap snapshot for an already approved character pack.

The lock exports existing SQLite state. It never grants approval or rights.
"""

import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import yaml
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from tovitunes.artifacts.character_intake import (
    IntakeRecipe,
    _digest,
    _extract,
    _source_path,
    _write_png,
    load_recipe,
    prepare_assets,
)
from tovitunes.artifacts.character_pack import assess_pack_assets
from tovitunes.artifacts.media import validate_media
from tovitunes.artifacts.store import AssetStore
from tovitunes.catalog import BrandCatalog
from tovitunes.domain.artifact import ArtifactDependency, ArtifactIdentity, Provenance
from tovitunes.domain.review import ApprovalDecision, RightsDecision


class LockedRightsDecision(RightsDecision):
    decision_id: str


class LockedApprovalDecision(ApprovalDecision):
    decision_id: str


class LockedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    owner_scope: Literal["episode", "brand"]
    owner_id: str
    kind: str
    slot_key: str
    schema_version: int = Field(ge=1)
    relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(gt=0)
    mime_type: str
    provenance: Provenance
    created_at: datetime
    dependencies: tuple[ArtifactDependency, ...]
    rights_decisions: tuple[LockedRightsDecision, ...]
    approval_decisions: tuple[LockedApprovalDecision, ...]


class LockedSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_scope: Literal["episode", "brand"]
    owner_id: str
    kind: str
    slot_key: str
    artifact_id: str
    selected_at: datetime


class CharacterPackLock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    pack_id: str
    character_id: str
    version: str
    brand_revision_id: str
    pack_revision_id: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_artifact_ids: dict[str, str]
    active_role_artifact_ids: dict[str, str]
    superseded_role_artifact_ids: dict[str, str]
    artifacts: tuple[LockedArtifact, ...]
    selections: tuple[LockedSelection, ...]


def _uuid(value: str) -> None:
    try:
        if str(UUID(value)) != value:
            raise ValueError("UUID is not normalized")
    except ValueError as exc:
        raise ValueError(f"invalid canonical UUID: {value}") from exc


def _role_kind_slot(role: str) -> tuple[str, str]:
    return (
        "character_reference" if role.startswith("view/") else "character_sprite",
        "tovi_v1_" + role.replace("/", "_"),
    )


def load_lock(path: Path) -> CharacterPackLock:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CharacterPackLock.model_validate(raw)


def validate_pack_lock(
    lock: CharacterPackLock, catalog: BrandCatalog, recipe: IntakeRecipe, recipe_path: Path
) -> None:
    """Validate the manifest and full lock graph without opening media or SQLite."""
    pack = catalog.packs[0]
    revision = catalog.pack_revisions[0]
    if pack.readiness != "approved":
        raise ValueError("canonical rehydration requires an approved pack manifest")
    if (lock.pack_id, lock.character_id, lock.version) != (
        pack.pack_id,
        pack.character_id,
        pack.version,
    ):
        raise ValueError("lock pack identity or version differs from manifest")
    if lock.brand_revision_id != catalog.version.revision_id:
        raise ValueError("lock brand owner/revision differs from catalog")
    if (lock.pack_revision_id, lock.manifest_sha256) != (
        revision.revision_id,
        revision.manifest_sha256,
    ):
        raise ValueError("lock pack revision differs from manifest")
    if lock.recipe_sha256 != _digest(recipe_path):
        raise ValueError("lock recipe revision differs from checked-in recipe")
    if lock.active_role_artifact_ids != pack.asset_artifact_ids:
        raise ValueError("lock active role mapping differs from pack.yaml")
    if set(lock.source_artifact_ids) != set(recipe.sources):
        raise ValueError("lock source mapping differs from recipe")
    if set(lock.active_role_artifact_ids) != {item.role for item in recipe.assets}:
        raise ValueError("lock active roles differ from recipe")
    if set(lock.superseded_role_artifact_ids) != {item.role for item in recipe.superseded_assets}:
        raise ValueError("lock superseded roles differ from recipe")
    groups = (
        lock.source_artifact_ids,
        lock.active_role_artifact_ids,
        lock.superseded_role_artifact_ids,
    )
    mapped_ids = [artifact_id for group in groups for artifact_id in group.values()]
    artifact_ids = [item.artifact_id for item in lock.artifacts]
    if len(set(mapped_ids)) != len(mapped_ids) or len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("duplicate canonical artifact ID")
    if set(mapped_ids) != set(artifact_ids):
        raise ValueError("lock contains missing or unmapped canonical artifacts")
    by_id = {item.artifact_id: item for item in lock.artifacts}
    all_decision_ids: list[str] = []
    for item in lock.artifacts:
        _uuid(item.artifact_id)
        if item.owner_scope != "brand" or item.owner_id != lock.brand_revision_id:
            raise ValueError("canonical artifact owner differs from brand revision")
        if item.schema_version != 1 or item.mime_type != "image/png":
            raise ValueError("unsupported canonical artifact schema or media type")
        relative = (
            f"brand-assets/{item.owner_id}/{item.kind}/{item.slot_key}/{item.artifact_id}.png"
        )
        if item.relative_path != relative:
            raise ValueError("canonical artifact path differs from identity")
        dependency_ids = tuple(dep.input_artifact_id for dep in item.dependencies)
        if item.provenance.input_artifact_ids != dependency_ids:
            raise ValueError("provenance input IDs differ from dependencies")
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError("duplicate canonical dependency")
        for dep in item.dependencies:
            if dep.consumer_artifact_id != item.artifact_id or dep.input_artifact_id not in by_id:
                raise ValueError("lock dependency references an unknown artifact")
            if by_id[dep.input_artifact_id].sha256 != dep.input_sha256:
                raise ValueError("lock dependency hash differs from input artifact")
        if not item.approval_decisions or not item.rights_decisions:
            raise ValueError("canonical artifact lacks approval or rights decision history")
        if item.approval_decisions[0].status != "pending":
            raise ValueError("canonical approval history lacks initial pending state")
        if item.rights_decisions[0].status != "unknown":
            raise ValueError("canonical rights history lacks initial unknown state")
        for approval_decision in item.approval_decisions:
            _uuid(approval_decision.decision_id)
            if (
                approval_decision.target_kind != "artifact"
                or approval_decision.target_id != item.artifact_id
            ):
                raise ValueError("approval decision targets another artifact")
            all_decision_ids.append(approval_decision.decision_id)
        for rights_decision in item.rights_decisions:
            _uuid(rights_decision.decision_id)
            if rights_decision.artifact_id != item.artifact_id:
                raise ValueError("rights decision targets another artifact")
            all_decision_ids.append(rights_decision.decision_id)
    if len(set(all_decision_ids)) != len(all_decision_ids):
        raise ValueError("duplicate canonical decision ID")

    expected_dependencies: dict[str, tuple[tuple[str, str], ...]] = {}
    for key, artifact_id in lock.source_artifact_ids.items():
        item = by_id[artifact_id]
        source_spec = recipe.sources[key]
        if (item.kind, item.slot_key, item.sha256) != (
            "character_reference",
            "source_" + key,
            source_spec.sha256,
        ):
            raise ValueError(f"source identity or hash differs: {key}")
        if (
            item.provenance.source_kind != "manual"
            or item.provenance.source_uri != source_spec.source_uri
        ):
            raise ValueError(f"source provenance differs: {key}")
        originals = ("original_profile", "original_banner") if source_spec.uses_originals else ()
        expected_dependencies[artifact_id] = tuple(
            (lock.source_artifact_ids[name], "original Tovi visual reference") for name in originals
        )
        expected_status = (
            "unknown"
            if key in {"original_profile", "original_banner"}
            else "commercial_use_confirmed"
            if source_spec.rights_basis
            else "unknown"
        )
        if item.approval_decisions[-1].status != "approved":
            raise ValueError(f"source lacks recorded approval: {key}")
        if item.rights_decisions[-1].status != expected_status:
            raise ValueError(f"source rights state differs: {key}")
        if source_spec.rights_basis:
            evidence = recipe.rights_evidence
            assert evidence is not None
            latest = item.rights_decisions[-1]
            if (
                latest.evidence_uri != evidence.uri
                or latest.policy_version != evidence.policy_version
            ):
                raise ValueError(f"source rights evidence differs from recipe: {key}")
    role_specs = {item.role: item for item in recipe.assets}
    old_specs = {item.role: item for item in recipe.superseded_assets}
    for mapping, specs, current in (
        (lock.active_role_artifact_ids, role_specs, True),
        (lock.superseded_role_artifact_ids, old_specs, False),
    ):
        for role, artifact_id in mapping.items():
            item = by_id[artifact_id]
            asset_spec = specs[role]
            expected_review = "approved" if current else "needs_review"
            if asset_spec.art_review != expected_review:
                raise ValueError(f"role review recipe differs from canonical state: {role}")
            if (item.kind, item.slot_key) != _role_kind_slot(role):
                raise ValueError(f"role kind or slot differs: {role}")
            if (
                item.provenance.source_kind != "deterministic"
                or item.provenance.source_uri != f"intake://tovi-v1/{asset_spec.source}/{role}"
            ):
                raise ValueError(f"role provenance differs: {role}")
            expected_dependencies[artifact_id] = (
                (lock.source_artifact_ids[asset_spec.source], "deterministic character crop"),
            )
            status = "approved" if current else "needs_review"
            if item.approval_decisions[-1].status != status:
                raise ValueError(f"role approval state differs: {role}")
            latest_rights = item.rights_decisions[-1]
            if current and (
                latest_rights.status != "commercial_use_confirmed" or not latest_rights.evidence_uri
            ):
                raise ValueError(f"active role lacks rights evidence: {role}")
            if recipe.sources[asset_spec.source].rights_basis:
                evidence = recipe.rights_evidence
                assert evidence is not None
                if (
                    latest_rights.status != "commercial_use_confirmed"
                    or latest_rights.evidence_uri != evidence.uri
                    or latest_rights.policy_version != evidence.policy_version
                ):
                    raise ValueError(f"role rights evidence differs from recipe: {role}")
    for artifact_id, expected in expected_dependencies.items():
        actual = tuple(
            (dep.input_artifact_id, dep.purpose) for dep in by_id[artifact_id].dependencies
        )
        if actual != expected:
            raise ValueError("canonical dependency graph differs from recipe")
    expected_selected_ids = set(lock.source_artifact_ids.values()) | set(
        lock.active_role_artifact_ids.values()
    )
    if len(lock.selections) != len(expected_selected_ids):
        raise ValueError("lock does not select exactly the canonical active artifacts")
    selected_ids: set[str] = set()
    selected_slots: set[tuple[str, str, str, str]] = set()
    for selection in lock.selections:
        if selection.artifact_id not in expected_selected_ids:
            raise ValueError("superseded or unknown artifact is selected")
        item = by_id[selection.artifact_id]
        slot = (selection.owner_scope, selection.owner_id, selection.kind, selection.slot_key)
        if slot != (item.owner_scope, item.owner_id, item.kind, item.slot_key):
            raise ValueError("selection slot differs from selected artifact")
        if slot in selected_slots:
            raise ValueError("duplicate canonical selection slot")
        selected_slots.add(slot)
        selected_ids.add(selection.artifact_id)
    if selected_ids != expected_selected_ids:
        raise ValueError("canonical selection mapping is incomplete")


def export_pack_lock(
    recipe_path: Path, store: AssetStore, catalog: BrandCatalog, destination: Path
) -> CharacterPackLock:
    """Export the existing approved SQLite graph; never invent canonical IDs."""
    recipe = load_recipe(recipe_path)
    pack = catalog.packs[0]
    if pack.readiness != "approved":
        raise ValueError("only an approved pack can be exported")
    assessment = assess_pack_assets(pack, store, catalog.version.revision_id)
    if not assessment.ready:
        raise ValueError(f"approved pack is not ready: {assessment.issues}")
    owner = catalog.version.revision_id
    source_ids: dict[str, str] = {}
    old_ids: dict[str, str] = {}
    with closing(store.database.connect()) as connection:
        for key, source_spec in recipe.sources.items():
            rows = connection.execute(
                "SELECT artifact_id FROM artifact_versions WHERE brand_revision_id = ? "
                "AND kind = 'character_reference' AND slot_key = ? AND sha256 = ?",
                (owner, "source_" + key, source_spec.sha256),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(f"canonical source has zero or multiple identities: {key}")
            source_ids[key] = rows[0]["artifact_id"]
        for old_spec in recipe.superseded_assets:
            kind, slot = _role_kind_slot(old_spec.role)
            rows = connection.execute(
                "SELECT artifact_id, provenance_json FROM artifact_versions "
                "WHERE brand_revision_id = ? AND kind = ? AND slot_key = ? "
                "AND artifact_id <> ?",
                (owner, kind, slot, pack.asset_artifact_ids[old_spec.role]),
            ).fetchall()
            matches = [
                row["artifact_id"]
                for row in rows
                if Provenance.model_validate_json(row["provenance_json"]).source_uri
                == f"intake://tovi-v1/{old_spec.source}/{old_spec.role}"
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"superseded candidate has zero or multiple identities: {old_spec.role}"
                )
            old_ids[old_spec.role] = matches[0]
        all_ids = (
            set(source_ids.values()) | set(pack.asset_artifact_ids.values()) | set(old_ids.values())
        )
        artifacts: list[LockedArtifact] = []
        for artifact_id in sorted(all_ids):
            row = connection.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"canonical artifact is absent: {artifact_id}")
            dependencies = [
                dict(dep)
                for dep in connection.execute(
                    "SELECT * FROM artifact_dependencies WHERE consumer_artifact_id = ? "
                    "ORDER BY rowid",
                    (artifact_id,),
                )
            ]
            rights = [
                dict(decision)
                for decision in connection.execute(
                    "SELECT * FROM rights_decisions WHERE artifact_id = ? ORDER BY rowid",
                    (artifact_id,),
                )
            ]
            approvals = [
                {
                    **dict(decision),
                    "target_id": artifact_id,
                    "target_kind": "artifact",
                }
                for decision in connection.execute(
                    "SELECT * FROM approval_decisions WHERE artifact_id = ? ORDER BY rowid",
                    (artifact_id,),
                )
            ]
            for decision in approvals:
                decision.pop("episode_id")
                decision.pop("artifact_id")
            artifacts.append(
                LockedArtifact.model_validate(
                    {
                        "artifact_id": artifact_id,
                        "owner_scope": row["owner_scope"],
                        "owner_id": row["brand_revision_id"],
                        "kind": row["kind"],
                        "slot_key": row["slot_key"],
                        "schema_version": row["schema_version"],
                        "relative_path": row["relative_path"],
                        "sha256": row["sha256"],
                        "byte_count": row["byte_count"],
                        "mime_type": row["mime_type"],
                        "provenance": json.loads(row["provenance_json"]),
                        "created_at": row["created_at"],
                        "dependencies": dependencies,
                        "rights_decisions": rights,
                        "approval_decisions": approvals,
                    }
                )
            )
        selections = [
            LockedSelection.model_validate(dict(row))
            for row in connection.execute(
                "SELECT * FROM artifact_selections WHERE owner_scope = 'brand' "
                "AND owner_id = ? ORDER BY kind, slot_key",
                (owner,),
            )
        ]
    lock = CharacterPackLock(
        schema_version=1,
        pack_id=pack.pack_id,
        character_id=pack.character_id,
        version=pack.version,
        brand_revision_id=owner,
        pack_revision_id=catalog.pack_revisions[0].revision_id,
        manifest_sha256=catalog.pack_revisions[0].manifest_sha256,
        recipe_sha256=_digest(recipe_path),
        source_artifact_ids=source_ids,
        active_role_artifact_ids=pack.asset_artifact_ids,
        superseded_role_artifact_ids=old_ids,
        artifacts=tuple(artifacts),
        selections=tuple(selections),
    )
    validate_pack_lock(lock, catalog, recipe, recipe_path)
    destination.write_text(
        yaml.safe_dump(lock.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    return lock


def _input_paths(
    recipe: IntakeRecipe,
    recipe_path: Path,
    source_dir: Path,
    prepared_dir: Path,
    lock: CharacterPackLock,
) -> dict[str, Path]:
    """Build and verify every locked media input before touching SQLite."""
    report = prepare_assets(recipe_path, source_dir, prepared_dir)
    prepared_root = prepared_dir.resolve(strict=True)
    source_root = source_dir.resolve(strict=True)
    paths: dict[str, Path] = {}
    for key, source_spec in recipe.sources.items():
        paths[lock.source_artifact_ids[key]] = _source_path(source_root, source_spec)
    assets_report = cast(list[dict[str, object]], report["assets"])
    reported = {str(item["role"]): item for item in assets_report}
    for asset_spec in recipe.assets:
        reported_item = reported[asset_spec.role]
        name = asset_spec.role.replace("/", "__") + ".png"
        path = prepared_root / name
        if (
            reported_item["filename"] != name
            or reported_item["source"] != asset_spec.source
            or path.is_symlink()
            or path.resolve(strict=True).parent != prepared_root
            or _digest(path) != reported_item["sha256"]
        ):
            raise ValueError(f"prepared role differs from preparation report: {asset_spec.role}")
        paths[lock.active_role_artifact_ids[asset_spec.role]] = path
    if recipe.superseded_assets:
        old_root = prepared_root / "superseded"
        old_root.mkdir(exist_ok=True)
        if old_root.is_symlink() or old_root.resolve(strict=True).parent != prepared_root:
            raise ValueError("superseded preparation directory is not trusted")
        for old_spec in recipe.superseded_assets:
            source = _source_path(source_root, recipe.sources[old_spec.source])
            with Image.open(source) as image:
                image.load()
                output, _, _ = _extract(image, old_spec, recipe.mouth_normalization)
            path = old_root / (old_spec.role.replace("/", "__") + ".png")
            _write_png(path, output)
            if path.is_symlink() or path.resolve(strict=True).parent != old_root:
                raise ValueError("superseded prepared file is not trusted")
            paths[lock.superseded_role_artifact_ids[old_spec.role]] = path
    for locked_item in lock.artifacts:
        path = paths[locked_item.artifact_id]
        if _digest(path) != locked_item.sha256 or path.stat().st_size != locked_item.byte_count:
            raise ValueError(f"media bytes differ from canonical lock: {locked_item.artifact_id}")
        mime_type, _, _ = validate_media(path)
        if mime_type != locked_item.mime_type:
            raise ValueError(f"media type differs from canonical lock: {locked_item.artifact_id}")
    return paths


def _restore_decisions(store: AssetStore, item: LockedArtifact) -> None:
    expected_rights = [
        (
            decision.decision_id,
            item.artifact_id,
            decision.status,
            decision.actor,
            decision.evidence_uri,
            decision.policy_version,
            decision.decided_at.isoformat(),
            decision.rationale,
        )
        for decision in item.rights_decisions
    ]
    expected_approvals = [
        (
            decision.decision_id,
            None,
            item.artifact_id,
            decision.status,
            decision.actor,
            decision.reason,
            decision.policy_version,
            decision.decided_at.isoformat(),
        )
        for decision in item.approval_decisions
    ]
    with closing(store.database.connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            rights = [
                tuple(row)
                for row in connection.execute(
                    "SELECT decision_id, artifact_id, status, actor, evidence_uri, "
                    "policy_version, decided_at, rationale FROM rights_decisions "
                    "WHERE artifact_id = ? ORDER BY rowid",
                    (item.artifact_id,),
                )
            ]
            approvals = [
                tuple(row)
                for row in connection.execute(
                    "SELECT decision_id, episode_id, artifact_id, status, actor, reason, "
                    "policy_version, decided_at FROM approval_decisions "
                    "WHERE artifact_id = ? ORDER BY rowid",
                    (item.artifact_id,),
                )
            ]
            if rights and rights != expected_rights:
                raise ValueError("existing rights history differs from canonical lock")
            if approvals and approvals != expected_approvals:
                raise ValueError("existing approval history differs from canonical lock")
            if not rights:
                connection.executemany(
                    "INSERT INTO rights_decisions "
                    "(decision_id, artifact_id, status, actor, evidence_uri, policy_version, "
                    "decided_at, rationale) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    expected_rights,
                )
            if not approvals:
                connection.executemany(
                    "INSERT INTO approval_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    expected_approvals,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _restore_selection(store: AssetStore, selection: LockedSelection) -> None:
    eligible, reason = store.eligibility(selection.artifact_id)
    if not eligible:
        raise ValueError(f"canonical selection is not eligible: {reason}")
    expected = (
        selection.owner_scope,
        selection.owner_id,
        selection.kind,
        selection.slot_key,
        selection.artifact_id,
        selection.selected_at.isoformat(),
    )
    with closing(store.database.connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT owner_scope, owner_id, kind, slot_key, artifact_id, selected_at "
                "FROM artifact_selections WHERE owner_scope = ? AND owner_id = ? "
                "AND kind = ? AND slot_key = ?",
                expected[:4],
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO artifact_selections VALUES (?, ?, ?, ?, ?, ?)", expected
                )
            elif tuple(row) != expected:
                raise ValueError("existing selection differs from canonical lock")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def rehydrate_pack(
    recipe_path: Path,
    lock_path: Path,
    source_dir: Path,
    prepared_dir: Path,
    store: AssetStore,
    catalog: BrandCatalog,
) -> dict[str, object]:
    """Restore an approved pack from exact bytes and recorded identities and decisions."""
    recipe = load_recipe(recipe_path)
    lock = load_lock(lock_path)
    validate_pack_lock(lock, catalog, recipe, recipe_path)
    manifest_path = recipe_path.with_name("pack.yaml")
    manifest_digest = _digest(manifest_path)
    if manifest_digest != catalog.pack_revisions[0].manifest_sha256:
        raise ValueError("approved pack manifest changed since catalog load")
    paths = _input_paths(recipe, recipe_path, source_dir, prepared_dir, lock)
    store.database.register_catalog(catalog)
    remaining = {item.artifact_id: item for item in lock.artifacts}
    restored: set[str] = set()
    ordered: list[LockedArtifact] = []
    while remaining:
        ready = [
            item
            for item in remaining.values()
            if all(dep.input_artifact_id in restored for dep in item.dependencies)
        ]
        if not ready:
            raise ValueError("canonical artifact dependency graph has a cycle")
        for item in sorted(ready, key=lambda value: value.artifact_id):
            store.rehydrate_artifact(
                paths[item.artifact_id],
                identity=ArtifactIdentity(
                    artifact_id=item.artifact_id,
                    owner_scope=item.owner_scope,
                    owner_id=item.owner_id,
                    kind=item.kind,
                    slot_key=item.slot_key,
                ),
                expected_sha256=item.sha256,
                expected_byte_count=item.byte_count,
                expected_mime_type=item.mime_type,
                expected_relative_path=item.relative_path,
                provenance=item.provenance,
                created_at=item.created_at.isoformat(),
                dependencies=item.dependencies,
            )
            restored.add(item.artifact_id)
            ordered.append(item)
            del remaining[item.artifact_id]
    for item in ordered:
        _restore_decisions(store, item)
    by_id = {item.artifact_id: item for item in lock.artifacts}
    for item in ordered:
        for selection in lock.selections:
            if selection.artifact_id == item.artifact_id:
                _restore_selection(store, selection)
    for selection in lock.selections:
        item = by_id[selection.artifact_id]
        current = store.selected(item.owner_scope, item.owner_id, item.kind, item.slot_key)
        if current is None or current.identity.artifact_id != item.artifact_id:
            raise ValueError("canonical selection was not restored")
    assessment = assess_pack_assets(catalog.packs[0], store, lock.brand_revision_id)
    if not assessment.ready:
        raise ValueError(f"rehydrated pack failed independent assessment: {assessment.issues}")
    if _digest(manifest_path) != manifest_digest:
        raise ValueError("approved manifest changed during rehydration")
    return {
        "brand_revision_id": lock.brand_revision_id,
        "pack_revision_id": lock.pack_revision_id,
        "artifact_count": len(lock.artifacts),
        "source_count": len(lock.source_artifact_ids),
        "active_role_count": len(lock.active_role_artifact_ids),
        "superseded_count": len(lock.superseded_role_artifact_ids),
        "ready": assessment.ready,
        "issues": assessment.issues,
    }
