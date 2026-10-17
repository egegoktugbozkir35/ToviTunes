"""Explicit cue routing, immutable history and phone-size composition regressions."""

import json
from contextlib import closing
from hashlib import sha256
from types import SimpleNamespace

import pytest
from PIL import Image
from test_lesson_objects import FakeReviewedQwen
from test_qwen_art_quality import episode as episode
from test_visual_story import evidence
from test_visual_story import runtime as runtime

from tovitunes.artifacts.store import AssetStore
from tovitunes.creative.provider import structured_json_instruction
from tovitunes.domain.storyboard import TimedScene
from tovitunes.domain.visual_draft import (
    CreativeObject,
    CreativeVisualDraft,
    compile_visual_draft,
    parse_visual_plan,
    validate_compiled_visual,
)
from tovitunes.domain.visual_plan import EpisodeVisualPlan, VisualRequirement
from tovitunes.persistence.db import Database
from tovitunes.persistence.requests import RequestLedger
from tovitunes.render import episode_assets
from tovitunes.render.character import animation_plan
from tovitunes.render.composition import (
    EPISODE_ASSET_STYLE_VERSION,
    CompositionRequest,
    PropDefinition,
    resolve_composition,
    validate_composition,
)
from tovitunes.render.episode_assets import generate_assets, persist_file
from tovitunes.render.motion import plan_motion, validate_motion
from tovitunes.render.props import dotted_circle_brushing, prop_image

CUE = "dotted_circle_brushing_v1"
DESCRIPTION = (
    "A floating 2D dotted circle swirling around the mirror, shown as an illustration overlay"
)


def draft(cue=None):
    return CreativeVisualDraft(scenes=({
        "description": "Tovi presents the round-and-round brushing path.",
        "objects": [{"name": "circle swirl", "description": DESCRIPTION,
                     **({"teaching_cue": cue} if cue else {})}],
    },))


def requirement(**changes):
    return VisualRequirement.model_validate({
        "asset_key": "path", "kind": "scene_element", "display_name": "brushing path",
        "semantic_label": "brushing path", "description": DESCRIPTION,
        "educational_role": "supporting", "teaching_cue": CUE, "grounded": False,
        **changes,
    })


@pytest.fixture
def setup(runtime, episode, tmp_path):
    store = AssetStore(runtime.data_root, Database(runtime.database_path),
                       generated_source_roots=[tmp_path], local_preview=True)
    path = tmp_path / "plan.json"
    path.write_text("{}")
    plan = persist_file(store, episode, "episode_visual_plan", "main", path, ())
    return store, plan.identity.artifact_id


def test_absent_field_keeps_historical_compiler_keys_and_json(episode):
    obj = CreativeObject(name="circle swirl", description=DESCRIPTION)
    assert obj.model_dump_json() == json.dumps(
        {"name": "circle swirl", "description": DESCRIPTION}, separators=(",", ":")
    )
    plan = compile_visual_draft(draft(), episode, ("s", "l", "m"), "raw", "b" * 64)
    asset = plan.required_assets[0]
    assert asset.asset_key == "prop_4afa8ebb34209b2a"
    assert sha256(asset.model_dump_json().encode()).hexdigest() == (
        "b2bd8e9815e6ff0dc68efbc3456822b2d05485802e3c4194d82b65dcd1fa8ebc"
    )
    assert "teaching_cue" not in plan.model_dump_json()
    assert asset.grounded and asset.teaching_cue is None


def test_explicit_cue_compiles_with_distinct_identity_and_roundtrips(episode):
    old = compile_visual_draft(draft(), episode, ("s", "l", "m"), "raw", "b" * 64)
    plan = compile_visual_draft(draft(CUE), episode, ("s", "l", "m"), "raw", "b" * 64)
    assert plan.required_assets[0].asset_key != old.required_assets[0].asset_key
    assert plan.required_assets[0].teaching_cue == CUE
    assert not plan.required_assets[0].grounded
    assert parse_visual_plan(plan.model_dump(mode="json")) == plan
    validate_compiled_visual(plan, episode, ("s", "l", "m"))


@pytest.mark.parametrize("model,schema_hash,instruction_hash", [
    (CreativeVisualDraft,
     "81f3add2bc0496056f4a2ebfe762cc3fd64de10126dec15909c352440092113a",
     "48fd4705c0fbb4bf8f4ad54973cb4d9ebcdfd0ce166c9a8982f82f972f0a94e8"),
    (EpisodeVisualPlan,
     "87a0cfda303ff74ee5f7a9132f12702caeab0fb0dac4d834b2220de6ae78dbcb",
     "03431e0959e915483fbc35b2091fc60b2d15aad983bcc6c786b262200cea4da3"),
])
def test_historical_provider_schema_and_request_instruction_are_frozen(
    model, schema_hash, instruction_hash,
):
    schema = model.model_json_schema()
    assert sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest() == schema_hash
    instruction = structured_json_instruction(schema)["content"]
    assert sha256(instruction.encode()).hexdigest() == instruction_hash
    assert "teaching_cue" not in instruction


@pytest.mark.parametrize("changes", [
    {"teaching_cue": "circle"}, {"kind": "lesson_object"}, {"allow_face": True},
    {"grounded": True}, {"target_color": "white"}, {"motion": "roll"},
    {"motion": "float"}, {"educational_role": "decorative"},
])
def test_unknown_or_conflicting_cue_contract_fails(changes):
    with pytest.raises(ValueError):
        requirement(**changes)


def test_cue_never_loads_provider_or_normalizer_and_reuses_selected(
    runtime, episode, setup, tmp_path, monkeypatch,
):
    def forbidden(*args, **kwargs):
        pytest.fail("deterministic cue reached provider or extraction")
    monkeypatch.setattr(episode_assets, "_provider", forbidden)
    monkeypatch.setattr(episode_assets, "_prepare_qwen_white_background", forbidden)
    store, plan_id = setup
    args = (runtime, store, episode, SimpleNamespace(required_assets=(requirement(),)),
            plan_id, tmp_path, "Preschool art")
    first = generate_assets(*args, assert_owner=lambda: None)
    data = store.path_for(first["path"]).read_bytes()
    assert generate_assets(*args, assert_owner=lambda: None) == first
    assert store.path_for(first["path"]).read_bytes() == data
    record = store.get(first["path"])
    assert record.provenance.source_kind == "deterministic"
    assert record.provenance.model == CUE
    with closing(store.database.connect()) as conn:
        assert conn.execute("SELECT count(*) FROM generation_requests").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM production_image_receipts").fetchone()[0] == 0
    # Even an existing illustrated selection is reused, never redrawn.
    illustrated = SimpleNamespace(required_assets=(requirement(teaching_cue=None),))
    assert generate_assets(runtime, store, episode, illustrated, plan_id, tmp_path, "art",
                           assert_owner=lambda: None) == first


@pytest.mark.parametrize("status", [
    "prepared", "remote_started", "ambiguous", "failed", "succeeded",
])
def test_cue_cannot_bypass_any_historical_request(runtime, episode, setup, tmp_path, status):
    store, plan_id = setup
    request = RequestLedger(store.database).prepare(
        episode.episode_id, "visual_asset", "path", "qwen_comfyui", "historical", "a" * 64
    )
    with closing(store.database.connect()) as conn, conn:
        conn.execute("UPDATE generation_requests SET status=? WHERE request_id=?",
                     (status, request.request_id))
        before = tuple(conn.execute("SELECT * FROM generation_requests").fetchone())
    with pytest.raises(ValueError, match="explicit reconciliation"):
        generate_assets(runtime, store, episode, SimpleNamespace(required_assets=(requirement(),)),
                        plan_id, tmp_path, "art", assert_owner=lambda: None)
    assert not (tmp_path / "path.png").exists()
    with closing(store.database.connect()) as conn:
        assert tuple(conn.execute("SELECT * FROM generation_requests").fetchone()) == before
        assert conn.execute("SELECT count(*) FROM production_image_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("name,description", [
    ("circle swirl", DESCRIPTION),
    ("dotted circle brushing path", "A round pale decorated disc with a face"),
    ("toothbrush", "A real illustrated toothbrush"),
])
def test_free_text_always_uses_qwen_without_explicit_cue(
    runtime, episode, setup, tmp_path, name, description,
):
    class Provider(FakeReviewedQwen):
        def translate(self, spec):
            return spec
    provider = Provider()
    store, plan_id = setup
    asset = requirement(teaching_cue=None, display_name=name, semantic_label=name,
                        description=description)
    generate_assets(runtime, store, episode, SimpleNamespace(required_assets=(asset,)),
                    plan_id, tmp_path, "art", provider, assert_owner=lambda: None)
    assert provider.calls == ["path"]


def test_existing_illustration_selection_and_receipts_are_unchanged(
    runtime, episode, setup, tmp_path,
):
    class Provider(FakeReviewedQwen):
        def translate(self, spec):
            return spec
    provider = Provider()
    store, plan_id = setup
    args = (runtime, store, episode)
    selected = generate_assets(
        *args, SimpleNamespace(required_assets=(requirement(teaching_cue=None),)),
        plan_id, tmp_path, "art", provider, assert_owner=lambda: None,
    )
    data = store.path_for(selected["path"]).read_bytes()
    with closing(store.database.connect()) as conn:
        before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]
                  for t in ("generation_requests", "production_image_receipts")}
    assert generate_assets(*args, SimpleNamespace(required_assets=(requirement(),)), plan_id,
                           tmp_path, "art", provider, assert_owner=lambda: None) == selected
    assert provider.calls == ["path"]
    assert store.path_for(selected["path"]).read_bytes() == data
    with closing(store.database.connect()) as conn:
        assert before == {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]
                          for t in before}
    different = tmp_path / "new-plan.json"
    different.write_text('{"changed":true}')
    other = persist_file(store, episode, "episode_visual_plan", "main", different, ())
    with pytest.raises(ValueError, match="dependency is no longer selected"):
        generate_assets(*args, SimpleNamespace(required_assets=(requirement(),)),
                        other.identity.artifact_id, tmp_path, "art", provider,
                        assert_owner=lambda: None)


def test_cue_fits_existing_mobile_composition_and_motion(tmp_path):
    image = dotted_circle_brushing()
    path = tmp_path / "cue.png"
    image.save(path)
    mobile = prop_image("path", 108, reviewed_asset_path=path,
                        style_version=EPISODE_ASSET_STYLE_VERSION)
    alpha = mobile.getchannel("A")
    assert mobile.mode == "RGBA" and mobile.size == (108, 108)
    assert alpha.crop((33, 33, 75, 75)).getextrema() == (0, 0)  # Hollow, never a disc.
    assert alpha.getpixel((0, 0)) == 0
    assert sum(a >= 240 for a in alpha.getdata()) > 250  # Visible ink at phone size.
    assert dotted_circle_brushing(108).tobytes() == dotted_circle_brushing(108).tobytes()
    definitions = {"path": PropDefinition(visual_class="abstract", grounded=False,
                                         render_strategy="reviewed_asset")}
    req = CompositionRequest("cue", 0, 6, "point", ("path",))
    comp = resolve_composition(req, prop_definitions=definitions)
    validate_composition(req, comp)
    x0, y0, x1, y1 = comp.props[0].bbox((360, 640), comp.ground_plane_y)
    assert 14 <= x0 < x1 <= 346 and 25 <= y0 < y1 <= 615
    assert comp.props[0].placement_mode == "display" and not comp.props[0].grounded
    alignment, beats = evidence()
    motion = plan_motion(comp, 0, 6, "cue", beats, alignment, ())
    scene = TimedScene(scene_id="cue", start=0, end=6, kind="lyric", tovi_action="point",
                       visual_focus="brushing path", required_props=("path",), section="lesson",
                       beat_index_range=(0, 12), downbeat_index_range=(0, 3),
                       lyric_text="round and round", lyric_start=0, lyric_end=6,
                       lesson_target="brushing path")
    animation = animation_plan(scene, Image.new("RGBA", (200, 300), "gold"), "sprite",
                               beats, (360, 640), "plan", comp)
    validate_motion(motion, alignment, (), animation, (360, 640))


def test_invalid_cue_size():
    with pytest.raises(ValueError):
        dotted_circle_brushing(0)
