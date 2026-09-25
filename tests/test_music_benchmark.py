"""Offline contract and recovery checks for the first-song benchmark."""

import os
import sqlite3
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


def fake_qa_checks() -> dict[str, object]:
    return {
        key: {"passed": True, "source": "offline deterministic fixture"}
        for key in (
            "lyric_adherence",
            "educational_correctness",
            "teaching_intelligibility",
            "preschool_safety",
            "beat_usable",
            "production_fit",
            "artifact_free",
        )
    }


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
    with pytest.raises(ValueError, match="rights not confirmed"):
        store.decision(blind_id, "approval", "approved", "teacher", "checked")
    store.decision(blind_id, "rights", "commercial_use_confirmed", "operator", "tier evidence")
    with pytest.raises(ValueError, match="exact lyrics"):
        store.decision(blind_id, "approval", "approved", "teacher", "checked")
    store.lyric_decision(lyrics, "approved", "teacher", "educational and diction check")
    with pytest.raises(ValueError, match="music QA"):
        store.decision(blind_id, "approval", "approved", "teacher", "checked")
    store.evaluate_qa(blind_id, fake_qa_checks())
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
    store._record_receipt(request_id, result, inspect_wav(result.audio_bytes), "pcm_s16le")
    assert store.reconcile(request_id)["status"] == "succeeded"
    assert store.run(item, fake)["action"] == "reused"
    assert fake.calls == 1


@pytest.mark.parametrize(
    "interruption,recoverable",
    [
        ("before_stage", False),
        ("after_stage", False),
        ("after_receipt", True),
        ("after_final", True),
        ("after_mapping", True),
    ],
)
def test_returned_audio_crash_windows_are_provider_free(
    store: MusicBenchmark,
    inputs: tuple[MusicBrief, object],
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
    recoverable: bool,
) -> None:
    brief, lyrics = inputs
    fake = FakeMusicProvider()
    item = plan(brief, lyrics, [fake])[0]
    request_id = store.prepare(item)["request_id"]

    def interrupted(*args: object) -> None:
        raise RuntimeError("simulated process interruption")

    original_write = store._write_audio
    original_receipt = store._record_receipt
    original_finalize = store._finalize

    def after_stage(*args: object) -> None:
        interrupted()

    def after_receipt(candidate_id: str) -> None:
        if interruption == "after_final":
            os.replace(store._staged_path(candidate_id), store._audio_path(candidate_id))
        if interruption == "after_mapping":
            with store.database.connect() as db:
                receipt = db.execute(
                    "SELECT sha256 FROM music_receipts WHERE request_id = ?", (candidate_id,)
                ).fetchone()
                assert receipt is not None
                db.execute(
                    "INSERT INTO music_outputs (request_id, blind_id, relative_path, sha256, "
                    "created_at) VALUES (?, ?, ?, ?, 'interrupted')",
                    (candidate_id, "interrupted", f"{candidate_id}.wav", receipt["sha256"]),
                )
        interrupted()

    with monkeypatch.context() as patch:
        if interruption == "before_stage":
            patch.setattr(store, "_write_audio", interrupted)
        elif interruption == "after_stage":
            patch.setattr(store, "_record_receipt", after_stage)
        else:
            patch.setattr(store, "_finalize", after_receipt)
        result = store.run(item, fake)
    assert result["action"] == "local_reconciliation_required"
    assert fake.calls == 1
    assert store.run(item, fake)["action"] == "manual_reconciliation_required"
    assert fake.calls == 1
    recovery = store.reconcile(request_id)
    assert recovery["status"] == "succeeded" if recoverable else "status" not in recovery
    assert fake.calls == 1
    if recoverable:
        assert store.reconcile(request_id)["status"] == "succeeded"
        assert store.run(item, fake)["action"] == "reused"
    else:
        assert recovery["action"] == "provider_side_reconciliation_required"
    assert fake.calls == 1
    assert store._write_audio.__func__ is original_write.__func__
    assert store._record_receipt.__func__ is original_receipt.__func__
    assert store._finalize.__func__ is original_finalize.__func__


def test_database_success_and_mapping_invariants(
    store: MusicBenchmark, inputs: tuple[MusicBrief, object]
) -> None:
    brief, lyrics = inputs
    fake = FakeMusicProvider()
    item = plan(brief, lyrics, [fake])[0]
    request_id = store.prepare(item)["request_id"]
    store._transition(request_id, "remote_started")
    with store.database.connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE music_requests SET status = 'succeeded' WHERE request_id = ?", (request_id,)
        )
    result = fake.generate(item.canonical_spec, item.translated_request, lambda: None)
    store._write_audio(request_id, result.audio_bytes)
    store._record_receipt(request_id, result, inspect_wav(result.audio_bytes), "pcm_s16le")
    with store.database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO music_outputs "
                "(request_id, blind_id, relative_path, sha256, created_at) "
                "VALUES (?, 'bad', ?, ?, 'now')",
                (request_id, f"{request_id}.wav", "0" * 64),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE music_requests SET status = 'succeeded' WHERE request_id = ?", (request_id,)
            )
    assert store.reconcile(request_id)["status"] == "succeeded"
    with store.database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE music_requests SET status = 'ambiguous' WHERE request_id = ?", (request_id,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE music_outputs SET sha256 = ? WHERE request_id = ?", ("0" * 64, request_id)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM music_receipts WHERE request_id = ?", (request_id,))


def test_reconcile_rejects_receipt_with_wrong_decoded_metadata(
    store: MusicBenchmark, inputs: tuple[MusicBrief, object]
) -> None:
    brief, lyrics = inputs
    fake = FakeMusicProvider()
    item = plan(brief, lyrics, [fake])[0]
    request_id = store.prepare(item)["request_id"]
    store._transition(request_id, "remote_started")
    result = fake.generate(item.canonical_spec, item.translated_request, lambda: None)
    store._write_audio(request_id, result.audio_bytes)
    with store.database.connect() as db:
        db.execute(
            "UPDATE music_requests SET provider_request_id = ? WHERE request_id = ?",
            (result.provider_request_id, request_id),
        )
        db.execute(
            "INSERT INTO music_receipts "
            "(request_id, provider_request_id, sha256, byte_count, duration_seconds, "
            "mime_type, container, codec, response_metadata_json, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'now')",
            (
                request_id,
                result.provider_request_id,
                sha256(result.audio_bytes).hexdigest(),
                len(result.audio_bytes),
                31.0,
                "audio/wav",
                "wav",
                "pcm_s16le",
            ),
        )
    recovery = store.reconcile(request_id)
    assert recovery["action"] == "provider_side_reconciliation_required"
    assert "metadata" in recovery["reason"]
    assert store.run(item, fake)["action"] == "manual_reconciliation_required"
    assert fake.calls == 1


def test_audit_tables_and_decision_pairs_are_enforced(
    store: MusicBenchmark, inputs: tuple[MusicBrief, object]
) -> None:
    brief, lyrics = inputs
    result = store.run(plan(brief, lyrics, [FakeMusicProvider()])[0], FakeMusicProvider())
    blind_id = result["blind_id"]
    store.review(
        MusicReview(
            blind_id=blind_id, reviewer="human", scores=dict.fromkeys(AXES, 3), evidence="heard"
        )
    )
    store.decision(blind_id, "rights", "unknown", "human", "unverified")
    store.lyric_decision(lyrics, "approved", "human", "read")
    store.save_timing(
        blind_id,
        TimingAnalysis(version=1, audio_sha256=store.status()[0]["sha256"], duration_seconds=30),
    )
    store.timing_decision(blind_id, 1, "approved", "human", "heard")
    with store.database.connect() as db:
        for table in (
            "music_reviews",
            "music_decisions",
            "music_lyric_decisions",
            "music_timing",
            "music_timing_decisions",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {table} SET created_at = 'changed'")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"DELETE FROM {table}")
        for decision_type, status in (
            ("rights", "approved"),
            ("approval", "restricted"),
            ("bogus", "pending"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO music_decisions (decision_id, blind_id, decision_type, "
                    "status, actor, evidence, created_at) "
                    "VALUES (?, ?, ?, ?, 'sql', 'invalid', 'now')",
                    (f"bad-{decision_type}-{status}", blind_id, decision_type, status),
                )


def test_exact_lyric_rejection_revokes_only_matching_audio(
    store: MusicBenchmark, inputs: tuple[MusicBrief, object]
) -> None:
    brief, lyrics = inputs
    other_lyrics = lyrics.model_copy(update={"rhyme_notes": "different exact candidate"})
    fake = FakeMusicProvider()
    first = store.run(plan(brief, lyrics, [fake])[0], fake)
    other = store.run(plan(brief, other_lyrics, [fake])[0], fake)
    store.lyric_decision(lyrics, "approved", "teacher", "exact words checked")
    store.lyric_decision(other_lyrics, "approved", "teacher", "other words checked")
    for candidate in (first, other):
        blind_id = candidate["blind_id"]
        store.decision(blind_id, "rights", "commercial_use_confirmed", "owner", "license")
        store.evaluate_qa(blind_id, fake_qa_checks())
        for reviewer in ("teacher", "producer"):
            store.review(
                MusicReview(
                    blind_id=blind_id,
                    reviewer=reviewer,
                    scores=dict.fromkeys(AXES, 3),
                    evidence="clean listening",
                )
            )
        store.decision(blind_id, "approval", "approved", "teacher", "heard and approved")
    store.lyric_decision(lyrics, "rejected", "editor", "exact lyric issue")
    statuses = {row["blind_id"]: row["approval_status"] for row in store.status()}
    assert statuses[first["blind_id"]] == "pending"
    assert statuses[other["blind_id"]] == "approved"
    with store.database.connect() as db:
        history = db.execute(
            "SELECT status, actor, evidence FROM music_decisions "
            "WHERE blind_id = ? AND decision_type = 'approval' ORDER BY rowid",
            (first["blind_id"],),
        ).fetchall()
    assert [(row["status"], row["actor"]) for row in history] == [
        ("approved", "teacher"),
        ("pending", "automated_release_policy_v1"),
    ]
    assert "exact lyrics rejected" in history[1]["evidence"]
    store.lyric_decision(lyrics, "approved", "editor", "corrected review")
    assert (
        next(row for row in store.status() if row["blind_id"] == first["blind_id"])[
            "approval_status"
        ]
        == "pending"
    )
    store.decision(first["blind_id"], "approval", "approved", "teacher", "new explicit review")
    assert (
        next(row for row in store.status() if row["blind_id"] == first["blind_id"])[
            "approval_status"
        ]
        == "approved"
    )


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
