"""Offline production integration: real ledgers, analysis, Qwen adapter and Renderer V4."""

import io
import json
import math
import struct
import wave
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml
from PIL import Image, ImageDraw
from pydantic import ValidationError
from test_render import render_fixture, rows_snapshot

from tovitunes.artifacts.store import AssetStore
from tovitunes.benchmark.providers import QwenComfyUIImageProvider
from tovitunes.cli import main
from tovitunes.config import ProductionAutomationConfig
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.provider import DurableStructuredGenerator
from tovitunes.creative.workflow import CreativeWorkflow
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.domain.review import RightsDecision
from tovitunes.domain.storyboard import TimedStoryboardV2, parse_storyboard
from tovitunes.domain.visual_plan import (
    COLORS_V1,
    ENVIRONMENT_ROLES,
    EpisodeVisualPlan,
    validate_visual_plan,
)
from tovitunes.music.ace_step import AceStepLocalProvider
from tovitunes.music.analysis import normalized_words
from tovitunes.music.analysis_models import (
    AlignmentEvidence,
    AnalyzerProvenance,
    RecognizedWord,
    TranscriptionEvidence,
)
from tovitunes.music.models import TimedText
from tovitunes.music.timing_runtime import measured_rhythm
from tovitunes.pipeline import short_production
from tovitunes.pipeline.creative import FakeDraftGenerator
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.pipeline.short_production import ProductionStop, ShortProductionWorkflow
from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.rights_policy import is_direct_rights_root
from tovitunes.publication.service import PublicationService
from tovitunes.render.episode_assets import swatch
from tovitunes.render.production import ProductionRenderer

WORKFLOW = Path("workflows/qwen_image_2_1_t2i_api.json").resolve()
pytest_plugins = ("test_web_youtube_v1",)


def tone(duration=10):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        frames = b"".join(
            struct.pack("<h", int(1600 * math.sin(2 * math.pi * 220 * i / 44100)))
            for i in range(44100 * duration)
        )
        output.writeframes(frames)
    return buffer.getvalue()


def visual_plan(episode, ids, lyrics, keys=("lesson_cup", "lesson_ribbon")):
    return EpisodeVisualPlan.model_validate(
        {
            "episode_id": episode.episode_id,
            "concept_id": episode.concept_id,
            "objective_id": episode.objective_id,
            "episode_spec_artifact_id": ids[0],
            "lyrics_artifact_id": ids[1],
            "music_spec_artifact_id": ids[2],
            "required_assets": [
                {
                    "asset_key": "target_card",
                    "kind": "color_swatch",
                    "semantic_label": "color",
                    "display_name": "Target color card",
                    "description": "A flat target color card",
                    "educational_role": "teaching",
                    "target_color": episode.concept_id,
                    "grounded": False,
                    "motion": "float",
                },
                *[
                    {
                        "asset_key": key,
                        "kind": "lesson_object",
                        "semantic_label": label,
                        "display_name": label,
                        "description": f"One {episode.concept_id} {label}",
                        "educational_role": "teaching",
                        "target_color": episode.concept_id,
                    }
                    for key, label in zip(keys, ("cup", "ribbon"), strict=True)
                ],
            ],
            "scenes": [
                {
                    "lyric_index": index,
                    "lyric_text": line.text,
                    "required_assets": ["target_card", keys[index % 2]],
                    "visual_focus": "Name the color",
                    "tovi_action": "present",
                    "environment_role": ENVIRONMENT_ROLES[index % 4],
                }
                for index, line in enumerate(lyrics.lines)
            ],
            "environments": [
                {"role": role, "description": "An open rounded preschool meadow stage"}
                for role in ENVIRONMENT_ROLES
            ],
        }
    )


@pytest.fixture
def case(tmp_path, catalog, monkeypatch):
    # Reuse the established renderer fixture; its complete Red selections must stay unchanged.
    config, original_store, red = render_fixture.__wrapped__(tmp_path, catalog, monkeypatch)
    import socket

    socket_connect = socket.socket.connect
    monkeypatch.setattr(
        "socket.socket.connect", lambda *args: pytest.fail("live network forbidden")
    )
    from tovitunes.creative.workflow import committed_curriculum_digest

    committed_root = Path(__file__).resolve().parents[1] / "brands/tovitunes"
    monkeypatch.setattr(
        "tovitunes.creative.workflow.committed_curriculum_digest",
        lambda root, catalog: committed_curriculum_digest(committed_root, catalog),
    )
    database = original_store.database
    episode = Episode.create(catalog, "blue", "existing-blue")
    database.create_episode(catalog, episode)
    fake = FakeNIMTransport()
    draft = FakeDraftGenerator().episode_spec(episode, variant=1).output
    draft = draft.model_copy(
        update={
            "concept": draft.concept.model_copy(
                update={
                    "premise": "Tovi presents a blue cup and a blue ribbon in a familiar playroom."
                }
            )
        }
    )
    fake.responses["EpisodeSpec"] = [draft.model_dump_json()]
    provider = DurableStructuredGenerator(database, fake)
    creative = CreativeWorkflow(config, provider, catalog=catalog)
    selected = creative.generate_next(episode_key=episode.external_key)
    ids = tuple(selected[k + "_artifact_id"] for k in ("episode_spec", "lyrics", "music_spec"))
    lyrics = LyricsSpec.model_validate(creative.store.read_json(ids[1]))
    plan = visual_plan(episode, ids, lyrics)
    fake.responses["EpisodeVisualPlan"] = [plan.model_dump_json()]

    music_events = []
    music_behavior = {"state": "success", "resume": "success"}
    audio = tone()

    def music_http(request):
        music_events.append(request.url.path)
        if request.url.path == "/health":
            data = {"status": "ok"}
        elif request.url.path == "/release_task":
            data = {"task_id": "retained-task"}
        elif request.url.path == "/query_result":
            if music_behavior["state"] == "ambiguous":
                raise httpx.ReadTimeout("Authorization: Bearer secret-must-not-leak")
            if music_behavior["state"] == "pending":
                data = [{"task_id": "retained-task", "status": 0}]
            else:
                data = [
                    {
                        "task_id": "retained-task",
                        "status": 1,
                        "result": json.dumps(
                            [
                                {
                                    "file": "/v1/audio?path=fixture.wav",
                                    "status": 1,
                                    "dit_model": "acestep-v15-turbo",
                                    "lm_model": "acestep-5Hz-lm-0.6B",
                                }
                            ]
                        ),
                    }
                ]
        else:
            assert request.url.path == "/v1/audio"
            return httpx.Response(200, content=audio)
        return httpx.Response(200, json={"code": 200, "data": data})

    music = AceStepLocalProvider(
        config.music_generation.model_copy(
            update={
                "timeout_seconds": 0.005,
                "poll_interval_seconds": 0.001,
            }
        ),
        transport=httpx.MockTransport(music_http),
    )
    image_events = []
    image_behavior = {"crash": None}

    def image_http(request):
        image_events.append(request.url.path)
        if request.url.path == "/prompt":
            if image_behavior["crash"] == len([x for x in image_events if x == "/prompt"]):
                raise SystemExit("simulated hard exit before response")
            return httpx.Response(200, json={"prompt_id": f"image-{len(image_events)}"})
        if request.url.path.startswith("/history/"):
            return httpx.Response(
                200,
                json={
                    request.url.path.split("/")[-1]: {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "461": {
                                "images": [
                                    {"filename": "fixture.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        assert request.url.path == "/view"
        width, height = image_behavior.get("dimensions", (1024, 1024))
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((width * 0.2, height * 0.2, width * 0.8, height * 0.8), fill="#246BDF")
        data = io.BytesIO()
        image.save(data, format="PNG")
        return httpx.Response(200, content=data.getvalue())

    qwen = QwenComfyUIImageProvider(
        WORKFLOW,
        width=1024,
        height=1024,
        purpose="lesson_object",
        transport=httpx.MockTransport(image_http),
    )
    environment_events = []

    def environment_http(request):
        environment_events.append(request.url.path)
        image_behavior["dimensions"] = (768, 1376)
        try:
            return image_http(request)
        finally:
            image_behavior.pop("dimensions", None)

    env = QwenComfyUIImageProvider(
        WORKFLOW,
        width=768,
        height=1376,
        purpose="environment",
        transport=httpx.MockTransport(environment_http),
    )
    monkeypatch.setattr(
        "tovitunes.render.environment_sets._configured_provider", lambda config: env
    )

    def rhythm(decoded, brief, root, device, digest):
        points = tuple(
            i * 60 / brief.target_bpm for i in range(int(decoded.duration * brief.target_bpm / 60))
        )
        return measured_rhythm(points, points[::4], decoded.duration, brief).model_copy(
            update={
                "provenance": AnalyzerProvenance(
                    name="Beat This",
                    version="offline",
                    device="cpu",
                    timestamp="2026-01-01",
                    source_audio_sha256=digest,
                    model_name="fixture",
                    model_revision="sha256:" + "a" * 64,
                )
            }
        )

    analysis_calls = []

    def asr(path, spec, duration, config):
        analysis_calls.append(path)
        tokens = normalized_words(spec.lyrics.text())
        step = 9 / len(tokens)
        words = tuple(
            RecognizedWord(
                text=word, start=0.4 + index * step, end=0.4 + (index + 0.8) * step, score=0.95
            )
            for index, word in enumerate(tokens)
        )
        lines, offset = [], 0
        for line in spec.lyrics.lines:
            count = len(normalized_words(line.text))
            lines.append(
                TimedText(
                    text=line.text, start=words[offset].start, end=words[offset + count - 1].end
                )
            )
            offset += count
        return (
            TranscriptionEvidence(
                status="complete",
                recognized_text=spec.lyrics.text(),
                words=words,
                mean_word_score=0.95,
            ),
            AlignmentEvidence(
                status="complete",
                canonical_words=words,
                lyric_lines=tuple(lines),
                aligned_word_count=len(words),
                aligned_line_count=len(lines),
            ),
            "fixture",
            "cpu",
        )

    monkeypatch.setattr("tovitunes.music.analysis.analyze_rhythm", rhythm)
    monkeypatch.setattr("tovitunes.music.analysis.transcribe_and_align", asr)
    actual_renderer = ProductionRenderer
    monkeypatch.setattr(
        short_production,
        "ProductionRenderer",
        lambda config, **kwargs: actual_renderer(config, canvas=(270, 480), **kwargs),
    )
    pipeline = ShortProductionWorkflow(
        config, creative_provider=provider, music_provider=music, image_provider=qwen
    )
    errors = []
    for name in ("_visual", "_environment", "_storyboard", "_render", "_metadata"):
        original = getattr(pipeline, name)

        def traced(*args, original=original, **kwargs):
            try:
                return original(*args, **kwargs)
            except Exception as exc:
                errors.append(str(exc))
                raise

        setattr(pipeline, name, traced)
    return dict(
        socket_connect=socket_connect,
        errors=errors,
        config=config,
        flow=pipeline,
        episode=episode,
        ids=ids,
        fake=fake,
        plan=plan,
        store=creative.store,
        music=music,
        music_events=music_events,
        music_behavior=music_behavior,
        image_events=image_events,
        image_behavior=image_behavior,
        environment_events=environment_events,
        analysis_calls=analysis_calls,
        red=red,
    )


def test_lossless_adapter_and_plan_only(case):
    flow, store, ids, episode = (case[k] for k in ("flow", "store", "ids", "episode"))
    canonical = creative_music_spec(
        episode,
        EpisodeSpec.model_validate(store.read_json(ids[0])),
        LyricsSpec.model_validate(store.read_json(ids[1])),
        MusicSpec.model_validate(store.read_json(ids[2])),
        ids,
    )
    assert case["music"].translate(canonical)["lyrics"] == "\n".join(
        line.text for line in canonical.lyrics.lines
    )
    before = rows_snapshot(case["config"].database_path)
    plan = flow.produce(episode.external_key)
    assert plan["current_stage"] == "MUSIC" and plan["provider_calls"] == 0
    assert rows_snapshot(case["config"].database_path) == before
    assert not case["music_events"] and not case["image_events"]


@pytest.mark.parametrize(
    "behavior,status", [("pending", "PENDING_PROVIDER"), ("ambiguous", "AMBIGUOUS")]
)
def test_music_stops_and_never_resubmits(case, behavior, status):
    case["music_behavior"]["state"] = behavior
    first = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert first["status"] == status, first
    second = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert second["status"] == status, second
    assert case["music_events"].count("/release_task") == 1
    assert not case["image_events"]
    with closing(case["store"].database.connect()) as db:
        assert (
            db.execute("SELECT provider_request_id FROM music_requests").fetchone()[0]
            == "retained-task"
        )
        assert "secret-must-not-leak" not in str(
            [tuple(row) for row in db.execute("SELECT * FROM music_requests")]
        )


def test_pending_then_success_same_task(case, monkeypatch):
    case["music_behavior"]["state"] = "pending"
    assert (
        case["flow"].produce(case["episode"].external_key, confirmed=True)["status"]
        == "PENDING_PROVIDER"
    )
    case["music_behavior"]["state"] = "success"
    monkeypatch.setattr(case["flow"], "_visual", lambda *args: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        result = case["flow"].produce(case["episode"].external_key, confirmed=True)
        pytest.fail(str(result.get("blocker"))[:700])
    assert case["music_events"].count("/release_task") == 1
    assert case["analysis_calls"]


def test_qa_failure_stops_without_regeneration(case, monkeypatch):
    monkeypatch.setattr(
        "tovitunes.music.analysis.compare_lyrics",
        lambda *a, **k: __import__(
            "tovitunes.music.analysis", fromlist=["LyricComparison"]
        ).LyricComparison(
            status="incomplete",
            expected_transcript=a[0],
            recognized_transcript="",
            expected_word_count=0,
            recognized_word_count=0,
            required_phrase_presence={},
        ),
    )
    for _ in range(2):
        result = case["flow"].produce(case["episode"].external_key, confirmed=True)
        assert result["status"] == "BLOCKED", result
        assert result["current_stage"] == "AUDIO_ANALYSIS"
    assert case["music_events"].count("/release_task") == 1
    assert not case["image_events"]


def test_complete_real_preview_and_reuse(case, monkeypatch):
    before = rows_snapshot(case["config"].database_path)
    result = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert result["status"] == "NEEDS_REVIEW", (result.get("blocker"), case["errors"])
    assert result["ready_local_preview"]
    assert Path(result["output_path"]).read_bytes()[4:8] == b"ftyp"
    store = AssetStore(case["config"].data_root, case["store"].database, local_preview=True)
    storyboard = parse_storyboard(
        store.read_json(
            store.selected(
                "episode", case["episode"].episode_id, "timed_storyboard", "main"
            ).identity.artifact_id
        )
    )
    assert isinstance(storyboard, TimedStoryboardV2)
    assert storyboard.concept_id == "blue" and storyboard.objective_id == "colors.blue.identify"
    assert set(storyboard.asset_artifact_ids) == {"target_card", "lesson_cup", "lesson_ribbon"}
    assert tuple(s.lyric_text for s in storyboard.scenes if s.kind == "lyric") == tuple(
        line.text for line in LyricsSpec.model_validate(store.read_json(case["ids"][1])).lines
    )
    assert case["music_events"].count("/release_task") == 1
    assert case["image_events"].count("/prompt") == 6
    calls = (len(case["music_events"]), len(case["image_events"]), len(case["fake"].calls))
    second = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert second["final_render_id"] == result["final_render_id"]
    assert (len(case["music_events"]), len(case["image_events"]), len(case["fake"].calls)) == calls
    assert len(case["analysis_calls"]) == 1
    assert not evaluate_release(case["config"], case["episode"].external_key).public_release_allowed
    after = rows_snapshot(case["config"].database_path)
    # All preexisting artifact IDs, selections, rights and publication rows remain byte-identical.
    for table in (
        "artifact_versions",
        "artifact_selections",
        "rights_decisions",
        "publication_attempts",
    ):
        assert all(row in after[table] for row in before[table])
    with closing(store.database.connect()) as db:
        assert all(
            row[0] == "pending"
            for row in db.execute(
                "SELECT d.status FROM approval_decisions d JOIN artifact_versions v "
                "ON v.artifact_id=d.artifact_id WHERE v.episode_id=? AND v.kind IN "
                "('visual_asset_source','visual_asset','final_render','publication_metadata')",
                (case["episode"].episode_id,),
            )
        )

    # Real publication service/ledgers, with only the YouTube client boundary replaced.
    class YouTubeFixture:
        uploads = 0
        promotions = 0

        def assert_channel(self, expected):
            assert expected == "fixture-channel"

        def upload_private(self, path, metadata, *, on_remote_start, assert_ownership):
            assert metadata.made_for_kids is True and path.read_bytes()[4:8] == b"ftyp"
            assert_ownership()
            on_remote_start()
            self.uploads += 1
            return "fixture-video"

        def video_status(self, video_id):
            return {
                "available": True,
                "video_id": video_id,
                "channel_id": "fixture-channel",
                "privacy": "private",
                "upload_status": "processed",
                "processing_status": "succeeded",
                "self_declared_made_for_kids": True,
                "contains_synthetic_media": True,
            }

        def publish_video(self, video_id, status):
            self.promotions += 1
            return {
                "id": video_id,
                "status": {
                    "privacyStatus": "public",
                    "selfDeclaredMadeForKids": True,
                    "containsSyntheticMedia": True,
                },
            }

    client = YouTubeFixture()
    monkeypatch.setattr(
        short_production,
        "PublicationService",
        lambda config: PublicationService(config, client_factory=lambda: client),
    )
    enabled = case["config"].model_copy(
        update={
            "expected_youtube_channel_id": "fixture-channel",
            "publication": case["config"].publication.model_copy(
                update={
                    "youtube": case["config"].publication.youtube.model_copy(
                        update={"enabled": True}
                    )
                }
            ),
            "automation": ProductionAutomationConfig(auto_publish=True, require_human_review=False),
        }
    )
    case["flow"].config = enabled
    assert case["flow"]._publication(case["episode"])["status"] == "COMPLETE"
    assert case["flow"]._publication(case["episode"])["status"] == "COMPLETE"
    assert client.uploads == 1 and client.promotions == 0
    public = enabled.model_copy(
        update={
            "automation": enabled.automation.model_copy(update={"publish_visibility": "public"})
        }
    )
    case["flow"].config = public
    with pytest.raises(ProductionStop, match="release gates"):
        case["flow"]._publication(case["episode"])
    assert client.uploads == 1 and client.promotions == 0
    # Test-only operator rights decisions; the pipeline itself never fabricates them.
    with closing(store.database.connect()) as db:
        artifact_ids = [row[0] for row in db.execute("SELECT artifact_id FROM artifact_versions")]
    for aid in artifact_ids:
        if is_direct_rights_root(store.get(aid)):
            store.record_rights(
                RightsDecision(
                    artifact_id=aid,
                    status="commercial_use_confirmed",
                    actor="human:offline-fixture",
                    evidence_uri="fixture://accepted-license",
                    rationale="Explicit offline test evidence",
                    policy_version="fixture",
                    decided_at=datetime.now(UTC),
                )
            )
    assert case["flow"]._publication(case["episode"])["status"] == "COMPLETE"
    assert case["flow"]._publication(case["episode"])["status"] == "COMPLETE"
    assert client.uploads == 1 and client.promotions == 1


@pytest.mark.parametrize("color", [*COLORS_V1, "rainbow"])
def test_swatches_are_generic_and_deterministic(color):
    assert swatch(color, 64).tobytes() == swatch(color, 64).tobytes()
    if color != "rainbow":
        assert swatch(color, 64).getpixel((32, 32))[:3] == tuple(
            bytes.fromhex(COLORS_V1[color][1:])
        )


@pytest.mark.parametrize(
    "defect", ["objective", "lyrics", "missing_asset", "color", "unsupported_entity"]
)
def test_visual_plan_rejects_unbound_creative_facts(case, defect):
    data = case["plan"].model_dump(mode="json")
    if defect == "objective":
        data["objective_id"] = "invented.claim"
    elif defect == "lyrics":
        data["scenes"][0]["lyric_text"] = "Rewritten words"
    elif defect == "missing_asset":
        data["scenes"][0]["required_assets"] = ["red_apple"]
    elif defect == "color":
        data["required_assets"][0]["target_color"] = "red"
    else:
        data["required_assets"][1]["semantic_label"] = "bus"
    with pytest.raises((ValueError, ValidationError)):
        plan = EpisodeVisualPlan.model_validate(data)
        validate_visual_plan(
            plan,
            case["episode"],
            EpisodeSpec.model_validate(case["store"].read_json(case["ids"][0])),
            LyricsSpec.model_validate(case["store"].read_json(case["ids"][1])),
            case["ids"],
        )


@pytest.mark.parametrize(
    "boundary",
    [
        "CREATIVE",
        "MUSIC",
        "AUDIO_ANALYSIS",
        "VISUAL_PLAN",
        "VISUAL_ASSETS",
        "STORYBOARD",
        "RENDER",
        "MEDIA_QA",
        "METADATA",
    ],
)
def test_restart_after_completed_stage(case, monkeypatch, boundary):
    original = case["flow"]._event
    crashed = False

    def crash(episode, stage, status, evidence):
        nonlocal crashed
        original(episode, stage, status, evidence)
        if stage == boundary and status == "COMPLETE" and not crashed:
            crashed = True
            raise SystemExit("offline stage-boundary crash")

    monkeypatch.setattr(case["flow"], "_event", crash)
    if boundary not in {"RENDER", "MEDIA_QA", "METADATA"}:

        def stop_before_render(*args):
            raise ProductionStop("BLOCKED", "Offline test reached the verified render boundary")

        monkeypatch.setattr(case["flow"], "_render", stop_before_render)
    with pytest.raises(SystemExit):
        case["flow"].produce(case["episode"].external_key, confirmed=True)
    result = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert result["status"] in {"BLOCKED", "NEEDS_REVIEW"}, result.get("blocker")
    assert case["music_events"].count("/release_task") == 1
    assert case["image_events"].count("/prompt") == 6
    assert case["fake"].calls.count("EpisodeSpec") == 1
    assert case["fake"].calls.count("LyricsSpec") == 1
    assert case["fake"].calls.count("MusicSpec") == 1
    assert case["fake"].calls.count("EpisodeVisualPlan") == 1
    assert len(case["analysis_calls"]) == 1


def test_partial_images_reuse_first_candidate(case, monkeypatch):
    from tovitunes.render import episode_assets

    original = episode_assets.persist_file
    crashed = False

    def crash(store, episode, kind, slot, path, deps, provenance=None):
        nonlocal crashed
        record = original(store, episode, kind, slot, path, deps, provenance)
        if kind == "visual_asset" and slot == "lesson_cup" and not crashed:
            crashed = True
            raise SystemExit("normalized object durably selected before audit finalization")
        return record

    monkeypatch.setattr(episode_assets, "persist_file", crash)
    monkeypatch.setattr(
        case["flow"],
        "_render",
        lambda *args: (_ for _ in ()).throw(ProductionStop("BLOCKED", "Offline render boundary")),
    )
    with pytest.raises(SystemExit):
        case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert case["image_events"].count("/prompt") == 1
    result = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert result["status"] == "BLOCKED"
    assert case["image_events"].count("/prompt") == 6
    with closing(case["store"].database.connect()) as db:
        assert [
            r[0]
            for r in db.execute("SELECT status FROM generation_requests WHERE kind='visual_asset'")
        ] == ["succeeded", "succeeded"]
        assert (
            db.execute(
                "SELECT count(*) FROM production_image_receipts "
                "WHERE normalized_artifact_id IS NOT NULL"
            ).fetchone()[0]
            == 2
        )


def test_ambiguous_image_cannot_resend(case):
    case["image_behavior"]["crash"] = 1
    with pytest.raises(SystemExit):
        case["flow"].produce(case["episode"].external_key, confirmed=True)
    case["image_behavior"]["crash"] = None
    result = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert result["status"] == "AMBIGUOUS" and result["current_stage"] == "VISUAL_ASSETS"
    assert case["image_events"].count("/prompt") == 1


def test_next_creation_and_active_run_resume(case, monkeypatch):
    def stopped(key, *, confirmed):
        assert confirmed
        return {"status": "BLOCKED", "episode_key": key}

    monkeypatch.setattr(case["flow"], "produce", stopped)
    first = case["flow"].produce_next(confirmed=True)
    assert first["episode_key"] not in {"colors-red-001", case["episode"].external_key}
    calls = list(case["fake"].calls)
    second = case["flow"].produce_next(confirmed=True)
    assert first == second and case["fake"].calls == calls
    assert calls.count("CreativeSubjectPool") == 1
    with closing(case["store"].database.connect()) as db:
        # Recover a create-next crash after creative completion, before the pipeline pointer update.
        db.execute("UPDATE production_next_runs SET episode_id=NULL")
        db.commit()
    assert case["flow"].produce_next(confirmed=True) == first
    assert case["fake"].calls == calls


def test_hard_exit_reclaims_only_marked_process_lease(tmp_path):
    import subprocess
    import sys

    from tovitunes.persistence.db import Database
    from tovitunes.persistence.leases import LeaseHeld, LeaseStore
    from tovitunes.pipeline.execution import production_execution

    database = Database(tmp_path / "crash.db")
    database.migrate()
    code = (
        "import os,sys; from pathlib import Path; "
        "from tovitunes.persistence.db import Database; "
        "from tovitunes.pipeline.execution import production_execution; "
        "lock=production_execution(Database(Path(sys.argv[1])), 'short-production:crash'); "
        "lock.__enter__(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", code, str(database.path)], check=True)
    with production_execution(database, "short-production:crash") as (leases, lease):
        leases.assert_owner(lease)
        with pytest.raises(LeaseHeld):
            with production_execution(database, "short-production:crash"):
                pytest.fail("live writer was not excluded")
    legacy = LeaseStore(database).acquire("creative-planning:legacy", duration_seconds=300)
    with pytest.raises(LeaseHeld):
        with production_execution(database, "creative-planning:legacy"):
            pytest.fail("unmarked legacy lease was incorrectly reclaimed")
    LeaseStore(database).release(legacy)


def test_future_committed_entities_need_no_object_registry():
    from tovitunes.creative.models import CreativeSubjectCandidate
    from tovitunes.creative.prompts import subject_messages
    from tovitunes.creative.validation import validate_subject

    candidate = CreativeSubjectCandidate(
        concept_id="triangle",
        premise="Tovi presents a triangle",
        hook="A triangle appears",
        setting="playroom",
        example_objects=("triangle",),
        song_angle="Name the triangle",
        reason="Committed objective",
    )
    validate_subject(candidate, {"triangle"}, [], allowed_examples=("triangle",))
    messages = subject_messages(
        {"eligible_concepts": [{"concept_id": "triangle", "example_entities": ["triangle"]}]}
    )
    assert "Familiar objects allowed: triangle" in messages[0]["content"]


@pytest.mark.parametrize("command", ["auto-next", "generate-next-short", "produce", "auto-resume"])
def test_cli_frontends_use_application_service(case, monkeypatch, capsys, command):
    calls = []

    class Service:
        def __init__(self, config, **kwargs):
            pass

        def produce_next(self, *, confirmed):
            calls.append(("next", confirmed))
            return {"status": "READY"}

        def produce(self, key, *, confirmed):
            calls.append((key, confirmed))
            return {"status": "READY"}

    monkeypatch.setattr(short_production, "ShortProductionWorkflow", Service)
    path = case["config"].database_path.parent / "operator.yaml"
    path.write_text(yaml.safe_dump(json.loads(case["config"].model_dump_json())), encoding="utf-8")
    args = ["--config", str(path), "production", command]
    if command in {"produce", "auto-resume"}:
        args += ["--episode-key", case["episode"].external_key]
    assert main(args) == 0
    assert calls[-1][1] is False
    assert main([*args, "--confirm-provider-generation"]) == 0
    assert calls[-1][1] is True
    assert not case["music_events"] and not case["image_events"]
    assert "READY" in capsys.readouterr().out


def test_web_frontend_plan_and_confirmation(case, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from tovitunes.web import app as web_app

    monkeypatch.setattr("socket.socket.connect", case["socket_connect"])
    calls = []

    class Service:
        def __init__(self, config, **kwargs):
            pass

        def plan(self, key=None):
            calls.append(("plan", key))
            return {"status": "READY", "provider_calls": 0}

        def produce(self, key, *, confirmed):
            calls.append(("produce", key, confirmed))
            return {"status": "PENDING_PROVIDER", "episode_key": key, "current_stage": "MUSIC"}

        def produce_next(self, *, confirmed):
            calls.append(("next", confirmed))
            return {"status": "NEEDS_REVIEW"}

    monkeypatch.setattr(web_app, "ShortProductionWorkflow", Service)
    app = web_app.create_app(case["config"])
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        key = case["episode"].external_key
        assert client.post(f"/api/episodes/{key}/produce").json()["provider_calls"] == 0
        assert calls == [("plan", key)]
        response = client.post(f"/api/episodes/{key}/produce?confirm_provider_generation=true")
        job_id = response.json()["job_id"]
        for _ in range(50):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert job["status"] == "pending_provider"
        assert calls[-1] == ("produce", key, True)
        assert client.post("/api/production/generate-next-short").json()["provider_calls"] == 0
    app.state.jobs.close()
    assert not case["music_events"] and not case["image_events"]


def test_published_red_is_read_only_in_new_pipeline(ready, monkeypatch):
    (config, database, episode, _), render, _, metadata = ready
    from test_public_release_v1 import record_upload

    record_upload(ready)
    before = rows_snapshot(config.database_path)
    monkeypatch.setattr(
        "socket.socket.connect", lambda *args: pytest.fail("historical provider call")
    )
    result = ShortProductionWorkflow(config).produce(episode.external_key, confirmed=True)
    assert result["status"] == "COMPLETE" and result["historical"]
    assert result["final_render_id"] == render.identity.artifact_id
    assert rows_snapshot(database.path) == before
    with closing(database.connect()) as db:
        assert (
            db.execute("SELECT youtube_video_id FROM publication_attempts").fetchone()[0]
            == "video-123"
        )
        assert (
            db.execute(
                "SELECT artifact_id FROM artifact_selections WHERE kind='publication_metadata'"
            ).fetchone()[0]
            == metadata.identity.artifact_id
        )


def test_pending_music_endpoint_change_blocks_query(case):
    case["music_behavior"]["state"] = "pending"
    assert (
        case["flow"].produce(case["episode"].external_key, confirmed=True)["status"]
        == "PENDING_PROVIDER"
    )
    calls = list(case["music_events"])
    case["flow"].config = case["config"].model_copy(
        update={
            "music_generation": case["config"].music_generation.model_copy(
                update={"base_url": "http://127.0.0.1:8002"}
            )
        }
    )
    result = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert result["status"] == "BLOCKED"
    assert case["music_events"] == calls


def test_compatible_environment_reuse_preserves_brand_selection(case, monkeypatch):
    from tovitunes.render.environment_sets import decide_set, generate_set, select_set

    stock = generate_set(case["config"], confirmed=True)
    aid = stock["manifest_artifact_id"]
    decide_set(
        case["config"],
        aid,
        status="approved",
        actor="human:fixture",
        reason="Reviewed shared world",
    )
    select_set(case["config"], aid)
    with closing(case["store"].database.connect()) as db:
        before = [
            tuple(row)
            for row in db.execute(
                "SELECT * FROM artifact_selections WHERE owner_scope='brand' ORDER BY rowid"
            )
        ]
    monkeypatch.setattr(
        case["flow"],
        "_render",
        lambda *args: (_ for _ in ()).throw(ProductionStop("BLOCKED", "Offline render boundary")),
    )
    result = case["flow"].produce(case["episode"].external_key, confirmed=True)
    assert result["current_stage"] == "RENDER", result.get("blocker")
    assert case["environment_events"].count("/prompt") == 4
    with closing(case["store"].database.connect()) as db:
        assert [
            tuple(row)
            for row in db.execute(
                "SELECT * FROM artifact_selections WHERE owner_scope='brand' ORDER BY rowid"
            )
        ] == before


@pytest.mark.parametrize("outcome", ["remote_started", "ambiguous", "terminal_failure"])
def test_uncertain_or_failed_publication_never_uploads_again(ready, monkeypatch, outcome):
    from dataclasses import replace

    from test_public_release_v1 import record_upload

    (config, database, episode, _), _, _, _ = ready
    record_upload(ready)
    with closing(database.connect()) as db:
        db.execute("UPDATE publication_attempts SET outcome=?", (outcome,))
        db.commit()
    enabled = config.model_copy(
        update={
            "automation": ProductionAutomationConfig(auto_publish=True, require_human_review=False)
        }
    )
    gate = replace(evaluate_release(config, episode.external_key), private_test_upload_allowed=True)
    monkeypatch.setattr(short_production, "evaluate_release", lambda *args: gate)
    before = rows_snapshot(database.path)
    with pytest.raises(ProductionStop) as stopped:
        ShortProductionWorkflow(enabled)._publication(episode)
    assert stopped.value.status == ("FAILED" if outcome == "terminal_failure" else "AMBIGUOUS")
    assert rows_snapshot(database.path) == before
