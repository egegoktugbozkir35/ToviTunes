"""Offline contract and recovery checks for the first-song benchmark."""

from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from tovitunes.music.benchmark import MusicBenchmark, inspect_wav, plan
from tovitunes.music.models import (
    AXES,
    MusicBrief,
    MusicReview,
    MusicRubric,
    TimingAnalysis,
    load_brief,
    load_lyrics,
    load_rubric,
)
from tovitunes.music.providers import FakeMusicProvider
from tovitunes.persistence.db import Database

ROOT = Path(__file__).resolve().parents[1] / "benchmarks/music"


@pytest.fixture
def inputs() -> tuple[MusicBrief, object]:
    return load_brief(ROOT / "colors_red_v1.yaml"), load_lyrics(ROOT / "colors_red_lyrics_v1.yaml")


@pytest.fixture
def store(tmp_path: Path) -> MusicBenchmark:
    database = Database(tmp_path / "test.sqlite")
    database.migrate()
    return MusicBenchmark(database, tmp_path / "audio")


def test_canonical_inputs_and_rubric(inputs: tuple[MusicBrief, object]) -> None:
    brief, lyrics = inputs
    assert brief.preferred_duration_seconds == (30, 40)
    assert brief.maximum_duration_seconds == 45
    assert brief.bpm_range[0] <= brief.target_bpm <= brief.bpm_range[1]
    assert lyrics.approval == "pending"
    assert "Red is a color" in lyrics.text()
    assert "apple" in lyrics.text() and "ball" in lyrics.text()
    rubric = load_rubric(ROOT / "rubric.v1.yaml")
    assert sum(rubric.weights.values()) == 100
    with pytest.raises(ValidationError):
        MusicBrief.model_validate({**brief.model_dump(), "bpm_range": [130, 140]})
    with pytest.raises(ValidationError):
        MusicRubric.model_validate({**rubric.model_dump(), "weights": {"x": 100}})


def test_plan_fingerprints_and_translation(inputs: tuple[MusicBrief, object]) -> None:
    brief, lyrics = inputs
    fake = FakeMusicProvider()
    first = plan(brief, lyrics, [fake])
    assert len(first) == 3
    assert first == plan(brief, lyrics, [fake])
    assert len({item.input_fingerprint for item in first}) == 3
    assert first[0].translated_request["bpm"] == 112
    assert first[0].capabilities["vocals"] is False
    assert fake.calls == 0


@pytest.mark.parametrize(
    "behavior,expected",
    [
        ("preflight", "retryable_failure"),
        ("retryable", "retryable_failure"),
        ("ambiguous", "ambiguous"),
        ("terminal", "terminal_failure"),
        ("malformed", "terminal_failure"),
    ],
)
def test_failure_states_are_fail_closed(
    store: MusicBenchmark,
    inputs: tuple[MusicBrief, object],
    behavior: str,
    expected: str,
) -> None:
    brief, lyrics = inputs
    item = plan(brief, lyrics, [FakeMusicProvider()])[0]
    fake = FakeMusicProvider(behavior)
    first = store.run(item, fake)
    assert first["status"] == expected
    row = store.request(first["request_id"])
    assert (row["remote_started_at"] is None) is (behavior == "preflight")
    fake.behavior = "success"
    second = store.run(item, fake)
    assert fake.calls == (2 if behavior == "preflight" else 1)
    assert second["status"] == ("succeeded" if behavior == "preflight" else expected)


def test_receipt_idempotence_and_immutable_bytes(
    store: MusicBenchmark,
    inputs: tuple[MusicBrief, object],
) -> None:
    brief, lyrics = inputs
    fake = FakeMusicProvider()
    item = plan(brief, lyrics, [fake])[0]
    first = store.run(item, fake)
    assert first["status"] == "succeeded"
    assert store.run(item, fake)["action"] == "reused"
    assert fake.calls == 1
    row = store.status()[0]
    assert row["duration_seconds"] == 30
    assert row["mime_type"] == "audio/wav"
    assert row["byte_count"] > 0
    assert row["actual_cost_amount"] is None and row["usage_json"] is None
    assert row["rights_status"] == "unknown" and row["approval_status"] == "pending"
    assert row["provider_request_id"] == "fake-1"
    audio = store.audio_root / f"{first['request_id']}.wav"
    assert sha256(audio.read_bytes()).hexdigest() == row["sha256"]
    with pytest.raises(FileExistsError):
        store._write_audio(first["request_id"], b"replacement")
    audio.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        store.run(item, fake)


def test_blind_review_decisions_and_timing(
    store: MusicBenchmark,
    inputs: tuple[MusicBrief, object],
) -> None:
    brief, lyrics = inputs
    result = store.run(plan(brief, lyrics, [FakeMusicProvider()])[0], FakeMusicProvider())
    blind_id = result["blind_id"]
    exported = store.review_export()
    assert len(exported) == 1
    assert "provider" not in exported[0] and "model" not in exported[0]
    review = MusicReview(
        blind_id=blind_id,
        reviewer="teacher",
        scores=dict.fromkeys(AXES, 3),
        evidence="Teaching phrase and pulse checked by listening.",
    )
    store.review(review)
    report = store.review_report(load_rubric(ROOT / "rubric.v1.yaml"))
    assert report[0]["weighted_score"] == 75
    assert "provider" not in report[0]
    with pytest.raises(ValueError, match="cleared rights"):
        store.decision(blind_id, "approval", "approved", "teacher", "checked")
    store.decision(blind_id, "rights", "commercial_use_confirmed", "operator", "tier evidence")
    with pytest.raises(ValueError, match="approved lyrics"):
        store.decision(blind_id, "approval", "approved", "teacher", "checked")
    store.lyric_decision(lyrics, "approved", "teacher", "educational and diction check")
    with pytest.raises(ValueError, match="two clean"):
        store.decision(blind_id, "approval", "approved", "teacher", "checked")
    store.review(review.model_copy(update={"reviewer": "producer"}))
    store.decision(blind_id, "approval", "approved", "teacher", "separate human decision")
    assert store.status()[0]["approval_status"] == "approved"
    store.decision(blind_id, "rights", "restricted", "operator", "later license finding")
    assert store.status()[0]["approval_status"] == "pending"
    timing = TimingAnalysis(
        version=1,
        audio_sha256=exported[0]["audio_sha256"],
        duration_seconds=30,
        estimated_bpm=112,
        beat_seconds=(0.0, 0.536),
    )
    store.save_timing(blind_id, timing)
    with pytest.raises(ValueError, match="cannot approve"):
        store.save_timing(
            blind_id, timing.model_copy(update={"version": 2, "approval": "approved"})
        )
    assert store.timing_decision(blind_id, 1, "approved", "editor", "beat map checked")
    with pytest.raises(Exception):
        store.save_timing(blind_id, timing)
    with pytest.raises(ValueError, match="hash"):
        store.save_timing(
            blind_id, timing.model_copy(update={"version": 2, "audio_sha256": "0" * 64})
        )


def test_timing_validation_and_wav(inputs: tuple[MusicBrief, object]) -> None:
    brief, lyrics = inputs
    item = plan(brief, lyrics, [FakeMusicProvider()])[0]
    fake = FakeMusicProvider()
    result = fake.generate(item.canonical_spec, item.translated_request, lambda: None)
    assert inspect_wav(result.audio_bytes) == 30
    with pytest.raises(ValueError):
        inspect_wav(b"broken")
    with pytest.raises(ValidationError):
        TimingAnalysis(
            version=1, audio_sha256="a" * 64, duration_seconds=30, beat_seconds=(2.0, 1.0)
        )


def test_local_receipt_reconciliation(
    store: MusicBenchmark,
    inputs: tuple[MusicBrief, object],
) -> None:
    brief, lyrics = inputs
    fake = FakeMusicProvider()
    item = plan(brief, lyrics, [fake])[0]
    request_id = store.prepare(item)["request_id"]
    store._transition(request_id, "remote_started")
    result = fake.generate(item.canonical_spec, item.translated_request, lambda: None)
    store._write_audio(request_id, result.audio_bytes)
    store._record_receipt(request_id, result, inspect_wav(result.audio_bytes))
    assert store.reconcile(request_id)["status"] == "succeeded"
    assert store.run(item, fake)["action"] == "reused"
    assert fake.calls == 1


def test_duplicate_remote_start_is_ambiguous(
    store: MusicBenchmark,
    inputs: tuple[MusicBrief, object],
) -> None:
    brief, lyrics = inputs

    class DoubleStart(FakeMusicProvider):
        def generate(self, spec, translated, on_remote_start):  # type: ignore[no-untyped-def]
            self.calls += 1
            on_remote_start()
            on_remote_start()
            raise AssertionError("unreachable")

    fake = DoubleStart()
    item = plan(brief, lyrics, [fake])[0]
    assert store.run(item, fake)["status"] == "ambiguous"
    assert store.run(item, fake)["action"] == "manual_reconciliation_required"
    assert fake.calls == 1
