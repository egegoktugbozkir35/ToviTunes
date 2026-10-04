"""One narrow production command, using selected immutable artifacts exclusively."""

import json
import os
import shutil
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from tovitunes.artifacts.store import ArtifactRecord, AssetStore, InputDependency
from tovitunes.catalog import load_brand
from tovitunes.config import RuntimeConfig
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.character import CharacterAssetPack
from tovitunes.domain.review import ApprovalDecision
from tovitunes.domain.storyboard import AudioAlignment, BeatAnalysis, TimedStoryboard, beat_range
from tovitunes.persistence.db import Database
from tovitunes.persistence.leases import Lease, LeaseStore
from tovitunes.render import VERSION, VISUAL_STORY_VERSION
from tovitunes.render.character import (
    ACTION_ROLES,
    animation_plan,
    attach_poses,
    pose_keyframe,
    position,
    validate_layout,
)
from tovitunes.render.composition import (
    LEGACY_PROP_STYLE_VERSION,
    LESSON_OBJECT_STYLE_VERSION,
    PROP_DEFINITIONS,
    CompositionRequest,
    SceneComposition,
    resolve_composition,
    validate_composition,
)
from tovitunes.render.ffmpeg import (
    ENCODE_TIMEOUT,
    MUX_TIMEOUT,
    doctor,
    mux_command,
    probe,
    run_process,
)
from tovitunes.render.models import RenderManifest, SceneRender
from tovitunes.render.motion import SceneMotionPlan, plan_motion, prop_state, validate_motion
from tovitunes.render.props import png_info, scene_art, validate_props
from tovitunes.render.qa import media_qa
from tovitunes.render.story import (
    EpisodeVisualStoryPlan,
    VisualStoryPlan,
    occupancy,
    plan_story,
    stage_composition,
)

V4_VERSION = VISUAL_STORY_VERSION


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class RenderInputs:
    store: AssetStore
    storyboard_record: ArtifactRecord
    storyboard: TimedStoryboard
    beats: BeatAnalysis
    pack: CharacterAssetPack
    alignment: AudioAlignment
    target_vocabulary: tuple[str, ...]


def load_inputs(config: RuntimeConfig, episode_key: str) -> RenderInputs:
    if not config.database_path.is_file() or not config.data_root.is_dir():
        raise ValueError("production AssetStore/database unavailable")
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    with closing(store.database.connect()) as db:
        row = db.execute(
            "SELECT episode_id FROM episodes WHERE external_key=?", (episode_key,)
        ).fetchone()
    if row is None:
        raise ValueError("selected production episode unavailable; prepare storyboard first")
    episode = store.database.get_episode(row[0])
    record = store.selected("episode", episode.episode_id, "timed_storyboard", "main")
    if record is None:
        raise ValueError("production TimedStoryboard is not selected")
    storyboard = TimedStoryboard.model_validate(store.read_json(record.identity.artifact_id))
    if (
        storyboard.episode_id != episode.episode_id
        or episode.character_packs != (storyboard.character_pack,)
        or storyboard.concept_id != episode.concept_id
        or storyboard.objective_id != episode.objective_id
    ):
        raise ValueError("storyboard differs from pinned episode")
    pins = {
        "audio_master": storyboard.audio_master_artifact_id,
        "audio_alignment": storyboard.audio_alignment_artifact_id,
        "beat_analysis": storyboard.beat_analysis_artifact_id,
    }
    for kind, artifact_id in pins.items():
        selected = store.selected("episode", episode.episode_id, kind, "main")
        if selected is None or selected.identity.artifact_id != artifact_id:
            raise ValueError(f"stale storyboard {kind} reference")
    audio = store.get(storyboard.audio_master_artifact_id)
    alignment = AudioAlignment.model_validate(
        store.read_json(storyboard.audio_alignment_artifact_id)
    )
    beats = BeatAnalysis.model_validate(store.read_json(storyboard.beat_analysis_artifact_id))
    for evidence in (alignment, beats):
        if (
            evidence.audio_master_artifact_id != audio.identity.artifact_id
            or evidence.audio_sha256 != audio.sha256
            or evidence.duration_seconds != storyboard.duration_seconds
        ):
            raise ValueError("selected timing/beat evidence differs from audio")
    if audio.sha256 != storyboard.audio_sha256:
        raise ValueError("storyboard audio SHA mismatch")
    audio_duration = float(probe(store.path_for(audio.identity.artifact_id))["format"]["duration"])
    if abs(audio_duration - storyboard.duration_seconds) > 0.005:
        raise ValueError("source audio duration differs from storyboard")
    lyric_scenes = tuple(s for s in storyboard.scenes if s.kind == "lyric")
    if tuple((s.lyric_text, s.lyric_start, s.lyric_end) for s in lyric_scenes) != tuple(
        (line.text, line.start, line.end) for line in alignment.lyric_lines
    ):
        raise ValueError("storyboard lyrics differ from selected alignment")
    for scene in storyboard.scenes:
        if (
            scene.tovi_action not in ACTION_ROLES
            or scene.beat_index_range
            != beat_range(beats.beat_seconds, scene.start, scene.end, storyboard.duration_seconds)
            or scene.downbeat_index_range
            != beat_range(
                beats.downbeat_seconds, scene.start, scene.end, storyboard.duration_seconds
            )
        ):
            raise ValueError("unsupported action or stale beat slices")
    catalog = load_brand(config.brand_root)
    if catalog.version.revision_id != episode.brand_revision_id:
        raise ValueError("current brand differs from pinned episode")
    pack = next(
        (
            p
            for p, revision in zip(catalog.packs, catalog.pack_revisions)
            if revision.revision_id == storyboard.character_pack.revision_id
        ),
        None,
    )
    if pack is None or pack.readiness != "approved" or pack.pack_id != "tovi-pack-v1":
        raise ValueError("selected approved tovi-pack-v1 is unavailable")
    for role in set(ACTION_ROLES.values()):
        if role not in pack.asset_artifact_ids:
            raise ValueError(f"missing approved full-body sprite: {role}")
        sprite = store.get(pack.asset_artifact_ids[role])
        selected = store.selected(
            sprite.identity.owner_scope,
            sprite.identity.owner_id,
            sprite.identity.kind,
            sprite.identity.slot_key,
        )
        if selected is None or selected.identity != sprite.identity:
            raise ValueError("approved sprite is no longer selected")
    return RenderInputs(
        store, record, storyboard, beats, pack, alignment, episode.target_vocabulary
    )


def validate_manifest(
    store: AssetStore, manifest: RenderManifest, storyboard: TimedStoryboard
) -> None:
    expected = tuple((s.scene_id, s.start, s.end) for s in storyboard.scenes)
    if (
        tuple((s.scene_id, s.start, s.end) for s in manifest.scenes) != expected
        or manifest.audio_master_artifact_id != storyboard.audio_master_artifact_id
        or manifest.audio_alignment_artifact_id != storyboard.audio_alignment_artifact_id
        or manifest.beat_analysis_artifact_id != storyboard.beat_analysis_artifact_id
        or manifest.character_pack_revision != storyboard.character_pack.revision_id
        or manifest.duration_seconds != storyboard.duration_seconds
    ):
        raise ValueError("manifest differs from selected storyboard")
    for artifact_id, digest in manifest.dependency_sha256.items():
        record = store.get(artifact_id)
        selected = store.selected(
            record.identity.owner_scope,
            record.identity.owner_id,
            record.identity.kind,
            record.identity.slot_key,
        )
        if selected is None or selected.identity != record.identity or record.sha256 != digest:
            raise ValueError("manifest has stale dependency")


class ProductionRenderer:
    def __init__(self, config: RuntimeConfig, *, canvas: tuple[int, int] = (1080, 1920)) -> None:
        self.config, self.canvas = config, canvas

    def render(self, episode_key: str, *, visual_story: bool = False) -> dict[str, Any]:
        load_inputs(self.config, episode_key)  # Complete preflight before artifact writes.
        environment_selection = None
        if visual_story:
            from tovitunes.render.environment_sets import selected_set

            environment_selection = selected_set(self.config)
        try:
            moviepy_version = version("moviepy")
        except PackageNotFoundError:
            raise ValueError("install the video-render extra (moviepy==2.2.1)") from None
        if moviepy_version != "2.2.1":
            raise ValueError("Production V1 requires moviepy==2.2.1")
        binaries = doctor()
        database = Database(self.config.database_path)
        leases = LeaseStore(database)
        lease = leases.acquire(
            f"production-render:{episode_key}", duration_seconds=ENCODE_TIMEOUT + 600
        )
        try:
            inputs = load_inputs(self.config, episode_key)
            return self._render(inputs, binaries, leases, lease, environment_selection)
        finally:
            leases.release(lease)

    def _render(
        self,
        inputs: RenderInputs,
        binaries: dict[str, str],
        leases: LeaseStore,
        lease: Lease,
        environment_selection: tuple[Any, str] | None = None,
    ) -> dict[str, Any]:
        storyboard = inputs.storyboard
        owner = storyboard.episode_id
        root = self.config.data_root / ".render-working"
        root.mkdir(exist_ok=True)
        actions: dict[str, str] = {}
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            stage = Path(temporary)
            store = AssetStore(
                self.config.data_root, inputs.store.database, generated_source_roots=[stage]
            )
            lesson_asset_paths: dict[str, Path] = {}
            lesson_asset_metadata: dict[str, dict[str, str]] = {}
            lesson_asset_ids: tuple[str, ...] = ()
            prop_style_version = LEGACY_PROP_STYLE_VERSION
            brand_id = inputs.store.database.get_episode(owner).brand_revision_id
            lesson_manifest = store.selected(
                "brand", brand_id, "lesson_object_manifest", LESSON_OBJECT_STYLE_VERSION
            )
            if lesson_manifest is not None:
                from tovitunes.render.lesson_objects import resolve_reviewed_assets

                lesson_asset_paths = resolve_reviewed_assets(store, brand_id)
                resolved_ids = []
                for key in sorted(lesson_asset_paths):
                    selected = store.selected("brand", brand_id, "lesson_object", key)
                    if selected is None:
                        raise ValueError(f"selected lesson-object disappeared: {key}")
                    resolved_ids.append(selected.identity.artifact_id)
                    lesson_asset_metadata[key] = {
                        "asset_artifact_id": selected.identity.artifact_id,
                        "asset_sha256": selected.sha256,
                        "anchor": "bottom_center",
                    }
                lesson_asset_ids = (lesson_manifest.identity.artifact_id, *resolved_ids)
                prop_style_version = LESSON_OBJECT_STYLE_VERSION

            def ensure(kind: str, slot: str, path: Path, deps: tuple[str, ...]) -> ArtifactRecord:
                leases.assert_owner(lease)
                if environment_selection is not None:
                    slot += "_v4"
                digest = sha256(path.read_bytes()).hexdigest()
                record = store.find_version("episode", owner, kind, slot, digest)
                actions[f"{kind}:{slot}"] = "reuse" if record else "create"
                if record:
                    self._check_dependencies(store, record, deps)
                else:
                    record = store.ingest(
                        path,
                        owner_scope="episode",
                        owner_id=owner,
                        kind=kind,
                        slot_key=slot,
                        provenance=Provenance(
                            source_kind="deterministic",
                            acquired_at=datetime.now(UTC),
                            provider="tovitunes.render",
                            model=V4_VERSION if environment_selection else VERSION,
                            input_artifact_ids=deps,
                        ),
                        dependencies=[InputDependency(d, "render_input") for d in deps],
                    )
                self._approve_select(store, record)
                return record

            sid = inputs.storyboard_record.identity.artifact_id
            scene_refs: list[SceneRender] = []
            worker_scenes: list[dict[str, Any]] = []
            deterministic: list[dict[str, Any]] = []
            previous: SceneComposition | None = None
            previous_motion: SceneMotionPlan | None = None
            previous_motion_id: str | None = None
            last_lyric_end = max(s.lyric_end for s in storyboard.scenes if s.lyric_end is not None)
            tail_seconds = storyboard.duration_seconds - last_lyric_end
            prepared: dict[str, tuple[SceneComposition, VisualStoryPlan]] = {}
            story_record: ArtifactRecord | None = None
            continuity_positions: dict[str, tuple[float, float]] = {}
            if environment_selection is not None:
                environment, environment_id = environment_selection
                prior: SceneComposition | None = None
                introduced: set[str] = set()
                for item in storyboard.scenes:
                    composition_request_pre = CompositionRequest(
                        item.scene_id,
                        item.start,
                        item.end,
                        item.tovi_action,
                        item.required_props,
                        item.kind,
                    )
                    raw = resolve_composition(
                        composition_request_pre,
                        prior,
                        post_lyric_tail_seconds=tail_seconds if item.kind == "outro" else 0,
                        measured_downbeats=inputs.beats.downbeat_seconds,
                    )
                    validate_composition(composition_request_pre, raw, prior)
                    word_cue = next(
                        (
                            word.start - item.start
                            for word in inputs.alignment.words
                            if item.start <= word.start < item.end
                            and word.text.strip(".,!? ").casefold()
                            in {v.casefold() for v in inputs.target_vocabulary}
                        ),
                        None,
                    )
                    planned_story = plan_story(
                        raw,
                        item.end - item.start,
                        PROP_DEFINITIONS,
                        introduced,
                        word_cue=word_cue,
                        scene_kind=item.kind,
                    )
                    staged = stage_composition(raw, planned_story)
                    prepared[item.scene_id] = (staged, planned_story)
                    prior = staged
                    introduced.update(p.type for p in staged.props)
                episode_story = EpisodeVisualStoryPlan(
                    storyboard_artifact_id=sid,
                    environment_set_artifact_id=environment_id,
                    scenes=tuple(value[1] for value in prepared.values()),
                )
                story_path = stage / "visual_story_plan.json"
                story_path.write_bytes(canonical(episode_story.model_dump(mode="json")))
                story_deps = tuple(
                    dict.fromkeys(
                        (
                            sid,
                            environment_id,
                            storyboard.beat_analysis_artifact_id,
                            storyboard.audio_alignment_artifact_id,
                            *inputs.pack.asset_artifact_ids.values(),
                        )
                    )
                )
                story_record = ensure("visual_story_plan", "main", story_path, story_deps)
            for scene in storyboard.scenes:
                story: VisualStoryPlan | None
                composition_request = CompositionRequest(
                    scene.scene_id,
                    scene.start,
                    scene.end,
                    scene.tovi_action,
                    scene.required_props,
                    scene.kind,
                )
                if environment_selection is None:
                    composition = resolve_composition(
                        composition_request,
                        previous,
                        post_lyric_tail_seconds=tail_seconds if scene.kind == "outro" else 0,
                        measured_downbeats=inputs.beats.downbeat_seconds,
                    )
                    validate_composition(composition_request, composition, previous)
                    story = None
                else:
                    composition, story = prepared[scene.scene_id]
                motion = plan_motion(
                    composition,
                    scene.start,
                    scene.end - scene.start,
                    sid,
                    inputs.beats,
                    inputs.alignment,
                    inputs.target_vocabulary,
                    previous=previous_motion,
                    previous_artifact_id=previous_motion_id,
                    canvas=self.canvas,
                    story=story,
                    continuity_positions=continuity_positions,
                )
                if story_record is not None:
                    motion = motion.model_copy(
                        update={"visual_story_plan_artifact_id": story_record.identity.artifact_id}
                    )
                image, metadata = scene_art(
                    scene,
                    inputs.pack.palette,
                    self.canvas,
                    sid,
                    composition,
                    background_only=True,
                    background_variant=motion.background_variant,
                    lesson_asset_paths=lesson_asset_paths,
                    lesson_asset_metadata=lesson_asset_metadata,
                    prop_style_version=prop_style_version,
                )
                background_deps: tuple[str, ...] = (sid, *lesson_asset_ids)
                if environment_selection is not None and story is not None:
                    plate = environment.plate(story.environment_plate_role)
                    with Image.open(store.path_for(plate.artifact_id)) as selected_plate:
                        if selected_plate.size != plate.dimensions:
                            raise ValueError("selected environment plate dimensions differ")
                        image = selected_plate.convert("RGB").resize(
                            self.canvas, Image.Resampling.LANCZOS
                        )
                    metadata["environment_set_artifact_id"] = environment_id
                    metadata["environment_plate_artifact_id"] = plate.artifact_id
                    metadata["environment_plate_role"] = story.environment_plate_role
                    metadata["environmental_theme"] = environment.theme
                    metadata["environment_layers"] = ["selected_provider_plate"]
                    metadata["renderer_version"] = V4_VERSION
                    background_deps = (
                        sid,
                        environment_id,
                        plate.artifact_id,
                        *lesson_asset_ids,
                    )
                png = stage / f"{scene.scene_id}.png"
                image.save(png, pnginfo=png_info(metadata))
                background = ensure("scene_image", scene.scene_id, png, background_deps)
                role = composition.sprite_role
                sprite_id = inputs.pack.asset_artifact_ids[role]
                sprite_path = store.path_for(sprite_id)
                with Image.open(sprite_path) as sprite:
                    animation = animation_plan(
                        scene,
                        sprite,
                        sprite_id,
                        inputs.beats,
                        self.canvas,
                        sid,
                        composition,
                        height_limit=0.48 if environment_selection else 0.38,
                    )
                if environment_selection:
                    animation = animation.model_copy(update={"renderer_version": V4_VERSION})
                validate_layout(animation, self.canvas)
                poses = []
                sprite_paths = {}
                for cue in motion.character_pose_sequence:
                    pose_id = inputs.pack.asset_artifact_ids[cue.sprite_role]
                    pose_path = store.path_for(pose_id)
                    with Image.open(pose_path) as pose_sprite:
                        poses.append(pose_keyframe(cue, pose_sprite, pose_id, animation.size[1]))
                    sprite_paths[pose_id] = str(pose_path)
                animation = attach_poses(animation, tuple(poses), self.canvas)
                motion_qa = validate_motion(
                    motion, inputs.alignment, inputs.target_vocabulary, animation, self.canvas
                )
                motion_path = stage / f"{scene.scene_id}.motion.json"
                motion_path.write_bytes(canonical(motion.model_dump(mode="json")))
                motion_deps = tuple(
                    dict.fromkeys(
                        (
                            sid,
                            storyboard.beat_analysis_artifact_id,
                            storyboard.audio_alignment_artifact_id,
                            background.identity.artifact_id,
                            *((story_record.identity.artifact_id,) if story_record else ()),
                            *sprite_paths,
                            *((previous_motion_id,) if motion.inherited_motion_artifact_id else ()),
                        )
                    )
                )
                motion_record = ensure("scene_motion", scene.scene_id, motion_path, motion_deps)
                animation = animation.model_copy(
                    update={
                        "scene_motion_artifact_id": motion_record.identity.artifact_id,
                    }
                )
                plan_path = stage / f"{scene.scene_id}.json"
                plan_path.write_bytes(canonical(animation.model_dump(mode="json")))
                plan_record = ensure(
                    "character_animation",
                    scene.scene_id,
                    plan_path,
                    tuple(
                        dict.fromkeys(
                            (
                                sid,
                                storyboard.beat_analysis_artifact_id,
                                sprite_id,
                                *sprite_paths,
                                motion_record.identity.artifact_id,
                            )
                        )
                    ),
                )
                # QA inspects the stored image metadata, not just the generator's temporary output.
                with Image.open(store.path_for(background.identity.artifact_id)) as stored:
                    stored_metadata = json.loads(stored.info["tovitunes_composition"])
                    if stored.size != self.canvas or stored_metadata != metadata:
                        raise ValueError("scene image metadata mismatch")
                validate_props(scene, metadata, animation, self.canvas)
                if animation.long_scene_activity:
                    samples = {
                        position(animation, (scene.end - scene.start) * i / 60) for i in range(61)
                    }
                    if len(samples) < 2:
                        raise ValueError("long character scene has no movement")
                story_diagnostics: dict[str, Any] = {}
                if story is not None:
                    w, h = self.canvas
                    x, y = animation.end_position
                    character_box = (
                        x / w,
                        y / h,
                        (x + animation.size[0]) / w,
                        (y + animation.size[1]) / h,
                    )
                    primary_track = next((p for p in motion.prop_tracks if p.primary), None)
                    action_box: tuple[float, float, float, float] | None = None
                    if primary_track is not None:
                        action_samples: list[tuple[float, float, float, float]] = []
                        for index in range(31):
                            t = motion.duration * index / 30
                            px, bottom, scale, _ = prop_state(primary_track, motion, t)
                            width = (primary_track.bbox[2] - primary_track.bbox[0]) * scale
                            height = (primary_track.bbox[3] - primary_track.bbox[1]) * scale
                            box = (px - width / 2, bottom - height, px + width / 2, bottom)
                            if box[2] > 0 and box[0] < 1 and box[3] > 0 and box[1] < 1:
                                action_samples.append(
                                    (
                                        max(0.0, box[0]),
                                        max(0.0, box[1]),
                                        min(1.0, box[2]),
                                        min(1.0, box[3]),
                                    )
                                )
                        if action_samples:
                            action_box = (
                                min(b[0] for b in action_samples),
                                min(b[1] for b in action_samples),
                                max(b[2] for b in action_samples),
                                max(b[3] for b in action_samples),
                            )
                    story_diagnostics = {
                        "story_action": story.story_action,
                        "environment_role": story.environment_plate_role,
                        "primary_focus": story.primary_focus,
                        **occupancy(
                            character_box, tuple(p.bbox for p in motion.prop_tracks), action_box
                        ),
                        "primary_visual_action_bbox": action_box,
                        "meaningful_event_count": sum(len(p.events) for p in motion.prop_tracks),
                        "camera_behavior": motion.camera_track.behavior,
                        "prop_actions": {
                            p.prop_key: [e.motion for e in p.events] for p in motion.prop_tracks
                        },
                        "pose_changes": max(0, len(motion.character_pose_sequence) - 1),
                        "keyword_events": len(motion.keyword_emphasis_events),
                    }
                deterministic.append(
                    {
                        "scene_id": scene.scene_id,
                        "props": metadata["props"],
                        "character_visible_alpha_pixels": animation.visible_alpha_pixels,
                        "character_resting_bbox": [
                            *animation.end_position,
                            animation.end_position[0] + animation.size[0],
                            animation.end_position[1] + animation.size[1],
                        ],
                        "character_layout_passed": True,
                        "composition": composition.model_dump(mode="json"),
                        "composition_qa_passed": True,
                        "outro_phase_count": len(composition.outro_phases),
                        "long_scene_activity": animation.long_scene_activity,
                        "motion_diagnostics": motion_qa,
                        **({"visual_story_diagnostics": story_diagnostics} if story else {}),
                    }
                )
                scene_refs.append(
                    SceneRender(
                        scene_id=scene.scene_id,
                        start=scene.start,
                        end=scene.end,
                        scene_image_artifact_id=background.identity.artifact_id,
                        character_animation_artifact_id=plan_record.identity.artifact_id,
                        scene_motion_artifact_id=motion_record.identity.artifact_id,
                    )
                )
                worker_scenes.append(
                    {
                        "start": scene.start,
                        "end": scene.end,
                        "canvas": self.canvas,
                        "background_path": str(store.path_for(background.identity.artifact_id)),
                        "sprite_path": str(sprite_path),
                        "animation": animation.model_dump(mode="json"),
                        "metadata": metadata,
                        "motion": motion.model_dump(mode="json"),
                        "sprite_paths": sprite_paths,
                        "lesson_asset_paths": {
                            key: str(path) for key, path in lesson_asset_paths.items()
                        },
                        "prop_style_version": prop_style_version,
                    }
                )
                previous = composition
                previous_motion = motion
                previous_motion_id = motion_record.identity.artifact_id
                if story is not None:
                    continuity_positions.update(
                        {
                            p.prop_key: ((p.bbox[0] + p.bbox[2]) / 2, p.bbox[3])
                            for p in motion.prop_tracks
                        }
                    )
            deps = tuple(
                dict.fromkeys(
                    (
                        sid,
                        storyboard.audio_master_artifact_id,
                        storyboard.audio_alignment_artifact_id,
                        storyboard.beat_analysis_artifact_id,
                        *((environment_id,) if environment_selection else ()),
                        *((story_record.identity.artifact_id,) if story_record else ()),
                        *lesson_asset_ids,
                        *(s.scene_image_artifact_id for s in scene_refs),
                        *(s.character_animation_artifact_id for s in scene_refs),
                        *(
                            s.scene_motion_artifact_id
                            for s in scene_refs
                            if s.scene_motion_artifact_id
                        ),
                        *(s["animation"]["sprite_artifact_id"] for s in worker_scenes),
                        *(key for s in worker_scenes for key in s["sprite_paths"]),
                    )
                )
            )
            manifest = RenderManifest(
                renderer_version=V4_VERSION if environment_selection else VERSION,
                camera_policy_version=(
                    "story_camera_v1" if environment_selection else "gentle_camera_v1"
                ),
                motion_grammar_version=(
                    "visual_story_motion_v1" if environment_selection else "preschool_motion_v1"
                ),
                environmental_theme=environment.theme
                if environment_selection
                else "playful_meadow_v2",
                environment_set_artifact_id=environment_id if environment_selection else None,
                visual_story_plan_artifact_id=(
                    story_record.identity.artifact_id if story_record else None
                ),
                episode_id=owner,
                audio_master_artifact_id=storyboard.audio_master_artifact_id,
                audio_alignment_artifact_id=storyboard.audio_alignment_artifact_id,
                timed_storyboard_artifact_id=sid,
                beat_analysis_artifact_id=storyboard.beat_analysis_artifact_id,
                character_pack_revision=storyboard.character_pack.revision_id,
                dependency_sha256={d: store.get(d).sha256 for d in deps},
                scenes=tuple(scene_refs),
                duration_seconds=storyboard.duration_seconds,
                canvas=self.canvas,
                ffmpeg_version=binaries["ffmpeg_version"],
                ffprobe_version=binaries["ffprobe_version"],
            )
            validate_manifest(store, manifest, storyboard)
            manifest_path = stage / "manifest.json"
            manifest_path.write_bytes(canonical(manifest.model_dump(mode="json")))
            manifest_record = ensure("render_manifest", "main", manifest_path, deps)
            final_deps = (manifest_record.identity.artifact_id, storyboard.audio_master_artifact_id)
            final = self._existing_final(store, owner, final_deps)
            if final is None:
                video = stage / "video.partial.mp4"
                request = stage / "worker.json"
                request.write_bytes(
                    canonical(
                        {
                            "scenes": worker_scenes,
                            "video_path": str(video),
                            "ffmpeg_path": binaries["ffmpeg_path"],
                        }
                    )
                )
                print("Encoding selected production storyboard with MoviePy...", file=sys.stderr)
                run_process(
                    [sys.executable, "-m", "tovitunes.render.composer", str(request)],
                    ENCODE_TIMEOUT,
                )
                partial = stage / "mux.partial.mp4"
                run_process(
                    mux_command(
                        video,
                        store.path_for(storyboard.audio_master_artifact_id),
                        partial,
                        storyboard.duration_seconds,
                    ),
                    MUX_TIMEOUT,
                )
                qa = media_qa(partial, storyboard.duration_seconds, self.canvas)
                if not qa["passed"]:
                    raise ValueError(f"media QA failed: {qa['errors']}")
                # Nothing authoritative is registered until full probe/decode QA succeeds.
                ready = stage / "ready.mp4"
                os.replace(partial, ready)
                leases.assert_owner(lease)
                load_inputs(self.config, inputs.store.database.get_episode(owner).external_key)
                validate_manifest(store, manifest, storyboard)
                final = ensure("final_render", "main", ready, final_deps)
            else:
                actions["final_render:main"] = "reuse"
                qa = media_qa(
                    store.path_for(final.identity.artifact_id),
                    storyboard.duration_seconds,
                    self.canvas,
                )
                if not qa["passed"]:
                    raise ValueError("previous final render no longer passes media QA")
                self._approve_select(store, final)
            qa.update(
                {
                    "render_artifact_id": final.identity.artifact_id,
                    "render_sha256": final.sha256,
                    "scene_coverage": {
                        "passed": True,
                        "scene_ids": list(storyboard.scene_ids),
                        "gaps": 0,
                        "overlaps": 0,
                    },
                    "character_layout": {"passed": True},
                    "educational_checks": deterministic,
                    "composition_qa": {
                        "passed": True,
                        "post_lyric_tail_seconds": tail_seconds,
                        "max_consecutive_identical_composition_count": max(
                            d["composition"]["consecutive_identical_composition_count"]
                            for d in deterministic
                        ),
                    },
                    "renderer_version": V4_VERSION if environment_selection else VERSION,
                    "dynamic_motion_qa": {"passed": True, "major_motion_budget": 3},
                    "visual_activity_diagnostics": [d["motion_diagnostics"] for d in deterministic],
                    "mouth_animation_supported": False,
                }
            )
            if environment_selection:
                qa.update(
                    {
                        "visual_story_diagnostics": [
                            d["visual_story_diagnostics"] for d in deterministic
                        ],
                        "environment_set_artifact_id": environment_id,
                    }
                )
            qa_path = stage / "qa.json"
            qa_path.write_bytes(canonical(qa))
            qa_record = ensure("media_qa", "main", qa_path, (final.identity.artifact_id,))
            output = self.config.brand_root.parent.parent / "outputs"
            output.mkdir(exist_ok=True)
            episode_key = inputs.store.database.get_episode(owner).external_key
            export_stem = (
                f"TOVITUNES_{episode_key.upper().replace('-', '_')}_PILOT_"
                f"{'V4' if environment_selection else 'V3'}"
            )
            export = output / f"{export_stem}.mp4"
            self._export(store.path_for(final.identity.artifact_id), export)
            self._export(
                store.path_for(qa_record.identity.artifact_id),
                output / f"{export_stem}_MEDIA_QA.json",
            )
            frames = (
                output / f"{episode_key.replace('-', '_')}_"
                f"{'v4' if environment_selection else 'v3'}_frames"
            )
            self._frames(export, storyboard, frames)
            review_sheets = (
                self._review_sheets(
                    frames,
                    output / f"{episode_key.replace('-', '_')}_v3_frames",
                    storyboard,
                    output,
                    export_stem,
                )
                if environment_selection
                else {}
            )
            return {
                "classification": (
                    "PILOT_V4_READY_FOR_VISUAL_STORY_REVIEW"
                    if environment_selection
                    else "PILOT_V3_READY_FOR_CHILD_ENGAGEMENT_REVIEW"
                ),
                **(
                    {
                        "environment_set_artifact_id": environment_id,
                        "visual_story_plan_artifact_id": story_record.identity.artifact_id,
                        "review_sheets": review_sheets,
                    }
                    if environment_selection and story_record
                    else {}
                ),
                "episode_id": owner,
                "storyboard_artifact_id": sid,
                "render_manifest_id": manifest_record.identity.artifact_id,
                "final_render_id": final.identity.artifact_id,
                "media_qa_id": qa_record.identity.artifact_id,
                "mp4_sha256": final.sha256,
                "byte_count": final.byte_count,
                "output_path": str(export),
                "frames_path": str(frames),
                "scene_count": len(scene_refs),
                "artifact_actions": actions,
                "media_qa": qa,
                "binaries": binaries,
                "moviepy_version": version("moviepy"),
                "mouth_animation_supported": False,
                "provider_calls": 0,
                "rights": "unknown",
                "publication": "blocked",
            }

    @staticmethod
    def _check_dependencies(
        store: AssetStore, record: ArtifactRecord, deps: tuple[str, ...]
    ) -> None:
        if not store.inspect(record.identity.artifact_id).valid:
            raise ValueError("existing artifact failed immutable validation")
        with closing(store.database.connect()) as db:
            rows = db.execute(
                "SELECT input_artifact_id,input_sha256 FROM artifact_dependencies "
                "WHERE consumer_artifact_id=? ORDER BY rowid",
                (record.identity.artifact_id,),
            ).fetchall()
        if tuple(r[0] for r in rows) != deps or any(store.get(r[0]).sha256 != r[1] for r in rows):
            raise ValueError("existing artifact differs from pinned dependencies")

    @classmethod
    def _existing_final(
        cls, store: AssetStore, owner: str, deps: tuple[str, ...]
    ) -> ArtifactRecord | None:
        with closing(store.database.connect()) as db:
            rows = db.execute(
                "SELECT a.artifact_id FROM artifact_versions a JOIN artifact_dependencies d "
                "ON d.consumer_artifact_id=a.artifact_id WHERE a.episode_id=? "
                "AND a.kind='final_render' AND d.input_artifact_id=? ORDER BY a.rowid DESC",
                (owner, deps[0]),
            ).fetchall()
        if not rows:
            return None
        record = store.get(rows[0][0])
        cls._check_dependencies(store, record, deps)
        return record

    @staticmethod
    def _approve_select(store: AssetStore, record: ArtifactRecord) -> None:
        artifact_id = record.identity.artifact_id
        with closing(store.database.connect()) as db:
            decision = db.execute(
                "SELECT status FROM approval_decisions WHERE artifact_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (artifact_id,),
            ).fetchone()
            current = db.execute(
                "SELECT artifact_id FROM artifact_selections WHERE owner_scope='episode' "
                "AND owner_id=? AND kind=? AND slot_key=?",
                (record.identity.owner_id, record.identity.kind, record.identity.slot_key),
            ).fetchone()
        if decision and decision[0] in {"rejected", "needs_review"}:
            raise ValueError("existing review blocks technical render selection")
        if not decision or decision[0] != "approved":
            store.record_approval(
                ApprovalDecision(
                    target_id=artifact_id,
                    target_kind="artifact",
                    status="approved",
                    actor="machine:tovitunes.render",
                    policy_version="technical_render_v1",
                    reason=(
                        "Technically suitable for local visual review only; "
                        "no rights or publication approval."
                    ),
                    decided_at=datetime.now(UTC),
                )
            )
        if current is None or current[0] != artifact_id:
            store.select(artifact_id)

    @staticmethod
    def _export(source: Path, destination: Path) -> None:
        if (
            destination.exists()
            and sha256(destination.read_bytes()).digest() == sha256(source.read_bytes()).digest()
        ):
            return
        partial = destination.with_suffix(destination.suffix + ".partial")
        try:
            shutil.copyfile(source, partial)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def _frames(video: Path, storyboard: TimedStoryboard, directory: Path) -> None:
        directory.mkdir(exist_ok=True)
        # Every scene is exported; review labels for this pilot are documented in the report.
        for scene in storyboard.scenes:
            frame = directory / f"{scene.scene_id}.png"
            run_process(
                [
                    doctor()["ffmpeg_path"],
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-ss",
                    str((scene.start + scene.end) / 2),
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    str(frame),
                ],
                30,
            )
            if scene.kind == "outro" and scene.end - scene.start > 3:
                for label, when in (("early", scene.start + 0.5), ("late", scene.end - 0.5)):
                    run_process(
                        [
                            doctor()["ffmpeg_path"],
                            "-v",
                            "error",
                            "-nostdin",
                            "-y",
                            "-ss",
                            str(when),
                            "-i",
                            str(video),
                            "-frames:v",
                            "1",
                            str(directory / f"{scene.scene_id}_{label}.png"),
                        ],
                        30,
                    )

    @staticmethod
    def _review_sheets(
        v4_frames: Path,
        v3_frames: Path,
        storyboard: TimedStoryboard,
        output: Path,
        export_stem: str,
    ) -> dict[str, str]:
        scenes = storyboard.scenes
        tile = (216, 384)

        def save_sheet(paths: list[tuple[str, Path]], columns: int, destination: Path) -> None:
            rows = (len(paths) + columns - 1) // columns
            image = Image.new("RGB", (columns * 240, rows * 420), "#ffffff")
            draw = ImageDraw.Draw(image)
            for index, (label, path) in enumerate(paths):
                with Image.open(path) as source:
                    thumb = source.convert("RGB").resize(tile, Image.Resampling.LANCZOS)
                x, y = (index % columns) * 240 + 12, (index // columns) * 420 + 8
                image.paste(thumb, (x, y))
                draw.text((x, y + 388), label, fill="#263238")
            candidate = destination.with_suffix(".partial.png")
            image.save(candidate)
            if (
                not destination.exists()
                or sha256(candidate.read_bytes()).digest()
                != sha256(destination.read_bytes()).digest()
            ):
                os.replace(candidate, destination)
            else:
                candidate.unlink()

        scene_paths = [(scene.scene_id, v4_frames / f"{scene.scene_id}.png") for scene in scenes]
        contact = output / f"{export_stem}_SCENE_CONTACT_SHEET.png"
        save_sheet(scene_paths, 3, contact)
        result = {"v4_scene_contact_sheet": str(contact)}
        if all((v3_frames / f"{scene.scene_id}.png").is_file() for scene in scenes):
            pairs = [
                (f"{scene.scene_id} V3", v3_frames / f"{scene.scene_id}.png")
                if index % 2 == 0
                else (f"{scene.scene_id} V4", v4_frames / f"{scene.scene_id}.png")
                for scene in scenes
                for index in range(2)
            ]
            comparison = output / f"{export_stem}_V3_V4_COMPARISON.png"
            save_sheet(pairs, 2, comparison)
            result["v3_v4_comparison"] = str(comparison)
        return result
