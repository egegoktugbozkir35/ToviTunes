"""Durable, immutable asset ingestion with explicit selection and recovery."""

import json
import os
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from tovitunes.artifacts.media import InvalidMedia, validate_media
from tovitunes.domain.artifact import ArtifactDependency, ArtifactIdentity, Provenance
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.persistence.db import Database

_SAFE_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _safe_segment(value: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value) or value in _WINDOWS_RESERVED:
        raise ValueError(f"unsafe storage segment: {value!r}")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class InputDependency:
    artifact_id: str
    purpose: str


@dataclass(frozen=True)
class ArtifactRecord:
    identity: ArtifactIdentity
    relative_path: str
    sha256: str
    byte_count: int
    mime_type: str
    provenance: Provenance


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reasons: tuple[str, ...]
    observed_sha256: str | None = None


class AssetStore:
    def __init__(
        self,
        root: Path,
        database: Database,
        *,
        generated_source_roots: Sequence[Path] = (),
        initialize: bool = True,
    ) -> None:
        if initialize:
            root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("asset root cannot be a symlink")
        self.root = root.resolve(strict=True)
        self.database = database
        self.generated_source_roots = tuple(
            path.resolve(strict=True) for path in generated_source_roots
        )
        for name in (".staging", "quarantine"):
            directory = self.root / name
            if initialize:
                directory.mkdir(exist_ok=True)
            resolved = directory.resolve(strict=False)
            if directory.is_symlink() or not resolved.is_relative_to(self.root):
                raise ValueError(f"asset {name} directory is not trusted")

    def _trusted_path(self, relative: str, *, must_exist: bool) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
            raise ValueError("artifact path is not root-relative")
        path = (self.root / candidate).resolve(strict=must_exist)
        if not path.is_relative_to(self.root):
            raise ValueError("artifact path escapes trusted root")
        return path

    def ingest(
        self,
        source: Path,
        *,
        owner_scope: Literal["episode", "brand"],
        owner_id: str,
        kind: str,
        slot_key: str,
        provenance: Provenance,
        dependencies: Sequence[InputDependency] = (),
        expected_media_type: str | None = None,
    ) -> ArtifactRecord:
        """Copy, validate and register bytes; a failed DB commit leaves a recoverable orphan."""
        _safe_segment(owner_id)
        identity = ArtifactIdentity.create(owner_scope, owner_id, kind, slot_key)
        _safe_segment(identity.kind)
        _safe_segment(identity.slot_key)
        source_path = source.resolve(strict=True)
        if not source_path.is_file():
            raise ValueError("source is not a regular file")
        if provenance.source_kind != "manual" and not any(
            source_path.is_relative_to(approved_root)
            for approved_root in self.generated_source_roots
        ):
            raise ValueError("generated source is outside configured trusted roots")
        suffix = source_path.suffix.lower()
        staging = self.root / ".staging" / f"{identity.artifact_id}{suffix}"
        digest = sha256()
        byte_count = 0
        finalized = False
        try:
            with source_path.open("rb") as src, staging.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            mime_type, extension, facts = validate_media(staging)
            if expected_media_type is not None and mime_type != expected_media_type:
                raise InvalidMedia("media type differs from expected type")
            owner_folder = "episodes" if owner_scope == "episode" else "brand-assets"
            relative = (
                f"{owner_folder}/{owner_id}/{identity.kind}/{identity.slot_key}/"
                f"{identity.artifact_id}{extension}"
            )
            final_path = self._trusted_path(relative, must_exist=False)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            final_path = self._trusted_path(relative, must_exist=False)
            if final_path.exists():
                raise FileExistsError(final_path)
            with closing(self.database.connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    os.replace(staging, final_path)
                    finalized = True
                    owner_column = "episode_id" if owner_scope == "episode" else "brand_revision_id"
                    connection.execute(
                        "INSERT INTO artifact_versions "
                        "(artifact_id, owner_scope, episode_id, brand_revision_id, kind, slot_key, "
                        "schema_version, relative_path, sha256, byte_count, mime_type, "
                        "provenance_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            identity.artifact_id,
                            owner_scope,
                            owner_id if owner_column == "episode_id" else None,
                            owner_id if owner_column == "brand_revision_id" else None,
                            identity.kind,
                            identity.slot_key,
                            1,
                            relative,
                            digest.hexdigest(),
                            byte_count,
                            mime_type,
                            provenance.model_dump_json(),
                            _now(),
                        ),
                    )
                    for dependency in dependencies:
                        input_row = connection.execute(
                            "SELECT sha256 FROM artifact_versions WHERE artifact_id = ?",
                            (dependency.artifact_id,),
                        ).fetchone()
                        if input_row is None:
                            raise ValueError("input dependency is not registered")
                        connection.execute(
                            "INSERT INTO artifact_dependencies VALUES (?, ?, ?, ?)",
                            (
                                identity.artifact_id,
                                dependency.artifact_id,
                                input_row["sha256"],
                                dependency.purpose,
                            ),
                        )
                    connection.execute(
                        "INSERT INTO artifact_validation VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid4()),
                            identity.artifact_id,
                            "file-signature-v1",
                            "passed",
                            digest.hexdigest(),
                            json.dumps(facts, sort_keys=True),
                            _now(),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO rights_decisions "
                        "(decision_id, artifact_id, status, actor, evidence_uri, "
                        "policy_version, decided_at, rationale) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid4()),
                            identity.artifact_id,
                            "unknown",
                            "system:ingestion",
                            None,
                            "ingest-v1",
                            _now(),
                            None,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO approval_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid4()),
                            None,
                            identity.artifact_id,
                            "pending",
                            "system:ingestion",
                            None,
                            "ingest-v1",
                            _now(),
                        ),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            return ArtifactRecord(
                identity=identity,
                relative_path=relative,
                sha256=digest.hexdigest(),
                byte_count=byte_count,
                mime_type=mime_type,
                provenance=provenance,
            )
        finally:
            if not finalized and staging.exists():
                staging.unlink()

    def rehydrate_artifact(
        self,
        source: Path,
        *,
        identity: ArtifactIdentity,
        expected_sha256: str,
        expected_byte_count: int,
        expected_mime_type: str,
        expected_relative_path: str,
        provenance: Provenance,
        created_at: str,
        dependencies: Sequence[ArtifactDependency] = (),
    ) -> ArtifactRecord:
        """Restore one locked identity without minting decisions or changing existing versions."""
        if str(UUID(identity.artifact_id)) != identity.artifact_id:
            raise ValueError("canonical artifact ID is not a normalized UUID")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or expected_byte_count <= 0:
            raise ValueError("invalid canonical hash or byte count")
        _safe_segment(identity.owner_id)
        _safe_segment(identity.kind)
        _safe_segment(identity.slot_key)
        source_path = source.resolve(strict=True)
        if source.is_symlink() or not source_path.is_file():
            raise ValueError("source is not a trusted regular file")
        if provenance.source_kind != "manual" and not any(
            source_path.is_relative_to(root) for root in self.generated_source_roots
        ):
            raise ValueError("generated source is outside configured trusted roots")
        if tuple(dependency.input_artifact_id for dependency in dependencies) != (
            provenance.input_artifact_ids
        ):
            raise ValueError("provenance input IDs differ from dependencies")
        if any(
            dependency.consumer_artifact_id != identity.artifact_id for dependency in dependencies
        ):
            raise ValueError("dependency consumer differs from canonical identity")
        if len({(item.input_artifact_id, item.purpose) for item in dependencies}) != len(
            dependencies
        ):
            raise ValueError("duplicate canonical dependency")
        staging = self.root / ".staging" / f"{identity.artifact_id}{source_path.suffix.lower()}"
        finalized = False
        try:
            digest = sha256()
            byte_count = 0
            with source_path.open("rb") as src, staging.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            if digest.hexdigest() != expected_sha256 or byte_count != expected_byte_count:
                raise ValueError("source bytes differ from canonical lock")
            mime_type, extension, facts = validate_media(staging)
            if mime_type != expected_mime_type:
                raise ValueError("source media type differs from canonical lock")
            owner_folder = "episodes" if identity.owner_scope == "episode" else "brand-assets"
            relative = (
                f"{owner_folder}/{identity.owner_id}/{identity.kind}/{identity.slot_key}/"
                f"{identity.artifact_id}{extension}"
            )
            if relative != expected_relative_path:
                raise ValueError("canonical artifact path differs from identity")
            final_path = self._trusted_path(relative, must_exist=False)
            expected_dependencies = [
                (item.input_artifact_id, item.input_sha256, item.purpose) for item in dependencies
            ]
            with closing(self.database.connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    owner_column = (
                        "episode_id" if identity.owner_scope == "episode" else "brand_revision_id"
                    )
                    conflict = connection.execute(
                        f"SELECT artifact_id FROM artifact_versions WHERE owner_scope = ? "
                        f"AND {owner_column} = ? AND kind = ? AND slot_key = ? AND sha256 = ? "
                        "AND artifact_id <> ?",
                        (
                            identity.owner_scope,
                            identity.owner_id,
                            identity.kind,
                            identity.slot_key,
                            expected_sha256,
                            identity.artifact_id,
                        ),
                    ).fetchone()
                    if conflict is not None:
                        raise ValueError("canonical slot and content already have another identity")
                    for dependency in dependencies:
                        input_row = connection.execute(
                            "SELECT sha256 FROM artifact_versions WHERE artifact_id = ?",
                            (dependency.input_artifact_id,),
                        ).fetchone()
                        if input_row is None or input_row["sha256"] != dependency.input_sha256:
                            raise ValueError("canonical dependency is missing or has another hash")
                    existing = connection.execute(
                        "SELECT * FROM artifact_versions WHERE artifact_id = ?",
                        (identity.artifact_id,),
                    ).fetchone()
                    if existing is not None:
                        expected_fields = {
                            "owner_scope": identity.owner_scope,
                            "episode_id": identity.owner_id
                            if identity.owner_scope == "episode"
                            else None,
                            "brand_revision_id": (
                                identity.owner_id if identity.owner_scope == "brand" else None
                            ),
                            "kind": identity.kind,
                            "slot_key": identity.slot_key,
                            "schema_version": 1,
                            "relative_path": relative,
                            "sha256": expected_sha256,
                            "byte_count": expected_byte_count,
                            "mime_type": expected_mime_type,
                            "created_at": created_at,
                        }
                        if any(existing[key] != value for key, value in expected_fields.items()):
                            raise ValueError(
                                "canonical artifact ID has different immutable metadata"
                            )
                        if (
                            Provenance.model_validate_json(existing["provenance_json"])
                            != provenance
                        ):
                            raise ValueError("canonical artifact provenance differs")
                        actual_dependencies = [
                            (row["input_artifact_id"], row["input_sha256"], row["purpose"])
                            for row in connection.execute(
                                "SELECT input_artifact_id, input_sha256, purpose "
                                "FROM artifact_dependencies WHERE consumer_artifact_id = ? "
                                "ORDER BY rowid",
                                (identity.artifact_id,),
                            )
                        ]
                        if actual_dependencies != expected_dependencies:
                            raise ValueError("canonical artifact dependencies differ")
                        if not self.inspect(identity.artifact_id).valid:
                            raise ValueError("existing canonical artifact file is invalid")
                    else:
                        if final_path.exists() or final_path.is_symlink():
                            raise ValueError("canonical path already contains unregistered bytes")
                        final_path.parent.mkdir(parents=True, exist_ok=True)
                        final_path = self._trusted_path(relative, must_exist=False)
                        os.replace(staging, final_path)
                        finalized = True
                        connection.execute(
                            "INSERT INTO artifact_versions "
                            "(artifact_id, owner_scope, episode_id, brand_revision_id, "
                            "kind, slot_key, "
                            "schema_version, relative_path, sha256, byte_count, mime_type, "
                            "provenance_json, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                identity.artifact_id,
                                identity.owner_scope,
                                identity.owner_id if identity.owner_scope == "episode" else None,
                                identity.owner_id if identity.owner_scope == "brand" else None,
                                identity.kind,
                                identity.slot_key,
                                1,
                                relative,
                                expected_sha256,
                                expected_byte_count,
                                expected_mime_type,
                                provenance.model_dump_json(),
                                created_at,
                            ),
                        )
                        for dependency in dependencies:
                            connection.execute(
                                "INSERT INTO artifact_dependencies VALUES (?, ?, ?, ?)",
                                (
                                    identity.artifact_id,
                                    dependency.input_artifact_id,
                                    dependency.input_sha256,
                                    dependency.purpose,
                                ),
                            )
                        connection.execute(
                            "INSERT INTO artifact_validation VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                str(uuid4()),
                                identity.artifact_id,
                                "file-signature-v1",
                                "passed",
                                expected_sha256,
                                json.dumps(facts, sort_keys=True),
                                _now(),
                            ),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            return self.get(identity.artifact_id)
        finally:
            if not finalized and staging.exists():
                staging.unlink()

    def _row(self, connection: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM artifact_versions WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return cast(sqlite3.Row, row)

    def get(self, artifact_id: str) -> ArtifactRecord:
        with closing(self.database.connect()) as connection:
            row = self._row(connection, artifact_id)
            owner_id = (
                row["episode_id"] if row["owner_scope"] == "episode" else row["brand_revision_id"]
            )
            return ArtifactRecord(
                identity=ArtifactIdentity(
                    artifact_id=row["artifact_id"],
                    owner_scope=row["owner_scope"],
                    owner_id=owner_id,
                    kind=row["kind"],
                    slot_key=row["slot_key"],
                ),
                relative_path=row["relative_path"],
                sha256=row["sha256"],
                byte_count=row["byte_count"],
                mime_type=row["mime_type"],
                provenance=Provenance.model_validate_json(row["provenance_json"]),
            )

    def find_version(
        self,
        owner_scope: Literal["episode", "brand"],
        owner_id: str,
        kind: str,
        slot_key: str,
        sha256_digest: str,
    ) -> ArtifactRecord | None:
        """Return an already ingested immutable version for repeatable intake."""
        owner_column = "episode_id" if owner_scope == "episode" else "brand_revision_id"
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                f"SELECT artifact_id FROM artifact_versions WHERE owner_scope = ? "
                f"AND {owner_column} = ? AND kind = ? AND slot_key = ? AND sha256 = ? "
                "ORDER BY created_at LIMIT 1",
                (owner_scope, owner_id, kind, slot_key, sha256_digest),
            ).fetchone()
        return self.get(row["artifact_id"]) if row is not None else None

    def inspect(self, artifact_id: str) -> ValidationResult:
        record = self.get(artifact_id)
        try:
            path = self._trusted_path(record.relative_path, must_exist=True)
            mime_type, _, _ = validate_media(path)
            digest = sha256()
            size = 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            reasons: list[str] = []
            if mime_type != record.mime_type:
                reasons.append("media_type_changed")
            if size != record.byte_count:
                reasons.append("size_changed")
            if digest.hexdigest() != record.sha256:
                reasons.append("hash_changed")
            return ValidationResult(not reasons, tuple(reasons), digest.hexdigest())
        except (FileNotFoundError, ValueError, OSError) as exc:
            return ValidationResult(False, (type(exc).__name__,))

    def path_for(self, artifact_id: str) -> Path:
        """Resolve a registered file beneath the trusted asset root."""
        return self._trusted_path(self.get(artifact_id).relative_path, must_exist=True)

    def read_json(self, artifact_id: str) -> object:
        record = self.get(artifact_id)
        if record.mime_type != "application/json" or not self.inspect(artifact_id).valid:
            raise ValueError("JSON artifact is invalid")
        path = self._trusted_path(record.relative_path, must_exist=True)
        return json.loads(path.read_text(encoding="utf-8"))

    def record_rights(self, decision: RightsDecision) -> None:
        with closing(self.database.connect()) as connection:
            connection.execute(
                "INSERT INTO rights_decisions "
                "(decision_id, artifact_id, status, actor, evidence_uri, "
                "policy_version, decided_at, rationale) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    decision.artifact_id,
                    decision.status,
                    decision.actor,
                    decision.evidence_uri,
                    decision.policy_version,
                    decision.decided_at.isoformat(),
                    decision.rationale,
                ),
            )
            connection.commit()

    def record_approval(self, decision: ApprovalDecision) -> None:
        episode_id = decision.target_id if decision.target_kind == "episode" else None
        artifact_id = decision.target_id if decision.target_kind == "artifact" else None
        with closing(self.database.connect()) as connection:
            connection.execute(
                "INSERT INTO approval_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    episode_id,
                    artifact_id,
                    decision.status,
                    decision.actor,
                    decision.reason,
                    decision.policy_version,
                    decision.decided_at.isoformat(),
                ),
            )
            connection.commit()

    def _eligible(self, connection: sqlite3.Connection, artifact_id: str, seen: set[str]) -> None:
        if artifact_id in seen:
            raise ValueError("artifact dependency cycle")
        seen.add(artifact_id)
        validation = self.inspect(artifact_id)
        if not validation.valid:
            raise ValueError(f"artifact file invalid: {validation.reasons}")
        approval = connection.execute(
            "SELECT status FROM approval_decisions WHERE artifact_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (artifact_id,),
        ).fetchone()
        if approval is None or approval["status"] != "approved":
            raise ValueError("artifact lacks current approval")
        rights = connection.execute(
            "SELECT status FROM rights_decisions WHERE artifact_id = ? ORDER BY rowid DESC LIMIT 1",
            (artifact_id,),
        ).fetchone()
        if rights is None:
            raise ValueError("artifact lacks rights state")
        if rights["status"] == "blocked":
            raise ValueError("artifact rights are blocked")
        dependencies = connection.execute(
            "SELECT input_artifact_id, input_sha256 FROM artifact_dependencies "
            "WHERE consumer_artifact_id = ?",
            (artifact_id,),
        ).fetchall()
        for dependency in dependencies:
            source = self._row(connection, dependency["input_artifact_id"])
            if source["sha256"] != dependency["input_sha256"]:
                raise ValueError("dependency hash differs from pinned hash")
            owner_id = source["episode_id"] or source["brand_revision_id"]
            selected = connection.execute(
                "SELECT artifact_id FROM artifact_selections WHERE "
                "owner_scope = ? AND owner_id = ? AND kind = ? AND slot_key = ?",
                (source["owner_scope"], owner_id, source["kind"], source["slot_key"]),
            ).fetchone()
            if selected is None or selected["artifact_id"] != dependency["input_artifact_id"]:
                raise ValueError("dependency is no longer selected")
            self._eligible(connection, dependency["input_artifact_id"], seen)
        seen.remove(artifact_id)

    def select(self, artifact_id: str) -> ArtifactRecord:
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._eligible(connection, artifact_id, set())
                row = self._row(connection, artifact_id)
                owner_id = row["episode_id"] or row["brand_revision_id"]
                connection.execute(
                    "INSERT INTO artifact_selections "
                    "(owner_scope, owner_id, kind, slot_key, artifact_id, selected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(owner_scope, owner_id, kind, slot_key) DO UPDATE SET "
                    "artifact_id = excluded.artifact_id, selected_at = excluded.selected_at",
                    (
                        row["owner_scope"],
                        owner_id,
                        row["kind"],
                        row["slot_key"],
                        artifact_id,
                        _now(),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(artifact_id)

    def eligibility(self, artifact_id: str) -> tuple[bool, str | None]:
        """Read-only gate check for the planner; selection rechecks it under a write lock."""
        with closing(self.database.connect()) as connection:
            try:
                self._eligible(connection, artifact_id, set())
            except (KeyError, ValueError) as exc:
                return False, str(exc)
        return True, None

    def deselect(self, artifact_id: str) -> bool:
        """Forget a selected pointer while retaining its immutable version and decisions."""
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "DELETE FROM artifact_selections WHERE artifact_id = ?", (artifact_id,)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return cursor.rowcount > 0

    def selected(
        self, owner_scope: Literal["episode", "brand"], owner_id: str, kind: str, slot_key: str
    ) -> ArtifactRecord | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT artifact_id FROM artifact_selections WHERE "
                "owner_scope = ? AND owner_id = ? AND kind = ? AND slot_key = ?",
                (owner_scope, owner_id, kind, slot_key),
            ).fetchone()
            if row is None:
                return None
            self._eligible(connection, row["artifact_id"], set())
            return self.get(row["artifact_id"])

    def quarantine_orphans(self) -> tuple[str, ...]:
        """Run at startup with registration paused; no file is silently accepted or deleted."""
        quarantined: list[str] = []
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                known = {
                    row["relative_path"]
                    for row in connection.execute("SELECT relative_path FROM artifact_versions")
                }
                for folder in (".staging", "episodes", "brand-assets"):
                    base = self.root / folder
                    if not base.exists():
                        continue
                    for directory, _, filenames in os.walk(base, followlinks=False):
                        for name in filenames:
                            path = Path(directory) / name
                            relative = path.relative_to(self.root).as_posix()
                            if relative in known:
                                continue
                            destination = self.root / "quarantine" / f"{uuid4()}-{name}"
                            os.replace(path, destination)
                            quarantined.append(relative)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return tuple(quarantined)
