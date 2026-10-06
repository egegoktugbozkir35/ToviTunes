"""Small renderer-facing contracts built from admitted production evidence."""

from bisect import bisect_left, bisect_right
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tovitunes.domain.episode import Episode, PinnedCharacterPack

Prop = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Action = Literal["enter", "idle", "point", "present", "question", "sing", "celebrate"]


class ProductionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Interval(ProductionModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def positive(self) -> "Interval":
        if self.end <= self.start:
            raise ValueError("interval must be positive")
        return self


class TimedText(Interval):
    text: str = Field(min_length=1)


class AudioAlignment(ProductionModel):
    schema_version: Literal[1, 2] = 1
    audio_master_artifact_id: str
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_blind_id: str
    source_analysis_version: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    words: tuple[TimedText, ...] = Field(min_length=1)
    lyric_lines: tuple[TimedText, ...] = Field(min_length=1)
    sections: tuple[TimedText, ...] = Field(min_length=1)
    pre_lyric: Interval | None
    post_lyric: Interval | None

    @model_validator(mode="after")
    def measured_ranges(self) -> "AudioAlignment":
        for group in (self.words, self.lyric_lines, self.sections):
            if any(x.end > self.duration_seconds for x in group) or any(
                a.end > b.start for a, b in zip(group, group[1:])
            ):
                raise ValueError("alignment ranges must be ordered within audio")
        first, last = self.lyric_lines[0].start, self.lyric_lines[-1].end
        expected_pre = Interval(start=0, end=first) if first > 0 else None
        expected_post = (
            Interval(start=last, end=self.duration_seconds)
            if last < self.duration_seconds
            else None
        )
        if self.pre_lyric != expected_pre or self.post_lyric != expected_post:
            raise ValueError("alignment edges differ from measured lyric edges")
        cursor = 0
        for line in self.lyric_lines:
            from tovitunes.music.analysis import normalized_words

            count = (
                len(normalized_words(line.text))
                if self.schema_version == 2
                else len(line.text.split())
            )
            words = self.words[cursor : cursor + count]
            words_match = (
                tuple(token for word in words for token in normalized_words(word.text))
                == normalized_words(line.text)
                if self.schema_version == 2
                else " ".join(w.text for w in words) == line.text
            )
            if (
                len(words) != count
                or not words_match
                or words[0].start != line.start
                or words[-1].end != line.end
            ):
                raise ValueError("canonical words and lyric lines differ")
            cursor += count
        if cursor != len(self.words):
            raise ValueError("canonical words are not covered exactly once")
        if any(
            sum(s.start <= line.start and line.end <= s.end for s in self.sections) != 1
            for line in self.lyric_lines
        ):
            raise ValueError("every line requires one measured section")
        return self


class BeatAnalysis(ProductionModel):
    schema_version: Literal[1] = 1
    audio_master_artifact_id: str
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0)
    source_analysis_version: int = Field(gt=0)
    estimated_bpm: float = Field(gt=0)
    beat_seconds: tuple[float, ...] = Field(min_length=1)
    downbeat_seconds: tuple[float, ...] = Field(min_length=1)
    detector: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    model_identity: str | None
    model_revision: str | None

    @model_validator(mode="after")
    def measured_grid(self) -> "BeatAnalysis":
        for points in (self.beat_seconds, self.downbeat_seconds):
            if any(p < 0 or p > self.duration_seconds for p in points) or any(
                a >= b for a, b in zip(points, points[1:])
            ):
                raise ValueError("beat grid must increase within audio")
        if not set(self.downbeat_seconds).issubset(self.beat_seconds):
            raise ValueError("downbeats must belong to beat grid")
        return self


class SceneIntent(ProductionModel):
    visual_focus: str = Field(min_length=1)
    required_props: tuple[Prop, ...] = ()
    tovi_action: Action
    lesson_target: str | None = None
    character_id: Literal["tovi"] = "tovi"

    @model_validator(mode="after")
    def unique_props(self) -> "SceneIntent":
        # Each identifier declares exactly one object; quantities are deliberately fixed in V1.
        if len(set(self.required_props)) != len(self.required_props):
            raise ValueError("props must be unique (one object per identifier)")
        return self


class LyricIntent(SceneIntent):
    lyric_text: str = Field(min_length=1)


class StoryboardTemplate(ProductionModel):
    schema_version: Literal[1, 2] = 1
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    concept_id: str
    objective_id: str
    intro: SceneIntent
    lyrics: tuple[LyricIntent, ...] = Field(min_length=1)
    outro: SceneIntent

    @model_validator(mode="after")
    def education(self) -> "StoryboardTemplate":
        if self.schema_version == 1 and (
            self.concept_id != "red" or self.objective_id != "colors.red.identify"
        ):
            raise ValueError("frozen V1 template must retain the Red objective")
        if self.intro.lesson_target or self.outro.lesson_target:
            raise ValueError("intro/outro cannot add teaching claims")
        if self.schema_version == 1 and any(
            not set(intent.required_props) <= {"red_swatch", "red_apple", "red_ball"}
            for intent in (self.intro, *self.lyrics, self.outro)
        ):
            raise ValueError("unsupported frozen V1 prop")
        for line in self.lyrics:
            if line.lesson_target != self.concept_id:
                raise ValueError("lyric scene target differs from concept")
            for phrase, prop in (("red apple", "red_apple"), ("red ball", "red_ball")):
                if (
                    self.schema_version == 1
                    and phrase in line.lyric_text.casefold()
                    and prop not in line.required_props
                ):
                    raise ValueError(f"{phrase} scene requires {prop}")
        return self


class TimedScene(SceneIntent, Interval):
    scene_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    kind: Literal["intro", "lyric", "outro"]
    section: str
    lyric_text: str | None = None
    lyric_start: float | None = Field(default=None, ge=0)
    lyric_end: float | None = Field(default=None, gt=0)
    beat_index_range: tuple[int, int]
    downbeat_index_range: tuple[int, int]

    @model_validator(mode="after")
    def vocal_interval(self) -> "TimedScene":
        for start, end in (self.beat_index_range, self.downbeat_index_range):
            if start < 0 or end < start:
                raise ValueError("beat index range must be nonnegative and ordered")
        if self.kind == "lyric":
            if not self.lyric_text or self.lyric_start is None or self.lyric_end is None:
                raise ValueError("lyric scenes require measured vocal timing")
            if not self.start == self.lyric_start < self.lyric_end <= self.end:
                raise ValueError("vocal interval must lie within scene")
            if not self.lesson_target:
                raise ValueError("lyric scene requires an episode lesson target")
        elif any(
            x is not None
            for x in (self.lyric_text, self.lyric_start, self.lyric_end, self.lesson_target)
        ):
            raise ValueError("intro/outro cannot add lyrics or teaching claims")
        return self


class TimedStoryboard(ProductionModel):
    schema_version: Literal[1, 2] = 1
    episode_id: str
    audio_master_artifact_id: str
    audio_alignment_artifact_id: str
    beat_analysis_artifact_id: str
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0)
    template_id: str
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    concept_id: str
    objective_id: str
    character_pack: PinnedCharacterPack
    scenes: tuple[TimedScene, ...] = Field(min_length=1)

    @property
    def scene_ids(self) -> tuple[str, ...]:
        return tuple(s.scene_id for s in self.scenes)

    @model_validator(mode="after")
    def coverage(self) -> "TimedStoryboard":
        if self.schema_version == 1 and (
            self.concept_id != "red" or self.objective_id != "colors.red.identify"
        ):
            raise ValueError("frozen V1 storyboard must retain the Red objective")
        for scene in self.scenes:
            if scene.kind == "lyric" and scene.lesson_target != self.concept_id:
                raise ValueError("scene teaching target differs from episode")
            if self.schema_version == 1 and scene.lyric_text:
                for phrase, prop in (("red apple", "red_apple"), ("red ball", "red_ball")):
                    if phrase in scene.lyric_text.casefold() and prop not in scene.required_props:
                        raise ValueError(f"{phrase} scene requires {prop}")
            if self.schema_version == 1 and not set(scene.required_props) <= {
                "red_swatch",
                "red_apple",
                "red_ball",
            }:
                raise ValueError("unsupported frozen V1 prop")
        if self.character_pack.character_id != "tovi":
            raise ValueError("storyboard must use pinned Tovi only")
        if len(set(self.scene_ids)) != len(self.scenes):
            raise ValueError("scene IDs must be unique")
        if self.scenes[0].start != 0 or self.scenes[-1].end != self.duration_seconds:
            raise ValueError("storyboard must cover entire audio")
        if any(a.end != b.start for a, b in zip(self.scenes, self.scenes[1:])):
            raise ValueError("scene coverage has a gap or overlap")
        kinds = [s.kind for s in self.scenes]
        if (
            kinds
            != (["intro"] if kinds[0] == "intro" else [])
            + ["lyric" for s in self.scenes if s.kind == "lyric"]
            + (["outro"] if kinds[-1] == "outro" else [])
            or "lyric" not in kinds
        ):
            raise ValueError("scenes must follow intro, lyrics, outro order")
        final_lyric = next(s for s in reversed(self.scenes) if s.kind == "lyric")
        if final_lyric.end != final_lyric.lyric_end:
            raise ValueError("final lyric scene must end at the measured final vocal end")
        return self


def beat_range(
    points: tuple[float, ...], start: float, end: float, duration: float
) -> tuple[int, int]:
    """Half-open scenes; the final scene also owns an event exactly at duration."""
    return bisect_left(points, start), (
        bisect_right(points, end) if end == duration else bisect_left(points, end)
    )


def build_storyboard(
    episode: Episode,
    alignment: AudioAlignment,
    beats: BeatAnalysis,
    alignment_id: str,
    beat_id: str,
    template: StoryboardTemplate,
    template_sha: str,
) -> TimedStoryboard:
    if (
        episode.concept_id != template.concept_id
        or episode.objective_id != template.objective_id
        or len(episode.character_packs) != 1
        or episode.character_packs[0].character_id != "tovi"
        or alignment.audio_master_artifact_id != beats.audio_master_artifact_id
        or alignment.audio_sha256 != beats.audio_sha256
        or alignment.duration_seconds != beats.duration_seconds
        or alignment.source_analysis_version != beats.source_analysis_version
        or tuple(x.text for x in alignment.lyric_lines)
        != tuple(x.lyric_text for x in template.lyrics)
    ):
        raise ValueError("storyboard inputs differ from pinned objective, lyrics, or audio")
    scenes: list[TimedScene] = []

    def add(
        scene_id: str,
        start: float,
        end: float,
        kind: Literal["intro", "lyric", "outro"],
        section: str,
        intent: SceneIntent,
        lyric: TimedText | None = None,
    ) -> None:
        fields = intent.model_dump(exclude={"lyric_text"})
        scenes.append(
            TimedScene(
                **fields,
                scene_id=scene_id,
                start=start,
                end=end,
                kind=kind,
                section=section,
                lyric_text=lyric.text if lyric else None,
                lyric_start=lyric.start if lyric else None,
                lyric_end=lyric.end if lyric else None,
                beat_index_range=beat_range(beats.beat_seconds, start, end, beats.duration_seconds),
                downbeat_index_range=beat_range(
                    beats.downbeat_seconds, start, end, beats.duration_seconds
                ),
            )
        )

    if alignment.pre_lyric:
        add("intro", 0, alignment.pre_lyric.end, "intro", "pre_lyric", template.intro)
    for i, (line, intent) in enumerate(zip(alignment.lyric_lines, template.lyrics, strict=True)):
        end = alignment.lyric_lines[i + 1].start if i + 1 < len(template.lyrics) else line.end
        section = next(
            s.text for s in alignment.sections if s.start <= line.start and line.end <= s.end
        )
        add(f"lyric_{i + 1:02d}", line.start, end, "lyric", section, intent, line)
    if alignment.post_lyric:
        add(
            "outro",
            alignment.post_lyric.start,
            alignment.duration_seconds,
            "outro",
            "post_lyric",
            template.outro,
        )
    return TimedStoryboard(
        schema_version=template.schema_version,
        episode_id=episode.episode_id,
        audio_master_artifact_id=alignment.audio_master_artifact_id,
        audio_alignment_artifact_id=alignment_id,
        beat_analysis_artifact_id=beat_id,
        audio_sha256=alignment.audio_sha256,
        duration_seconds=alignment.duration_seconds,
        template_id=template.template_id,
        template_sha256=template_sha,
        concept_id=template.concept_id,
        objective_id=template.objective_id,
        character_pack=episode.character_packs[0],
        scenes=tuple(scenes),
    )


class TimedStoryboardV2(TimedStoryboard):
    schema_version: Literal[1, 2] = 2
    lyrics_artifact_id: str
    visual_plan_artifact_id: str
    asset_artifact_ids: dict[str, str]
    environment_set_artifact_id: str

    @model_validator(mode="after")
    def admitted_assets(self) -> "TimedStoryboardV2":
        if self.schema_version != 2 or not all(
            set(scene.required_props) <= set(self.asset_artifact_ids) for scene in self.scenes
        ):
            raise ValueError("V2 storyboard references assets outside its admitted plan")
        return self


def parse_storyboard(value: object) -> TimedStoryboard:
    if isinstance(value, dict) and value.get("schema_version") == 2:
        return TimedStoryboardV2.model_validate(value)
    return TimedStoryboard.model_validate(value)
