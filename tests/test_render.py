"""Network-free unit checks and a two-second real portrait composition/mux."""

import io
import json
import shutil
import sqlite3
import sys
import wave
from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from tovitunes.artifacts.store import AssetStore, InputDependency
from tovitunes.config import RuntimeConfig
from tovitunes.domain.artifact import Provenance
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import ApprovalDecision, RightsDecision
from tovitunes.domain.storyboard import AudioAlignment, BeatAnalysis, TimedScene, TimedStoryboard
from tovitunes.persistence.db import Database
from tovitunes.render import production
from tovitunes.render.character import ACTION_ROLES, animation_plan, position, validate_layout
from tovitunes.render.composer import ball_position, build_scene
from tovitunes.render.ffmpeg import doctor, mux_command, resolve_binary, run_process
from tovitunes.render.layout import compute_fit_box
from tovitunes.render.models import CharacterAnimation, RenderManifest
from tovitunes.render.production import (
    ProductionRenderer,
    canonical,
    load_inputs,
    validate_manifest,
)
from tovitunes.render.props import LESSON_RED, png_info, prop_image, scene_art
from tovitunes.render.qa import check_probe


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external-generation/network entry point used by renderer")

    for target in (
        "socket.socket.connect",
        "socket.create_connection",
        "tovitunes.music.benchmark.MusicBenchmark.run",
        "tovitunes.music.benchmark.MusicBenchmark.provider_resume",
        "tovitunes.music.benchmark.MusicBenchmark.analyze_audio",
        "tovitunes.music.vertex_lyria.VertexLyriaProvider.generate",
        "tovitunes.benchmark.runner.BenchmarkRunner.run",
        "tovitunes.benchmark.providers.GeminiImageProvider.generate",
        "tovitunes.benchmark.providers.OpenAIImageProvider.generate",
        "tovitunes.benchmark.providers.QwenComfyUIImageProvider.generate",
        "tovitunes.pipeline.creative.FakeDraftGenerator.episode_spec",
    ):
        monkeypatch.setattr(target, forbidden)


@pytest.fixture
def beat_data():
    return BeatAnalysis(
        audio_master_artifact_id="audio",
        audio_sha256="a" * 64,
        duration_seconds=2,
        source_analysis_version=1,
        estimated_bpm=120,
        beat_seconds=(0.25, 0.75, 1.25, 1.75),
        downbeat_seconds=(0.25, 1.25),
        detector="fixture",
        detector_version="fixture",
        model_identity=None,
        model_revision=None,
    )


def scene(action="point", props=("red_ball",)):
    return TimedScene(
        scene_id="lesson",
        kind="lyric",
        section="hook",
        start=0,
        end=2,
        lyric_text="Red!",
        lyric_start=0,
        lyric_end=2,
        lesson_target="red",
        visual_focus="one clear red object",
        required_props=props,
        tovi_action=action,
        beat_index_range=(0, 4),
        downbeat_index_range=(0, 2),
    )


@pytest.mark.parametrize(
    "source,target,mode",
    [
        ((1920, 1080), (1080, 1920), "cover"),
        ((1080, 1920), (1080, 1080), "cover"),
        ((1920, 1080), (1080, 1080), "contain"),
        ((1536, 1664), (810, 768), "contain"),
        ((1080, 1920), (1080, 1920), "cover"),
    ],
)
def test_donor_layout(source, target, mode):
    box = compute_fit_box(source, target, mode=mode)
    assert 0 <= box.crop_x <= source[0] - box.crop_w
    assert 0 <= box.crop_y <= source[1] - box.crop_h
    assert box.out_w <= target[0] and box.out_h <= target[1]
    if mode == "contain":
        assert (box.crop_w, box.crop_h) == source
        assert abs(box.out_w / box.out_h - source[0] / source[1]) < 0.002
    else:
        assert (box.out_w, box.out_h) == target


@pytest.mark.parametrize("mode", ["stretch", "unknown"])
def test_bad_fit_mode(mode):
    with pytest.raises(ValueError):
        compute_fit_box((10, 20), (30, 40), mode=mode)


def test_binary_resolution(monkeypatch, tmp_path):
    binary = tmp_path / "binary with spaces.exe"
    binary.write_bytes(b"fixture")
    for name in ("ffmpeg", "ffprobe"):
        monkeypatch.setattr(shutil, "which", lambda name: "system binary")
        monkeypatch.delenv(f"TOVITUNES_{name.upper()}_BIN", raising=False)
        assert resolve_binary(name) == "system binary"
        monkeypatch.setenv(f"TOVITUNES_{name.upper()}_BIN", str(binary))
        assert resolve_binary(name) == str(binary.resolve())
        monkeypatch.setenv(f"TOVITUNES_{name.upper()}_BIN", str(binary) + "missing")
        with pytest.raises(ValueError, match="configured"):
            resolve_binary(name)


def test_missing_binary(monkeypatch):
    monkeypatch.delenv("TOVITUNES_FFMPEG_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="system ffmpeg unavailable"):
        resolve_binary("ffmpeg")


def test_process_timeout_and_space_path(tmp_path):
    helper = tmp_path / "helper with spaces.py"
    helper.write_text("print('resolved')")
    assert run_process([sys.executable, str(helper)], 5).strip() == "resolved"
    with pytest.raises(TimeoutError, match="deadline"):
        run_process([sys.executable, "-c", "import time; time.sleep(30)"], 0.15)
    with pytest.raises(RuntimeError) as exc:
        run_process(
            [sys.executable, "-c", "import sys; sys.stderr.write('x'*10000); sys.exit(1)"], 5
        )
    assert len(str(exc.value)) < 4100


@pytest.mark.parametrize("kind", ["red_swatch", "red_apple", "red_ball"])
def test_deterministic_red_props(kind, catalog):
    assert LESSON_RED == "#E53935"
    image = prop_image(kind)
    assert image.getpixel((280, 280))[:3] == (229, 57, 53)
    outputs = []
    for _ in range(2):
        image, metadata = scene_art(
            scene(props=(kind,)), catalog.packs[0].palette, (270, 480), "story"
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", pnginfo=png_info(metadata))
        outputs.append(buffer.getvalue())
        assert metadata["props"][0]["count"] == 1
        assert metadata["props"][0]["lesson_color"] == LESSON_RED
        assert metadata["contains_tovi"] is False
        assert metadata["prop_style_version"] == "preschool_soft_v1"
        assert metadata["supersample"] == 4
        assert all(
            metadata["prop_style_contract"][key]
            for key in ("antialiased", "soft_shadow", "highlight")
        )
    assert sha256(outputs[0]).digest() == sha256(outputs[1]).digest()


@pytest.mark.parametrize("action", list(ACTION_ROLES))
def test_approved_character_motion_and_mouth_fallback(action, beat_data):
    sprite = Image.new("RGBA", (100, 160), (0, 0, 0, 0))
    ImageDraw.Draw(sprite).ellipse((10, 10, 90, 150), fill="blue")
    plan = animation_plan(scene(action), sprite, "sprite", beat_data, (270, 480), "story")
    assert plan.sprite_role == ACTION_ROLES[action]
    assert plan.mouth_animation_supported is False
    assert "anchor" in plan.mouth_reason
    validate_layout(plan, (270, 480))
    samples = [position(plan, i / 30) for i in range(60)]
    assert samples == [position(plan, i / 30) for i in range(60)]
    if action == "enter":
        assert position(plan, 0)[0] + plan.size[0] == 0
        assert position(plan, plan.enter_seconds) == plan.end_position
    elif action == "celebrate":
        assert position(plan, 0.42)[1] < plan.end_position[1]


def test_invalid_sprite_and_scale(beat_data):
    with pytest.raises(ValueError, match="alpha"):
        animation_plan(
            scene(), Image.new("RGBA", (10, 10)), "sprite", beat_data, (270, 480), "story"
        )
    plan = animation_plan(
        scene(), Image.new("RGBA", (10, 20), "blue"), "sprite", beat_data, (270, 480), "story"
    )
    with pytest.raises(ValidationError):
        CharacterAnimation.model_validate({**plan.model_dump(), "size": (200, 20)})
    with pytest.raises(ValidationError):
        CharacterAnimation.model_validate({**plan.model_dump(), "sprite_role": "sprite/unapproved"})
    with pytest.raises(ValidationError):
        scene("unsupported")
    with pytest.raises(ValidationError):
        scene(props=("blue_apple",))
    with pytest.raises(ValueError, match="safe"):
        validate_layout(plan.model_copy(update={"end_position": (270, 0)}), (270, 480))


def good_probe():
    return {
        "format": {"duration": "2.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
                "duration": "2.0",
                "sample_aspect_ratio": "1:1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
                "duration": "2.0",
            },
        ],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 720),
        ("codec_name", "hevc"),
        ("pix_fmt", "yuv444p"),
        ("avg_frame_rate", "24/1"),
        ("duration", "2.3"),
        ("duration", "nan"),
        ("sample_aspect_ratio", "2:1"),
    ],
)
def test_media_qa_rejects_wrong_video(field, value):
    data = good_probe()
    data["streams"][0][field] = value
    assert not check_probe(data, 2)["passed"]


def test_media_qa_streams_audio_duration():
    data = good_probe()
    assert check_probe(data, 2)["passed"]
    data["streams"].pop()
    assert not check_probe(data, 2)["passed"]
    data = good_probe()
    data["streams"][1]["sample_rate"] = "0"
    assert not check_probe(data, 2)["passed"]
    data = good_probe()
    data["format"]["duration"] = "3"
    assert not check_probe(data, 2)["passed"]


def test_ball_roll_and_mux_mapping(tmp_path):
    bbox = [50, 100, 150, 200]
    assert ball_position(0, bbox, 270) == (270, 100)
    assert ball_position(0.95, bbox, 270) == (50, 100)
    assert 50 < ball_position(0.3, bbox, 270)[0] < 270
    if not shutil.which("ffmpeg"):
        pytest.skip("system FFmpeg absent")
    cmd = mux_command(
        tmp_path / "video with spaces.mp4", tmp_path / "audio.wav", tmp_path / "x.partial.mp4", 2
    )
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert "1:a:0" in cmd and "aac" in cmd and "+faststart" in cmd
    assert "192k" in cmd and cmd[-1].endswith(".partial.mp4")


@pytest.fixture
def render_fixture(tmp_path, catalog, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("system FFmpeg and ffprobe required")
    pytest.importorskip("moviepy")
    brand = tmp_path / "project/brands/tovitunes"
    shutil.copytree(Path(__file__).parents[1] / "brands/tovitunes", brand)
    config = RuntimeConfig(
        database_path=tmp_path / "state.db", data_root=tmp_path / "assets", brand_root=brand
    )
    db = Database(config.database_path)
    db.migrate()
    episode = Episode.create(catalog, "red", "colors-red-001")
    db.create_episode(catalog, episode)
    store = AssetStore(config.data_root, db, generated_source_roots=[tmp_path])

    def ingest(kind, slot, path, deps=(), scope="episode"):
        record = store.ingest(
            path,
            owner_scope=scope,
            owner_id=episode.episode_id if scope == "episode" else episode.brand_revision_id,
            kind=kind,
            slot_key=slot,
            provenance=Provenance(
                source_kind="deterministic",
                acquired_at=datetime.now(UTC),
                provider="fixture",
                input_artifact_ids=deps,
            ),
            dependencies=[InputDependency(d, "fixture") for d in deps],
        )
        store.record_approval(
            ApprovalDecision(
                target_id=record.identity.artifact_id,
                target_kind="artifact",
                status="approved",
                actor="fixture",
                policy_version="fixture",
                decided_at=datetime.now(UTC),
            )
        )
        store.select(record.identity.artifact_id)
        return record.identity.artifact_id

    audio_path = tmp_path / "audio.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 88200)
    audio_id = ingest("audio_master", "main", audio_path)
    digest = store.get(audio_id).sha256

    def json_artifact(kind, payload, deps):
        path = tmp_path / f"{kind}.json"
        path.write_bytes(canonical(payload.model_dump(mode="json")))
        return ingest(kind, "main", path, deps)

    alignment = AudioAlignment(
        audio_master_artifact_id=audio_id,
        audio_sha256=digest,
        source_blind_id="fixture",
        source_analysis_version=1,
        duration_seconds=2,
        words=({"text": "Red!", "start": 0.5, "end": 1.5},),
        lyric_lines=({"text": "Red!", "start": 0.5, "end": 1.5},),
        sections=({"text": "hook", "start": 0.5, "end": 1.5},),
        pre_lyric={"start": 0, "end": 0.5},
        post_lyric={"start": 1.5, "end": 2},
    )
    alignment_id = json_artifact("audio_alignment", alignment, (audio_id,))
    beats = BeatAnalysis(
        audio_master_artifact_id=audio_id,
        audio_sha256=digest,
        duration_seconds=2,
        source_analysis_version=1,
        estimated_bpm=120,
        beat_seconds=(0.25, 0.75, 1.25, 1.75),
        downbeat_seconds=(0.25, 1.25),
        detector="fixture",
        detector_version="fixture",
        model_identity=None,
        model_revision=None,
    )
    beat_id = json_artifact("beat_analysis", beats, (audio_id,))
    scenes = (
        TimedScene(
            scene_id="intro",
            kind="intro",
            section="pre",
            start=0,
            end=0.5,
            tovi_action="enter",
            visual_focus="hello",
            beat_index_range=(0, 1),
            downbeat_index_range=(0, 1),
        ),
        TimedScene(
            scene_id="lesson",
            kind="lyric",
            section="hook",
            start=0.5,
            end=1.5,
            tovi_action="point",
            visual_focus="ball",
            required_props=("red_ball",),
            lesson_target="red",
            lyric_text="Red!",
            lyric_start=0.5,
            lyric_end=1.5,
            beat_index_range=(1, 3),
            downbeat_index_range=(1, 2),
        ),
        TimedScene(
            scene_id="outro",
            kind="outro",
            section="post",
            start=1.5,
            end=2,
            tovi_action="celebrate",
            visual_focus="goodbye",
            beat_index_range=(3, 4),
            downbeat_index_range=(2, 2),
        ),
    )
    storyboard = TimedStoryboard(
        episode_id=episode.episode_id,
        audio_master_artifact_id=audio_id,
        audio_alignment_artifact_id=alignment_id,
        beat_analysis_artifact_id=beat_id,
        audio_sha256=digest,
        duration_seconds=2,
        template_id="fixture",
        template_sha256="a" * 64,
        concept_id="red",
        objective_id="colors.red.identify",
        character_pack=episode.character_packs[0],
        scenes=scenes,
    )
    json_artifact("timed_storyboard", storyboard, (audio_id, alignment_id, beat_id))
    sprite = Image.new("RGBA", (50, 90), (0, 0, 0, 0))
    ImageDraw.Draw(sprite).ellipse((5, 5, 45, 85), fill="#5FBFFC")
    sprite_path = tmp_path / "sprite.png"
    sprite.save(sprite_path)
    references = {}
    for i, role in enumerate(sorted(set(ACTION_ROLES.values()))):
        references[role] = ingest("character_sprite", f"sprite_{i}", sprite_path, scope="brand")
    pack = catalog.packs[0].model_copy(update={"asset_artifact_ids": references})
    monkeypatch.setattr(production, "load_brand", lambda root: replace(catalog, packs=(pack,)))
    return config, store, storyboard


def rows_snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return {
            table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_tiny_real_render_idempotency_and_manifest(render_fixture, monkeypatch):
    config, store, storyboard = render_fixture
    renderer = ProductionRenderer(config, canvas=(270, 480))
    first = renderer.render("colors-red-001")
    assert first["media_qa"]["passed"]
    assert first["media_qa"]["dimensions"] == [270, 480]
    export = Path(first["output_path"])
    assert export.read_bytes() == store.path_for(first["final_render_id"]).read_bytes()
    assert export.read_bytes().find(b"moov") < export.read_bytes().find(b"mdat")
    before = rows_snapshot(config.database_path)
    original_run = production.run_process

    def no_encode(argv, timeout):
        assert "tovitunes.render.composer" not in argv
        return original_run(argv, timeout)

    monkeypatch.setattr(production, "run_process", no_encode)
    second = renderer.render("colors-red-001")
    assert all(value == "reuse" for value in second["artifact_actions"].values())
    assert first["mp4_sha256"] == second["mp4_sha256"]
    assert rows_snapshot(config.database_path) == before
    manifest = RenderManifest.model_validate(store.read_json(first["render_manifest_id"]))
    lesson = next(scene for scene in manifest.scenes if scene.scene_id == "lesson")
    with Image.open(store.path_for(lesson.scene_image_artifact_id)) as scene_image:
        metadata = json.loads(scene_image.info["tovitunes_composition"])
    assert metadata["prop_style_version"] == "preschool_soft_v1"
    assert "environment_set_artifact_id" not in manifest.model_dump(mode="json")
    assert "visual_story_plan_artifact_id" not in manifest.model_dump(mode="json")
    for scene in manifest.scenes:
        assert scene.scene_motion_artifact_id in manifest.dependency_sha256
        animation_json = store.read_json(scene.character_animation_artifact_id)
        assert animation_json["scene_motion_artifact_id"] == scene.scene_motion_artifact_id
        assert all(
            pose["sprite_artifact_id"] in manifest.dependency_sha256
            for pose in animation_json["pose_sequence"]
        )
        with closing(store.database.connect()) as db:
            deps = {
                row[0]
                for row in db.execute(
                    "SELECT input_artifact_id FROM artifact_dependencies "
                    "WHERE consumer_artifact_id=?",
                    (scene.scene_motion_artifact_id,),
                )
            }
        assert {
            manifest.timed_storyboard_artifact_id,
            manifest.audio_alignment_artifact_id,
            manifest.beat_analysis_artifact_id,
            scene.scene_image_artifact_id,
        } <= deps
    validate_manifest(store, manifest, storyboard)
    altered = manifest.model_copy(
        update={"dependency_sha256": {k: "b" * 64 for k in manifest.dependency_sha256}}
    )
    with pytest.raises(ValueError, match="stale"):
        validate_manifest(store, altered, storyboard)
    incomplete = manifest.model_dump()
    incomplete["dependency_sha256"] = {}
    with pytest.raises(ValidationError, match="missing"):
        RenderManifest.model_validate(incomplete)
    assert (
        canonical(manifest.model_dump(mode="json"))
        == store.path_for(first["render_manifest_id"]).read_bytes()
    )


@pytest.mark.parametrize("activate_manifest", [False, True])
def test_selected_lesson_manifest_switches_renderer_to_reviewed_props(
    render_fixture, activate_manifest
):
    config, store, storyboard = render_fixture
    brand_id = store.database.get_episode(storyboard.episode_id).brand_revision_id
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((170, 180, 854, 1023), fill="#E8302A")
    stage = config.database_path.parent / "reviewed-prop.png"
    image.save(stage)
    prop_ids = {}
    for object_key in ("red_apple", "red_ball"):
        record = store.ingest(
            stage,
            owner_scope="brand",
            owner_id=brand_id,
            kind="lesson_object",
            slot_key=object_key,
            provenance=Provenance(
                source_kind="deterministic",
                acquired_at=datetime.now(UTC),
                provider="fixture",
            ),
            expected_media_type="image/png",
        )
        prop_ids[object_key] = record.identity.artifact_id
        store.record_approval(
            ApprovalDecision(
                target_id=record.identity.artifact_id,
                target_kind="artifact",
                status="approved",
                actor="human:fixture",
                policy_version="fixture",
                decided_at=datetime.now(UTC),
            )
        )
        store.record_rights(
            RightsDecision(
                artifact_id=record.identity.artifact_id,
                status="review_required",
                actor="human:fixture",
                policy_version="fixture",
                decided_at=datetime.now(UTC),
            )
        )
        store.select(record.identity.artifact_id)
    manifest_path = config.database_path.parent / "reviewed-props.json"
    manifest_path.write_text(json.dumps({"style_version": "lesson_object_assets_v2"}))
    reviewed_manifest = store.ingest(
        manifest_path,
        owner_scope="brand",
        owner_id=brand_id,
        kind="lesson_object_manifest",
        slot_key="lesson_object_assets_v2",
        provenance=Provenance(
            source_kind="deterministic",
            acquired_at=datetime.now(UTC),
            provider="fixture",
            input_artifact_ids=tuple(prop_ids.values()),
        ),
        dependencies=[InputDependency(artifact_id, key) for key, artifact_id in prop_ids.items()],
        expected_media_type="application/json",
    )
    store.record_approval(
        ApprovalDecision(
            target_id=reviewed_manifest.identity.artifact_id,
            target_kind="artifact",
            status="approved",
            actor="human:fixture",
            policy_version="fixture",
            decided_at=datetime.now(UTC),
        )
    )
    store.record_rights(
        RightsDecision(
            artifact_id=reviewed_manifest.identity.artifact_id,
            status="review_required",
            actor="human:fixture",
            policy_version="fixture",
            decided_at=datetime.now(UTC),
        )
    )

    def lesson_metadata(result):
        render_manifest = RenderManifest.model_validate(
            store.read_json(result["render_manifest_id"])
        )
        lesson = next(scene for scene in render_manifest.scenes if scene.scene_id == "lesson")
        with Image.open(store.path_for(lesson.scene_image_artifact_id)) as scene_image:
            return json.loads(scene_image.info["tovitunes_composition"]), render_manifest

    renderer = ProductionRenderer(config, canvas=(270, 480))
    assert (
        store.selected("brand", brand_id, "lesson_object_manifest", "lesson_object_assets_v2")
        is None
    )
    if activate_manifest:
        store.select(reviewed_manifest.identity.artifact_id)
    reviewed_metadata, render_manifest = lesson_metadata(renderer.render("colors-red-001"))
    ball = next(prop for prop in reviewed_metadata["props"] if prop["type"] == "red_ball")
    if not activate_manifest:
        assert reviewed_metadata["prop_style_version"] == "preschool_soft_v1"
        assert "asset_artifact_id" not in ball
        assert reviewed_manifest.identity.artifact_id not in render_manifest.dependency_sha256
        return
    assert reviewed_metadata["prop_style_version"] == "lesson_object_assets_v2"
    assert ball["asset_artifact_id"] == prop_ids["red_ball"]
    assert ball["asset_sha256"] == store.get(prop_ids["red_ball"]).sha256
    assert reviewed_manifest.identity.artifact_id in render_manifest.dependency_sha256
    assert set(prop_ids.values()) <= set(render_manifest.dependency_sha256)


def test_tiny_v4_render_uses_reviewed_fixture_environment(render_fixture, monkeypatch):
    from io import BytesIO

    from tovitunes.benchmark.providers import ProviderResult
    from tovitunes.render.environment_sets import decide_set, generate_set, select_set

    config, store, _ = render_fixture
    plate = Image.new("RGB", (900, 1600), "#afe3f3")
    ImageDraw.Draw(plate).ellipse((-200, 800, 1100, 1850), fill="#88c876")
    buffer = BytesIO()
    plate.save(buffer, format="PNG")

    class FixtureProvider:
        provider = "fixture"
        model = "fixture-world"

        def __init__(self):
            self.calls = 0

        def generate(self, spec, reference_paths, *, on_remote_start=None):
            self.calls += 1
            assert not reference_paths if self.calls == 1 else len(reference_paths) == 1
            on_remote_start()
            return ProviderResult(
                image_bytes=buffer.getvalue(),
                mime_type="image/png",
                provider_request_id=f"fixture-{self.calls}",
            )

    provider = FixtureProvider()
    generated = generate_set(config, confirmed=True, provider=provider)
    decide_set(
        config,
        generated["manifest_artifact_id"],
        actor="human:fixture",
        reason="fixture plate inspected",
        status="approved",
    )
    select_set(config, generated["manifest_artifact_id"])
    renderer = ProductionRenderer(config, canvas=(270, 480))
    first = renderer.render("colors-red-001", visual_story=True)
    assert first["classification"] == "PILOT_V4_READY_FOR_VISUAL_STORY_REVIEW"
    assert first["media_qa"]["passed"] and provider.calls == 4
    assert first["environment_set_artifact_id"] == generated["manifest_artifact_id"]
    assert "PILOT_V4" in first["output_path"]
    assert all(
        scene["dead_space_warning"] is None
        for scene in first["media_qa"]["visual_story_diagnostics"]
    )
    before = rows_snapshot(config.database_path)
    original_run = production.run_process

    def no_encode(argv, timeout):
        assert "tovitunes.render.composer" not in argv
        return original_run(argv, timeout)

    monkeypatch.setattr(production, "run_process", no_encode)
    second = renderer.render("colors-red-001", visual_story=True)
    assert second["mp4_sha256"] == first["mp4_sha256"]
    assert all(action == "reuse" for action in second["artifact_actions"].values())
    assert rows_snapshot(config.database_path) == before
    assert provider.calls == 4
    manifest = RenderManifest.model_validate(store.read_json(first["render_manifest_id"]))
    assert manifest.environment_set_artifact_id == generated["manifest_artifact_id"]
    assert manifest.visual_story_plan_artifact_id == first["visual_story_plan_artifact_id"]


def test_v4_render_requires_selected_environment(render_fixture):
    config, _, _ = render_fixture
    with pytest.raises(ValueError, match="no selected approved environment set"):
        ProductionRenderer(config, canvas=(270, 480)).render("colors-red-001", visual_story=True)


@pytest.mark.parametrize("failure_stage", ["encode", "mux", "qa"])
def test_encode_failure_no_authoritative_render(render_fixture, monkeypatch, failure_stage):
    config, store, storyboard = render_fixture

    real_run = production.run_process

    def fail(argv, timeout):
        if failure_stage == "encode" or (failure_stage == "mux" and "-map" in argv):
            raise RuntimeError("fixture encode/mux failure")
        return real_run(argv, timeout)

    monkeypatch.setattr(production, "run_process", fail)
    if failure_stage == "qa":
        monkeypatch.setattr(
            production, "media_qa", lambda *args: {"passed": False, "errors": ["failure"]}
        )
    with pytest.raises((RuntimeError, ValueError), match="fail"):
        ProductionRenderer(config, canvas=(270, 480)).render("colors-red-001")
    with closing(store.database.connect()) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM artifact_versions WHERE kind='final_render'"
            ).fetchone()[0]
            == 0
        )
    assert not list((config.data_root / ".render-working").iterdir())
    assert store.selected("episode", storyboard.episode_id, "final_render", "main") is None


def test_optional_dependency_diagnostic(render_fixture, monkeypatch):
    config, _, _ = render_fixture

    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(production, "version", missing)
    before = rows_snapshot(config.database_path)
    with pytest.raises(ValueError, match="video-render extra"):
        ProductionRenderer(config).render("colors-red-001")
    assert rows_snapshot(config.database_path) == before


def test_transparent_moviepy_composition(render_fixture):
    config, store, _ = render_fixture
    inputs = load_inputs(config, "colors-red-001")
    selected_scene = inputs.storyboard.scenes[1]
    sprite_id = inputs.pack.asset_artifact_ids["sprite/pointing"]
    with Image.open(store.path_for(sprite_id)) as sprite:
        plan = animation_plan(selected_scene, sprite, sprite_id, inputs.beats, (270, 480), "story")
    image, metadata = scene_art(selected_scene, inputs.pack.palette, (270, 480), "story")
    background = config.data_root / "background.png"
    image.save(background)
    with ExitStack() as stack:
        clip = build_scene(
            {
                "start": 0.5,
                "end": 1.5,
                "canvas": (270, 480),
                "animation": plan.model_dump(),
                "metadata": metadata,
                "sprite_path": str(store.path_for(sprite_id)),
                "background_path": str(background),
            },
            stack,
        )
        frame = clip.get_frame(0.5)
        assert tuple(frame[10, 10]) == image.getpixel((10, 10))
        x, y = plan.end_position
        assert tuple(frame[int(y + plan.size[1] / 2), int(x + plan.size[0] / 2)]) == (95, 191, 252)


def test_doctor_versions():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("system tools absent")
    info = doctor()
    assert info["ffmpeg_version"].startswith("ffmpeg version")
    assert info["ffprobe_version"].startswith("ffprobe version")
