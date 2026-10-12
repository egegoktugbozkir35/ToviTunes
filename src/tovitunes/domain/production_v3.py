"""Duration authority and frame-based visuals, independent of lyric timing evidence."""

import math
from typing import Literal

from pydantic import Field, model_validator

from tovitunes.domain.episode import Episode, PinnedCharacterPack
from tovitunes.domain.storyboard import (
    BeatAnalysis,
    Interval,
    ProductionModel,
    SceneIntent,
    TimedText,
    beat_range,
)
from tovitunes.domain.visual_draft import EpisodeVisualPlanV2
from tovitunes.music.analysis_models import AnalysisThresholds, TechnicalMetrics


class AudioClock(ProductionModel):
    schema_version: Literal[1] = 1
    policy_version: Literal["duration_audio_v1"] = "duration_audio_v1"
    audio_master_artifact_id: str
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_request_id: str
    source_provider_request_id: str
    source_blind_id: str
    duration_seconds: float = Field(gt=0)
    technical_metrics: TechnicalMetrics
    measured_beats: BeatAnalysis | None = None

    @property
    def beat_seconds(self) -> tuple[float, ...]:
        return self.measured_beats.beat_seconds if self.measured_beats else ()

    @property
    def downbeat_seconds(self) -> tuple[float, ...]:
        return self.measured_beats.downbeat_seconds if self.measured_beats else ()

    @property
    def words(self) -> tuple[TimedText, ...]:
        # No word evidence is used by this path; this is never an ASR artifact.
        return ()

    @model_validator(mode="after")
    def technical_admission(self) -> "AudioClock":
        m, t = self.technical_metrics, AnalysisThresholds()
        if (
            not m.decode_integrity
            or m.invalid_pcm_samples
            or abs(m.duration_seconds - self.duration_seconds) > 0.005
            or m.clipping_ratio > t.maximum_clipping_ratio
            or m.near_silence_ratio > t.maximum_silence_ratio
            or m.longest_near_silent_span_seconds > t.maximum_dropout_seconds
            or m.beginning_silence_seconds > t.maximum_edge_silence_seconds
            or m.ending_silence_seconds > t.maximum_edge_silence_seconds
        ):
            raise ValueError("audio clock rejects measured audio defects")
        if self.measured_beats and (
            self.measured_beats.audio_master_artifact_id != self.audio_master_artifact_id
            or self.measured_beats.audio_sha256 != self.audio_sha256
            or self.measured_beats.duration_seconds != self.duration_seconds
        ):
            raise ValueError("optional measured beats differ from retained audio")
        return self


class VisualTimedScene(SceneIntent, Interval):
    scene_id: str = Field(pattern=r"^visual_[0-9]{3}$")
    kind: Literal["visual"] = "visual"
    source_scene_ids: tuple[str, ...] = Field(min_length=1)
    environment_role: Literal["meadow_wide", "lesson_garden", "play_path", "celebration_meadow"]
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    beat_index_range: tuple[int, int]
    downbeat_index_range: tuple[int, int]

    @property
    def lyric_end(self) -> None:
        return None

    @property
    def lyric_start(self) -> None:
        return None

    @property
    def lyric_text(self) -> None:
        return None


class TimedStoryboardV3(ProductionModel):
    schema_version: Literal[3] = 3
    compiler_version: Literal["duration_storyboard_v1"] = "duration_storyboard_v1"
    episode_id: str
    concept_id: str
    objective_id: str
    character_pack: PinnedCharacterPack
    audio_master_artifact_id: str
    audio_clock_artifact_id: str
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_duration_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    fps: Literal[30] = 30
    scheduling_mode: Literal["duration", "measured_beats"]
    visual_plan_artifact_id: str
    visual_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_artifact_ids: dict[str, str]
    environment_set_artifact_id: str
    scenes: tuple[VisualTimedScene, ...] = Field(min_length=1, max_length=64)
    diagnostics: tuple[str, ...] = ()

    @property
    def scene_ids(self) -> tuple[str, ...]:
        return tuple(s.scene_id for s in self.scenes)

    @model_validator(mode="after")
    def coverage(self) -> "TimedStoryboardV3":
        frames = max(1, math.ceil(self.source_duration_seconds * self.fps - 1e-9))
        if (
            self.character_pack.character_id != "tovi"
            or self.duration_seconds != frames / self.fps
            or self.scenes[0].start_frame != 0
            or self.scenes[-1].end_frame != frames
            or len(set(self.scene_ids)) != len(self.scenes)
            or any(a.end_frame != b.start_frame for a, b in zip(self.scenes, self.scenes[1:]))
        ):
            raise ValueError("V3 storyboard has invalid frame coverage or character identity")
        for scene in self.scenes:
            if (
                scene.end_frame <= scene.start_frame
                or scene.start != scene.start_frame / self.fps
                or scene.end != scene.end_frame / self.fps
                or not set(scene.required_props) <= set(self.asset_artifact_ids)
                or len(scene.required_props) > 3
                or scene.lesson_target is not None
            ):
                raise ValueError("V3 scene timing or required asset references are invalid")
        return self


def compile_storyboard_v3(
    episode: Episode,
    visual: EpisodeVisualPlanV2,
    plan_id: str,
    plan_sha: str,
    clock: AudioClock,
    clock_id: str,
    assets: dict[str, str],
    environment_id: str,
) -> TimedStoryboardV3:
    if (
        visual.episode_id != episode.episode_id
        or len(episode.character_packs) != 1
        or set(assets) != {a.asset_key for a in visual.required_assets}
    ):
        raise ValueError("V3 compiler input identity or assets differ")
    total = max(1, math.ceil(clock.duration_seconds * 30 - 1e-9))
    # Prefer >= 0.5 second shots; short audio still gets a positive one-frame scene.
    count = min(len(visual.scenes), max(1, total // 15))
    groups = [
        visual.scenes[i * len(visual.scenes) // count : (i + 1) * len(visual.scenes) // count]
        for i in range(count)
    ]
    cuts = [i * total // count for i in range(count)] + [total]
    candidates = sorted({round(t * 30) for t in clock.downbeat_seconds or clock.beat_seconds})
    mode: Literal["duration", "measured_beats"] = "duration"
    if candidates:
        snapped = [0]
        for i, target in enumerate(cuts[1:-1], 1):
            usable = [c for c in candidates if snapped[-1] + 1 <= c <= total - (count - i)]
            nearest = min(usable, key=lambda c: (abs(c - target), c)) if usable else target
            snapped.append(nearest if abs(nearest - target) <= 15 else target)
        snapped.append(total)
        if all(a < b for a, b in zip(snapped, snapped[1:])):
            cuts = snapped
            mode = "measured_beats"
    scenes = []
    for i, group in enumerate(groups):
        props = tuple(dict.fromkeys(k for s in group for k in s.required_assets))
        if len(props) > 3:
            raise ValueError("merged short scenes require more than three simultaneous props")
        start, end = cuts[i] / 30, cuts[i + 1] / 30
        scenes.append(
            VisualTimedScene(
                scene_id=f"visual_{i + 1:03d}",
                start=start,
                end=end,
                start_frame=cuts[i],
                end_frame=cuts[i + 1],
                source_scene_ids=tuple(s.scene_id for s in group),
                visual_focus="; ".join(s.visual_focus for s in group),
                tovi_action=group[0].tovi_action,
                required_props=props,
                environment_role=group[0].environment_role,
                beat_index_range=beat_range(clock.beat_seconds, start, end, total / 30),
                downbeat_index_range=beat_range(clock.downbeat_seconds, start, end, total / 30),
            )
        )
    return TimedStoryboardV3(
        episode_id=episode.episode_id,
        concept_id=episode.concept_id,
        objective_id=episode.objective_id,
        character_pack=episode.character_packs[0],
        audio_master_artifact_id=clock.audio_master_artifact_id,
        audio_clock_artifact_id=clock_id,
        audio_sha256=clock.audio_sha256,
        source_duration_seconds=clock.duration_seconds,
        duration_seconds=total / 30,
        scheduling_mode=mode,
        visual_plan_artifact_id=plan_id,
        visual_plan_sha256=plan_sha,
        asset_artifact_ids=assets,
        environment_set_artifact_id=environment_id,
        scenes=tuple(scenes),
        diagnostics=(
            "Short proposals merge in order; each merged shot uses its first action and plate.",
        )
        if count != len(visual.scenes)
        else (),
    )
