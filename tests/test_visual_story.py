"""Generic semantic staging and reviewed environment generation are provider-free in tests."""

import io
import json
import shutil
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from tovitunes.artifacts.store import AssetStore
from tovitunes.benchmark.providers import (
    ProviderFailure,
    ProviderResult,
    QwenComfyUIImageProvider,
)
from tovitunes.config import EnvironmentGenerationConfig, RuntimeConfig
from tovitunes.domain.storyboard import AudioAlignment, BeatAnalysis, TimedScene
from tovitunes.persistence.db import Database
from tovitunes.render.character import animation_plan, attach_poses, pose_keyframe
from tovitunes.render.composer import build_scene, encode_worker
from tovitunes.render.composition import (
    PROP_DEFINITIONS,
    CompositionRequest,
    PropDefinition,
    resolve_composition,
)
from tovitunes.render.environment_sets import (
    CANVAS,
    MIN_SOURCE_SIZE,
    ROLES,
    SOURCE_DIMENSIONS,
    _validate_image,
    comparison_sheet,
    decide_set,
    generate_set,
    inspect_set,
    plan,
    select_set,
    selected_set,
)
from tovitunes.render.motion import activity_diagnostics, plan_motion, prop_state, validate_motion
from tovitunes.render.props import scene_art, validate_props
from tovitunes.render.story import occupancy, plan_story, stage_composition


@pytest.fixture
def runtime(tmp_path, brand_root):
    return RuntimeConfig(
        database_path=tmp_path / "data" / "tovitunes.db",
        data_root=tmp_path / "data",
        brand_root=brand_root,
    )


def picture(size=MIN_SOURCE_SIZE) -> bytes:
    image = Image.new("RGB", size, "#a5def0")
    draw = ImageDraw.Draw(image)
    draw.ellipse((-100, size[1] // 2, size[0] + 100, size[1] + 100), fill="#7ac977")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_qwen_environment_uses_text_contract_and_exact_source_bytes(runtime):
    config = runtime.model_copy(
        update={
            "environment_generation": EnvironmentGenerationConfig(
                provider="qwen_comfyui",
                workflow_path=Path("workflows/qwen_image_2_1_t2i_api.json").resolve(),
            )
        }
    )
    preview = plan(config)
    assert all(request["reference_assets"] == [] for request in preview["requests"])
    prompts = []
    calls = []
    source = picture((768, 1376))

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            prompts.append(body["prompt"]["459:452"]["inputs"]["prompt"])
            assert body["prompt"]["459:456"]["inputs"]["width"] == 768
            assert body["prompt"]["459:456"]["inputs"]["height"] == 1376
            return httpx.Response(200, json={"prompt_id": f"qwen-{len(prompts)}"})
        if request.url.path.startswith("/history/"):
            task_id = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(
                200,
                json={
                    task_id: {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "461": {
                                "images": [
                                    {"filename": "plate.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        assert request.url.path == "/view"
        return httpx.Response(200, content=source)

    generation = config.environment_generation
    provider = QwenComfyUIImageProvider(
        generation.workflow_path,
        width=768,
        height=1376,
        purpose="environment",
        transport=httpx.MockTransport(handle),
    )
    result = generate_set(config, confirmed=True, provider=provider)
    assert calls.count("/prompt") == len(ROLES)
    assert len(prompts) == len(ROLES)
    assert all("plain white background" not in prompt.lower() for prompt in prompts)
    assert all("rounded shape language" in prompt for prompt in prompts)
    manifest = inspect_set(config, result["manifest_artifact_id"])
    store = AssetStore(config.data_root, Database(config.database_path))
    for plate in manifest["environment_set"]["plates"]:
        assert plate["source_references"] == []
        assert plate["provider_request_id"].startswith("qwen-")
        assert plate["source_sha256"] == sha256(source).hexdigest()
        assert store.path_for(plate["source_artifact_id"]).read_bytes() == source


def test_documented_vertex_1k_portrait_is_accepted_and_normalized():
    assert _validate_image(picture()).size == CANVAS
    with pytest.raises(ValueError, match="too small"):
        _validate_image(picture((MIN_SOURCE_SIZE[0] - 1, MIN_SOURCE_SIZE[1])))


class FakeProvider:
    provider = "fixture"

    def __init__(
        self,
        *,
        ambiguous=False,
        model="fixture-image-v1",
        image_size="1K",
        location="global",
    ):
        self.calls = 0
        self.ambiguous = ambiguous
        self.model = model
        self.image_size = image_size
        self.location = location

    def generate(self, spec, reference_paths, *, on_remote_start=None):
        self.calls += 1
        assert (not reference_paths) == (spec.case_id == ROLES[0])
        if on_remote_start:
            on_remote_start()
        if self.ambiguous:
            raise ProviderFailure("uncertain", outcome="ambiguous")
        size = SOURCE_DIMENSIONS.get((self.model, self.image_size), MIN_SOURCE_SIZE)
        return ProviderResult(
            image_bytes=picture(size),
            mime_type="image/png",
            provider_request_id=f"fixture-{self.calls}",
            usage={"prompt_token_count": 7},
            response_metadata={"backend": "fixture"},
        )


def test_environment_requires_confirmation_and_is_reviewed(runtime):
    provider = FakeProvider()
    preview = plan(runtime)
    assert preview["request_count"] == 4 and preview["provider_calls"] == 0
    assert all("NO bird mascot" in request["prompt"] for request in preview["requests"])
    with pytest.raises(ValueError, match="confirm-provider-generation"):
        generate_set(runtime, confirmed=False, provider=provider)
    assert provider.calls == 0 and not runtime.database_path.exists()
    result = generate_set(runtime, confirmed=True, provider=provider)
    assert provider.calls == 4
    assert result["provider_calls"] == {
        "prepared": 4,
        "remote_started": 4,
        "succeeded": 4,
        "failed": 0,
        "ambiguous": 0,
    }
    assert inspect_set(runtime, result["manifest_artifact_id"])["review_status"] == "pending"
    assert result["contact_sheet"] and __import__("pathlib").Path(result["contact_sheet"]).is_file()
    with Image.open(result["contact_sheet"]) as sheet:
        assert sheet.size == (960, 1760)
        for index in range(4):
            cell_x = (index % 2) * 480
            cell_y = (index // 2) * 880
            label = sheet.crop((cell_x + 8, cell_y + 833, cell_x + 473, cell_y + 873))
            assert sum(max(pixel) > 240 for pixel in label.getdata()) > 10
    with pytest.raises(ValueError, match="approval"):
        select_set(runtime, result["manifest_artifact_id"])
    decide_set(
        runtime,
        result["manifest_artifact_id"],
        actor="human:test",
        reason="reviewed contact sheet",
        status="approved",
    )
    select_set(runtime, result["manifest_artifact_id"])
    environment, selected_id = selected_set(runtime)
    assert selected_id == result["manifest_artifact_id"]
    assert tuple(p.role for p in environment.plates) == ROLES
    store = AssetStore(runtime.data_root, Database(runtime.database_path))
    for plate in environment.plates:
        record = store.get(plate.artifact_id)
        assert record.sha256 == plate.sha256 and plate.dimensions == (1080, 1920)
        assert record.provenance.provider == "fixture"
        assert record.provenance.model == provider.model
        assert record.provenance.local_request_id == plate.local_request_id
        assert record.provenance.request_id == plate.provider_request_id
        with store.database.connect() as conn:
            rights = conn.execute(
                "SELECT status FROM rights_decisions WHERE artifact_id=?", (plate.artifact_id,)
            ).fetchone()
            request = conn.execute(
                "SELECT response_metadata_json FROM environment_requests WHERE request_id=?",
                (plate.local_request_id,),
            ).fetchone()
        assert rights[0] == "unknown"
        metadata = json.loads(request["response_metadata_json"])
        assert metadata["backend"] == "fixture"
        assert metadata["normalized_dimensions"] == [1080, 1920]
        assert metadata["source_dimensions"] == [768, 1376]
        assert metadata["technical_validation"] == "passed"
        assert metadata["requested_image_size"] == "1K"
        assert metadata["usage_metadata"] == {"prompt_token_count": 7}
        assert len(metadata["response_sha256"]) == 64
        assert store.get(metadata["source_artifact_id"]).provenance.model == provider.model
    repeated = generate_set(runtime, confirmed=True, provider=provider)
    assert repeated["manifest_artifact_id"] == selected_id and provider.calls == 4
    assert repeated["review_status"] == "approved"
    assert repeated["provider_calls"]["remote_started"] == 0


def test_environment_plan_and_fingerprint_pin_model_size_and_references(runtime):
    flash = plan(runtime)
    pro_1k = plan(
        runtime.model_copy(
            update={
                "environment_generation": EnvironmentGenerationConfig(
                    model="gemini-3-pro-image", location="global", image_size="1K"
                )
            }
        )
    )
    pro_2k = plan(
        runtime.model_copy(
            update={
                "environment_generation": EnvironmentGenerationConfig(
                    model="gemini-3-pro-image", location="global", image_size="2K"
                )
            }
        )
    )
    assert pro_2k["provider"] == "google"
    assert pro_2k["model"] == "gemini-3-pro-image"
    assert pro_2k["requested_image_size"] == "2K"
    assert pro_2k["location"] == "global"
    assert pro_2k["role_count"] == 4
    assert pro_2k["provider_calls"] == 0 and pro_2k["live_calls"] == 0
    assert pro_2k["requests"][0]["reference_assets"] == []
    assert all(request["reference_assets"] == ["meadow_wide"] for request in pro_2k["requests"][1:])
    assert (
        len(
            {
                flash["generation_fingerprint"],
                pro_1k["generation_fingerprint"],
                pro_2k["generation_fingerprint"],
            }
        )
        == 3
    )


def test_pro_2k_set_is_immutable_and_comparable_with_flash(runtime, tmp_path):
    flash_provider = FakeProvider(model="gemini-3.1-flash-image", image_size="1K")
    flash_result = generate_set(runtime, confirmed=True, provider=flash_provider)
    flash_manifest = inspect_set(runtime, flash_result["manifest_artifact_id"])

    pro_config = runtime.model_copy(
        update={
            "environment_generation": EnvironmentGenerationConfig(
                model="gemini-3-pro-image", location="global", image_size="2K"
            )
        }
    )
    pro_provider = FakeProvider(model="gemini-3-pro-image", image_size="2K")
    pro_result = generate_set(pro_config, confirmed=True, provider=pro_provider)
    assert flash_provider.calls == 4 and pro_provider.calls == 4
    assert pro_result["manifest_artifact_id"] != flash_result["manifest_artifact_id"]
    assert pro_result["set_id"] != flash_result["set_id"]
    assert inspect_set(runtime, flash_result["manifest_artifact_id"]) == flash_manifest

    inspected = inspect_set(pro_config, pro_result["manifest_artifact_id"])
    environment = inspected["environment_set"]
    assert environment["provider"] == "fixture"
    assert environment["model"] == "gemini-3-pro-image"
    assert environment["requested_image_size"] == "2K"
    assert environment["location"] == "global"
    assert len(environment["generation_fingerprint"]) == 64
    assert all(plate["source_dimensions"] == [1536, 2752] for plate in environment["plates"])
    assert all(plate["source_artifact_id"] for plate in environment["plates"])
    assert inspected["review_status"] == "pending"

    output = comparison_sheet(
        pro_config,
        flash_result["manifest_artifact_id"],
        pro_result["manifest_artifact_id"],
        tmp_path / "comparison.png",
    )
    with Image.open(output) as comparison:
        assert comparison.size == (960, 3440)


def test_schema_one_flash_manifest_remains_readable():
    from tovitunes.render.environment_sets import EnvironmentSet

    historical = EnvironmentSet.model_validate(
        {
            "schema_version": 1,
            "set_id": "preschool-world-v1",
            "plates": [
                {
                    "role": role,
                    "artifact_id": f"historical-{role}",
                    "sha256": "a" * 64,
                    "provider": "google",
                    "model": "gemini-3.1-flash-image",
                    "local_request_id": f"request-{role}",
                    "generated_at": "2026-09-30T00:00:00Z",
                }
                for role in ROLES
            ],
        }
    )
    assert historical.schema_version == 1
    assert historical.model is None
    assert all(plate.model == "gemini-3.1-flash-image" for plate in historical.plates)


def test_ambiguous_environment_request_never_resends(runtime):
    provider = FakeProvider(ambiguous=True)
    with pytest.raises(ProviderFailure):
        generate_set(runtime, confirmed=True, provider=provider)
    assert provider.calls == 1
    with pytest.raises(ValueError, match="no automatic resend"):
        generate_set(runtime, confirmed=True, provider=provider)
    assert provider.calls == 1


def test_source_dimensions_survive_technical_validation_failure(runtime):
    class TooSmallProvider(FakeProvider):
        def generate(self, spec, reference_paths, *, on_remote_start=None):
            self.calls += 1
            if on_remote_start:
                on_remote_start()
            return ProviderResult(
                image_bytes=picture((MIN_SOURCE_SIZE[0] - 1, MIN_SOURCE_SIZE[1])),
                mime_type="image/png",
                provider_request_id="fixture-too-small",
                usage={"prompt_token_count": 3},
            )

    with pytest.raises(ValueError, match="too small"):
        generate_set(runtime, confirmed=True, provider=TooSmallProvider())
    with Database(runtime.database_path).connect() as conn:
        row = conn.execute(
            "SELECT status,response_metadata_json FROM environment_requests"
        ).fetchone()
    assert row["status"] == "terminal_failure"
    assert json.loads(row["response_metadata_json"]) == {
        "continuity_mode": "shared_text_world_contract",
        "reference_images_supported": True,
        "requested_image_size": "1K",
        "source_dimensions": [767, 1376],
        "technical_validation": "failed",
        "usage_metadata": {"prompt_token_count": 3},
    }


def test_character_like_plate_can_be_rejected_by_human(runtime):
    generated = generate_set(runtime, confirmed=True, provider=FakeProvider())
    artifact_id = generated["manifest_artifact_id"]
    decide_set(
        runtime,
        artifact_id,
        actor="human:fixture",
        reason="bird-like character visible in contact sheet",
        status="rejected",
    )
    assert inspect_set(runtime, artifact_id)["review_status"] == "rejected"
    with pytest.raises(ValueError, match="new generation attempt"):
        decide_set(
            runtime, artifact_id, actor="human:fixture", reason="changed mind", status="approved"
        )
    with pytest.raises(ValueError, match="approval"):
        select_set(runtime, artifact_id)


def test_missing_vertex_project_fails_before_request(runtime, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    legacy = runtime.model_copy(
        update={
            "environment_generation": EnvironmentGenerationConfig(
                provider="google", model="gemini-3.1-flash-image"
            )
        }
    )
    with pytest.raises(ProviderFailure, match="GOOGLE_CLOUD_PROJECT"):
        generate_set(legacy, confirmed=True)
    assert not runtime.database_path.exists()


def evidence():
    alignment = AudioAlignment(
        audio_master_artifact_id="audio",
        audio_sha256="a" * 64,
        source_blind_id="fixture",
        source_analysis_version=1,
        duration_seconds=6,
        words=({"text": "shape", "start": 0.5, "end": 1.0},),
        lyric_lines=({"text": "shape", "start": 0.5, "end": 1.0},),
        sections=({"text": "lesson", "start": 0.5, "end": 1.0},),
        pre_lyric={"start": 0, "end": 0.5},
        post_lyric={"start": 1.0, "end": 6},
    )
    beats = BeatAnalysis(
        audio_master_artifact_id="audio",
        audio_sha256="a" * 64,
        duration_seconds=6,
        source_analysis_version=1,
        estimated_bpm=120,
        beat_seconds=tuple(i / 2 for i in range(13)),
        downbeat_seconds=(0, 2, 4, 6),
        detector="fixture",
        detector_version="fixture",
        model_identity=None,
        model_revision=None,
    )
    return alignment, beats


def test_generic_drop_roll_compare_and_occupancy():
    definitions = {
        "generic_drop_object": PropDefinition(supports_drop=True, supports_bounce=True),
        "generic_roll_object": PropDefinition(motion_class="roll", supports_roll=True),
    }
    align, beats = evidence()
    drop = resolve_composition(
        CompositionRequest("drop", 0, 3, "present", ("generic_drop_object",)),
        prop_definitions=definitions,
    )
    drop_story = plan_story(drop, 3, definitions, set())
    assert drop_story.story_action == "drop_and_settle"
    drop = stage_composition(drop, drop_story)
    assert drop.props[0].width == 0.25
    drop_motion = plan_motion(drop, 0, 3, "story", beats, align, (), story=drop_story)
    assert drop_motion.ambient_tracks == ()
    drop_track = drop_motion.prop_tracks[0]
    assert [e.motion for e in drop_track.events[:2]] == ["fall_in", "bounce_settle"]
    assert prop_state(drop_track, drop_motion, 0)[1] < drop_track.bbox[1]
    assert prop_state(drop_track, drop_motion, 3)[1] == pytest.approx(drop_track.bbox[3])
    assert all(
        prop_state(drop_track, drop_motion, i / 10)[1] <= drop_track.bbox[3] for i in range(31)
    )
    scene = TimedScene(
        scene_id="drop",
        kind="lyric",
        section="lesson",
        start=0,
        end=3,
        tovi_action="present",
        visual_focus="falling object",
        required_props=("red_apple",),
        lesson_target="red",
        lyric_text="shape",
        lyric_start=0,
        lyric_end=1,
        beat_index_range=(0, 6),
        downbeat_index_range=(0, 2),
    )
    sprite = Image.new("RGBA", (915, 1209), "blue")
    animation = animation_plan(
        scene, sprite, "base", beats, (1080, 1920), "story", drop, height_limit=0.48
    )
    wide_pose = Image.new("RGBA", (325, 388), "blue")
    poses = tuple(
        pose_keyframe(cue, wide_pose, f"pose_{index}", animation.size[1])
        for index, cue in enumerate(drop_motion.character_pose_sequence)
    )
    animation = attach_poses(animation, poses, (1080, 1920))
    validate_motion(drop_motion, align, (), animation, (1080, 1920))

    roll = resolve_composition(
        CompositionRequest("roll", 0, 3, "point", ("generic_roll_object",)),
        prop_definitions=definitions,
    )
    roll_story = plan_story(roll, 3, definitions, {"generic_drop_object"})
    assert roll_story.story_action == "roll_through"
    roll_motion = plan_motion(roll, 0, 3, "story", beats, align, (), story=roll_story)
    roll_track = roll_motion.prop_tracks[0]
    assert roll_track.events[0].motion == "roll_in"
    assert roll_motion.camera_track.behavior == "gentle_pan_right"
    assert prop_state(roll_track, roll_motion, 1)[1] == pytest.approx(roll_track.bbox[3])

    compare = resolve_composition(
        CompositionRequest("compare", 0, 3, "question", tuple(definitions)),
        prop_definitions=definitions,
    )
    compare_story = plan_story(compare, 3, definitions, set(definitions))
    staged = stage_composition(compare, compare_story)
    assert compare_story.story_action == "compare"
    assert staged.character_slot == "lower_center"
    assert staged.props[0].center[0] < 0.5 < staged.props[1].center[0]
    assert staged.props[1].center[0] - staged.props[0].center[0] > 0.5
    assert (
        occupancy((0.35, 0.5, 0.65, 0.92), ((0.18, 0.32, 0.43, 0.62),))["dead_space_warning"]
        is None
    )
    assert (
        occupancy((0.35, 0.7, 0.65, 0.92), ())["dead_space_warning"]
        == "excessive_dead_visual_space"
    )


def test_performance_and_outro_form_a_staged_sequence():
    definitions = {
        "generic_swatch": PropDefinition(
            visual_class="abstract", grounded=False, supports_float=True
        ),
        "generic_drop_object": PropDefinition(supports_drop=True),
        "generic_roll_object": PropDefinition(motion_class="roll", supports_roll=True),
    }
    swatch = resolve_composition(
        CompositionRequest("reveal", 0, 3, "point", ("generic_swatch",)),
        prop_definitions=definitions,
    )
    reveal = plan_story(swatch, 3, definitions, set())
    assert reveal.story_action == "reveal"
    align, beats = evidence()
    revealed_motion = plan_motion(swatch, 0, 3, "story", beats, align, (), story=reveal)
    assert revealed_motion.prop_tracks[0].events[0].motion == "reveal"
    compare = resolve_composition(
        CompositionRequest(
            "compare", 3, 6, "question", ("generic_drop_object", "generic_roll_object")
        ),
        prop_definitions=definitions,
    )
    compared = plan_story(compare, 3, definitions, set(definitions))
    assert compared.story_action == "compare"
    performance = resolve_composition(
        CompositionRequest("performance", 6, 9, "sing", tuple(definitions)),
        prop_definitions=definitions,
    )
    performed = plan_story(performance, 3, definitions, set(definitions))
    staged = stage_composition(performance, performed)
    assert performed.story_action == "performance"
    assert performed.environment_plate_role == "celebration_meadow"
    assert staged.props[0].center[1] < staged.props[1].center[1]
    assert staged.props[1].center[0] < 0.5 < staged.props[2].center[0]
    assert tuple(prop.width for prop in staged.props[1:]) == (0.12, 0.12)
    compared_stage = stage_composition(compare, compared)
    compared_motion = plan_motion(
        compared_stage,
        0,
        3,
        "story",
        beats,
        align,
        (),
        story=compared,
        continuity_positions={
            "generic_drop_object": (0.20, 0.86),
            "generic_roll_object": (0.85, 0.86),
        },
    )
    assert [track.events[0].origin[0] for track in compared_motion.prop_tracks] == [-0.15, 1.15]
    positions = {
        track.prop_key: ((track.bbox[0] + track.bbox[2]) / 2, track.bbox[3])
        for track in compared_motion.prop_tracks
    }
    performed_motion = plan_motion(
        staged,
        0,
        3,
        "story",
        beats,
        align,
        (),
        story=performed,
        previous=compared_motion,
        continuity_positions=positions,
    )
    assert {accent.kind for accent in performed_motion.ambient_tracks} == {"note"}
    assert performed_motion.ambient_tracks[0].center[1] == 0.16
    assert performed_motion.camera_track.behavior == "slow_push_in"
    assert activity_diagnostics(performed_motion)["max_simultaneous_major_motion"] <= 3
    assert [
        track.events[0].origin[0] for track in performed_motion.prop_tracks[1:]
    ] == pytest.approx(
        [
            0.115,
            0.885,
        ],
        abs=1 / 1080,
    )
    for motion_plan in (compared_motion, performed_motion):
        for frame in range(31):
            t = frame / 10
            for track in motion_plan.prop_tracks:
                x, bottom, scale, _ = prop_state(track, motion_plan, t)
                width = (track.bbox[2] - track.bbox[0]) * scale
                height = (track.bbox[3] - track.bbox[1]) * scale
                box = (x - width / 2, bottom - height, x + width / 2, bottom)
                character = (0.265, 0.38, 0.735, 0.86)
                assert not (
                    box[0] < character[2]
                    and box[2] > character[0]
                    and box[1] < character[3]
                    and box[3] > character[1]
                )
    outro = resolve_composition(
        CompositionRequest("outro", 9, 17.85, "celebrate", kind="outro"),
        staged,
        prop_definitions=definitions,
        post_lyric_tail_seconds=8.85,
    )
    celebrated = plan_story(outro, 8.85, definitions, set(definitions), scene_kind="outro")
    staged_outro = stage_composition(outro, celebrated)
    assert celebrated.story_action == "celebrate"
    assert tuple(p.name for p in staged_outro.outro_phases) == ("celebrate", "recap", "settle")
    assert 2.5 <= staged_outro.outro_phases[0].end <= 3.0
    assert staged_outro.outro_phases[-1].end - staged_outro.outro_phases[-1].start == pytest.approx(
        1.8
    )


def test_real_performance_props_clear_character_envelope():
    _, beats = evidence()
    scene = TimedScene(
        scene_id="performance",
        kind="lyric",
        section="lesson",
        start=0,
        end=3,
        tovi_action="sing",
        visual_focus="performance",
        required_props=("red_swatch", "red_apple", "red_ball"),
        lesson_target="red",
        lyric_text="Red",
        lyric_start=0,
        lyric_end=1,
        beat_index_range=(0, 6),
        downbeat_index_range=(0, 2),
    )
    composition = resolve_composition(
        CompositionRequest(
            scene.scene_id,
            scene.start,
            scene.end,
            scene.tovi_action,
            scene.required_props,
            scene.kind,
        )
    )
    story = plan_story(composition, 3, PROP_DEFINITIONS, set(PROP_DEFINITIONS))
    composition = stage_composition(composition, story)
    sprite = Image.new("RGBA", (298, 373), "blue")
    animation = animation_plan(
        scene, sprite, "singing", beats, (1080, 1920), "story", composition, height_limit=0.48
    )
    _, metadata = scene_art(
        scene,
        {"belly_cream": "#FCEDB4"},
        (1080, 1920),
        "story",
        composition,
        background_only=True,
    )
    validate_props(scene, metadata, animation, (1080, 1920))


def test_tiny_drop_story_real_moviepy_ffmpeg(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("system FFmpeg required")
    pytest.importorskip("moviepy")
    align, beats = evidence()
    scene = TimedScene(
        scene_id="drop",
        kind="lyric",
        section="lesson",
        start=0,
        end=3,
        tovi_action="present",
        visual_focus="falling object",
        required_props=("red_apple",),
        lesson_target="red",
        lyric_text="shape",
        lyric_start=0,
        lyric_end=1.0,
        beat_index_range=(0, 6),
        downbeat_index_range=(0, 2),
    )
    comp = resolve_composition(CompositionRequest("drop", 0, 3, "present", ("red_apple",)))
    story = plan_story(
        comp, 3, {"red_apple": PropDefinition(supports_drop=True, supports_bounce=True)}, set()
    )
    comp = stage_composition(comp, story)
    motion = plan_motion(comp, 0, 3, "story", beats, align, (), canvas=(270, 480), story=story)
    _, metadata = scene_art(
        scene, {"belly_cream": "#FCEDB4"}, (270, 480), "story", comp, background_only=True
    )
    background = tmp_path / "fixture_environment.png"
    Image.open(io.BytesIO(picture())).resize((270, 480)).save(background)
    sprite = Image.new("RGBA", (50, 80))
    ImageDraw.Draw(sprite).ellipse((5, 5, 45, 75), fill="blue")
    initial = animation_plan(
        scene, sprite, "initial", beats, (270, 480), "story", comp, height_limit=0.46
    )
    paths = {}
    poses = []
    for index, cue in enumerate(motion.character_pose_sequence):
        pose = sprite.copy()
        ImageDraw.Draw(pose).ellipse((15, 15, 35, 35), fill="white" if index == 0 else "orange")
        identity = f"pose_{index}"
        path = tmp_path / f"{identity}.png"
        pose.save(path)
        paths[identity] = str(path)
        poses.append(pose_keyframe(cue, pose, identity, initial.size[1]))
    animation = attach_poses(initial, tuple(poses), (270, 480))
    assert len(poses) > 1
    assert motion.camera_track.behavior == "focus_push"
    payload = {
        "start": 0,
        "end": 3,
        "canvas": [270, 480],
        "background_path": str(background),
        "sprite_paths": paths,
        "animation": animation.model_dump(mode="json"),
        "motion": motion.model_dump(mode="json"),
        "metadata": metadata,
    }
    with ExitStack() as stack:
        clip = build_scene(payload, stack)
        assert clip.get_frame(0.1).shape == (480, 270, 3)
        assert (clip.get_frame(0.1) != clip.get_frame(2.8)).any()
    from tovitunes.render.ffmpeg import doctor

    request = tmp_path / "worker.json"
    video = tmp_path / "drop.mp4"
    request.write_text(
        json.dumps(
            {"scenes": [payload], "video_path": str(video), "ffmpeg_path": doctor()["ffmpeg_path"]}
        ),
        encoding="utf-8",
    )
    encode_worker(request)
    assert video.is_file() and video.stat().st_size > 0
