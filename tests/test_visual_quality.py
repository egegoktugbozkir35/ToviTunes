"""The mixed conceptual recap must not assign tangible props to a sky surface."""

from tovitunes.render.composition import CompositionRequest, PropDefinition, resolve_composition
from tovitunes.render.story import stage_visual_quality


def test_mixed_display_surface_and_farewell_continuity():
    definitions = {key: PropDefinition() for key in ("banana", "sun", "duck")}
    request = CompositionRequest("scene", 0, 4, "celebrate", tuple(definitions))
    composition = resolve_composition(request, prop_definitions=definitions)
    mixed = stage_visual_quality(composition, {"sun"})
    assert [(p.placement_mode, p.surface_y) for p in mixed.props] == [
        ("ground", 0.76), ("display", None), ("ground", 0.52)
    ]
    display = stage_visual_quality(composition, set(definitions))
    farewell = stage_visual_quality(composition.model_copy(update={"resolved_action": "enter"}),
                                    {"sun"}, display)
    assert all(p.placement_mode == "display" for p in farewell.props)
    assert tuple((p.center, p.width) for p in farewell.props) == tuple(
        (p.center, p.width) for p in display.props
    )


def test_gathered_farewell_keeps_approved_greeting_in_place():
    definitions = {key: PropDefinition() for key in ("puppy", "kitten", "duckling")}
    raw = resolve_composition(CompositionRequest("scene", 0, 4, "enter", tuple(definitions)),
                              prop_definitions=definitions)
    staged = stage_visual_quality(raw, set())
    assert staged.resolved_action == "present"
    assert staged.sprite_role == "sprite/hello"
    assert staged.character_slot == "lower_left"
    assert all(p.placement_mode == "ground" for p in staged.props)


def test_visible_feet_ignore_faint_padding_without_changing_source():
    from PIL import Image, ImageDraw

    from tovitunes.render.layout import visible_alpha_bounds

    image = Image.new("RGBA", (200, 200), (20, 30, 40, 2))
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 40, 130, 145), fill=(50, 100, 200, 255))
    draw.point((4, 198), fill=(0, 0, 0, 255))
    before = image.tobytes()
    b = visible_alpha_bounds(image)
    assert b == (70, 40, 131, 146)
    assert image.tobytes() == before
    definitions = {"animal": PropDefinition()}
    raw = resolve_composition(CompositionRequest("scene", 0, 4, "present", ("animal",)),
                              prop_definitions=definitions)
    staged = stage_visual_quality(raw, set(), visible_bounds={
        "animal": tuple(v / 200 for v in b)
    })
    prop = staged.props[0]
    box = prop.bbox((1080, 1920), staged.ground_plane_y)
    visible_foot = box[1] + b[3] / 200 * (box[3] - box[1])
    assert abs(visible_foot - 0.76 * 1920) < 1
    assert box[3] < 1920 * 0.96  # Complete source still fits the checked envelope.
