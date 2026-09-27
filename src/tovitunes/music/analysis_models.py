"""Versioned, SHA-bound observations from local music analysis."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from tovitunes.music.models import StrictModel, TimedText, TimingAnalysis

EvidenceStatus = Literal["complete", "incomplete", "unavailable"]


class AnalysisModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class AnalyzerProvenance(AnalysisModel):
    name: str
    version: str
    model_name: str | None = None
    model_revision: str | None = None
    device: str
    timestamp: str
    source_audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: dict[str, str | bool | int] = Field(default_factory=dict)


class AnalysisThresholds(AnalysisModel):
    version: Literal[1] = 1
    minimum_timing_coverage: float = 0.85
    maximum_timing_wer: float = 0.25
    maximum_lyric_wer: float = 0.15
    minimum_lyric_coverage: float = 0.9
    minimum_alignment_score: float = 0.5
    maximum_intelligibility_wer: float = 0.2
    maximum_educational_wer: float = 0.25
    maximum_insertion_ratio: float = 0.1
    minimum_beats: int = 12
    maximum_beat_interval_cv: float = 0.3
    maximum_silence_ratio: float = 0.2
    maximum_clipping_ratio: float = 0.001
    maximum_dropout_seconds: float = 2.0
    maximum_edge_silence_seconds: float = 2.0


class TechnicalMetrics(AnalysisModel):
    decode_integrity: bool
    invalid_pcm_samples: int = Field(ge=0)
    sample_rate_hz: int = Field(gt=0)
    channel_count: int = Field(gt=0)
    decoded_frames: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    peak_amplitude: float = Field(ge=0)
    rms_amplitude: float = Field(ge=0)
    dc_offset: float
    clipping_ratio: float = Field(ge=0, le=1)
    near_silence_ratio: float = Field(ge=0, le=1)
    longest_near_silent_span_seconds: float = Field(ge=0)
    beginning_silence_seconds: float = Field(ge=0)
    ending_silence_seconds: float = Field(ge=0)
    near_silence_threshold_dbfs: float
    clipping_threshold_amplitude: float
    window_seconds: float = Field(gt=0)
    integrated_lufs: float | None = None


class RecognizedWord(TimedText):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    score: float | None = Field(default=None, ge=0, le=1)


class TranscriptionEvidence(AnalysisModel):
    status: EvidenceStatus
    recognized_text: str = ""
    words: tuple[RecognizedWord, ...] = ()
    mean_word_score: float | None = Field(default=None, ge=0, le=1)
    word_score_kind: Literal["ctc_alignment_score"] = "ctc_alignment_score"
    recognition_confidence: float | None = Field(default=None, ge=0, le=1)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def complete_has_words(self) -> TranscriptionEvidence:
        if self.status == "complete" and (not self.recognized_text or not self.words):
            raise ValueError("complete transcription requires recognized aligned words")
        return self


class EditOperation(AnalysisModel):
    kind: Literal["match", "substitution", "insertion", "deletion"]
    expected: str | None = None
    recognized: str | None = None


class LyricComparison(AnalysisModel):
    status: EvidenceStatus
    expected_transcript: str
    recognized_transcript: str
    expected_word_count: int = Field(ge=0)
    recognized_word_count: int = Field(ge=0)
    matched_word_count: int | None = Field(default=None, ge=0)
    substitutions: int | None = Field(default=None, ge=0)
    insertions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)
    wer: float | None = Field(default=None, ge=0)
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    required_phrase_presence: dict[str, bool | None]
    operations: tuple[EditOperation, ...] = ()


class RhythmEvidence(AnalysisModel):
    status: EvidenceStatus
    estimated_bpm: float | None = Field(default=None, gt=0)
    beat_seconds: tuple[float, ...] = ()
    beat_count: int = Field(ge=0)
    mean_interval_seconds: float | None = Field(default=None, gt=0)
    interval_std_seconds: float | None = Field(default=None, ge=0)
    interval_cv: float | None = Field(default=None, ge=0)
    target_bpm: int = Field(gt=0)
    allowed_bpm_range: tuple[int, int]
    failure_reason: str | None = None

    @model_validator(mode="after")
    def beats_ordered(self) -> RhythmEvidence:
        if (
            self.beat_count != len(self.beat_seconds)
            or tuple(sorted(self.beat_seconds)) != self.beat_seconds
            or any(b <= a for a, b in zip(self.beat_seconds, self.beat_seconds[1:]))
        ):
            raise ValueError("beat timestamps must increase and match beat count")
        return self


class AlignmentEvidence(AnalysisModel):
    status: EvidenceStatus
    canonical_words: tuple[RecognizedWord, ...] = ()
    lyric_lines: tuple[TimedText, ...] = ()
    aligned_word_count: int = Field(ge=0)
    aligned_line_count: int = Field(ge=0)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def counts_match(self) -> AlignmentEvidence:
        if (
            self.aligned_word_count != len(self.canonical_words)
            or self.aligned_line_count != len(self.lyric_lines)
            or (self.status == "complete" and (not self.canonical_words or not self.lyric_lines))
        ):
            raise ValueError("alignment counts or complete evidence are inconsistent")
        return self


class AudioAnalysis(AnalysisModel):
    schema_version: Literal[1] = 1
    analyzer_version: Literal["real_audio_v1"] = "real_audio_v1"
    version: int = Field(gt=0)
    blind_id: str
    request_id: str
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analyzer_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analyzer_configuration: dict[str, str | bool]
    thresholds: AnalysisThresholds = Field(default_factory=AnalysisThresholds)
    duration_seconds: float = Field(gt=0)
    source_format: str
    sample_rate_hz: int = Field(gt=0)
    bitrate_bps: int | None = Field(default=None, gt=0)
    channel_count: int = Field(gt=0)
    technical_metrics: TechnicalMetrics
    transcription: TranscriptionEvidence
    lyric_comparison: LyricComparison
    rhythm: RhythmEvidence
    alignment: AlignmentEvidence
    timing: TimingAnalysis
    warnings: tuple[str, ...] = ()
    analyzer_provenance: dict[str, AnalyzerProvenance]

    @model_validator(mode="after")
    def bound_and_ordered(self) -> AudioAnalysis:
        if (
            self.thresholds != AnalysisThresholds()
            or self.timing.approval != "pending"
            or self.sample_rate_hz != self.technical_metrics.sample_rate_hz
            or self.channel_count != self.technical_metrics.channel_count
            or self.rhythm.beat_seconds != self.timing.beat_seconds
            or any(t < 0 or t > self.duration_seconds for t in self.rhythm.beat_seconds)
            or self.timing.audio_sha256 != self.audio_sha256
            or self.timing.version != self.version
            or abs(self.technical_metrics.duration_seconds - self.duration_seconds) > 1e-6
            or abs(self.timing.duration_seconds - self.duration_seconds) > 1e-6
            or sha256(
                json.dumps(
                    self.analyzer_configuration, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            != self.analyzer_config_sha256
            or any(
                p.source_audio_sha256 != self.audio_sha256
                for p in self.analyzer_provenance.values()
            )
        ):
            raise ValueError("analysis identity or duration mismatch")
        for sequence in (self.transcription.words, self.alignment.canonical_words):
            if any(w.end > self.duration_seconds for w in sequence) or any(
                a.end > b.start for a, b in zip(sequence, sequence[1:])
            ):
                raise ValueError("word timestamps must be ordered and bounded")
        lines = self.alignment.lyric_lines
        if any(line.end > self.duration_seconds for line in lines) or any(
            a.end > b.start for a, b in zip(lines, lines[1:])
        ):
            raise ValueError("lyric lines must be ordered and bounded")
        return self
