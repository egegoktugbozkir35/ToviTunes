"""Curriculum-independent staging, evidence-bound movement, and shared prop style."""

from dataclasses import replace
from hashlib import sha256

import pytest
from PIL import Image, ImageDraw

from tovitunes.render.character import animation_plan, position
from tovitunes.render.composer import prop_position
from tovitunes.render.composition import (
    GROUND_PLANE_Y,
    MIN_DISTINCT_VISUAL_SCENE_SECONDS,
    PROP_STYLE_VERSION,
    SLOTS,
    ActionMetadata,
    CompositionRequest,
    PropDefinition,
    resolve_composition,
    validate_composition,
)
from tovitunes.render.props import prop_image, sphere_shading

GENERIC = {
    "generic_primary_object": PropDefinition(color="#6A50C4"),
    "generic_secondary_object": PropDefinition(color="#F3B541"),
    "generic_rolling_object": PropDefinition(motion_class="roll", color="#43AABF"),
    "generic_abstract_target": PropDefinition("abstract", False, color="#A757A2"),
}


def resolve(request, previous=None, **kwargs):
    return resolve_composition(request, previous, prop_definitions=GENERIC, **kwargs)


@pytest.mark.parametrize("direction", ["left", "right"])
@pytest.mark.parametrize("target", list(GENERIC))
def test_directional_point_has_spatially_coherent_target(direction, target):
    request = CompositionRequest("unrelated_scene", 0, 4, "point", (target,))
    plan = resolve(request, action_metadata=ActionMetadata("sprite/pointing", direction))
    validate_composition(request, plan)
    char_x = SLOTS[plan.character_slot][0]
    target_x = plan.props[0].center[0]
    assert (char_x < target_x) if direction == "right" else (target_x < char_x)
    wrong = plan.model_copy(
        update={"character_facing": "left" if direction == "right" else "right"}
    )
    with pytest.raises(ValueError, match="gesture direction"):
        validate_composition(request, wrong)


def test_present_alternates_for_new_targets_and_retains_existing_target():
    first = resolve(CompositionRequest("a", 0, 4, "present", ("generic_primary_object",)))
    again = resolve(CompositionRequest("b", 4, 8, "present", ("generic_primary_object",)), first)
    other = resolve(CompositionRequest("c", 8, 12, "present", ("generic_secondary_object",)), again)
    assert first.character_slot == again.character_slot
    assert first.props[0].bbox((1080, 1920), GROUND_PLANE_Y) == again.props[0].bbox(
        (1080, 1920), GROUND_PLANE_Y
    )
    assert first.character_slot != other.character_slot
    assert again.visual_state_persistence == "modify"


def test_question_groups_generic_recall_targets_with_clear_character_separation():
    request = CompositionRequest("recall", 0, 4, "question", tuple(GENERIC)[:2])
    plan = resolve(request)
    validate_composition(request, plan)
    assert plan.character_slot == "lower_left"
    assert all(p.center[0] > SLOTS[plan.character_slot][0] for p in plan.props)
    assert plan.props[0].center[0] == plan.props[1].center[0]
    assert plan.props[0].width > plan.props[1].width
    assert (
        plan.props[0].bbox((1080, 1920), GROUND_PLANE_Y)[1]
        > plan.props[1].bbox((1080, 1920), GROUND_PLANE_Y)[3]
    )


def test_sing_has_primary_secondary_hierarchy_and_central_character():
    plan = resolve(CompositionRequest("song", 0, 4, "sing", tuple(GENERIC)[:3]))
    assert plan.character_slot == "lower_center"
    assert plan.character_width > max(p.width for p in plan.props)
    assert plan.props[0].width > plan.props[1].width == plan.props[2].width
    assert len(set(p.slot for p in plan.props)) == 3


def test_micro_emphasis_and_following_tail_inherit_same_state_without_retiming():
    normal = CompositionRequest("song", 0, 4, "sing", tuple(GENERIC)[:2])
    micro = CompositionRequest("accent", 4, 4.24, "celebrate", ("generic_primary_object",))
    outro = CompositionRequest("close", 4.24, 12.24, "celebrate", kind="outro")
    first = resolve(normal)
    accent = resolve(micro, first)
    close = resolve(outro, accent, post_lyric_tail_seconds=8, measured_downbeats=(4.5, 6.5, 8.5))
    validate_composition(micro, accent, first)
    validate_composition(outro, close, accent)
    assert first.visual_state_id == accent.visual_state_id == close.visual_state_id
    assert first.props == accent.props == close.props
    assert accent.sprite_role == first.sprite_role
    assert accent.emphasis == "gentle_pulse"
    assert accent.visual_origin_start == normal.start
    assert accent.visual_origin_duration == normal.end - normal.start
    assert close.visual_state_persistence == "inherit"
    assert (micro.start, micro.end, outro.start, outro.end) == (4, 4.24, 4.24, 12.24)
    bad = accent.model_copy(update={"background_variant": "unreadable_reset"})
    with pytest.raises(ValueError, match="micro scene"):
        validate_composition(micro, bad, first)


@pytest.mark.parametrize(
    "duration,micro", [(0.24, True), (0.59, True), (0.6, False), (0.75, False)]
)
def test_readability_threshold(duration, micro):
    assert MIN_DISTINCT_VISUAL_SCENE_SECONDS * 30 == 18
    first = resolve(CompositionRequest("a", 0, 4, "sing", ("generic_primary_object",)))
    plan = resolve(
        CompositionRequest("b", 4, 4 + duration, "celebrate", ("generic_primary_object",)), first
    )
    assert plan.micro_scene == micro
    assert bool(plan.emphasis) == micro


def test_explicit_reset_and_new_teaching_requirement_override_inheritance():
    first = resolve(CompositionRequest("a", 0, 4, "sing", ("generic_primary_object",)))
    reset = CompositionRequest("reset", 4, 4.24, "celebrate", explicit_visual_reset=True)
    blank = resolve(reset, first)
    validate_composition(reset, blank, first)
    assert not blank.props and blank.visual_state_persistence == "replace"
    new = resolve(CompositionRequest("new", 4, 4.24, "point", ("generic_secondary_object",)), first)
    assert new.visual_state_persistence == "replace"
    assert tuple(p.type for p in new.props) == ("generic_secondary_object",)


@pytest.mark.parametrize("downbeats", [(), (4.5, 6.5, 8.5)])
def test_eight_second_tail_has_real_internal_phases_and_no_fabricated_beats(downbeats):
    request = CompositionRequest("close", 4, 12, "celebrate", kind="outro")
    plan = resolve(request, post_lyric_tail_seconds=8, measured_downbeats=downbeats)
    validate_composition(request, plan)
    assert tuple(p.name for p in plan.outro_phases) == ("celebrate", "recap", "settle")
    assert plan.outro_phases[0].start == 0
    assert plan.outro_phases[-1].end == 8
    assert all(a.end == b.start for a, b in zip(plan.outro_phases, plan.outro_phases[1:]))
    assert all(p.end > p.start for p in plan.outro_phases)
    if not downbeats:
        assert plan.outro_phases[-1].start <= 0.8
    bad = plan.model_copy(update={"outro_phases": ()})
    with pytest.raises(ValueError, match="long outro"):
        validate_composition(request, bad)


def test_generic_rolling_object_contacts_ground_at_all_samples_and_finishes_at_target():
    request = CompositionRequest("arrival", 0, 4, "point", ("generic_rolling_object",))
    plan = resolve(request)
    validate_composition(request, plan)
    placement = plan.props[0]
    bbox = placement.bbox((1080, 1920), GROUND_PLANE_Y)
    prop = {**placement.model_dump(), "bbox": bbox, "ground_plane_y": round(1920 * GROUND_PLANE_Y)}
    points = [prop_position(t, prop, 1080) for t in (0, 0.1, 0.4, 0.7, 0.95, 2, 4)]
    assert points[0][0] == 1080
    assert points[-1] == tuple(bbox[:2])
    assert all(a[0] >= b[0] for a, b in zip(points, points[1:]))
    assert all(y + bbox[3] - bbox[1] == prop["ground_plane_y"] for _, y in points)
    assert bbox[1] > 1920 * 0.5
    bad = {**prop, "bbox": [bbox[0], 100, bbox[2], 100 + bbox[3] - bbox[1]]}
    with pytest.raises(ValueError, match="ground plane"):
        prop_position(0.4, bad, 1080)


def test_composition_repeat_diagnostic_and_stable_identity():
    a = CompositionRequest("a", 0, 4, "point", ("generic_abstract_target",))
    first = resolve(a)
    second = resolve(replace(a, scene_id="b", start=4, end=8), first)
    assert second.consecutive_identical_composition_count == 2
    assert resolve(a) == first
    assert resolve(replace(a, scene_id="unrelated_name")).visual_state_id == first.visual_state_id


@pytest.mark.parametrize("kind", ["red_swatch", "red_apple", "red_ball"])
def test_high_resolution_prop_style_is_deterministic_antialiased_and_shared(kind, monkeypatch):
    # Organic silhouettes must never fall back to a coarse polygon path.
    def no_polygon(*args, **kwargs):
        raise AssertionError("coarse polygon silhouette")

    monkeypatch.setattr(ImageDraw.ImageDraw, "polygon", no_polygon)
    image = prop_image(kind, 1080)
    second = prop_image(kind, 1080)
    assert sha256(image.tobytes()).digest() == sha256(second.tobytes()).digest()
    assert image.size == (1080, 1080)
    alpha = image.getchannel("A")
    assert len([i for i, count in enumerate(alpha.histogram()) if 0 < i < 255 and count]) > 100
    assert PROP_STYLE_VERSION == "preschool_soft_v1"
    assert alpha.getbbox()[3] == 1080


def test_animation_micro_boundary_retains_actual_pose_and_motion_phase():
    # Reuse the admitted boundary adapter with simple duck-typed scenes, no red curriculum.
    from types import SimpleNamespace

    from tovitunes.domain.storyboard import BeatAnalysis

    beats = BeatAnalysis(
        audio_master_artifact_id="audio",
        audio_sha256="a" * 64,
        duration_seconds=12,
        source_analysis_version=1,
        estimated_bpm=120,
        beat_seconds=(0.5, 2.5, 4.1, 4.5, 6.5),
        downbeat_seconds=(0.5, 4.5, 6.5),
        detector="fixture",
        detector_version="fixture",
        model_identity=None,
        model_revision=None,
    )
    sprite = Image.new("RGBA", (100, 160), "blue")
    req = CompositionRequest("a", 0, 4, "sing", ("generic_primary_object",))
    first = resolve(req)
    micro = resolve(CompositionRequest("b", 4, 4.24, "celebrate", req.required_props), first)

    def scene_for(request):
        return SimpleNamespace(
            scene_id=request.scene_id,
            start=request.start,
            end=request.end,
            tovi_action=request.action,
            required_props=request.required_props,
            kind=request.kind,
            beat_index_range=(0, 0),
            downbeat_index_range=(0, 0),
        )

    a = animation_plan(scene_for(req), sprite, "sprite", beats, (270, 480), "story", first)
    b = animation_plan(
        scene_for(CompositionRequest("b", 4, 4.24, "celebrate")),
        sprite,
        "sprite",
        beats,
        (270, 480),
        "story",
        micro,
    )
    assert position(a, 4) == position(b, 0)
    assert a.end_position == b.end_position and a.size == b.size
    close_req = CompositionRequest("close", 4.24, 12, "celebrate", kind="outro")
    close = resolve(
        close_req, micro, post_lyric_tail_seconds=7.76, measured_downbeats=beats.downbeat_seconds
    )
    closing = animation_plan(
        scene_for(close_req), sprite, "sprite", beats, (270, 480), "story", close
    )
    assert closing.downbeat_seconds == pytest.approx((0.26, 2.26))
    assert position(closing, 7.76) == closing.end_position
    assert len({position(closing, t / 30) for t in range(30)}) > 1


def test_shared_shading_has_no_horizontal_band_at_lower_third():
    surface = sphere_shading(Image.new("L", (600, 600), 255), "#E53935")
    values = [surface.getpixel((480, y))[:3] for y in range(375, 411)]
    assert all(max(abs(x - y) for x, y in zip(a, b)) <= 2 for a, b in zip(values, values[1:]))


def test_persistent_grounded_object_does_not_reenter_on_consecutive_point():
    first_request = CompositionRequest("a", 0, 4, "point", ("generic_rolling_object",))
    first = resolve(first_request)
    second = resolve(replace(first_request, scene_id="b", start=4, end=8), first)
    assert first.props[0].motion == "roll_in"
    assert second.props[0].motion == "static"
    assert first.props[0].center == second.props[0].center
    assert first.visual_state_id == second.visual_state_id


def test_left_facing_roll_enters_from_left_without_crossing_character_region():
    request = CompositionRequest("a", 0, 4, "point", ("generic_rolling_object",))
    plan = resolve(request, action_metadata=ActionMetadata("sprite/pointing", "left"))
    prop = plan.props[0]
    bbox = prop.bbox((1080, 1920), GROUND_PLANE_Y)
    metadata = {**prop.model_dump(), "bbox": bbox, "ground_plane_y": round(1920 * GROUND_PLANE_Y)}
    samples = [prop_position(t / 30, metadata, 1080) for t in range(31)]
    width = bbox[2] - bbox[0]
    assert samples[0][0] + width == 0
    assert samples[-1] == tuple(bbox[:2])
    char_left = 1080 * (SLOTS[plan.character_slot][0] - plan.character_width / 2)
    assert all(x + width < char_left for x, _ in samples)
