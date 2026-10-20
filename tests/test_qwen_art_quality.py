"""Source regressions for scene prompts and conservative exterior-only keying."""

import io
import json
from contextlib import closing
from hashlib import sha256

import pytest
from PIL import Image, ImageDraw
from test_lesson_objects import FakeReviewedQwen
from test_visual_story import FakeProvider
from test_visual_story import runtime as runtime

from tovitunes.artifacts.store import AssetStore
from tovitunes.domain.episode import Episode
from tovitunes.domain.visual_plan import VisualRequirement
from tovitunes.persistence.db import Database
from tovitunes.render.environment_sets import (
    EPISODE_PROMPT_VERSION,
    PROMPT_VERSION,
    ROLES,
    THEME,
    _generation_fingerprint,
    _spec,
    generate_set,
    inspect_set,
)
from tovitunes.render.episode_assets import (
    IMAGE_PROMPT,
    LEGACY_IMAGE_PROMPT,
    generate_assets,
    image_spec,
    persist_file,
)
from tovitunes.render.lesson_objects import (
    WHITE_BACKGROUND_V1,
    WHITE_BACKGROUND_V2,
    _prepare_qwen_white_background,
    normalize,
)


def png(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def extract(image, version=WHITE_BACKGROUND_V2):
    return _prepare_qwen_white_background(png(image), "image/png", version=version)


@pytest.fixture
def episode(runtime, catalog):
    db = Database(runtime.database_path)
    db.migrate()
    item = Episode.create(catalog, "red", "art-quality-test").model_copy(
        update={"episode_id": "episode"}
    )
    db.create_episode(catalog, item)
    return item


def test_historical_environment_identity_is_frozen():
    assert _spec("meadow_wide", "test-brand", 1, None).fingerprint() == (
        "ee3cacd7a07b45a532f843d6b438f6d69b9e69e07b10f82a53f146459bedde9b"
    )
    assert _generation_fingerprint(THEME, "qwen_comfyui", "test-model", "local", "1K") == (
        "4d350ce003a70bc63ef514de87f6eb7c53fb5214771c4d94bdcc894e352dc346"
    )


@pytest.mark.parametrize("setting", [
    "A bright cozy bathroom with a mirror above the sink",
    "A single outdoor farmyard with a barn, fences and an open staging area",
    "A sheltered underwater coral cove with a sandy open floor",
])
def test_episode_setting_has_one_authoritative_description(setting):
    for role in ROLES:
        spec = _spec(role, "brand", 1, None, scene_brief=setting)
        assert spec.prompt_version == EPISODE_PROMPT_VERSION
        assert spec.scene_brief == setting
        # Role identifiers are data; they must not become world instructions.
        assert "meadow" not in spec.prompt().lower()
        assert "sky/hills" not in spec.prompt()
        assert "horizon" not in spec.prompt()
        assert spec.references == () and spec.palette == {}
        assert "single viewpoint" in spec.prompt()
        assert len(spec.prompt()) < 1400


@pytest.mark.parametrize("status", ["prepared", "succeeded", "terminal_failure", "ambiguous"])
def test_prompt_version_change_cannot_silently_reissue_episode_environment(
    runtime, episode, status,
):
    db = Database(runtime.database_path)
    db.migrate()
    with closing(db.connect()) as conn, conn:
        conn.execute(
            "INSERT INTO environment_requests "
            "(request_id,set_id,role,attempt,fingerprint,provider,model,status,"
            "spec_json,created_at,updated_at,episode_id) VALUES "
            "('original','historical-set','meadow_wide',1,'old','fixture','old',?,"
            "'{}','2026-01-01','2026-01-01','episode')", (status,),
        )
        original = tuple(conn.execute("SELECT * FROM environment_requests").fetchone())
    provider = FakeProvider()
    with pytest.raises(ValueError, match="explicit reconciliation"):
        generate_set(
            runtime, confirmed=True, provider=provider, episode_id="episode",
            role_briefs=dict.fromkeys(ROLES, "A cozy indoor playroom"), assert_owner=lambda: None,
        )
    assert provider.calls == 0
    with closing(db.connect()) as conn:
        assert tuple(conn.execute("SELECT * FROM environment_requests").fetchone()) == original


def test_environment_receipts_pin_new_prompt_and_reuse_completed_sources(runtime, episode):
    provider = FakeProvider()
    kwargs = dict(
        confirmed=True, provider=provider, episode_id="episode",
        role_briefs=dict.fromkeys(ROLES, "A cozy indoor playroom"), assert_owner=lambda: None,
    )
    result = generate_set(runtime, **kwargs)
    manifest = inspect_set(runtime, result["manifest_artifact_id"])["environment_set"]
    assert manifest["prompt_version"] == EPISODE_PROMPT_VERSION
    with closing(Database(runtime.database_path).connect()) as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM environment_requests")]
        assert all(json.loads(r[0])["prompt_version"] == EPISODE_PROMPT_VERSION for r in
                   conn.execute("SELECT spec_json FROM environment_requests"))
    assert generate_set(runtime, **kwargs)["manifest_artifact_id"] == result["manifest_artifact_id"]
    assert provider.calls == 4
    with closing(Database(runtime.database_path).connect()) as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM environment_requests")] == before


def test_legacy_scene_prompt_remains_explicitly_available(runtime, episode):
    provider = FakeProvider()
    result = generate_set(
        runtime, confirmed=True, provider=provider, episode_id=episode.episode_id,
        role_briefs=dict.fromkeys(ROLES, "A cozy indoor playroom"),
        prompt_version=PROMPT_VERSION, assert_owner=lambda: None,
    )
    assert inspect_set(runtime, result["manifest_artifact_id"])["environment_set"][
        "prompt_version"
    ] == PROMPT_VERSION


def test_historical_extraction_bytes_are_unchanged():
    image = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, 103, 103), fill=(230, 230, 230))
    draw.rectangle((28, 28, 99, 99), fill="white")
    actual = _prepare_qwen_white_background(png(image), "image/png")
    assert sha256(actual.tobytes()).hexdigest() == (
        "51fe2c90d2ca6cce9ee76559a110d33e2e0d3a658a77084348fe7cc014300e5c"
    )
    assert actual.tobytes() == extract(image, WHITE_BACKGROUND_V1).tobytes()


def test_pale_contour_and_enclosed_white_detail_survive_without_mask_growth():
    image = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((24, 24, 103, 103), fill="white", outline=(247, 247, 247), width=4)
    draw.line((48, 60, 80, 60), fill=(244, 244, 244), width=2)
    with pytest.raises(ValueError, match="coverage"):
        extract(image, WHITE_BACKGROUND_V1)  # Flood crosses the pale outline and loses the object.
    result = extract(image)
    assert result.getpixel((64, 64)) == (255, 255, 255, 255)
    assert result.getpixel((64, 60)) == (244, 244, 244, 255)
    assert result.getpixel((64, 24)) == (247, 247, 247, 255)
    assert result.getpixel((64, 23))[3] == 0
    assert result.getchannel("A").getbbox() == (24, 24, 104, 104)
    assert result.tobytes() == extract(image).tobytes()
    assert normalize(result).tobytes() == normalize(extract(image)).tobytes()


def test_tinted_qwen_border_does_not_key_warm_pale_details():
    image = Image.new("RGBA", (128, 128), (253, 255, 250, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((24, 24, 103, 103), fill=(255, 248, 241, 255))
    result = extract(image)
    assert result.getpixel((64, 24)) == (255, 248, 241, 255)
    assert result.getpixel((64, 23))[3] == 0
    with pytest.raises(ValueError, match="coverage"):
        extract(image, WHITE_BACKGROUND_V1)


def test_antialiased_contour_pixels_and_interior_alpha_are_not_repainted():
    source = Image.new("RGBA", (512, 512), "white")
    ImageDraw.Draw(source).ellipse((96, 96, 415, 415), fill="white", outline="#ececec", width=16)
    source = source.resize((128, 128), Image.Resampling.LANCZOS)
    result = extract(source)
    retained_edges = 0
    for raw, keyed in zip(source.getdata(), result.getdata(), strict=True):
        if 235 < raw[0] < 254:
            assert raw == keyed
            retained_edges += 1
    assert retained_edges > 200
    assert result.getpixel((64, 64)) == (255, 255, 255, 255)


@pytest.mark.parametrize("case", ["invisible", "touching", "uneven"])
def test_unsuitable_source_produces_actionable_diagnostic(case):
    image = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(image)
    if case == "touching":
        draw.rectangle((0, 40, 60, 90), fill="#e0e0e0")
    elif case == "uneven":
        draw.rectangle((0, 0, 127, 20), fill="#e8e8e8")
    with pytest.raises(ValueError, match="WHITE_BACKGROUND_UNSAFE"):
        extract(image)


def test_complete_object_close_to_border_is_not_cropped():
    image = Image.new("RGB", (128, 128), "white")
    ImageDraw.Draw(image).rectangle((1, 24, 70, 103), fill="#eeeeee")
    result = extract(image)
    assert result.getchannel("A").getbbox() == (1, 24, 71, 104)
    assert result.getpixel((1, 24)) == (238, 238, 238, 255)


def test_isolated_backdrop_pixels_must_not_silently_shift_normalized_subject():
    image = Image.new("RGB", (128, 128), (253, 255, 250))
    draw = ImageDraw.Draw(image)
    draw.ellipse((24, 24, 103, 80), fill="#e0e0e0")
    # Retained smile source has similar single-pixel cyan VAE artifacts below its subject.
    image.putpixel((32, 115), (245, 254, 255))
    with pytest.raises(ValueError, match="isolated pixels control trim"):
        extract(image)
    # A one-pixel tip connected to the subject is meaningful and must remain.
    image.putpixel((32, 115), (253, 255, 250))
    image.putpixel((64, 81), (224, 224, 224))
    result = extract(image)
    assert result.getpixel((64, 81)) == (224, 224, 224, 255)


def test_foreground_prompt_scopes_subject_and_protects_unspecified_pale_parts():
    from types import SimpleNamespace

    episode = SimpleNamespace(
        objective="Brush teeth with a toothbrush",
        character_packs=[SimpleNamespace(revision_id="p")],
        brand_revision_id="brand",
    )
    requirement = VisualRequirement(
        asset_key="circle", kind="scene_element", display_name="circle swirl",
        semantic_label="circle swirl", description="A pale dotted circle",
        educational_role="supporting",
    )
    new = image_spec(episode, requirement, "Preschool art.")
    old = image_spec(episode, requirement, "Preschool art.", prompt_version=LEGACY_IMAGE_PROMPT)
    assert new.prompt_version == IMAGE_PROMPT and old.prompt_version == LEGACY_IMAGE_PROMPT
    assert "toothbrush" not in new.prompt() and "toothbrush" in old.prompt()
    assert "continuous visible tonal contour" in new.prompt() and new.palette == {}
    assert "No decorative faces" in new.prompt()
    assert new.fingerprint() != old.fingerprint()


@pytest.mark.parametrize("crash_before_normalization", [False, True])
def test_historical_foreground_reuse_never_reinterprets_or_resends(
    runtime, episode, tmp_path, monkeypatch, crash_before_normalization,
):
    from types import SimpleNamespace

    from tovitunes.render import episode_assets

    store = AssetStore(
        runtime.data_root, Database(runtime.database_path),
        generated_source_roots=[tmp_path], local_preview=True,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}")
    plan_id = persist_file(
        store, episode, "episode_visual_plan", "main", plan_path, (),
    ).identity.artifact_id
    visual = SimpleNamespace(required_assets=(VisualRequirement(
        asset_key="cup", kind="scene_element", display_name="cup", semantic_label="cup",
        description="One plain cup", educational_role="supporting",
    ),))

    class Provider(FakeReviewedQwen):
        def translate(self, spec):
            return spec

    provider = Provider()
    args = (runtime, store, episode, visual, plan_id, tmp_path, "Preschool art.", provider)
    original = episode_assets._prepare_qwen_white_background
    if crash_before_normalization:
        def crash(*args, **kwargs):
            raise SystemExit("raw source retained before normalization")
        monkeypatch.setattr(episode_assets, "_prepare_qwen_white_background", crash)
        with pytest.raises(SystemExit):
            generate_assets(*args, image_prompt=LEGACY_IMAGE_PROMPT, assert_owner=lambda: None)
        monkeypatch.setattr(episode_assets, "_prepare_qwen_white_background", original)
    else:
        generate_assets(*args, image_prompt=LEGACY_IMAGE_PROMPT, assert_owner=lambda: None)
    with closing(store.database.connect()) as conn:
        receipt_before = dict(conn.execute("SELECT * FROM production_image_receipts").fetchone())
        request_before = dict(conn.execute("SELECT * FROM generation_requests").fetchone())
    source_id = receipt_before["source_artifact_id"]
    raw_before = store.path_for(source_id).read_bytes()
    if crash_before_normalization:
        with pytest.raises(ValueError, match="explicit reconciliation"):
            generate_assets(*args, assert_owner=lambda: None)
        generate_assets(*args, image_prompt=LEGACY_IMAGE_PROMPT, assert_owner=lambda: None)
    else:
        generate_assets(*args, assert_owner=lambda: None)
    assert provider.calls == ["cup"]
    assert store.path_for(source_id).read_bytes() == raw_before
    selected = store.selected("episode", episode.episode_id, "visual_asset", "cup")
    assert Image.open(store.path_for(selected.identity.artifact_id)).tobytes() == normalize(
        original(raw_before, "image/png")
    ).tobytes()
    with closing(store.database.connect()) as conn:
        receipt_after = dict(conn.execute("SELECT * FROM production_image_receipts").fetchone())
        request_after = dict(conn.execute("SELECT * FROM generation_requests").fetchone())
    for field in ("spec_json", "provider_profile_json", "source_artifact_id", "response_sha256"):
        assert receipt_before[field] == receipt_after[field]
    assert request_after["input_fingerprint"] == request_before["input_fingerprint"]
    if not crash_before_normalization:
        assert receipt_before == receipt_after and request_before == request_after

def test_scene_v2_prompt_identity_remains_frozen():
    from tovitunes.render.environment_sets import LEGACY_EPISODE_PROMPT_VERSION

    spec = _spec(
        'meadow_wide', 'brand', 1, None, scene_brief='A cozy indoor playroom',
        prompt_version=LEGACY_EPISODE_PROMPT_VERSION,
    )
    assert spec.fingerprint() == '70fdff48746ef3648e69fde33f76787dbdf9df20237b08e9f7f60c84f2ff8d33'


def test_shared_art_direction_changes_environment_identity_without_resubmission(runtime, episode):
    from tovitunes.render.lesson_objects import ART_DIRECTION_V1

    provider = FakeProvider()
    kwargs = dict(
        confirmed=True, provider=provider, episode_id=episode.episode_id,
        role_briefs=dict.fromkeys(ROLES, 'One quiet classroom'), assert_owner=lambda: None,
        visual_direction='Soft watercolor texture with cool shadows.',
    )
    result = generate_set(runtime, **kwargs)
    with closing(Database(runtime.database_path).connect()) as conn:
        row = conn.execute('SELECT spec_json FROM environment_requests LIMIT 1').fetchone()
    prompt = json.loads(row[0])
    assert ART_DIRECTION_V1 in prompt['common_brief']
    assert kwargs['visual_direction'] in prompt['common_brief']
    assert generate_set(runtime, **kwargs)['manifest_artifact_id'] == result['manifest_artifact_id']
    kwargs['visual_direction'] = 'Soft chalk texture.'
    with pytest.raises(ValueError, match='explicit reconciliation'):
        generate_set(runtime, **kwargs)
    assert provider.calls == 4


@pytest.mark.parametrize('shape', ['streak', 'square'])
def test_extraction_v3_refuses_connected_specks_that_v2_mistakes_for_contours(shape):
    from tovitunes.render.lesson_objects import WHITE_BACKGROUND_V3

    source = Image.new('RGB', (128, 128), 'white')
    draw = ImageDraw.Draw(source)
    draw.ellipse((24, 24, 103, 80), fill='#dddddd')
    draw.rectangle((60, 115, 61, 115 if shape == 'streak' else 116), fill='#dddddd')
    old = extract(source, WHITE_BACKGROUND_V2)
    assert old.getchannel('A').getbbox()[3] >= 116
    with pytest.raises(ValueError, match='detached backdrop specks control trim'):
        extract(source, WHITE_BACKGROUND_V3)


def test_extraction_v3_keeps_pale_details_diagonal_tips_and_meaningful_detached_parts():
    from tovitunes.render.lesson_objects import WHITE_BACKGROUND_V3

    source = Image.new('RGB', (128, 128), 'white')
    draw = ImageDraw.Draw(source)
    draw.ellipse((24, 24, 103, 90), fill='white', outline='#f7f7f7', width=4)
    # Deliberately detached, visible illustration detail; a multi-part subject is valid.
    draw.ellipse((40, 100, 51, 111), fill='#eeeeee')
    source.putpixel((64, 91), (247, 247, 247))
    result = extract(source, WHITE_BACKGROUND_V3)
    assert result.getpixel((64, 50)) == (255, 255, 255, 255)
    assert result.getpixel((64, 91)) == (247, 247, 247, 255)
    assert result.getpixel((45, 105)) == (238, 238, 238, 255)
    assert result.tobytes() == extract(source, WHITE_BACKGROUND_V2).tobytes()


def test_new_environment_contract_refuses_local_alpha_hole_hidden_by_frame_average():
    from tovitunes.render.environment_sets import ENVIRONMENT_ALPHA_V2, _validate_image

    source = Image.new('RGBA', (144, 256), '#a5def0')
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 180, 143, 255), fill='#7ac977')
    draw.rectangle((70, 100, 81, 111), fill=(255, 255, 255, 0))
    # Historical average-opacity behavior is retained explicitly.
    assert _validate_image(
        png(source), source.size, allow_near_opaque_alpha=True,
    ).size == (1080, 1920)
    with pytest.raises(ValueError, match='local transparency'):
        _validate_image(
            png(source), source.size, allow_near_opaque_alpha=True,
            normalization_version=ENVIRONMENT_ALPHA_V2,
        )
    draw.rectangle((70, 100, 81, 111), fill=(165, 222, 240, 245))
    assert _validate_image(
        png(source), source.size, allow_near_opaque_alpha=True,
        normalization_version=ENVIRONMENT_ALPHA_V2,
    ).size == (1080, 1920)


@pytest.mark.parametrize('swap', ['owner', 'role', 'source'])
def test_environment_admission_refuses_swapped_owner_role_or_raw_source(
    runtime, episode, tmp_path, swap,
):
    from tovitunes.artifacts.store import InputDependency
    from tovitunes.domain.artifact import Provenance
    from tovitunes.render.visual_evidence import validate_environment_assets

    generated = generate_set(
        runtime, confirmed=True, provider=FakeProvider(), episode_id=episode.episode_id,
        role_briefs=dict.fromkeys(ROLES, 'One indoor classroom'), assert_owner=lambda: None,
    )
    store = AssetStore(runtime.data_root, Database(runtime.database_path))
    aid = generated['manifest_artifact_id']
    environment = validate_environment_assets(store, aid, episode.episode_id, shared=False)
    if swap == 'owner':
        with pytest.raises(ValueError, match='different owner'):
            validate_environment_assets(store, aid, 'another-episode', shared=False)
        return
    first, second = environment.plates[:2]
    changes = (
        {'artifact_id': second.artifact_id, 'sha256': second.sha256} if swap == 'role' else
        {'source_artifact_id': second.source_artifact_id, 'source_sha256': second.source_sha256}
    )
    forged = environment.model_copy(update={
        'plates': (first.model_copy(update=changes), *environment.plates[1:]),
    })
    path = tmp_path / 'forged.json'
    path.write_text(forged.model_dump_json(), encoding='utf-8')
    record = store.ingest(
        path, owner_scope='episode', owner_id=episode.episode_id,
        kind='environment_set', slot_key='main', provenance=Provenance.manual('test', 'test://forged'),
        dependencies=[InputDependency(aid, 'environment_plate')
                      for aid in dict.fromkeys(p.artifact_id for p in forged.plates)],
    )
    with pytest.raises(ValueError, match='mismatched'):
        validate_environment_assets(
            store, record.identity.artifact_id, episode.episode_id, shared=False,
        )


def test_extraction_v3_preserves_diagonal_one_pixel_tip():
    from tovitunes.render.lesson_objects import WHITE_BACKGROUND_V3

    source = Image.new('RGB', (128, 128), 'white')
    draw = ImageDraw.Draw(source)
    draw.rectangle((24, 24, 90, 90), fill='#dddddd')
    source.putpixel((91, 91), (221, 221, 221))
    result = extract(source, WHITE_BACKGROUND_V3)
    assert result.getchannel('A').getbbox() == (24, 24, 92, 92)
    assert result.getpixel((91, 91)) == (221, 221, 221, 255)


def test_environment_receipt_must_match_planned_setting(runtime, episode):
    from tovitunes.render.visual_evidence import validate_environment_assets

    generated = generate_set(
        runtime, confirmed=True, provider=FakeProvider(), episode_id=episode.episode_id,
        role_briefs=dict.fromkeys(ROLES, 'One indoor classroom'), assert_owner=lambda: None,
    )
    store = AssetStore(runtime.data_root, Database(runtime.database_path))
    with pytest.raises(ValueError, match='original planned setting'):
        validate_environment_assets(
            store, generated['manifest_artifact_id'], episode.episode_id, shared=False,
            role_briefs=dict.fromkeys(ROLES, 'An unrelated outdoor garden'),
        )


def test_old_environment_normalizer_cannot_bypass_scene_v3_source_admission(
    runtime, episode, monkeypatch,
):
    from test_visual_story import picture

    from tovitunes.benchmark.providers import ProviderResult
    from tovitunes.render import environment_sets
    from tovitunes.render.visual_evidence import validate_environment_assets

    source = Image.open(io.BytesIO(picture())).convert('RGBA')
    ImageDraw.Draw(source).rectangle((320, 500, 339, 519), fill=(255, 255, 255, 0))

    class OldSourceProvider(FakeProvider):
        def generate(self, spec, reference_paths, *, on_remote_start=None):
            self.calls += 1
            if on_remote_start:
                on_remote_start()
            return ProviderResult(
                image_bytes=png(source), mime_type='image/png',
                provider_request_id='old-normalizer-source',
            )

    validate = environment_sets._validate_image

    def old_normalizer(data, dimensions, **kwargs):
        return validate(data, dimensions, allow_near_opaque_alpha=True)

    monkeypatch.setattr(environment_sets, '_validate_image', old_normalizer)
    generated = generate_set(
        runtime, confirmed=True, provider=OldSourceProvider(), episode_id=episode.episode_id,
        role_briefs=dict.fromkeys(ROLES, 'One indoor classroom'), assert_owner=lambda: None,
    )
    monkeypatch.setattr(environment_sets, '_validate_image', validate)
    store = AssetStore(runtime.data_root, Database(runtime.database_path))
    with pytest.raises(ValueError, match='local transparency'):
        validate_environment_assets(
            store, generated['manifest_artifact_id'], episode.episode_id, shared=False,
        )
