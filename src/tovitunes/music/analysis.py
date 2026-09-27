"""Offline measurements; provider text and generation prompts are never observations."""

from __future__ import annotations

import importlib.metadata
import json
import math
import os
import re
import statistics
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import miniaudio
from pydantic import Field

from tovitunes.music.analysis_models import (
    AlignmentEvidence,
    AnalysisThresholds,
    AnalyzerProvenance,
    AudioAnalysis,
    EditOperation,
    LyricComparison,
    RecognizedWord,
    RhythmEvidence,
    TechnicalMetrics,
    TranscriptionEvidence,
)
from tovitunes.music.audio import inspect_audio
from tovitunes.music.models import CanonicalMusicSpec, StrictModel, TimedText, TimingAnalysis

ANALYZER_VERSION = "real_audio_v1"
REQUIRED_PHRASES = ("red", "color", "red apple", "red ball", "red is a color")


class AnalysisConfig(StrictModel):
    analyzer_version: Literal["real_audio_v1"] = "real_audio_v1"
    asr_model: str = Field(default="small.en", min_length=1)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    allow_model_download: bool = False


def configuration_sha(config: AnalysisConfig) -> str:
    payload = json.dumps(analyzer_configuration(config), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def analyzer_configuration(config: AnalysisConfig) -> dict[str, str | bool]:
    configuration: dict[str, str | bool] = config.model_dump(mode="json")
    source = sha256()
    for name in ("analysis.py", "analysis_models.py", "audio.py", "models.py", "benchmark.py"):
        source.update(name.encode())
        source.update(Path(__file__).with_name(name).read_text(encoding="utf-8").encode())
    configuration["source_code_sha256"] = source.hexdigest()
    for package in ("miniaudio", "librosa", "whisperx", "torchaudio"):
        configuration[f"{package}_version"] = _version(package)
    return configuration


def normalized_words(text: str) -> tuple[str, ...]:
    """Normalize case, punctuation and spacing only; never paraphrase lyrics."""
    return tuple(re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.casefold()))


def compare_lyrics(expected: str, recognized: str, *, complete: bool) -> LyricComparison:
    reference, hypothesis = normalized_words(expected), normalized_words(recognized)
    phrases: dict[str, bool | None] = {}
    for phrase in REQUIRED_PHRASES:
        needle = normalized_words(phrase)
        phrases[phrase] = (
            any(hypothesis[i : i + len(needle)] == needle for i in range(len(hypothesis)))
            if complete
            else None
        )
    if not complete:
        return LyricComparison(
            status="incomplete",
            expected_transcript=expected,
            recognized_transcript=recognized,
            expected_word_count=len(reference),
            recognized_word_count=len(hypothesis),
            required_phrase_presence=phrases,
        )
    costs = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for i in range(len(reference) + 1):
        costs[i][0] = i
    for j in range(len(hypothesis) + 1):
        costs[0][j] = j
    for i, left in enumerate(reference, 1):
        for j, right in enumerate(hypothesis, 1):
            costs[i][j] = min(
                costs[i - 1][j - 1] + (left != right),
                costs[i - 1][j] + 1,
                costs[i][j - 1] + 1,
            )
    i, j = len(reference), len(hypothesis)
    operations = []
    while i or j:
        if i and j and costs[i][j] == costs[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            kind: Literal["match", "substitution"] = (
                "match" if reference[i - 1] == hypothesis[j - 1] else "substitution"
            )
            operations.append(
                EditOperation(kind=kind, expected=reference[i - 1], recognized=hypothesis[j - 1])
            )
            i -= 1
            j -= 1
        elif i and costs[i][j] == costs[i - 1][j] + 1:
            operations.append(EditOperation(kind="deletion", expected=reference[i - 1]))
            i -= 1
        else:
            operations.append(EditOperation(kind="insertion", recognized=hypothesis[j - 1]))
            j -= 1
    operations.reverse()
    matches = sum(o.kind == "match" for o in operations)
    substitutions = sum(o.kind == "substitution" for o in operations)
    insertions = sum(o.kind == "insertion" for o in operations)
    deletions = sum(o.kind == "deletion" for o in operations)
    return LyricComparison(
        status="complete",
        expected_transcript=expected,
        recognized_transcript=recognized,
        expected_word_count=len(reference),
        recognized_word_count=len(hypothesis),
        matched_word_count=matches,
        substitutions=substitutions,
        insertions=insertions,
        deletions=deletions,
        wer=(substitutions + insertions + deletions) / len(reference) if reference else None,
        coverage_ratio=matches / len(reference) if reference else None,
        required_phrase_presence=phrases,
        operations=tuple(operations),
    )


def technical_measurements(decoded: Any) -> TechnicalMetrics:
    samples = decoded.samples
    channels, rate, frames = decoded.nchannels, decoded.sample_rate, decoded.num_frames
    if channels <= 0 or rate <= 0 or frames <= 0 or len(samples) != channels * frames:
        raise ValueError("decoded PCM shape is invalid")
    window_frames = max(1, round(rate * 0.01))
    threshold = 10 ** (-50 / 20)
    square_sum = total = peak = 0.0
    clipped = invalid = 0
    silent_windows = longest = current = leading = 0
    first_non_silent_seen = False
    window_square = 0.0
    window_samples = 0
    window_count = 0
    for index, sample in enumerate(samples):
        if not math.isfinite(sample):
            invalid += 1
            sample = 0.0
        absolute = abs(sample)
        peak = max(peak, absolute)
        clipped += absolute >= 0.999
        total += sample
        square = sample * sample
        square_sum += square
        window_square += square
        window_samples += 1
        if (index + 1) % (window_frames * channels) == 0 or index + 1 == len(samples):
            window_count += 1
            if math.sqrt(window_square / window_samples) <= threshold:
                silent_windows += 1
                current += 1
                longest = max(longest, current)
                if not first_non_silent_seen:
                    leading += 1
            else:
                first_non_silent_seen = True
                current = 0
            window_square = 0.0
            window_samples = 0
    seconds_per_window = window_frames / rate
    return TechnicalMetrics(
        decode_integrity=invalid == 0,
        invalid_pcm_samples=invalid,
        sample_rate_hz=rate,
        channel_count=channels,
        decoded_frames=frames,
        duration_seconds=frames / rate,
        peak_amplitude=peak,
        rms_amplitude=math.sqrt(square_sum / len(samples)),
        dc_offset=total / len(samples),
        clipping_ratio=clipped / len(samples),
        near_silence_ratio=silent_windows / window_count,
        longest_near_silent_span_seconds=min(frames / rate, longest * seconds_per_window),
        beginning_silence_seconds=min(frames / rate, leading * seconds_per_window),
        ending_silence_seconds=min(frames / rate, current * seconds_per_window),
        near_silence_threshold_dbfs=-50,
        clipping_threshold_amplitude=0.999,
        window_seconds=seconds_per_window,
    )


def _provenance(
    name: str,
    version: str,
    sha: str,
    *,
    device: str = "cpu",
    model: str | None = None,
    revision: str | None = None,
    configuration: dict[str, str | bool | int] | None = None,
) -> AnalyzerProvenance:
    return AnalyzerProvenance(
        name=name,
        version=version,
        model_name=model,
        model_revision=revision,
        device=device,
        timestamp=datetime.now(UTC).isoformat(),
        source_audio_sha256=sha,
        configuration=configuration or {},
    )


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def analyze_rhythm(decoded: Any, brief: Any) -> RhythmEvidence:
    try:
        import librosa
        import numpy as np

        pcm = np.frombuffer(decoded.samples, dtype=np.float32).reshape(-1, decoded.nchannels)
        mono = pcm.mean(axis=1)
        tempo, beat_frames = librosa.beat.beat_track(y=mono, sr=decoded.sample_rate, trim=False)
        times = tuple(float(t) for t in librosa.frames_to_time(beat_frames, sr=decoded.sample_rate))
        bpm = float(np.asarray(tempo).reshape(-1)[0])
        if not math.isfinite(bpm) or bpm <= 0 or len(times) < 2:
            raise ValueError("beat track contains insufficient beats")
        if tuple(sorted(times)) != times or any(t < 0 or t > decoded.duration for t in times):
            raise ValueError("beat track is unordered or out of bounds")
        intervals = [b - a for a, b in zip(times, times[1:])]
        mean = statistics.mean(intervals)
        deviation = statistics.pstdev(intervals)
        return RhythmEvidence(
            status="complete",
            estimated_bpm=bpm,
            beat_seconds=times,
            beat_count=len(times),
            mean_interval_seconds=mean,
            interval_std_seconds=deviation,
            interval_cv=deviation / mean,
            target_bpm=brief.target_bpm,
            allowed_bpm_range=brief.bpm_range,
        )
    except ImportError:
        reason = "librosa optional dependency is unavailable"
    except Exception as exc:
        reason = f"beat analysis failed: {type(exc).__name__}"
    return RhythmEvidence(
        status="unavailable",
        beat_count=0,
        target_bpm=brief.target_bpm,
        allowed_bpm_range=brief.bpm_range,
        failure_reason=reason,
    )


def _aligned_words(segments: list[dict[str, Any]], duration: float) -> tuple[RecognizedWord, ...]:
    words = []
    for segment in segments:
        for word in segment.get("words", []):
            start, end = word.get("start"), word.get("end")
            if not isinstance(start, (float, int)) or not isinstance(end, (float, int)):
                raise ValueError("alignment omitted word timestamp")
            if not 0 <= start < end <= duration:
                raise ValueError("alignment word is outside audio")
            score = word.get("score")
            words.append(
                RecognizedWord(
                    start=float(start),
                    end=float(end),
                    text=str(word.get("word", "")),
                    score=float(score) if score is not None else None,
                )
            )
    if any(a.end > b.start for a, b in zip(words, words[1:])):
        raise ValueError("alignment words overlap or are unordered")
    return tuple(words)


def _prepare_alignment_resources(root: Path, allow_download: bool) -> None:
    import nltk

    nltk_root = root / "nltk"
    if str(nltk_root) not in nltk.data.path:
        nltk.data.path.insert(0, str(nltk_root))
    try:
        nltk.data.find("tokenizers/punkt_tab/english/")
    except LookupError:
        if not allow_download:
            raise FileNotFoundError("English sentence tokenizer is not cached") from None
        nltk.download("punkt_tab", download_dir=str(nltk_root), quiet=True, raise_on_error=True)


def _asr_revision(path: Path, model_name: str) -> str | None:
    model = Path(model_name)
    if model.is_dir() and (model / "model.bin").is_file():
        return "sha256:" + sha256((model / "model.bin").read_bytes()).hexdigest()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", model_name):
        return None
    refs = list(
        (path.parent / ".analysis-models" / "asr").glob(
            f"models--*--faster-whisper-{model_name}/refs/main"
        )
    )
    if len(refs) == 1:
        revision = refs[0].read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", revision):
            return revision
    return None


def _alignment_revision(path: Path) -> str | None:
    weight = (
        path.parent
        / ".analysis-models"
        / "alignment"
        / ("wav2vec2_fairseq_base_ls960_asr_ls960.pth")
    )
    return "sha256:" + sha256(weight.read_bytes()).hexdigest() if weight.is_file() else None


@contextmanager
def _model_cache_environment(root: Path, allow_download: bool) -> Iterator[None]:
    values = {"HF_HOME": str(root / "hf"), "TORCH_HOME": str(root / "torch")}
    if not allow_download:
        values.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def transcribe_and_align(
    path: Path,
    spec: CanonicalMusicSpec,
    duration: float,
    config: AnalysisConfig,
) -> tuple[TranscriptionEvidence, AlignmentEvidence, str, str]:
    with _model_cache_environment(path.parent / ".analysis-models", config.allow_model_download):
        return _transcribe_and_align_impl(path, spec, duration, config)


def _transcribe_and_align_impl(
    path: Path,
    spec: CanonicalMusicSpec,
    duration: float,
    config: AnalysisConfig,
) -> tuple[TranscriptionEvidence, AlignmentEvidence, str, str]:
    """Independent ASR first; a separate canonical forced alignment supplies timing only."""
    empty_alignment = AlignmentEvidence(
        status="unavailable",
        aligned_word_count=0,
        aligned_line_count=0,
        failure_reason="ASR unavailable",
    )
    try:
        import torch
        import whisperx
    except ImportError:
        return (
            TranscriptionEvidence(
                status="unavailable", failure_reason="WhisperX optional dependency is unavailable"
            ),
            empty_alignment,
            "unavailable",
            config.device,
        )
    device = "cuda" if config.device == "auto" and torch.cuda.is_available() else config.device
    if device == "auto":
        device = "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    try:
        audio = whisperx.load_audio(str(path))
        model = whisperx.load_model(
            config.asr_model,
            device,
            compute_type=compute_type,
            language="en",
            download_root=str(path.parent / ".analysis-models" / "asr"),
            local_files_only=not config.allow_model_download,
        )
        recognized = model.transcribe(audio, batch_size=4)
        segments = recognized.get("segments", [])
        text = " ".join(str(s.get("text", "")).strip() for s in segments).strip()
        if not text:
            raise ValueError("ASR returned no recognized words")
    except Exception as exc:
        return (
            TranscriptionEvidence(
                status="incomplete", failure_reason=f"independent ASR failed: {type(exc).__name__}"
            ),
            empty_alignment,
            _version("whisperx"),
            device,
        )
    try:
        import torchaudio

        _prepare_alignment_resources(path.parent / ".analysis-models", config.allow_model_download)
        alignment_cache = path.parent / ".analysis-models" / "alignment"
        bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        if (
            not config.allow_model_download
            and not (alignment_cache / Path(bundle._path).name).is_file()
        ):
            raise FileNotFoundError("alignment model is not cached")
        model_a, metadata = whisperx.load_align_model(
            language_code="en",
            device=device,
            model_name="WAV2VEC2_ASR_BASE_960H",
            model_dir=str(alignment_cache),
            model_cache_only=not config.allow_model_download,
        )
        recognized_alignment = whisperx.align(
            segments,
            model_a,
            metadata,
            audio,
            device,
            return_char_alignments=False,
            interpolate_method="ignore",
        )
        recognized_words = _aligned_words(recognized_alignment.get("segments", []), duration)
        scores = [w.score for w in recognized_words if w.score is not None]
        transcription = TranscriptionEvidence(
            status="complete",
            recognized_text=text,
            words=recognized_words,
            mean_word_score=statistics.mean(scores) if scores else None,
        )
    except Exception as exc:
        return (
            TranscriptionEvidence(
                status="incomplete",
                recognized_text=text,
                failure_reason=f"independent word alignment failed: {type(exc).__name__}",
            ),
            empty_alignment,
            _version("whisperx"),
            device,
        )
    try:
        canonical = whisperx.align(
            [{"start": 0.0, "end": duration, "text": spec.lyrics.text()}],
            model_a,
            metadata,
            audio,
            device,
            return_char_alignments=False,
            interpolate_method="ignore",
        )
        words = _aligned_words(canonical.get("segments", []), duration)
        if tuple(
            token for word in words for token in normalized_words(word.text)
        ) != normalized_words(spec.lyrics.text()):
            raise ValueError("canonical alignment omitted or changed words")
        lines = []
        offset = 0
        for line in spec.lyrics.lines:
            count = len(normalized_words(line.text))
            group = words[offset : offset + count]
            if len(group) != count:
                raise ValueError("canonical alignment omitted a lyric line")
            lines.append(TimedText(start=group[0].start, end=group[-1].end, text=line.text))
            offset += count
        alignment = AlignmentEvidence(
            status="complete",
            canonical_words=words,
            lyric_lines=tuple(lines),
            aligned_word_count=len(words),
            aligned_line_count=len(lines),
        )
    except Exception as exc:
        alignment = AlignmentEvidence(
            status="incomplete",
            aligned_word_count=0,
            aligned_line_count=0,
            failure_reason=f"canonical forced alignment failed: {type(exc).__name__}",
        )
    return transcription, alignment, _version("whisperx"), device


def build_timing(
    version: int,
    sha: str,
    duration: float,
    rhythm: RhythmEvidence,
    alignment: AlignmentEvidence,
    spec: CanonicalMusicSpec,
    comparison: LyricComparison,
) -> TimingAnalysis:
    thresholds = AnalysisThresholds()
    reliable = (
        alignment.status == "complete"
        and comparison.status == "complete"
        and comparison.coverage_ratio is not None
        and comparison.coverage_ratio >= thresholds.minimum_timing_coverage
        and comparison.wer is not None
        and comparison.wer <= thresholds.maximum_timing_wer
        and bool(alignment.canonical_words)
        and all(
            w.score is not None and w.score >= thresholds.minimum_alignment_score
            for w in alignment.canonical_words
        )
    )
    sections: list[TimedText] = []
    if reliable:
        section_names = {
            "hook": "hook",
            "teaching_line": "teaching",
            "reinforcement": "reinforcement",
            "short_ending": "ending",
        }
        for section, label in section_names.items():
            members = [
                line
                for line, canonical in zip(alignment.lyric_lines, spec.lyrics.lines)
                if canonical.section == section
            ]
            if members:
                sections.append(TimedText(start=members[0].start, end=members[-1].end, text=label))
    return TimingAnalysis(
        version=version,
        audio_sha256=sha,
        duration_seconds=duration,
        estimated_bpm=rhythm.estimated_bpm,
        beat_seconds=rhythm.beat_seconds if rhythm.status == "complete" else (),
        downbeat_seconds=(),
        sections=tuple(sections),
        lyric_lines=alignment.lyric_lines if reliable else (),
        words=tuple(
            TimedText(start=w.start, end=w.end, text=w.text) for w in alignment.canonical_words
        )
        if reliable
        else (),
        phonemes=(),
    )


def analyze_audio(
    path: Path,
    data: bytes,
    mime_type: str,
    spec: CanonicalMusicSpec,
    blind_id: str,
    request_id: str,
    version: int,
    config: AnalysisConfig,
) -> AudioAnalysis:
    sha = sha256(data).hexdigest()
    info = inspect_audio(data, mime_type)
    decoded = (
        miniaudio.mp3_read_f32(data) if mime_type == "audio/mpeg" else miniaudio.wav_read_f32(data)
    )
    technical = technical_measurements(decoded)
    if abs(info.duration_seconds - technical.duration_seconds) > 0.001:
        raise ValueError("decoded duration does not match verified receipt")
    rhythm = analyze_rhythm(decoded, spec.brief)
    transcription, alignment, asr_version, device = transcribe_and_align(
        path,
        spec,
        technical.duration_seconds,
        config,
    )
    comparison = compare_lyrics(
        spec.lyrics.text(),
        transcription.recognized_text,
        complete=bool(transcription.recognized_text),
    )
    timing = build_timing(
        version, sha, technical.duration_seconds, rhythm, alignment, spec, comparison
    )
    config_sha = configuration_sha(config)
    warnings = [
        "downbeat analysis unavailable; no downbeats inferred from beat positions",
        "intro/outro analysis unavailable; no requested prompt boundaries copied",
    ]
    for evidence in (rhythm, transcription, alignment):
        if evidence.failure_reason:
            warnings.append(evidence.failure_reason)
    return AudioAnalysis(
        version=version,
        blind_id=blind_id,
        request_id=request_id,
        audio_sha256=sha,
        analyzer_config_sha256=config_sha,
        analyzer_configuration=analyzer_configuration(config),
        duration_seconds=technical.duration_seconds,
        source_format=mime_type,
        sample_rate_hz=technical.sample_rate_hz,
        bitrate_bps=info.bitrate_bps,
        channel_count=technical.channel_count,
        technical_metrics=technical,
        transcription=transcription,
        lyric_comparison=comparison,
        rhythm=rhythm,
        alignment=alignment,
        timing=timing,
        warnings=tuple(warnings),
        analyzer_provenance={
            "technical": _provenance("miniaudio-pcm-measurements", _version("miniaudio"), sha),
            "rhythm": _provenance("librosa.beat.beat_track", _version("librosa"), sha),
            "transcription": _provenance(
                "WhisperX independent ASR",
                asr_version,
                sha,
                device=device,
                model=config.asr_model,
                revision=_asr_revision(path, config.asr_model)
                if transcription.recognized_text
                else None,
                configuration={
                    "language": "en",
                    "batch_size": 4,
                    "diarization": False,
                    "compute_type": "float16" if device == "cuda" else "int8",
                    "canonical_prompt_supplied": False,
                },
            ),
            "canonical_alignment": _provenance(
                "WhisperX forced alignment",
                f"WhisperX {asr_version}; torchaudio {_version('torchaudio')}",
                sha,
                device=device,
                model="WAV2VEC2_ASR_BASE_960H",
                revision=_alignment_revision(path) if alignment.status == "complete" else None,
                configuration={
                    "language": "en",
                    "interpolate_method": "ignore",
                    "phoneme_timestamps_emitted": False,
                },
            ),
        },
    )
