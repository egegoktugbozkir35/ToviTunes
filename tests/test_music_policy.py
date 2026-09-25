"""Offline policy gates; no real provider or paid API is used."""

import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from tovitunes.music.benchmark import MusicBenchmark, plan
from tovitunes.music.models import (
    AXES,
    LyricCandidate,
    MusicBrief,
    MusicReview,
    TimedText,
    TimingAnalysis,
    load_brief,
    load_lyrics,
)
from tovitunes.music.providers import FakeMusicProvider
from tovitunes.persistence.db import Database

ROOT = Path(__file__).resolve().parents[1] / "benchmarks/music"


@pytest.fixture
def case(
    tmp_path: Path,
) -> tuple[MusicBenchmark, MusicBrief, LyricCandidate, str, FakeMusicProvider]:
    database = Database(tmp_path / "music.sqlite")
    database.migrate()
    benchmark = MusicBenchmark(database, tmp_path / "audio")
    brief = load_brief(ROOT / "colors_red_v1.yaml")
    lyrics = load_lyrics(ROOT / "colors_red_lyrics_v1.yaml")
    provider = FakeMusicProvider()
    blind_id = benchmark.run(plan(brief, lyrics, [provider])[0], provider)["blind_id"]
    return benchmark, brief, lyrics, blind_id, provider


def rights_evidence() -> dict[str, object]:
    snapshot = "Fixture terms explicitly grant commercial music use on this account tier."
    return {
        "provider": "offline_fake",
        "model": "sine-v1",
        "tier": "fixture-commercial",
        "account_id": "offline-fixture",
        "terms_version": "fixture-v1",
        "terms_date": "2026-01-01",
        "terms_source": "offline fixture",
        "terms_snapshot": snapshot,
        "terms_sha256": sha256(snapshot.encode()).hexdigest(),
        "usage_mode": "commercial",
        "commercial_use_allowed": True,
    }


def qa_evidence() -> dict[str, object]:
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


def passing_prerequisites(
    benchmark: MusicBenchmark, brief: MusicBrief, lyrics: LyricCandidate, blind_id: str
) -> None:
    assert benchmark.evaluate_lyrics(brief, lyrics)["status"] == "pass"
    assert benchmark.evaluate_rights(blind_id, rights_evidence())["status"] == "pass"
    assert benchmark.evaluate_qa(blind_id, qa_evidence())["status"] == "pass"


def test_zero_human_reviews_or_decisions_needed(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, provider = case
    passing_prerequisites(benchmark, brief, lyrics, blind_id)
    assert benchmark.evaluate_approval(blind_id)["status"] == "pass"
    assert benchmark.status()[0]["approval_status"] == "approved"
    with benchmark.database.connect() as db:
        assert db.execute("SELECT count(*) FROM music_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM music_lyric_decisions").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT count(*) FROM music_decisions WHERE actor_type = 'human'"
            ).fetchone()[0]
            == 0
        )
    assert provider.calls == 1
    assert all(row["evaluator_type"] == "machine" for row in benchmark.policy_status())


def test_rights_unknown_and_incomplete_configuration_block(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, _ = case
    benchmark.evaluate_lyrics(brief, lyrics)
    benchmark.evaluate_qa(blind_id, qa_evidence())
    assert benchmark.evaluate_approval(blind_id)["status"] == "blocked"
    evidence = rights_evidence()
    del evidence["terms_snapshot"]
    assert benchmark.evaluate_rights(blind_id, evidence)["status"] == "blocked"
    assert benchmark.status()[0]["rights_status"] == "unknown"
    assert benchmark.evaluate_approval(blind_id)["status"] == "blocked"


def test_rights_configuration_must_match_request_and_snapshot(case: tuple) -> None:
    benchmark, _, _, blind_id, _ = case
    evidence = rights_evidence()
    evidence["provider"] = "someone-else"
    assert benchmark.evaluate_rights(blind_id, evidence)["status"] == "blocked"
    evidence = rights_evidence()
    evidence["terms_snapshot"] = "changed"
    assert benchmark.evaluate_rights(blind_id, evidence)["status"] == "blocked"
    assert benchmark.evaluate_rights(blind_id, rights_evidence())["status"] == "pass"
    assert benchmark.status()[0]["rights_status"] == "commercial_use_confirmed"


def test_qa_failure_invalidates_and_repass_needs_new_approval(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, _ = case
    passing_prerequisites(benchmark, brief, lyrics, blind_id)
    benchmark.evaluate_approval(blind_id)
    failed = qa_evidence()
    failed["teaching_intelligibility"] = {"passed": False, "source": "fixture transcript"}
    assert benchmark.evaluate_qa(blind_id, failed)["status"] == "fail"
    assert benchmark.status()[0]["approval_status"] == "pending"
    assert benchmark.evaluate_approval(blind_id)["status"] == "blocked"
    assert benchmark.evaluate_qa(blind_id, qa_evidence())["status"] == "pass"
    assert benchmark.status()[0]["approval_status"] == "pending"
    assert benchmark.evaluate_approval(blind_id)["status"] == "pass"
    with benchmark.database.connect() as db:
        history = db.execute(
            "SELECT status FROM music_decisions WHERE blind_id = ? "
            "AND decision_type = 'approval' ORDER BY rowid",
            (blind_id,),
        ).fetchall()
    assert [row[0] for row in history] == ["approved", "pending", "approved"]


def test_exact_lyrics_policy_failure_invalidates_and_repass_waits(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, _ = case
    passing_prerequisites(benchmark, brief, lyrics, blind_id)
    benchmark.evaluate_approval(blind_id)
    wrong_brief = brief.model_copy(update={"id": "wrong_brief"})
    assert benchmark.evaluate_lyrics(wrong_brief, lyrics)["status"] == "fail"
    assert benchmark.status()[0]["approval_status"] == "pending"
    assert benchmark.evaluate_lyrics(brief, lyrics)["status"] == "pass"
    assert benchmark.status()[0]["approval_status"] == "pending"
    assert benchmark.evaluate_approval(blind_id)["status"] == "pass"


def test_rights_revocation_and_repass_wait_for_approval(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, _ = case
    passing_prerequisites(benchmark, brief, lyrics, blind_id)
    benchmark.evaluate_approval(blind_id)
    bad = rights_evidence()
    bad["commercial_use_allowed"] = False
    assert benchmark.evaluate_rights(blind_id, bad)["status"] == "blocked"
    assert benchmark.status()[0]["approval_status"] == "pending"
    benchmark.evaluate_rights(blind_id, rights_evidence())
    assert benchmark.status()[0]["approval_status"] == "pending"
    assert benchmark.evaluate_approval(blind_id)["status"] == "pass"


def test_timing_machine_approval(case: tuple) -> None:
    benchmark, _, _, blind_id, _ = case
    sha = benchmark.status()[0]["sha256"]
    analysis = TimingAnalysis(
        version=1,
        audio_sha256=sha,
        duration_seconds=30,
        beat_seconds=(0.0, 0.5),
        downbeat_seconds=(0.0,),
        sections=(TimedText(start=0, end=30, text="song"),),
        lyric_lines=(TimedText(start=1, end=3, text="Red is a color"),),
        words=(TimedText(start=1, end=2, text="Red"),),
    )
    benchmark.save_timing(blind_id, analysis)
    assert benchmark.evaluate_timing(blind_id, 1)["status"] == "pass"
    with benchmark.database.connect() as db:
        row = db.execute("SELECT * FROM music_timing_decisions").fetchone()
    assert row["status"] == "approved" and row["actor_type"] == "machine"


def test_timing_incomplete_fails_closed(case: tuple) -> None:
    benchmark, _, _, blind_id, _ = case
    benchmark.save_timing(
        blind_id,
        TimingAnalysis(
            version=1, audio_sha256=benchmark.status()[0]["sha256"], duration_seconds=30
        ),
    )
    assert benchmark.evaluate_timing(blind_id, 1)["status"] == "fail"
    with benchmark.database.connect() as db:
        assert db.execute("SELECT count(*) FROM music_timing_decisions").fetchone()[0] == 0


def test_policy_records_are_immutable(case: tuple) -> None:
    benchmark, brief, lyrics, _, _ = case
    benchmark.evaluate_lyrics(brief, lyrics)
    with benchmark.database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE music_policy_evaluations SET status = 'fail'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM music_policy_evaluations")


def test_manual_rejection_veto_and_optional_review(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, _ = case
    passing_prerequisites(benchmark, brief, lyrics, blind_id)
    benchmark.review(
        MusicReview(
            blind_id=blind_id,
            reviewer="teacher",
            scores=dict.fromkeys(AXES, 3),
            evidence="optional listening",
        )
    )
    benchmark.decision(blind_id, "approval", "rejected", "teacher", "heard a concern")
    assert benchmark.evaluate_approval(blind_id)["status"] == "blocked"
    benchmark.decision(blind_id, "approval", "pending", "teacher", "concern resolved")
    assert benchmark.evaluate_approval(blind_id)["status"] == "pass"


def test_manual_hard_failure_revokes(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, _ = case
    passing_prerequisites(benchmark, brief, lyrics, blind_id)
    benchmark.evaluate_approval(blind_id)
    benchmark.review(
        MusicReview(
            blind_id=blind_id,
            reviewer="producer",
            scores=dict.fromkeys(AXES, 2),
            evidence="artifact detected",
            hard_failures=("artifact",),
        )
    )
    assert benchmark.status()[0]["approval_status"] == "pending"
    assert benchmark.evaluate_approval(blind_id)["status"] == "blocked"


def test_audio_integrity_change_revokes_on_policy_rerun(case: tuple) -> None:
    benchmark, brief, lyrics, blind_id, _ = case
    passing_prerequisites(benchmark, brief, lyrics, blind_id)
    benchmark.evaluate_approval(blind_id)
    path = benchmark.audio_root / f"{benchmark.status()[0]['request_id']}.wav"
    path.write_bytes(b"tampered")
    assert benchmark.evaluate_approval(blind_id)["status"] == "blocked"
    assert benchmark.status()[0]["approval_status"] == "pending"


@pytest.mark.parametrize(
    "replacement",
    [
        "Red is not a color.",
        "Red is blue.",
        "Blue is a color.",
        "Sing like Disney!",
        "Red is a color, apple only.",
        "Red is a color, take a gun.",
    ],
)
def test_deterministic_lyrics_policy_rejects_known_failures(case: tuple, replacement: str) -> None:
    benchmark, brief, lyrics, _, _ = case
    changed = lyrics.model_copy(
        update={"lines": (lyrics.lines[0].model_copy(update={"text": replacement}),)}
    )
    assert benchmark.evaluate_lyrics(brief, changed)["status"] == "fail"
