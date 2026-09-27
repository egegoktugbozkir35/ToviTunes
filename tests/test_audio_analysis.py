"""Offline evidence and persistence checks; no model downloads or provider traffic."""

import io
import json
import sqlite3
import wave
from array import array
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tovitunes.music.analysis import (
    AnalysisConfig,
    build_timing,
    compare_lyrics,
    normalized_words,
    technical_measurements,
    transcribe_and_align,
)
from tovitunes.music.analysis_models import (
    AlignmentEvidence,
    AudioAnalysis,
    RecognizedWord,
    RhythmEvidence,
    TranscriptionEvidence,
)
from tovitunes.music.audio import inspect_audio
from tovitunes.music.benchmark import MusicBenchmark, plan
from tovitunes.music.models import TimedText, TimingAnalysis, load_brief, load_lyrics
from tovitunes.music.providers import FakeMusicProvider, MusicResult
from tovitunes.persistence.db import Database

ROOT = Path(__file__).resolve().parents[1] / "benchmarks/music"


@pytest.fixture(autouse=True)
def deterministic_ml(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args, **kwargs):
        raise AssertionError("audio analysis must not contact a provider or network")

    monkeypatch.setattr("socket.socket.connect", no_network)

    def rhythm(decoded, brief):
        return RhythmEvidence(
            status="unavailable",
            beat_count=0,
            target_bpm=brief.target_bpm,
            allowed_bpm_range=brief.bpm_range,
            failure_reason="fixture",
        )

    def asr(path, spec, duration, config):
        return (
            TranscriptionEvidence(status="unavailable", failure_reason="fixture"),
            AlignmentEvidence(
                status="unavailable",
                aligned_word_count=0,
                aligned_line_count=0,
                failure_reason="fixture",
            ),
            "fixture",
            "cpu",
        )

    monkeypatch.setattr("tovitunes.music.analysis.analyze_rhythm", rhythm)
    monkeypatch.setattr("tovitunes.music.analysis.transcribe_and_align", asr)


@pytest.fixture
def case(tmp_path: Path) -> tuple[MusicBenchmark, str, FakeMusicProvider]:
    database = Database(tmp_path / "state.sqlite")
    database.migrate()
    store = MusicBenchmark(database, tmp_path / "audio")
    provider = FakeMusicProvider()
    item = plan(
        load_brief(ROOT / "colors_red_v1.yaml"),
        load_lyrics(ROOT / "colors_red_lyrics_v1.yaml"),
        [provider],
        attempt=1,
    )[0]
    blind_id = store.run(item, provider)["blind_id"]
    return store, blind_id, provider


def test_normalization_and_edit_operations() -> None:
    assert normalized_words(" RED,  red!\nA red apple. ") == ("red", "red", "a", "red", "apple")
    substitution = compare_lyrics("red color", "red blue", complete=True)
    insertion = compare_lyrics("red color", "red bright color", complete=True)
    deletion = compare_lyrics("red color", "red", complete=True)
    assert (substitution.substitutions, substitution.insertions, substitution.deletions) == (
        1,
        0,
        0,
    )
    assert (insertion.substitutions, insertion.insertions, insertion.deletions) == (0, 1, 0)
    assert (deletion.substitutions, deletion.insertions, deletion.deletions) == (0, 0, 1)
    assert substitution.wer == pytest.approx(0.5)
    assert [o.kind for o in insertion.operations] == ["match", "insertion", "match"]
    phrases = compare_lyrics("red apple red ball", "red apple ball", complete=True)
    assert phrases.required_phrase_presence["red apple"] is True
    assert phrases.required_phrase_presence["red ball"] is False
    weak = compare_lyrics("red is a color", "red is a color", complete=False)
    assert weak.status == "incomplete" and weak.wer is None
    assert weak.required_phrase_presence["red is a color"] is None


def test_versioned_report_qa_and_original_bytes(case: tuple) -> None:
    store, blind_id, provider = case
    row = store.status()[0]
    path = store.audio_root / f"{row['request_id']}.wav"
    original_sha = sha256(path.read_bytes()).hexdigest()
    report, reused = store.analyze_audio(blind_id, 1, AnalysisConfig())
    assert not reused and report.audio_sha256 == original_sha
    assert report.technical_metrics.decode_integrity
    assert report.technical_metrics.channel_count == 1
    assert report.transcription.status == "unavailable"
    assert report.lyric_comparison.wer is None
    assert report.timing.downbeat_seconds == ()
    assert store.evaluate_analysis_qa(blind_id, 1)["status"] == "fail"
    assert store.evaluate_timing(blind_id, 1)["status"] == "fail"
    again, reused = store.analyze_audio(blind_id, 1, AnalysisConfig())
    assert reused and again == report
    with pytest.raises(ValueError, match="version already exists"):
        store.analyze_audio(blind_id, 1, AnalysisConfig(asr_model="other"))
    second, reused = store.analyze_audio(blind_id, 2, AnalysisConfig())
    assert not reused and second.version == 2
    with store.database.connect() as db:
        first_json = db.execute(
            "SELECT analysis_json FROM music_audio_analysis WHERE version=1"
        ).fetchone()[0]
        assert AudioAnalysis.model_validate_json(first_json) == report
        assert db.execute("SELECT count(*) FROM music_audio_analysis").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM music_timing").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE music_audio_analysis SET version = 2")
    assert sha256(path.read_bytes()).hexdigest() == original_sha
    assert provider.calls == 1
    with pytest.raises(ValidationError):
        AudioAnalysis.model_validate({**report.model_dump(), "passed": True})
    with pytest.raises(ValidationError):
        AudioAnalysis.model_validate({**report.model_dump(), "audio_sha256": "f" * 64})
    with store.database.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO music_audio_analysis VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    blind_id,
                    2,
                    report.request_id,
                    "f" * 64,
                    report.analyzer_config_sha256,
                    report.model_dump_json(),
                    "fixture",
                ),
            )


@pytest.mark.parametrize("damage", ["missing", "modified", "corrupt"])
def test_audio_integrity_fails_before_analysis(case: tuple, damage: str) -> None:
    store, blind_id, provider = case
    request_id = store.status()[0]["request_id"]
    path = store.audio_root / f"{request_id}.wav"
    if damage == "missing":
        path.unlink()
    elif damage == "modified":
        path.write_bytes(path.read_bytes() + b"x")
    else:
        path.write_bytes(b"not audio")
    with pytest.raises(ValueError):
        store.analyze_audio(blind_id, 1, AnalysisConfig())
    with store.database.connect() as db:
        assert db.execute("SELECT count(*) FROM music_audio_analysis").fetchone()[0] == 0
    assert provider.calls == 1


def test_ordered_timing_and_missing_downbeats() -> None:
    with pytest.raises(ValidationError):
        RhythmEvidence(
            status="complete",
            beat_seconds=(2.0, 1.0),
            beat_count=2,
            target_bpm=112,
            allowed_bpm_range=(100, 124),
        )
    with pytest.raises(ValidationError):
        AlignmentEvidence(
            status="complete",
            canonical_words=(),
            lyric_lines=(),
            aligned_word_count=0,
            aligned_line_count=0,
        )
    with pytest.raises(ValidationError):
        TimingAnalysis(
            version=1,
            audio_sha256="a" * 64,
            duration_seconds=3,
            words=(TimedText(start=2, end=4, text="red"),),
        )
    for field in ("words", "lyric_lines"):
        with pytest.raises(ValidationError):
            TimingAnalysis.model_validate(
                {
                    "version": 1,
                    "audio_sha256": "a" * 64,
                    "duration_seconds": 3,
                    field: [
                        {"start": 2, "end": 2.5, "text": "color"},
                        {"start": 1, "end": 1.5, "text": "red"},
                    ],
                }
            )
    with pytest.raises(ValueError):
        inspect_audio(b"corrupt mp3", "audio/mpeg")


def test_58_second_audio_fails_45_second_ceiling(tmp_path: Path) -> None:
    class LongLocalFixture(FakeMusicProvider):
        def generate(self, spec, translated, on_remote_start):  # type: ignore[no-untyped-def]
            self.calls += 1
            on_remote_start()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(b"\x00\x00" * (58 * 8000))
            return MusicResult(
                audio_bytes=buffer.getvalue(),
                mime_type="audio/wav",
                provider_request_id="local-fixture",
            )

    database = Database(tmp_path / "state.sqlite")
    database.migrate()
    store = MusicBenchmark(database, tmp_path / "audio")
    provider = LongLocalFixture()
    item = plan(
        load_brief(ROOT / "colors_red_v1.yaml"),
        load_lyrics(ROOT / "colors_red_lyrics_v1.yaml"),
        [provider],
        attempt=1,
    )[0]
    blind_id = store.run(item, provider)["blind_id"]
    report, _ = store.analyze_audio(blind_id, 1, AnalysisConfig())
    result = store.evaluate_analysis_qa(blind_id, 1)
    assert report.duration_seconds == pytest.approx(58)
    assert result["status"] == "fail"
    evidence = json.loads(result["evidence_json"])
    assert evidence["objective_checks"]["duration_in_scope"] is False
    assert evidence["derived_checks"]["production_fit"]["status"] == "fail"


def test_pcm_measurements_and_invalid_samples() -> None:
    decoded = SimpleNamespace(
        samples=array("f", [0.0] * 10 + [0.5] * 20 + [1.0] * 10 + [0.0] * 10),
        nchannels=1,
        sample_rate=100,
        num_frames=50,
    )
    metrics = technical_measurements(decoded)
    assert metrics.duration_seconds == 0.5
    assert metrics.peak_amplitude == 1
    assert metrics.rms_amplitude == pytest.approx((15 / 50) ** 0.5)
    assert metrics.clipping_ratio == pytest.approx(0.2)
    assert metrics.near_silence_ratio == pytest.approx(0.4)
    assert metrics.beginning_silence_seconds == pytest.approx(0.1)
    assert metrics.ending_silence_seconds == pytest.approx(0.1)
    decoded.samples[20] = float("nan")
    assert technical_measurements(decoded).invalid_pcm_samples == 1
    assert not technical_measurements(decoded).decode_integrity


def test_failed_asr_keeps_failure_and_no_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    def fail(*args, **kwargs):
        raise RuntimeError("fixture load failure")

    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    )
    monkeypatch.setitem(
        sys.modules, "whisperx", SimpleNamespace(load_audio=lambda path: [], load_model=fail)
    )
    spec = plan(
        load_brief(ROOT / "colors_red_v1.yaml"),
        load_lyrics(ROOT / "colors_red_lyrics_v1.yaml"),
        [FakeMusicProvider()],
        attempt=1,
    )[0].canonical_spec
    transcript, alignment, _, device = transcribe_and_align(
        Path("fixture.mp3"), spec, 30, AnalysisConfig(device="cpu")
    )
    assert transcript.status == "incomplete"
    assert "ffmpeg" in transcript.failure_reason or "asr_model_missing" in transcript.failure_reason
    assert transcript.words == alignment.canonical_words == ()
    assert device == "cpu"


def test_forced_alignment_cannot_prove_lyric_adherence() -> None:
    spec = plan(
        load_brief(ROOT / "colors_red_v1.yaml"),
        load_lyrics(ROOT / "colors_red_lyrics_v1.yaml"),
        [FakeMusicProvider()],
        attempt=1,
    )[0].canonical_spec
    words = tuple(
        RecognizedWord(start=i * 0.4, end=i * 0.4 + 0.3, text=word, score=0.99)
        for i, word in enumerate(normalized_words(spec.lyrics.text()))
    )
    lines = []
    offset = 0
    for line in spec.lyrics.lines:
        count = len(normalized_words(line.text))
        lines.append(
            TimedText(start=words[offset].start, end=words[offset + count - 1].end, text=line.text)
        )
        offset += count
    aligned = AlignmentEvidence(
        status="complete",
        canonical_words=words,
        lyric_lines=tuple(lines),
        aligned_word_count=len(words),
        aligned_line_count=len(lines),
    )
    rhythm = RhythmEvidence(
        status="unavailable", beat_count=0, target_bpm=112, allowed_bpm_range=(100, 124)
    )
    comparison = compare_lyrics(spec.lyrics.text(), "blue sky", complete=True)
    timing = build_timing(1, "a" * 64, 30, rhythm, aligned, spec, comparison)
    assert comparison.wer > 0.25
    assert timing.words == timing.lyric_lines == timing.downbeat_seconds == ()


def test_legacy_booleans_cannot_unlock_google_automatic_approval(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite")
    database.migrate()
    store = MusicBenchmark(database, tmp_path / "audio")
    provider = FakeMusicProvider()
    provider.provider = "google"  # local fixture identity only; no Google adapter is called
    item = plan(
        load_brief(ROOT / "colors_red_v1.yaml"),
        load_lyrics(ROOT / "colors_red_lyrics_v1.yaml"),
        [provider],
        attempt=1,
    )[0]
    blind_id = store.run(item, provider)["blind_id"]
    fabricated = {
        key: {"passed": True, "source": "unsupported assertion"}
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
    assert store.evaluate_qa(blind_id, fabricated)["status"] == "pass"
    result = store.evaluate_approval(blind_id)
    assert result["status"] == "blocked"
    assert "music QA policy not passing" in json.loads(result["evidence_json"])["blockers"]
    assert store.status()[0]["approval_status"] == "pending"


def test_cli_analysis_never_calls_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tovitunes.cli import main

    def forbidden(*args, **kwargs):
        raise AssertionError("generation and provider-resume are forbidden during analysis")

    monkeypatch.setattr("tovitunes.music.vertex_lyria.VertexLyriaProvider.generate", forbidden)
    monkeypatch.setattr(
        "tovitunes.music.vertex_lyria.VertexLyriaProvider.generate_with_identity", forbidden
    )
    monkeypatch.setattr(MusicBenchmark, "provider_resume", forbidden)
    database = Database(tmp_path / "state.sqlite")
    database.migrate()
    store = MusicBenchmark(database, tmp_path / "music-benchmark")
    provider = FakeMusicProvider()
    item = plan(
        load_brief(ROOT / "colors_red_v1.yaml"),
        load_lyrics(ROOT / "colors_red_lyrics_v1.yaml"),
        [provider],
        attempt=1,
    )[0]
    blind_id = store.run(item, provider)["blind_id"]
    config = tmp_path / "config.yaml"
    brand = ROOT.parents[1] / "brands/tovitunes"
    config.write_text(
        f"schema_version: 1\ndatabase_path: {database.path.as_posix()}\n"
        f"data_root: {tmp_path.as_posix()}\nbrand_root: {brand.as_posix()}\n",
        encoding="utf-8",
    )
    assert (
        main(["--config", str(config), "music-benchmark", "analyze-audio", "--blind-id", blind_id])
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["qa_status"] == "fail"
    assert summary["transcription_status"] == "unavailable"
    assert provider.calls == 1


@pytest.mark.parametrize("missing", [False, True])
def test_independent_asr_and_canonical_alignment_use_separate_inputs(
    monkeypatch: pytest.MonkeyPatch,
    missing: bool,
) -> None:
    import sys
    from contextlib import nullcontext

    monkeypatch.setattr("tovitunes.music.analysis._prepare_alignment_resources", lambda *args: None)
    monkeypatch.setattr(
        "tovitunes.music.analysis.tokenizer_environment", lambda *args: nullcontext()
    )
    monkeypatch.setattr("tovitunes.music.analysis.ffmpeg_status", lambda: {"status": "found"})
    monkeypatch.setattr(
        "tovitunes.music.analysis.require_cache", lambda *args: Path("fixture-cache")
    )
    spec = plan(
        load_brief(ROOT / "colors_red_v1.yaml"),
        load_lyrics(ROOT / "colors_red_lyrics_v1.yaml"),
        [FakeMusicProvider()],
        attempt=1,
    )[0].canonical_spec
    recognized_text = spec.lyrics.text().replace("ahead", "instead")
    calls = []

    def align(segments, model, metadata, audio, device, **kwargs):
        assert kwargs["interpolate_method"] == "ignore"
        text = segments[0]["text"]
        calls.append(text)
        words = [
            {"word": word, "start": i * 0.3, "end": i * 0.3 + 0.2, "score": 0.9}
            for i, word in enumerate(normalized_words(text))
        ]
        if missing and len(calls) == 2:
            words[0].pop("start")
        return {"segments": [{"words": words}]}

    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    )
    monkeypatch.setitem(
        sys.modules,
        "torchaudio",
        SimpleNamespace(
            pipelines=SimpleNamespace(WAV2VEC2_ASR_BASE_960H=SimpleNamespace(_path="fixture.pth"))
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "whisperx",
        SimpleNamespace(
            load_audio=lambda path: [],
            load_model=lambda *args, **kwargs: SimpleNamespace(
                transcribe=lambda *a, **k: {
                    "segments": [{"start": 0, "end": 30, "text": recognized_text}]
                }
            ),
            load_align_model=lambda **kwargs: (object(), {}),
            align=align,
        ),
    )
    transcript, alignment, _, _ = transcribe_and_align(
        Path("fixture.mp3"), spec, 30, AnalysisConfig(device="cpu")
    )
    assert calls == [recognized_text, spec.lyrics.text()]
    assert transcript.recognized_text == recognized_text
    assert transcript.mean_word_score == pytest.approx(0.9)
    assert alignment.aligned_line_count == (6 if missing else 7)
    assert alignment.aligned_word_count == (35 if missing else 36)
    assert alignment.missing_words == (("red",) if missing else ())
    comparison = compare_lyrics(spec.lyrics.text(), transcript.recognized_text, complete=True)
    assert comparison.substitutions == 1
    rhythm = RhythmEvidence(
        status="unavailable", beat_count=0, target_bpm=112, allowed_bpm_range=(100, 124)
    )
    timing = build_timing(1, "a" * 64, 30, rhythm, alignment, spec, comparison)
    if missing:
        assert timing.words == timing.lyric_lines == ()
    else:
        assert len(timing.words) == 36 and len(timing.lyric_lines) == 7
    assert timing.downbeat_seconds == timing.phonemes == ()
