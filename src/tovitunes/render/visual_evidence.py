"""Admission of immutable model evidence and compiled scene assets."""

import json
from contextlib import closing
from hashlib import sha256

from tovitunes.artifacts.store import AssetStore
from tovitunes.domain.visual_draft import CreativeVisualDraft, EpisodeVisualPlanV2
from tovitunes.render.environment_sets import (
    ENVIRONMENT_ALPHA_V2,
    EPISODE_PROMPT_VERSION,
    ROLES,
    EnvironmentSet,
    _validate_image,
)


def validate_environment_assets(
    store: AssetStore, manifest_id: str, episode_id: str, *, shared: bool,
    role_briefs: dict[str, str] | None = None,
) -> EnvironmentSet:
    """Verify role/source identity before selection, without re-normalizing old art."""
    manifest = store.get(manifest_id)
    owner = manifest.identity
    if owner.kind != "environment_set" or (
        owner.owner_scope != "brand" if shared else
        owner.owner_scope != "episode" or owner.owner_id != episode_id
    ):
        raise ValueError("environment manifest belongs to a different owner or artifact kind")
    environment = EnvironmentSet.model_validate(store.read_json(manifest_id))
    if tuple(p.role for p in environment.plates) != ROLES:
        raise ValueError("environment manifest must contain each original role exactly once")
    with closing(store.database.connect()) as db:
        for plate in environment.plates:
            if environment.prompt_version == EPISODE_PROMPT_VERSION and (
                plate.source_artifact_id is None or plate.source_dimensions is None
            ):
                raise ValueError(f"environment role {plate.role} lacks its scene-v3 raw source")
            record = store.get(plate.artifact_id)
            if (
                record.identity.owner_scope != owner.owner_scope
                or record.identity.owner_id != owner.owner_id
                or record.identity.kind != "environment_plate"
                or record.identity.slot_key != plate.role
                or record.sha256 != plate.sha256
                or not store.inspect(plate.artifact_id).valid
            ):
                raise ValueError(
                    f"environment role {plate.role} has mismatched plate identity/bytes"
                )
            edge = db.execute(
                "SELECT 1 FROM artifact_dependencies WHERE consumer_artifact_id=? "
                "AND input_artifact_id=? AND input_sha256=?",
                (manifest_id, plate.artifact_id, plate.sha256),
            ).fetchone()
            if edge is None:
                raise ValueError(f"environment role {plate.role} is not bound to its manifest")
            if not shared and role_briefs is not None:
                request = db.execute(
                    "SELECT episode_id,set_id,role,spec_json FROM environment_requests "
                    "WHERE request_id=?", (plate.local_request_id,),
                ).fetchone()
                if (
                    request is None or request["episode_id"] != episode_id
                    or request["set_id"] != environment.set_id or request["role"] != plate.role
                    or json.loads(request["spec_json"]).get("scene_brief")
                    != role_briefs[plate.role]
                ):
                    raise ValueError(
                        f"environment role {plate.role} differs from its original planned setting"
                    )
            # Schema-one historical sets may have no retained raw source. Their
            # admitted plate remains authoritative; do not fabricate a source.
            if plate.source_artifact_id is not None:
                source = store.get(plate.source_artifact_id)
                if (
                    source.identity.owner_scope != owner.owner_scope
                    or source.identity.owner_id != owner.owner_id
                    or source.identity.kind != "environment_source_plate"
                    or source.identity.slot_key != plate.role
                    or source.sha256 != plate.source_sha256
                    or source.provenance.local_request_id != plate.local_request_id
                    or record.provenance.local_request_id != plate.local_request_id
                    or not store.inspect(plate.source_artifact_id).valid
                    or db.execute(
                        "SELECT 1 FROM artifact_dependencies WHERE consumer_artifact_id=? "
                        "AND input_artifact_id=? AND input_sha256=?",
                        (plate.artifact_id, plate.source_artifact_id, source.sha256),
                    ).fetchone() is None
                ):
                    raise ValueError(f"environment role {plate.role} has mismatched source binding")
                if environment.prompt_version == EPISODE_PROMPT_VERSION:
                    # Explicit recovery can retain a derivative produced under an
                    # older contract. Recheck the new source contract at admission;
                    # do not let an older normalizer hide a local alpha hole.
                    # This creates no artifact and never processes historical V1/V2 sets.
                    _validate_image(
                        store.path_for(plate.source_artifact_id).read_bytes(),
                        plate.source_dimensions,
                        allow_near_opaque_alpha=True,
                        normalization_version=ENVIRONMENT_ALPHA_V2,
                    )
    return environment


def validate_visual_receipt(store: AssetStore, plan: EpisodeVisualPlanV2) -> None:
    raw = store.get(plan.creative_draft_artifact_id)
    current = store.selected("episode", plan.episode_id, "creative_visual_draft", "main")
    if (
        current is None
        or current.identity.artifact_id != raw.identity.artifact_id
        or raw.identity.owner_id != plan.episode_id
        or raw.identity.owner_scope != "episode"
        or raw.identity.kind != "creative_visual_draft"
        or raw.provenance.source_kind != "provider"
        or raw.provenance.prompt_version != plan.prompt_version
        or raw.sha256 != plan.creative_draft_sha256
        or not store.inspect(raw.identity.artifact_id).valid
        or CreativeVisualDraft.model_validate(store.read_json(raw.identity.artifact_id))
        != plan.proposal
    ):
        raise ValueError("visual draft identity, bytes or selected proposal differs")
    with closing(store.database.connect()) as db:
        receipt = db.execute(
            "SELECT * FROM generation_requests WHERE request_id=?",
            (raw.provenance.local_request_id,),
        ).fetchone()
    if (
        receipt is None
        or receipt["status"] != "succeeded"
        or receipt["episode_id"] != plan.episode_id
        or receipt["prompt_version"] != plan.prompt_version
        or receipt["kind"] not in {"creative_visual_draft", "creative_visual_draft_repair"}
        or receipt["provider"] != raw.provenance.provider
        or receipt["model"] != raw.provenance.model
        or receipt["provider_request_id"] != raw.provenance.request_id
        or receipt["response_content"] is None
        or sha256(receipt["response_content"].encode()).hexdigest() != receipt["response_sha256"]
        or CreativeVisualDraft.model_validate(json.loads(receipt["response_content"]))
        != plan.proposal
    ):
        raise ValueError("visual model response evidence is missing, corrupt or mismatched")
