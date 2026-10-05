"""Commercial-rights applicability and dependency inheritance policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tovitunes.artifacts.store import ArtifactRecord

RIGHTS_APPLICABILITY_POLICY = "tovitunes_rights_inheritance_v1"

# These artifacts introduce creative content that is itself used in the published work.
# The source must be provider- or operator-supplied; deterministic versions inherit.
_DIRECT_CREATIVE_KINDS = frozenset(
    {
        "audio_master",
        "character_reference",
        "environment_source_plate",
        "lesson_object_source",
        "lyrics",
        "publication_metadata",
    }
)


def is_direct_rights_root(record: ArtifactRecord) -> bool:
    """Return whether independent external/source rights enter at this artifact."""
    if record.identity.kind == "publication_metadata":
        # Operator-written copy is reviewed and SHA-pinned, but has no external
        # provider licence to clear. Provider output remains a direct root.
        return record.provenance.source_kind == "provider"
    return record.identity.kind in _DIRECT_CREATIVE_KINDS and record.provenance.source_kind in {
        "provider",
        "manual",
    }


@dataclass(frozen=True)
class InheritedRights:
    direct_roots: tuple[str, ...]
    blocked_artifacts: tuple[str, ...]
    dependency_graph_valid: bool
    commercially_cleared: bool


def evaluate_inherited_rights(
    artifact_id: str,
    *,
    records: Mapping[str, ArtifactRecord],
    dependencies: Mapping[str, Sequence[str]],
    latest_rights: Mapping[str, str | None],
    local_integrity: Mapping[str, bool],
) -> InheritedRights:
    """Resolve direct roots, explicit blocks, and integrity over one ancestor closure."""
    seen: set[str] = set()
    roots: set[str] = set()
    blocked: set[str] = set()
    valid = True

    def walk(current: str) -> None:
        nonlocal valid
        if current in seen:
            return
        seen.add(current)
        record = records.get(current)
        if record is None or not local_integrity.get(current, False):
            valid = False
            return
        if latest_rights.get(current) == "blocked":
            blocked.add(current)
        if is_direct_rights_root(record):
            roots.add(current)
        for dependency in dependencies.get(current, ()):
            walk(dependency)

    walk(artifact_id)
    cleared = (
        valid
        and not blocked
        and all(latest_rights.get(root) == "commercial_use_confirmed" for root in roots)
    )
    return InheritedRights(tuple(sorted(roots)), tuple(sorted(blocked)), valid, cleared)
