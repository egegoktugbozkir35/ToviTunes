"""Generic motion policy, evidence binding, envelopes and a tiny real dynamic encode."""

import json
import shutil
import wave
from contextlib import ExitStack

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from tovitunes.domain.storyboard import AudioAlignment, BeatAnalysis, TimedScene
from tovitunes.render.character import (
    animation_plan,
    attach_poses,
    pose_keyframe,
    pose_position,
    position,
)
from tovitunes.render.composer import build_scene, encode_worker
from tovitunes.render.composition import (
    CompositionRequest,
    PropDefinition,
    resolve_composition,
)
from tovitunes.render.environment import ambient_sprite, background_plate
from tovitunes.render.ffmpeg import doctor, mux_command, run_process
from tovitunes.render.models import CharacterAnimation, PoseKeyframe
from tovitunes.render.motion import (
    ActivityEvent,
    CameraTrack,
    MotionEvent,
    SceneMotionPlan,
    activity_diagnostics,
    camera_box,
    camera_state,
    plan_motion,
    prop_state,
    validate_motion,
)
from tovitunes.render.props import scene_art
from tovitunes.render.qa import media_qa


@pytest.fixture
def evidence():
    alignment = AudioAlignment(
        audio_master_artifact_id="audio",
        audio_sha256="a" * 64,
        source_blind_id="synthetic",
        source_analysis_version=1,
        duration_seconds=8,
        words=tuple(
            {"text": word, "start": start, "end": end}
            for word, start, end in (
                ("Blue!", 0.2, 0.6),
                ("kite", 1, 1.3),
                ("blue", 2, 2.4),
                ("blue", 5, 5.5),
            )
        ),
        lyric_lines=({"text": "Blue! kite blue blue", "start": 0.2, "end": 5.5},),
        sections=({"text": "lesson", "start": 0.2, "end": 5.5},),
        pre_lyric={"start": 0, "end": 0.2},
        post_lyric={"start": 5.5, "end": 8},
    )
    beats = BeatAnalysis(
        audio_master_artifact_id="audio",
        audio_sha256="a" * 64,
        duration_seconds=8,
        source_analysis_version=1,
        estimated_bpm=120,
        beat_seconds=tuple(i / 2 for i in range(17)),
        downbeat_seconds=(0, 2, 4, 6, 8),
        detector="fixture",
        detector_version="fixture",
        model_identity=None,
        model_revision=None,
    )
    return alignment, beats


def composition(action="point", duration=6, count=1, previous=None, start=0, rolling=False):
    definitions = {
        f"shape_{i}": PropDefinition(grounded=rolling, motion_class="roll" if rolling else "static")
        for i in range(count)
    }
    return resolve_composition(
        CompositionRequest("generic", start, start + duration, action, tuple(definitions)),
        previous,
        prop_definitions=definitions,
    )


def motion(evidence, comp=None, **kwargs):
    alignment, beats = evidence
    return plan_motion(comp or composition(), 0, 6, "story", beats, alignment, ("blue",), **kwargs)


def animation():
    return CharacterAnimation(
        storyboard_artifact_id="story",
        scene_id="generic",
        sprite_role="sprite/pointing",
        sprite_artifact_id="pose",
        crop_bbox=(0, 0, 40, 70),
        visible_alpha_pixels=2800,
        scale=10,
        size=(400, 700),
        start_position=(100, 1066),
        end_position=(100, 1066),
        motion_type="point",
        amplitude=10,
        enter_seconds=0.5,
        beat_indices=(0, 0),
        downbeat_indices=(0, 0),
        beat_seconds=(),
        downbeat_seconds=(),
        duration_seconds=6,
        motion_duration_seconds=6,
    )


def test_plan_deterministic_and_json_roundtrip(evidence):
    first, second = motion(evidence), motion(evidence)
    assert "visual_story_plan_artifact_id" not in first.model_dump(mode="json")
    assert first == second and first.stable_hash() == second.stable_hash()
    assert SceneMotionPlan.model_validate_json(first.model_dump_json()) == first
    changed = plan_motion(
        composition(), 0, 6, "different_story", evidence[1], evidence[0], ("blue",)
    )
    assert first.seed != changed.seed and first.stable_hash() != changed.stable_hash()
    assert activity_diagnostics(first)["meaningful_activity_event_count"] >= 2
    assert activity_diagnostics(first)["max_simultaneous_major_motion"] <= 3
    validate_motion(first, evidence[0], ("blue",), animation(), (1080, 1920))


@pytest.mark.parametrize("target", ["blue", "red"])
def test_keyword_occurrences_are_exact_and_generic(evidence, target):
    alignment, beats = evidence
    if target == "red":
        raw = alignment.model_dump()
        raw["words"] = tuple(
            {**w, "text": w["text"].replace("Blue", "Red").replace("blue", "red")}
            for w in raw["words"]
        )
        raw["lyric_lines"][0]["text"] = "Red! kite red red"
        alignment = AudioAlignment.model_validate(raw)
    plan = plan_motion(composition(), 0, 6, "story", beats, alignment, (target,))
    assert [e.word_index for e in plan.keyword_emphasis_events] == [0, 2, 3]
    assert [(e.word_start, e.word_end) for e in plan.keyword_emphasis_events] == [
        (0.2, 0.6),
        (2, 2.4),
        (5, 5.5),
    ]
    assert all(e.vocabulary_item == target for e in plan.keyword_emphasis_events)
    # Punctuation-only normalization never interprets a near spelling as an admitted target.
    assert not plan_motion(
        composition(), 0, 6, "story", beats, alignment, (target + "s",)
    ).keyword_emphasis_events


def test_keyword_qa_rejects_fabricated_and_missing_event(evidence):
    plan = motion(evidence)
    event = plan.keyword_emphasis_events[0].model_copy(update={"word_end": 0.7})
    bad = plan.model_copy(
        update={"keyword_emphasis_events": (event, *plan.keyword_emphasis_events[1:])}
    )
    with pytest.raises(ValueError, match="admitted target timing"):
        validate_motion(bad, evidence[0], ("blue",), animation(), (1080, 1920))
    bad = plan.model_copy(update={"keyword_emphasis_events": plan.keyword_emphasis_events[1:]})
    with pytest.raises(ValueError, match="occurrence coverage"):
        validate_motion(bad, evidence[0], ("blue",), animation(), (1080, 1920))


def test_stagnant_and_hyperactive_rejected(evidence):
    plan = motion(evidence)
    with pytest.raises(ValueError, match="stagnant"):
        validate_motion(
            plan.model_copy(update={"activity_events": ()}),
            evidence[0],
            ("blue",),
            animation(),
            (1080, 1920),
        )
    extra = ActivityEvent(start=0, end=6, channel="foreground", reason="large_effect")
    # Force four distinct major tracks to overlap; coordinated small decor is excluded.
    activities = tuple(
        ActivityEvent(start=0, end=6, channel=c, reason="test")
        for c in ("camera", "character", "lesson")
    ) + (extra,)
    with pytest.raises(ValueError, match="budget"):
        validate_motion(
            plan.model_copy(update={"activity_events": activities}),
            evidence[0],
            ("blue",),
            animation(),
            (1080, 1920),
        )


def test_micro_inherits_camera_pose_environment_without_entry(evidence):
    comp = composition(action="sing")
    prior = motion(evidence, comp)
    micro_comp = composition(action="celebrate", duration=0.24, previous=comp, start=6)
    micro = plan_motion(
        micro_comp,
        6,
        0.24,
        "story",
        evidence[1],
        evidence[0],
        ("blue",),
        previous=prior,
        previous_artifact_id="motion_1",
    )
    assert micro.micro_scene and micro.scene_entry_effect == "none"
    assert micro.character_pose_sequence == prior.character_pose_sequence
    assert micro.camera_track == prior.camera_track
    assert micro.ambient_tracks == prior.ambient_tracks
    assert micro.clock_offset == 6 and micro.inherited_motion_artifact_id == "motion_1"
    assert not micro.activity_events and not any(p.events for p in micro.prop_tracks)
    assert micro.prop_tracks[0].bbox == prior.prop_tracks[0].bbox


def test_pose_normalization_anchor_and_approved_roles(evidence):
    cues = motion(evidence).character_pose_sequence
    small = Image.new("RGBA", (50, 80))
    ImageDraw.Draw(small).rectangle((10, 5, 40, 75), fill="blue")
    wide = Image.new("RGBA", (90, 100))
    ImageDraw.Draw(wide).rectangle((4, 12, 85, 90), fill="blue")
    poses = tuple(
        pose_keyframe(cue, wide if i else small, f"asset_{i}", 700) for i, cue in enumerate(cues)
    )
    plan = attach_poses(animation(), poses, (1080, 1920))
    for pose in poses:
        x, y = pose_position(plan, pose, 2)
        assert x + pose.size[0] / 2 == pytest.approx(plan.end_position[0] + plan.size[0] / 2)
        assert y + pose.size[1] == pytest.approx(position(plan, 2)[1] + plan.size[1])
        assert pose.size[1] == 700 and 0.08 <= pose.transition_seconds <= 0.16
        assert pose.sprite_artifact_id.startswith("asset_")
    raw = poses[0].model_dump()
    for key, value in (
        ("sprite_role", "sprite/mouth"),
        ("transition_seconds", 0.3),
        ("size", (999, 700)),
    ):
        with pytest.raises(ValidationError):
            PoseKeyframe.model_validate({**raw, key: value})
    with pytest.raises(ValueError, match="visible alpha"):
        pose_keyframe(cues[0], Image.new("RGBA", (50, 80)), "bad", 700)


@pytest.mark.parametrize(
    "behavior,z0,z1,p0,p1",
    [
        ("static", 1.02, 1.02, (0, 0), (0, 0)),
        ("slow_push_in", 1.02, 1.05, (0, 0), (0, 0)),
        ("slow_pull_out", 1.05, 1.02, (0, 0), (0, 0)),
        ("focus_push", 1.02, 1.04, (0, 0), (0.008, 0.008)),
        ("gentle_pan_left", 1.02, 1.02, (0.008, 0), (-0.008, 0)),
        ("gentle_pan_right", 1.02, 1.02, (-0.008, 0), (0.008, 0)),
    ],
)
def test_camera_tracks_overscan_and_focus(behavior, z0, z1, p0, p1):
    track = CameraTrack(
        behavior=behavior, duration=6, zoom_start=z0, zoom_end=z1, pan_start=p0, pan_end=p1
    )
    for i in range(61):
        z, x, y = camera_state(track, i / 10)
        assert 1 <= z <= 1.06 and abs(x) <= (z - 1) / 2 + 1e-9 and abs(y) <= (z - 1) / 2 + 1e-9
        x0, y0, x1, y1 = camera_box((0.1, 0.1, 0.9, 0.9), track, i / 10)
        assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    with pytest.raises(ValidationError, match="borders"):
        CameraTrack(behavior="focus_push", duration=6, zoom_start=1, pan_end=(0.02, 0))


@pytest.mark.parametrize(
    "grammar", ["pop_in", "gentle_bounce", "pulse", "wiggle", "roll_in", "float_in", "settle"]
)
def test_prop_grammar_easing_bounds_and_ground(evidence, grammar):
    plan = motion(evidence, composition(rolling=True))
    track = plan.prop_tracks[0].model_copy(
        update={
            "events": (MotionEvent(start=0, end=0.9, motion=grammar, timing_source="scene_entry"),)
        }
    )
    plan = plan.model_copy(update={"keyword_emphasis_events": (), "prop_tracks": (track,)})
    states = [prop_state(track, plan, i * 0.01) for i in range(91)]
    final = prop_state(track, plan, 1)
    assert final == pytest.approx(((track.bbox[0] + track.bbox[2]) / 2, track.bbox[3], 1, 0))
    assert max(s[2] for s in states) <= 1.09 and max(abs(s[3]) for s in states) <= 4
    if grammar == "roll_in":
        assert all(s[1] == track.bbox[3] for s in states)
        assert states[0][0] > 1 and all(a[0] >= b[0] for a, b in zip(states, states[1:]))
    assert any(s != final for s in states)


@pytest.mark.parametrize("variant", ["wide", "lesson_focus", "performance", "celebration"])
def test_environment_deterministic_and_transparent_decor(variant):
    assert (
        background_plate((270, 480), variant).tobytes()
        == background_plate((270, 480), variant).tobytes()
    )
    for kind in ("cloud", "flower", "leaf", "note", "sparkle"):
        image = ambient_sprite(kind, 64)
        assert image.mode == "RGBA" and image.getchannel("A").getextrema()[0] == 0
        assert image.getchannel("A").getbbox()


@pytest.mark.parametrize("damage", ["camera", "prop", "foreground"])
def test_motion_envelope_fails_closed(evidence, damage):
    plan = motion(evidence)
    if damage == "camera":
        # model_copy bypasses construction: QA still independently catches exposed borders.
        camera = plan.camera_track.model_copy(update={"pan_end": (0.2, 0)})
        plan = plan.model_copy(update={"camera_track": camera})
        message = "borders"
    elif damage == "prop":
        track = plan.prop_tracks[0].model_copy(update={"bbox": (0.95, 0.5, 1.1, 0.7)})
        plan = plan.model_copy(update={"prop_tracks": (track,)})
        message = "exceeds frame"
    else:
        accent = plan.ambient_tracks[-1].model_copy(update={"center": (0.28, 0.70)})
        plan = plan.model_copy(update={"ambient_tracks": (accent,)})
        message = "obscures lesson plane"
    with pytest.raises(ValueError, match=message):
        validate_motion(plan, evidence[0], ("blue",), animation(), (1080, 1920))


def test_measured_pose_cues_and_nonrhythmic_fallback(evidence):
    plan = motion(evidence)
    assert all(
        p.time in evidence[1].downbeat_seconds
        for p in plan.character_pose_sequence
        if p.timing_source == "measured_downbeat"
    )
    late = plan_motion(composition(), 9, 6, "story", evidence[1], evidence[0], ("blue",))
    assert all(p.timing_source == "scene_fraction" for p in late.character_pose_sequence[1:])
    assert [p.time for p in late.character_pose_sequence[1:]] == [2, 4]


def test_singing_keeps_approved_pose_and_outro_settles(evidence):
    comp = composition(action="sing", count=3)
    performance = motion(evidence, comp)
    assert len(performance.character_pose_sequence) == 1
    assert performance.character_pose_sequence[0].sprite_role == "sprite/singing"
    outro = resolve_composition(
        CompositionRequest("outro", 6, 14, "celebrate", kind="outro"),
        comp,
        post_lyric_tail_seconds=8,
        measured_downbeats=(6, 8),
    )
    plan = plan_motion(outro, 6, 8, "story", evidence[1], evidence[0], ("blue",))
    assert tuple(p.prop_key for p in plan.prop_tracks) == tuple(p.type for p in comp.props)
    assert [p[0] for p in plan.outro_phases] == ["celebrate", "recap", "settle"]
    assert plan.character_pose_sequence[-1].sprite_role == "sprite/neutral_full_body"
    assert plan.camera_track.hold_from == plan.settle_start
    assert camera_state(plan.camera_track, 7) == camera_state(plan.camera_track, 8)
    assert all(e.end <= plan.settle_start for p in plan.prop_tracks for e in p.events)
    assert not plan.keyword_emphasis_events


def test_background_plate_excludes_lesson_props(evidence):
    scene = TimedScene(
        scene_id="demo",
        kind="intro",
        section="demo",
        start=0,
        end=3,
        tovi_action="point",
        visual_focus="target",
        required_props=("red_swatch",),
        beat_index_range=(0, 6),
        downbeat_index_range=(0, 2),
    )
    comp = resolve_composition(CompositionRequest("demo", 0, 3, "point", ("red_swatch",)))
    plate, metadata = scene_art(
        scene,
        {"belly_cream": "#FCEDB4"},
        (270, 480),
        "story",
        comp,
        background_only=True,
        background_variant="lesson_focus",
    )
    assert plate.tobytes() == background_plate((270, 480), "lesson_focus").tobytes()
    assert len(metadata["props"]) == 1 and metadata["background_only"]
    assert len(metadata["environment_layers"]) == 5


def test_tiny_dynamic_real_moviepy_ffmpeg(tmp_path, evidence):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("system FFmpeg required")
    pytest.importorskip("moviepy")
    scene = TimedScene(
        scene_id="demo",
        kind="intro",
        section="demo",
        start=0,
        end=3,
        tovi_action="point",
        visual_focus="target",
        required_props=("red_swatch",),
        beat_index_range=(0, 6),
        downbeat_index_range=(0, 2),
    )
    comp = resolve_composition(CompositionRequest("demo", 0, 3, "point", ("red_swatch",)))
    plan = plan_motion(comp, 0, 3, "story", evidence[1], evidence[0], ("blue",), canvas=(270, 480))
    image, metadata = scene_art(
        scene,
        {"belly_cream": "#FCEDB4"},
        (270, 480),
        "story",
        comp,
        background_only=True,
        background_variant=plan.background_variant,
    )
    background = tmp_path / "background.png"
    image.save(background)
    sprite = Image.new("RGBA", (50, 80))
    ImageDraw.Draw(sprite).ellipse((5, 5, 45, 75), fill="blue")
    initial = animation_plan(
        scene, sprite, "initial", evidence[1], (270, 480), "story", comp, height_limit=0.38
    )
    paths, poses = {}, []
    for i, cue in enumerate(plan.character_pose_sequence):
        identity = f"pose_{i}"
        pose_sprite = sprite.copy()
        ImageDraw.Draw(pose_sprite).ellipse((15, 15, 35, 35), fill="orange" if i % 2 else "white")
        path = tmp_path / f"{identity}.png"
        pose_sprite.save(path)
        paths[identity] = str(path)
        poses.append(pose_keyframe(cue, pose_sprite, identity, initial.size[1]))
    anim = attach_poses(initial, tuple(poses), (270, 480))
    validate_motion(plan, evidence[0], ("blue",), anim, (270, 480))
    assert len(poses) > 1
    payload = {
        "start": 0,
        "end": 3,
        "canvas": [270, 480],
        "background_path": str(background),
        "sprite_paths": paths,
        "animation": anim.model_dump(mode="json"),
        "motion": plan.model_dump(mode="json"),
        "metadata": metadata,
    }
    with ExitStack() as stack:
        clip = build_scene(payload, stack)
        assert clip.get_frame(0.3).shape == (480, 270, 3)
        assert (clip.get_frame(0.3) != clip.get_frame(2.3)).any()
        assert clip.get_frame(2.3)[0].mean() > 100
        # The rendered prop must occupy its admitted lesson slot, including camera mapping.
        target = plan.prop_tracks[0]
        cx = (target.bbox[0] + target.bbox[2]) / 2
        cy = (target.bbox[1] + target.bbox[3]) / 2
        mapped = camera_box((cx, cy, cx, cy), plan.camera_track, 2.3)
        r, g, b = clip.get_frame(2.3)[round(mapped[1] * 480), round(mapped[0] * 270)]
        assert int(r) > 150 and int(r) > int(g) * 2 and int(r) > int(b) * 2
        r, g, b = clip.get_frame(2.3)[10, 10]
        assert b > r  # The sky corner must not contain the default-position lesson sprite.
    binaries = doctor()
    video = tmp_path / "video.mp4"
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {"scenes": [payload], "video_path": str(video), "ffmpeg_path": binaries["ffmpeg_path"]}
        )
    )
    encode_worker(request)
    audio_path = tmp_path / "audio.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 132300)
    final = tmp_path / "final.mp4"
    run_process(mux_command(video, audio_path, final, 3), 30)
    assert media_qa(final, 3, (270, 480))["passed"]
