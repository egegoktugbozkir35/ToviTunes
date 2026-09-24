"""Read-only readiness check for a character pack's pinned brand artifacts."""

from contextlib import closing
from dataclasses import dataclass

from tovitunes.artifacts.store import AssetStore
from tovitunes.domain.character import CharacterAssetPack


@dataclass(frozen=True)
class PackReadiness:
    ready: bool
    issues: tuple[str, ...]
    checked_artifact_ids: tuple[str, ...]


def assess_pack_assets(
    pack: CharacterAssetPack, store: AssetStore, brand_revision_id: str
) -> PackReadiness:
    """Check selection, file validity, review, rights and ownership without mutation."""
    issues: list[str] = []
    if pack.readiness != "approved":
        issues.append("pack manifest remains draft")
    references = dict(pack.asset_artifact_ids)
    if pack.rig_data:
        references["rig"] = pack.rig_data
    checked: list[str] = []
    for role, artifact_id in sorted(references.items()):
        checked.append(artifact_id)
        expected_kind = (
            "character_reference"
            if role.startswith("view/")
            else "character_rig"
            if role == "rig"
            else "character_sprite"
        )
        try:
            record = store.get(artifact_id)
        except KeyError:
            issues.append(f"{role}: artifact is not registered")
            continue
        identity = record.identity
        if (
            identity.owner_scope != "brand"
            or identity.owner_id != brand_revision_id
            or identity.kind != expected_kind
        ):
            issues.append(f"{role}: artifact owner or kind differs from the pack contract")
            continue
        expected_mimes = (
            {"image/png", "image/jpeg", "image/webp"}
            if role.startswith("view/")
            else {"application/json"}
            if role == "rig"
            else {"image/png"}
        )
        if record.mime_type not in expected_mimes:
            issues.append(f"{role}: unsupported media type")
        eligible, problem = store.eligibility(artifact_id)
        if not eligible:
            issues.append(f"{role}: {problem}")
        else:
            try:
                current = store.selected(
                    "brand", brand_revision_id, identity.kind, identity.slot_key
                )
            except ValueError as exc:
                issues.append(f"{role}: {exc}")
            else:
                if current is None or current.identity.artifact_id != artifact_id:
                    issues.append(f"{role}: artifact is not the selected brand version")
        with closing(store.database.connect()) as connection:
            rights = connection.execute(
                "SELECT status, evidence_uri FROM rights_decisions WHERE artifact_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (artifact_id,),
            ).fetchone()
        if (
            rights is None
            or rights["status"] != "commercial_use_confirmed"
            or not rights["evidence_uri"]
        ):
            issues.append(f"{role}: commercial-use rights lack evidence")
    if pack.readiness == "approved" and not references:
        issues.append("approved pack has no registered assets")
    return PackReadiness(not issues, tuple(issues), tuple(checked))

