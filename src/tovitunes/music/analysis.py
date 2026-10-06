"""Offline measurements; provider text and generation prompts are never observations."""

from __future__ import annotations

import importlib.metadata
import json
import math
import re
import statistics
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
from tovitunes.music.analysis_runtime import (
    ALIGNMENT_MODEL,
    RuntimeFailure,
    diagnostic,
    ffmpeg_status,
    model_environment,
    prepare_tokenizer,
    require_cache,
    tokenizer_environment,
)
from tovitunes.music.audio import inspect_audio
from tovitunes.music.models import (
    CanonicalMusicSpec,
    StrictModel,
    TimedText,
    TimeRange,
    TimingAnalysis,
)
from tovitunes.music.timing_runtime import analyze_rhythm

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
    for name in (
        "analysis.py",
        "analysis_models.py",
        "analysis_runtime.py",
        "timing_runtime.py",
        "audio.py",
        "models.py",
        "benchmark.py",
    ):
        source.update(name.encode())
        source.update(Path(__file__).with_name(name).read_text(encoding="utf-8").encode())
    configuration["source_code_sha256"] = source.hexdigest()
    for package in ("miniaudio", "librosa", "whisperx", "torchaudio", "beat-this"):
        configuration[f"{package}_version"] = _version(package)
    return configuration


def normalized_words(text: str) -> tuple[str, ...]:
    """Normalize case, punctuation and spacing only; never paraphrase lyrics."""
    return tuple(re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.casefold()))


def compare_lyrics(
    expected: str,
    recognized: str,
    *,
    complete: bool,
    required_phrases: tuple[str, ...] = REQUIRED_PHRASES,
) -> LyricComparison:
    reference, hypothesis = normalized_words(expected), normalized_words(recognized)
    phrases: dict[str, bool | None] = {}
    for phrase in required_phrases:
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
    prepare_tokenizer(root, allow_download)


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
    inventory = path.parent / ".analysis-models" / "inventory.json"
    if inventory.is_file():
        data = json.loads(inventory.read_text(encoding="utf-8"))
        for asset in data.get("assets", []):
            if asset.get("asset") == "alignment" and asset.get("files"):
                return "sha256:" + str(asset["files"][0]["sha256"])
    return None


def transcribe_and_align(
    path: Path,
    spec: CanonicalMusicSpec,
    duration: float,
    config: AnalysisConfig,
) -> tuple[TranscriptionEvidence, AlignmentEvidence, str, str]:
    with model_environment(path.parent / ".analysis-models", config.allow_model_download):
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
    stage = "ffmpeg_unavailable"
    root = path.parent / ".analysis-models"
    try:
        if ffmpeg_status()["status"] != "found":
            raise RuntimeFailure("ffmpeg_unavailable: install a working ffmpeg executable on PATH")
        stage = "device_failed"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeFailure("cuda_unavailable: select --device cpu")
        stage = "asr_model_missing_or_incomplete"
        snapshot = None if config.allow_model_download else require_cache(root, config.asr_model)
        stage = "audio_decode_failed"
        audio = whisperx.load_audio(str(path))
        stage = "asr_model_load_failed"
        model = whisperx.load_model(
            str(snapshot) if snapshot else config.asr_model,
            device,
            compute_type=compute_type,
            language="en",
            download_root=str(path.parent / ".analysis-models" / "asr"),
            local_files_only=not config.allow_model_download,
            use_auth_token=False,
        )
        stage = "asr_inference_failed"
        recognized = model.transcribe(audio, batch_size=4)
        segments = recognized.get("segments", [])
        text = " ".join(str(s.get("text", "")).strip() for s in segments).strip()
        if not text:
            raise ValueError("ASR returned no recognized words")
    except Exception as exc:
        return (
            TranscriptionEvidence(status="incomplete", failure_reason=diagnostic(stage, exc)),
            empty_alignment,
            _version("whisperx"),
            device,
        )
    try:
        stage = "nltk_resource_missing"
        _prepare_alignment_resources(root, config.allow_model_download)
        stage = "alignment_model_load_failed"
        if not config.allow_model_download:
            require_cache(root, config.asr_model)
        model_a, metadata = whisperx.load_align_model(
            language_code="en",
            device=device,
            model_name=ALIGNMENT_MODEL,
            model_dir=str(root / "alignment"),
            model_cache_only=not config.allow_model_download,
        )
        stage = "alignment_inference_failed"
        with tokenizer_environment(root):
            recognized_alignment = whisperx.align(
                segments,
                model_a,
                metadata,
                audio,
                device,
                return_char_alignments=False,
                interpolate_method="ignore",
            )
        stage = "invalid_timestamp_output"
        recognized_words = _aligned_words(recognized_alignment.get("segments", []), duration)
        if tuple(token for word in recognized_words for token in normalized_words(word.text)) != (
            normalized_words(text)
        ):
            raise RuntimeFailure("independent_alignment_missing_words: transcript preserved")
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
                failure_reason=diagnostic(stage, exc),
            ),
            empty_alignment,
            _version("whisperx"),
            device,
        )
    try:
        stage = "canonical_alignment_inference_failed"
        with tokenizer_environment(root):
            canonical = whisperx.align(
                [{"start": 0.0, "end": duration, "text": spec.lyrics.text()}],
                model_a,
                metadata,
                audio,
                device,
                return_char_alignments=False,
                interpolate_method="ignore",
            )
        stage = "invalid_canonical_timestamp_output"
        raw_words = [
            w for segment in canonical.get("segments", []) for w in segment.get("words", [])
        ]
        expected = normalized_words(spec.lyrics.text())
        if (
            tuple(
                token for word in raw_words for token in normalized_words(str(word.get("word", "")))
            )
            != expected
        ):
            raise RuntimeFailure("canonical_alignment_omitted_or_changed_words")
        # Keep trustworthy observations, leaving missing timestamps absent. No interpolation.
        indexed: list[RecognizedWord | None] = []
        missing = []
        for raw in raw_words:
            if raw.get("start") is None or raw.get("end") is None:
                indexed.append(None)
                missing.append(str(raw.get("word", "")))
            else:
                indexed.append(_aligned_words([{"words": [raw]}], duration)[0])
        words = tuple(word for word in indexed if word is not None)
        if any(a.end > b.start for a, b in zip(words, words[1:])):
            raise RuntimeFailure("invalid_canonical_timestamp_output: overlapping words")
        lines = []
        offset = 0
        for line in spec.lyrics.lines:
            count = len(normalized_words(line.text))
            group = indexed[offset : offset + count]
            if len(group) == count and all(word is not None for word in group):
                present = [word for word in group if word is not None]
                lines.append(TimedText(start=present[0].start, end=present[-1].end, text=line.text))
            offset += count
        alignment = AlignmentEvidence(
            status="incomplete" if missing else "complete",
            canonical_words=words,
            lyric_lines=tuple(lines),
            aligned_word_count=len(words),
            aligned_line_count=len(lines),
            missing_words=tuple(missing),
            failure_reason="canonical_alignment_missing_word_timestamps" if missing else None,
        )
    except Exception as exc:
        alignment = AlignmentEvidence(
            status="incomplete",
            aligned_word_count=0,
            aligned_line_count=0,
            failure_reason=diagnostic(stage, exc),
        )
    return transcription, alignment, _version("whisperx"), device


def timing_admission_evidence(
    duration: float,
    alignment: AlignmentEvidence,
    spec: CanonicalMusicSpec,
    comparison: LyricComparison,
    transcription: TranscriptionEvidence,
) -> dict[str, Any]:
    """Two-source admission; CTC character means are not recognition probabilities."""
    thresholds = AnalysisThresholds()
    words = alignment.canonical_words
    expected = normalized_words(spec.lyrics.text())
    scores = [w.score for w in words if w.score is not None]
    mean = statistics.mean(scores) if words and len(scores) == len(words) else None
    minimum_word = min(
        (w for w in words if w.score is not None), key=lambda w: w.score or 0.0, default=None
    )
    lines_complete = len(alignment.lyric_lines) == len(spec.lyrics.lines)
    offset = 0
    for measured, canonical in zip(alignment.lyric_lines, spec.lyrics.lines):
        count = len(normalized_words(canonical.text))
        group = words[offset : offset + count]
        lines_complete = lines_complete and (
            len(group) == count
            and bool(group)
            and measured.text == canonical.text
            and measured.start == group[0].start
            and measured.end == group[-1].end
        )
        offset += count

    def ordered_bounded(sequence: tuple[TimedText, ...]) -> bool:
        return all(
            math.isfinite(w.start) and math.isfinite(w.end) and 0 <= w.start < w.end <= duration
            for w in sequence
        ) and all(a.end <= b.start for a, b in zip(sequence, sequence[1:]))

    checks = {
        "transcription_complete": transcription.status == "complete",
        "comparison_complete": comparison.status == "complete",
        "wer_in_range": comparison.wer is not None
        and comparison.wer <= thresholds.maximum_timing_wer,
        "coverage_in_range": comparison.coverage_ratio is not None
        and comparison.coverage_ratio >= thresholds.minimum_timing_coverage,
        "independent_mean_in_range": transcription.mean_word_score is not None
        and transcription.mean_word_score >= thresholds.minimum_alignment_score,
        "alignment_complete": alignment.status == "complete",
        "no_missing_timestamps": not alignment.missing_words and len(words) == len(expected),
        "canonical_scores_present": bool(words) and len(scores) == len(words),
        "canonical_mean_in_range": mean is not None and mean >= thresholds.minimum_alignment_score,
        "canonical_identity_matches": bool(expected)
        and len(words) == len(expected)
        and tuple(token for w in words for token in normalized_words(w.text)) == expected,
        "comparison_identity_matches": comparison.expected_word_count == len(expected)
        and comparison.expected_transcript == spec.lyrics.text()
        and comparison.recognized_transcript == transcription.recognized_text,
        "lines_complete": lines_complete,
        "timestamps_ordered_bounded": ordered_bounded(words)
        and ordered_bounded(alignment.lyric_lines)
        and ordered_bounded(transcription.words),
    }
    return {
        "rule": "aggregate_two_source_v1",
        "independent_mean_word_score": transcription.mean_word_score,
        "canonical_mean_word_score": mean,
        "canonical_minimum_word_score": minimum_word.score if minimum_word else None,
        "canonical_minimum_score_word": minimum_word.model_dump(mode="json")
        if minimum_word
        else None,
        "canonical_low_score_count": sum(s < thresholds.minimum_alignment_score for s in scores),
        "canonical_missing_score_count": len(words) - len(scores),
        "missing_timestamp_count": max(len(alignment.missing_words), len(expected) - len(words)),
        "wer": comparison.wer,
        "coverage": comparison.coverage_ratio,
        "checks": checks,
        "eligible": all(checks.values()),
    }


def build_timing(
    version: int,
    sha: str,
    duration: float,
    rhythm: RhythmEvidence,
    alignment: AlignmentEvidence,
    spec: CanonicalMusicSpec,
    comparison: LyricComparison,
    transcription: TranscriptionEvidence,
) -> TimingAnalysis:
    reliable = timing_admission_evidence(duration, alignment, spec, comparison, transcription)[
        "eligible"
    ]
    sections: list[TimedText] = []
    if reliable and spec.brief.id.startswith("episode_"):
        # Creative sections can repeat; group adjacent measured lines, never prompt durations.
        previous_section = None
        for line, canonical in zip(alignment.lyric_lines, spec.lyrics.lines, strict=True):
            if canonical.section == previous_section:
                sections[-1] = sections[-1].model_copy(update={"end": line.end})
            else:
                sections.append(TimedText(start=line.start, end=line.end, text=canonical.section))
            previous_section = canonical.section
    elif reliable:
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
        downbeat_seconds=rhythm.downbeat_seconds if rhythm.status == "complete" else (),
        sections=tuple(sections),
        lyric_lines=alignment.lyric_lines if reliable else (),
        words=tuple(
            TimedText(start=w.start, end=w.end, text=w.text) for w in alignment.canonical_words
        )
        if reliable
        else (),
        phonemes=(),
        intro=TimeRange(start=0, end=alignment.canonical_words[0].start)
        if reliable and alignment.canonical_words[0].start > 0
        else None,
        outro=TimeRange(start=alignment.canonical_words[-1].end, end=duration)
        if reliable and alignment.canonical_words[-1].end < duration
        else None,
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
    rhythm = analyze_rhythm(
        decoded, spec.brief, path.parent / ".analysis-models", config.device, sha
    )
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
        required_phrases=(
            spec.brief.examples if spec.brief.id.startswith("episode_") else REQUIRED_PHRASES
        ),
    )
    timing = build_timing(
        version, sha, technical.duration_seconds, rhythm, alignment, spec, comparison, transcription
    )
    config_sha = configuration_sha(config)
    warnings = []
    if not timing.downbeat_seconds:
        warnings.append("downbeat evidence unavailable; no downbeats inferred")
    if not timing.words:
        warnings.append("canonical timing not admitted; measured lyric edge regions unavailable")
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
            "rhythm": rhythm.provenance
            or _provenance(
                "Beat This",
                _version("beat-this"),
                sha,
                device=config.device,
                model="final0",
                configuration={"float16": config.device == "cuda", "dbn": False},
            ),
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
