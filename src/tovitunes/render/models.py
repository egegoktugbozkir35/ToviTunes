"""Immutable data at the MoviePy adapter boundary."""

from typing import Literal

from pydantic import Field, model_validator

from tovitunes.domain.storyboard import ProductionModel
from tovitunes.render import VERSION

SpriteRole = Literal[
    "sprite/hello",
    "sprite/pointing",
    "sprite/neutral_full_body",
    "sprite/singing",
    "sprite/hopping",
]
Motion = Literal["enter", "bob", "point", "present", "question", "sing", "celebrate"]


class CharacterAnimation(ProductionModel):
    renderer_version: str = VERSION
    storyboard_artifact_id: str
    scene_id: str
    sprite_role: SpriteRole
    sprite_artifact_id: str
    crop_bbox: tuple[int, int, int, int]
    visible_alpha_pixels: int = Field(gt=0)
    scale: float = Field(gt=0)  # One scalar, never non-uniform scaling.
    size: tuple[int, int]
    start_position: tuple[float, float]
    end_position: tuple[float, float]
    motion_type: Motion
    amplitude: float = Field(ge=0)
    enter_seconds: float = Field(gt=0)
    beat_indices: tuple[int, int]
    downbeat_indices: tuple[int, int]
    beat_seconds: tuple[float, ...]
    downbeat_seconds: tuple[float, ...]
    composition_style: str = "legacy_center"
    character_slot: str = "lower_center"
    gesture_direction: Literal["left", "right", "none"] = "none"
    visual_state_id: str = "legacy"
    inherited_from_scene_id: str | None = None
    micro_scene: bool = False
    emphasis: Literal["gentle_pulse"] | None = None
    duration_seconds: float = Field(default=1, gt=0)
    motion_time_offset: float = Field(default=0, ge=0)
    motion_duration_seconds: float = Field(default=1, gt=0)
    motion_has_drift: bool = False
    outro_phases: tuple[tuple[str, float, float], ...] = ()
    long_scene_activity: bool = False
    mouth_animation_supported: Literal[False] = False
    mouth_mode: Literal["approved_sprite_as_is"] = "approved_sprite_as_is"
    mouth_reason: str = "Component normalization has no registered singing-pose mouth anchor."

    @model_validator(mode="after")
    def uniform(self) -> "CharacterAnimation":
        x0, y0, x1, y1 = self.crop_bbox
        expected = (round((x1 - x0) * self.scale), round((y1 - y0) * self.scale))
        if min(self.size) <= 0 or self.size != expected:
            raise ValueError("character size must preserve uniform scale and positive alpha bounds")
        return self


class SceneRender(ProductionModel):
    scene_id: str
    start: float
    end: float
    scene_image_artifact_id: str
    character_animation_artifact_id: str


class RenderManifest(ProductionModel):
    schema_version: Literal[1] = 1
    renderer_version: str = VERSION
    episode_id: str
    audio_master_artifact_id: str
    audio_alignment_artifact_id: str
    timed_storyboard_artifact_id: str
    beat_analysis_artifact_id: str
    character_pack_revision: str
    dependency_sha256: dict[str, str]
    scenes: tuple[SceneRender, ...]
    duration_seconds: float = Field(gt=0)
    canvas: tuple[int, int] = (1080, 1920)
    fps: Literal[30] = 30
    video_codec: Literal["libx264"] = "libx264"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    audio_codec: Literal["aac"] = "aac"
    audio_bitrate: Literal["192k"] = "192k"
    crf: Literal[18] = 18
    preset: Literal["medium"] = "medium"
    faststart: Literal[True] = True
    moviepy_version: Literal["2.2.1"] = "2.2.1"
    ffmpeg_version: str
    ffprobe_version: str

    @model_validator(mode="after")
    def timeline(self) -> "RenderManifest":
        if not self.scenes or self.scenes[0].start != 0:
            raise ValueError("manifest must start at zero")
        if self.scenes[-1].end != self.duration_seconds:
            raise ValueError("manifest must cover duration")
        if len({s.scene_id for s in self.scenes}) != len(self.scenes):
            raise ValueError("duplicate scene IDs")
        if any(s.end <= s.start for s in self.scenes) or any(
            a.end != b.start for a, b in zip(self.scenes, self.scenes[1:])
        ):
            raise ValueError("manifest has gap, overlap, or empty scene")
        if min(self.canvas) <= 0 or any(x % 2 for x in self.canvas):
            raise ValueError("canvas must have positive even dimensions")
        required = {
            self.audio_master_artifact_id,
            self.audio_alignment_artifact_id,
            self.timed_storyboard_artifact_id,
            self.beat_analysis_artifact_id,
            *(s.scene_image_artifact_id for s in self.scenes),
            *(s.character_animation_artifact_id for s in self.scenes),
        }
        if not required.issubset(self.dependency_sha256):
            raise ValueError("manifest is missing pinned dependencies")
        return self
