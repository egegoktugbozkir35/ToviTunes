"""Admission of immutable model evidence and compiled scene assets."""

import json
from contextlib import closing
from hashlib import sha256

from tovitunes.artifacts.store import AssetStore
from tovitunes.domain.visual_draft import CreativeVisualDraft, EpisodeVisualPlanV2


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
