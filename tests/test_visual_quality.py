"""The mixed conceptual recap must not assign tangible props to a sky surface."""

from tovitunes.render.composition import CompositionRequest, PropDefinition, resolve_composition
from tovitunes.render.story import stage_visual_quality


def test_mixed_display_surface_and_farewell_continuity():
    definitions = {key: PropDefinition() for key in ("banana", "sun", "duck")}
    request = CompositionRequest("scene", 0, 4, "celebrate", tuple(definitions))
    composition = resolve_composition(request, prop_definitions=definitions)
    mixed = stage_visual_quality(composition, {"sun"})
    assert [(p.placement_mode, p.surface_y) for p in mixed.props] == [
        ("ground", 0.86), ("display", None), ("ground", 0.60)
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
    assert staged.character_slot == "lower_center"
    assert all(p.placement_mode == "ground" for p in staged.props)
