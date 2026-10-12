"""Renderer V3 storyboard admission verifies compilation, media and immutable receipts."""

from contextlib import closing

from PIL import Image

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig
from tovitunes.domain.creative_drafts import parse_episode_spec, parse_lyrics, parse_music_spec
from tovitunes.domain.episode import Episode
from tovitunes.domain.production_v3 import AudioClock, TimedStoryboardV3, compile_storyboard_v3
from tovitunes.domain.visual_draft import EpisodeVisualPlanV2, validate_compiled_visual
from tovitunes.pipeline.duration_source import admit_duration_source
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.render.environment_sets import ROLES, EnvironmentSet
from tovitunes.render.visual_evidence import validate_visual_receipt


def load_v3_evidence(
    config: RuntimeConfig,
    store: AssetStore,
    episode: Episode,
    storyboard: TimedStoryboardV3,
    storyboard_id: str,
) -> AudioClock:
    def selected(kind: str, aid: str | None = None, slot: str = "main") -> str:
        record = store.selected("episode", episode.episode_id, kind, slot)
        if record is None or (aid is not None and record.identity.artifact_id != aid):
            raise ValueError(f"V3 selected {kind} identity differs")
        return record.identity.artifact_id

    master_id = selected("audio_master", storyboard.audio_master_artifact_id)
    clock_id = selected("audio_clock", storyboard.audio_clock_artifact_id)
    plan_id = selected("episode_visual_plan", storyboard.visual_plan_artifact_id)
    ids = (selected("episode_spec"), selected("lyrics"), selected("music_spec"))
    spec, lyrics, music = (
        parse_episode_spec(store.read_json(ids[0])),
        parse_lyrics(store.read_json(ids[1])),
        parse_music_spec(store.read_json(ids[2])),
    )
    clock = AudioClock.model_validate(store.read_json(clock_id))
    measured = admit_duration_source(
        config,
        episode,
        clock.source_request_id,
        creative_music_spec(episode, spec, lyrics, music, ids),
        master_id,
    )
    if clock != measured:
        raise ValueError("V3 clock differs from original source/receipt or measured QA")
    visual = EpisodeVisualPlanV2.model_validate(store.read_json(plan_id))
    validate_compiled_visual(visual, episode, ids)
    validate_visual_receipt(store, visual)
    with closing(store.database.connect()) as db:
        for consumer, required_inputs in (
            (clock_id, (master_id,)),
            (plan_id, (*ids, visual.creative_draft_artifact_id)),
            (visual.creative_draft_artifact_id, (*ids, clock_id)),
        ):
            dependencies = dict(
                db.execute(
                    "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                    "WHERE consumer_artifact_id=?",
                    (consumer,),
                ).fetchall()
            )
            if dependencies != {aid: store.get(aid).sha256 for aid in required_inputs}:
                raise ValueError("V3 creative or clock dependency set differs")
    binding_id = selected("episode_environment")
    binding = store.read_json(binding_id)
    if not isinstance(binding, dict) or (
        binding.get("visual_plan_artifact_id") != plan_id
        or binding.get("environment_set_artifact_id") != storyboard.environment_set_artifact_id
    ):
        raise ValueError("V3 environment binding differs")
    environment = EnvironmentSet.model_validate(
        store.read_json(storyboard.environment_set_artifact_id)
    )
    if (
        environment.theme != visual.world_contract
        or tuple(p.role for p in environment.plates) != ROLES
    ):
        raise ValueError("V3 environment world differs")
    for plate in environment.plates:
        record = store.get(plate.artifact_id)
        if record.sha256 != plate.sha256 or not store.inspect(plate.artifact_id).valid:
            raise ValueError("V3 environment plate SHA differs")
        with Image.open(store.path_for(plate.artifact_id)) as image:
            image.load()
            if image.size != plate.dimensions or image.format != "PNG":
                raise ValueError("V3 environment image format or dimensions differ")
        if plate.source_artifact_id is None or plate.source_sha256 is None:
            raise ValueError("V3 environment lacks provider source evidence")
        source = store.get(plate.source_artifact_id)
        with closing(store.database.connect()) as db:
            receipt = db.execute(
                "SELECT * FROM environment_requests WHERE request_id=?",
                (plate.local_request_id,),
            ).fetchone()
        if (
            source.sha256 != plate.source_sha256
            or source.identity.owner_id != episode.episode_id
            or source.identity.kind != "environment_source_plate"
            or source.identity.slot_key != plate.role
            or not store.inspect(plate.source_artifact_id).valid
            or source.provenance.local_request_id != plate.local_request_id
            or receipt is None
            or receipt["status"] != "succeeded"
            or receipt["episode_id"] != episode.episode_id
            or receipt["set_id"] != environment.set_id
            or receipt["role"] != plate.role
            or receipt["artifact_id"] != plate.artifact_id
            or receipt["response_sha256"] != source.sha256
            or receipt["provider"] != plate.provider
            or receipt["model"] != plate.model
            or receipt["provider_request_id"] != plate.provider_request_id
        ):
            raise ValueError("V3 environment provider receipt differs")
    for key, aid in storyboard.asset_artifact_ids.items():
        selected("visual_asset", aid, key)
        with Image.open(store.path_for(aid)) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGBA"
                or image.getchannel("A").getbbox() is None
            ):
                raise ValueError("V3 required prop is not a usable transparent PNG")
        with closing(store.database.connect()) as db:
            receipt = db.execute(
                "SELECT r.status,r.provider AS request_provider,r.model AS request_model,"
                "r.provider_request_id AS remote_id,p.*,v.sha256 AS source_sha "
                "FROM generation_requests r "
                "JOIN production_image_receipts p ON p.request_id=r.request_id "
                "JOIN artifact_versions v ON v.artifact_id=p.source_artifact_id "
                "WHERE r.episode_id=? AND r.kind='visual_asset' AND r.slot_key=? "
                "AND p.normalized_artifact_id=?",
                (episode.episode_id, key, aid),
            ).fetchone()
        if (
            receipt is None
            or receipt["status"] != "succeeded"
            or receipt["response_sha256"] != receipt["source_sha"]
        ):
            raise ValueError("V3 required image receipt is missing or mismatched")
        source = store.get(receipt["source_artifact_id"])
        if (
            not store.inspect(source.identity.artifact_id).valid
            or source.identity.owner_id != episode.episode_id
            or source.identity.kind != "visual_asset_source"
            or source.identity.slot_key != key
            or source.provenance.local_request_id != receipt["request_id"]
            or source.provenance.provider != receipt["request_provider"]
            or source.provenance.model != receipt["request_model"]
            or source.provenance.request_id != receipt["provider_request_id"]
            or receipt["remote_id"] != receipt["provider_request_id"]
        ):
            raise ValueError("V3 required image source is missing or corrupt")
        with closing(store.database.connect()) as db:
            dependencies = dict(
                db.execute(
                    "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                    "WHERE consumer_artifact_id=?",
                    (aid,),
                ).fetchall()
            )
        if dependencies != {
            plan_id: store.get(plan_id).sha256,
            source.identity.artifact_id: source.sha256,
        }:
            raise ValueError("V3 required image dependencies differ")
    expected = compile_storyboard_v3(
        episode,
        visual,
        plan_id,
        store.get(plan_id).sha256,
        clock,
        clock_id,
        storyboard.asset_artifact_ids,
        storyboard.environment_set_artifact_id,
    )
    if expected != storyboard:
        raise ValueError("V3 storyboard differs from deterministic compilation")
    required = (
        master_id,
        clock_id,
        plan_id,
        *storyboard.asset_artifact_ids.values(),
        binding_id,
        storyboard.environment_set_artifact_id,
    )
    with closing(store.database.connect()) as db:
        deps = dict(
            db.execute(
                "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                "WHERE consumer_artifact_id=?",
                (storyboard_id,),
            ).fetchall()
        )
    if deps != {aid: store.get(aid).sha256 for aid in required}:
        raise ValueError("V3 storyboard dependency set or hashes differ")
    return clock
