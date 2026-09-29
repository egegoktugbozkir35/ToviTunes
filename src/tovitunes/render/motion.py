"""Compact deterministic motion grammar built only from admitted immutable evidence.

Coordinates are normalized; event times are local except keyword word_start/end,
which retain the exact admitted audio interval. No MoviePy objects enter this model.
"""

import hashlib
import json
import math
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from tovitunes.domain.storyboard import AudioAlignment, BeatAnalysis, ProductionModel
from tovitunes.render import VERSION
from tovitunes.render.composition import SceneComposition
from tovitunes.render.models import CharacterAnimation, SpriteRole

MAX_MAJOR_SIMULTANEOUS_MOTIONS = 3
CameraBehavior = Literal[
    "static", "slow_push_in", "slow_pull_out", "gentle_pan_left", "gentle_pan_right", "focus_push"
]
PropMotion = Literal["pop_in", "gentle_bounce", "pulse", "wiggle", "roll_in", "float_in", "settle"]


def smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def canonical_word(text: str) -> str:
    # Exact lexical equality after case/punctuation normalization; never fuzzy ASR matching.
    return re.sub(r"^[^\w]+|[^\w]+$", "", text.casefold())


class CameraTrack(ProductionModel):
    behavior: CameraBehavior = "static"
    duration: float = Field(gt=0)
    zoom_start: float = Field(default=1.02, ge=1, le=1.06)
    zoom_end: float = Field(default=1.02, ge=1, le=1.06)
    pan_start: tuple[float, float] = (0, 0)
    pan_end: tuple[float, float] = (0, 0)
    hold_from: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def safe_overscan(self) -> "CameraTrack":
        for pan, zoom in ((self.pan_start, self.zoom_start), (self.pan_end, self.zoom_end)):
            if any(abs(p) > min(0.04, (zoom - 1) / 2) + 1e-9 for p in pan):
                raise ValueError("camera pan exposes borders or exceeds bounds")
        if self.hold_from is not None and self.hold_from > self.duration:
            raise ValueError("camera hold exceeds track")
        if self.behavior == "static" and (
            self.zoom_start != self.zoom_end or self.pan_start != self.pan_end
        ):
            raise ValueError("static camera cannot move")
        return self


def camera_state(track: CameraTrack, t: float) -> tuple[float, float, float]:
    u = smooth(t / max(0.001, track.hold_from or track.duration))
    zoom = track.zoom_start + (track.zoom_end - track.zoom_start) * u
    px, py = tuple(a + (b - a) * u for a, b in zip(track.pan_start, track.pan_end))
    return zoom, px, py


def camera_box(
    box: tuple[float, float, float, float], track: CameraTrack, t: float
) -> tuple[float, float, float, float]:
    z, px, py = camera_state(track, t)
    x0, y0, x1, y1 = box
    return (
        (x0 - 0.5) * z + 0.5 - px,
        (y0 - 0.5) * z + 0.5 - py,
        (x1 - 0.5) * z + 0.5 - px,
        (y1 - 0.5) * z + 0.5 - py,
    )


class PoseCue(ProductionModel):
    time: float = Field(ge=0)
    sprite_role: SpriteRole
    timing_source: Literal["initial", "measured_downbeat", "scene_fraction", "settle_phase"]


class MotionEvent(ProductionModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    motion: PropMotion
    timing_source: Literal["scene_entry", "measured_downbeat", "scene_fraction", "recap_phase"]

    @model_validator(mode="after")
    def positive(self) -> "MotionEvent":
        if self.end <= self.start:
            raise ValueError("motion event must have positive duration")
        return self


class PropTrack(ProductionModel):
    prop_key: str
    bbox: tuple[float, float, float, float]
    grounded: bool
    rolling: bool
    primary: bool
    events: tuple[MotionEvent, ...] = ()


class AmbientTrack(ProductionModel):
    kind: Literal["cloud", "flower", "leaf", "note", "sparkle"]
    plane: Literal["far_background", "mid_background", "foreground"]
    center: tuple[float, float]
    width: float = Field(gt=0, le=0.3)
    drift: float = Field(ge=-0.025, le=0.025)
    period: float = Field(ge=4)
    phase: float = Field(ge=0, le=2 * math.pi)
    opacity: float = Field(gt=0, le=0.8)


class KeywordEmphasisEvent(ProductionModel):
    word_index: int = Field(ge=0)
    vocabulary_item: str
    word_start: float = Field(ge=0)
    word_end: float = Field(gt=0)
    prop_key: str
    effect: Literal["pulse_halo"] = "pulse_halo"


class ActivityEvent(ProductionModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    channel: Literal["camera", "character", "lesson", "foreground"]
    reason: str
    motion_group: str = ""


class SceneMotionPlan(ProductionModel):
    renderer_version: str = VERSION
    camera_policy_version: str = "gentle_camera_v1"
    motion_grammar_version: str = "preschool_motion_v1"
    keyword_emphasis_policy: str = "canonical_target_words_v1"
    environmental_theme: str = "playful_meadow_v2"
    scene_id: str
    scene_start: float = Field(ge=0)
    duration: float = Field(gt=0)
    seed: str
    background_variant: Literal["wide", "lesson_focus", "performance", "celebration"]
    camera_track: CameraTrack
    character_pose_sequence: tuple[PoseCue, ...]
    prop_tracks: tuple[PropTrack, ...]
    ambient_tracks: tuple[AmbientTrack, ...]
    keyword_emphasis_events: tuple[KeywordEmphasisEvent, ...]
    scene_entry_effect: Literal["soft_pop", "side_reveal", "focus_in", "none"]
    scene_entry_seconds: float = Field(default=0.24, ge=0.12, le=0.30)
    scene_exit_behavior: Literal["hold", "settle"] = "hold"
    activity_events: tuple[ActivityEvent, ...]
    micro_scene: bool
    inherited_from_scene_id: str | None = None
    inherited_motion_artifact_id: str | None = None
    clock_offset: float = Field(default=0, ge=0)
    ambient_time_offset: float = Field(default=0, ge=0)
    settle_start: float | None = Field(default=None, ge=0)
    outro_phases: tuple[tuple[str, float, float], ...] = ()
    max_major_simultaneous_motions: int = MAX_MAJOR_SIMULTANEOUS_MOTIONS

    @model_validator(mode="after")
    def tracks(self) -> "SceneMotionPlan":
        if not self.character_pose_sequence or self.character_pose_sequence[0].time != 0:
            raise ValueError("pose sequence must start at zero")
        if any(
            a.time >= b.time
            for a, b in zip(self.character_pose_sequence, self.character_pose_sequence[1:])
        ):
            raise ValueError("pose sequence must increase")
        if not self.micro_scene and any(
            p.time >= self.duration for p in self.character_pose_sequence
        ):
            raise ValueError("pose outside scene")
        if self.micro_scene and self.scene_entry_effect != "none":
            raise ValueError("micro scene cannot rebuild frame")
        if len({p.prop_key for p in self.prop_tracks}) != len(self.prop_tracks):
            raise ValueError("duplicate prop tracks")
        keys = {p.prop_key for p in self.prop_tracks}
        for keyword in self.keyword_emphasis_events:
            if (
                keyword.prop_key not in keys
                or keyword.word_end <= keyword.word_start
                or not self.scene_start <= keyword.word_start < self.scene_start + self.duration
            ):
                raise ValueError("keyword event outside visual context")
        for event in self.activity_events:
            if event.end <= event.start or event.end > self.duration + 1e-9:
                raise ValueError("activity outside scene")
        for prop in self.prop_tracks:
            if not (
                0 <= prop.bbox[0] < prop.bbox[2] <= 1 and 0 <= prop.bbox[1] < prop.bbox[3] <= 1
            ):
                raise ValueError("invalid prop resting bounds")
            for prop_event in prop.events:
                if prop_event.end > self.duration + 1e-9 and not self.micro_scene:
                    raise ValueError("prop event outside scene")
                if prop_event.motion == "roll_in" and not (prop.rolling and prop.grounded):
                    raise ValueError("roll requires ground-contact semantics")
        return self

    def stable_hash(self) -> str:
        data = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()


def _camera(action: str, duration: float, focus: tuple[float, float]) -> CameraTrack:
    if action == "enter":
        return CameraTrack(behavior="slow_push_in", duration=duration, zoom_end=1.04)
    if action in {"point", "present"}:
        pan = (
            max(-0.008, min(0.008, (focus[0] - 0.5) * 0.02)),
            max(-0.008, min(0.008, (focus[1] - 0.5) * 0.02)),
        )
        return CameraTrack(behavior="focus_push", duration=duration, zoom_end=1.04, pan_end=pan)
    if action in {"question", "celebrate"}:
        return CameraTrack(
            behavior="slow_pull_out", duration=duration, zoom_start=1.04, zoom_end=1.02
        )
    direction = -1 if focus[0] < 0.5 else 1
    return CameraTrack(
        behavior="gentle_pan_left" if direction < 0 else "gentle_pan_right",
        duration=duration,
        pan_start=(-direction * 0.007, 0),
        pan_end=(direction * 0.007, 0),
    )


def plan_motion(
    composition: SceneComposition,
    scene_start: float,
    duration: float,
    storyboard_id: str,
    beats: BeatAnalysis,
    alignment: AudioAlignment,
    target_vocabulary: tuple[str, ...],
    *,
    previous: SceneMotionPlan | None = None,
    previous_artifact_id: str | None = None,
    canvas: tuple[int, int] = (1080, 1920),
) -> SceneMotionPlan:
    action = composition.resolved_action
    seed = hashlib.sha256(f"{storyboard_id}:{composition.scene_id}:{VERSION}".encode()).hexdigest()
    phase = int(seed[:8], 16) / 0xFFFFFFFF * 2 * math.pi
    downbeats = tuple(
        b - scene_start for b in beats.downbeat_seconds if scene_start < b < scene_start + duration
    )

    def cue(fraction: float) -> tuple[float, Literal["measured_downbeat", "scene_fraction"]]:
        target = fraction * duration
        candidates = tuple(t for t in downbeats if 0.4 <= t <= duration - 0.3)
        closest = min(candidates, key=lambda t: abs(t - target), default=target)
        if candidates and abs(closest - target) <= min(0.7, duration * 0.18):
            return closest, "measured_downbeat"
        return target, "scene_fraction"

    roles: list[PoseCue] = [
        PoseCue(time=0, sprite_role=composition.sprite_role, timing_source="initial")
    ]
    if not composition.micro_scene and duration > 1.1 and action != "sing":
        sequences: dict[str, tuple[SpriteRole, ...]] = {
            "enter": ("sprite/neutral_full_body",),
            "present": ("sprite/pointing", "sprite/neutral_full_body")
            if composition.character_facing == "right" or composition.character_slot == "lower_left"
            else ("sprite/hello", "sprite/neutral_full_body"),
            "point": ("sprite/neutral_full_body", "sprite/pointing"),
            "question": ("sprite/neutral_full_body", "sprite/pointing"),
            "celebrate": ("sprite/hello", "sprite/hopping"),
            "idle": ("sprite/hello", "sprite/neutral_full_body"),
        }
        sequence = sequences[action]
        for i, role in enumerate(sequence):
            t, source = cue((i + 1) / (len(sequence) + 1))
            if t > roles[-1].time + 0.3 and role != roles[-1].sprite_role:
                roles.append(PoseCue(time=t, sprite_role=role, timing_source=source))
    phases = tuple((p.name, p.start, p.end) for p in composition.outro_phases)
    settle = phases[-1][1] if phases else None
    if settle is not None:
        roles = [p for p in roles if p.time < settle]
        roles.append(
            PoseCue(
                time=settle, sprite_role="sprite/neutral_full_body", timing_source="settle_phase"
            )
        )

    primary = next((p for p in composition.props if p.primary), None)
    camera = _camera(action, duration, primary.center if primary else (0.5, 0.6))
    if settle is not None:
        camera = camera.model_copy(update={"hold_from": settle})
    variant: Literal["wide", "lesson_focus", "performance", "celebration"] = (
        "performance"
        if action == "sing"
        else "celebration"
        if phases
        else "lesson_focus"
        if primary
        else "wide"
    )
    vocab = {canonical_word(v): v for v in target_vocabulary}
    keywords = tuple(
        KeywordEmphasisEvent(
            word_index=i,
            vocabulary_item=vocab[canonical_word(word.text)],
            word_start=word.start,
            word_end=word.end,
            prop_key=primary.type,
        )
        for i, word in enumerate(alignment.words)
        if primary
        and canonical_word(word.text) in vocab
        and scene_start <= word.start < scene_start + duration
    )
    tracks: list[PropTrack] = []
    for i, prop in enumerate(composition.props):
        # Composition's ground/display semantics and registry are the only physical inputs.
        w, h = canvas
        pixels = prop.bbox(canvas, composition.ground_plane_y)
        bbox = (pixels[0] / w, pixels[1] / h, pixels[2] / w, pixels[3] / h)
        events: list[MotionEvent] = []
        new = composition.visual_state_persistence == "replace"
        if new and not composition.micro_scene:
            entry: PropMotion = "roll_in" if prop.motion == "roll_in" else "pop_in"
            events.append(
                MotionEvent(
                    start=0,
                    end=min(duration, 0.95 if entry == "roll_in" else 0.4),
                    motion=entry,
                    timing_source="scene_entry",
                )
            )
        if not composition.micro_scene and duration > 2.5:
            fraction = (i + 1) / (len(composition.props) + 1)
            t, source = cue(fraction)
            if phases:
                t = phases[1][1] + (phases[1][2] - phases[1][1]) * fraction
                source = "scene_fraction"
            # Avoid a second independent emphasis on top of a keyword pulse.
            blocked = any(abs((k.word_start - scene_start) - t) < 0.7 for k in keywords)
            if t + 0.5 < (settle if settle is not None else duration) and not blocked:
                events.append(
                    MotionEvent(
                        start=t,
                        end=t + 0.5,
                        motion="pulse" if action == "sing" or phases else "gentle_bounce",
                        timing_source="recap_phase" if phases else source,
                    )
                )
        tracks.append(
            PropTrack(
                prop_key=prop.type,
                bbox=bbox,
                grounded=prop.placement_mode == "ground",
                rolling=prop.motion_class == "roll",
                primary=prop.primary,
                events=tuple(events),
            )
        )
    # Two quiet environmental channels plus a performance decoration. Foreground stays at edges.
    ambient: tuple[AmbientTrack, ...] = (
        AmbientTrack(
            kind="cloud",
            plane="far_background",
            center=(0.18, 0.12),
            width=0.27,
            drift=0.010,
            period=22,
            phase=0,
            opacity=0.78,
        ),
        AmbientTrack(
            kind="cloud",
            plane="far_background",
            center=(0.81, 0.075),
            width=0.23,
            drift=0.008,
            period=25,
            phase=1,
            opacity=0.70,
        ),
        AmbientTrack(
            kind="flower",
            plane="mid_background",
            center=(0.075, 0.69),
            width=0.075,
            drift=0.006,
            period=8,
            phase=phase,
            opacity=0.65,
        ),
        AmbientTrack(
            kind="leaf",
            plane="foreground",
            center=(0.96, 0.978),
            width=0.16,
            drift=-0.009,
            period=10,
            phase=phase,
            opacity=0.65,
        ),
    )
    if action == "sing" or phases:
        ambient += (
            AmbientTrack(
                kind="note" if action == "sing" else "sparkle",
                plane="mid_background",
                center=(0.91, 0.47),
                width=0.045,
                drift=0.018,
                period=5,
                phase=phase,
                opacity=0.5,
            ),
        )
    offset = 0.0
    if composition.micro_scene and previous:
        roles = list(previous.character_pose_sequence)
        camera, variant, ambient = (
            previous.camera_track,
            previous.background_variant,
            previous.ambient_tracks,
        )
        offset = previous.clock_offset + scene_start - previous.scene_start
        tracks = [p.model_copy(update={"events": ()}) for p in previous.prop_tracks]
    activities: list[ActivityEvent] = []
    if not composition.micro_scene:
        if camera.behavior != "static":
            activities.append(
                ActivityEvent(
                    start=0,
                    end=settle if settle is not None else duration,
                    channel="camera",
                    reason=camera.behavior,
                )
            )
        if action == "enter":
            activities.append(
                ActivityEvent(
                    start=0,
                    end=min(0.85, duration),
                    channel="character",
                    reason="character_arrival",
                )
            )
        for pose_cue in roles[1:]:
            activities.append(
                ActivityEvent(
                    start=pose_cue.time,
                    end=min(duration, pose_cue.time + 0.12),
                    channel="character",
                    reason="approved_pose_change",
                )
            )
        for track in tracks:
            for event in track.events:
                activities.append(
                    ActivityEvent(
                        start=event.start,
                        end=event.end,
                        channel="lesson",
                        reason=f"{track.prop_key}:{event.motion}",
                        motion_group="lesson_entry"
                        if event.timing_source == "scene_entry"
                        else f"prop:{track.prop_key}",
                    )
                )
    for keyword in keywords:
        activities.append(
            ActivityEvent(
                start=keyword.word_start - scene_start,
                end=min(duration, keyword.word_end - scene_start),
                channel="lesson",
                reason="canonical_keyword",
                motion_group=f"prop:{keyword.prop_key}",
            )
        )
    return SceneMotionPlan(
        scene_id=composition.scene_id,
        scene_start=scene_start,
        duration=duration,
        seed=seed,
        background_variant=variant,
        camera_track=camera,
        character_pose_sequence=tuple(roles),
        prop_tracks=tuple(tracks),
        ambient_tracks=ambient,
        keyword_emphasis_events=keywords,
        scene_entry_effect="none"
        if composition.micro_scene or composition.visual_state_persistence != "replace"
        else "side_reveal"
        if action == "enter"
        else "soft_pop",
        scene_exit_behavior="settle" if phases else "hold",
        activity_events=tuple(activities),
        micro_scene=composition.micro_scene,
        clock_offset=offset,
        ambient_time_offset=scene_start,
        inherited_from_scene_id=composition.inherited_from_scene_id,
        inherited_motion_artifact_id=previous_artifact_id
        if composition.micro_scene and previous
        else None,
        settle_start=settle,
        outro_phases=phases,
    )


def prop_state(
    track: PropTrack, plan: SceneMotionPlan, t: float
) -> tuple[float, float, float, float]:
    """Center x, bottom y, uniform scale, degrees; base stays on ground except explicit bounce."""
    x0, _, x1, y1 = track.bbox
    x, bottom, scale, angle = (x0 + x1) / 2, y1, 1.0, 0.0
    for event in track.events:
        if event.start <= t <= event.end:
            u = (t - event.start) / (event.end - event.start)
            pulse = math.sin(math.pi * u) ** 2
            if event.motion == "pop_in":
                scale = (
                    0.75 + 0.31 * smooth(u / 0.65)
                    if u < 0.65
                    else 1.06 - 0.06 * smooth((u - 0.65) / 0.35)
                )
            elif event.motion == "pulse":
                scale += 0.07 * pulse
            elif event.motion == "gentle_bounce":
                bottom -= 0.010 * pulse
            elif event.motion == "wiggle":
                angle = 4 * math.sin(2 * math.pi * u) * math.sin(math.pi * u)
            elif event.motion in {"roll_in", "float_in"}:
                if event.motion == "roll_in":
                    # Retain V2's tested nearest-edge arrival and visible ground contact.
                    # Expanded rotation would lift the alpha silhouette off that plane.
                    origin = 1 + (x1 - x0) / 2 if x >= 0.5 else -(x1 - x0) / 2
                    x += (origin - x) * (1 - u) ** 3
                else:
                    available = max(0.0, min(0.08, 0.975 - x1 if x >= 0.5 else x0 - 0.025))
                    x += (available if x >= 0.5 else -available) * (1 - smooth(u))
                    bottom -= 0.02 * (1 - smooth(u))
            elif event.motion == "settle":
                scale += 0.04 * (1 - smooth(u))
    for keyword in plan.keyword_emphasis_events:
        if keyword.prop_key == track.prop_key:
            start, end = keyword.word_start - plan.scene_start, keyword.word_end - plan.scene_start
            if start <= t <= end:
                scale += 0.07 * math.sin(math.pi * (t - start) / (end - start)) ** 2
    # Combined reactions have the same bounded lesson focus envelope.
    return x, bottom, min(1.09, scale), angle


def activity_diagnostics(plan: SceneMotionPlan) -> dict[str, Any]:
    boundaries = sorted(
        {
            0.0,
            plan.duration,
            *(e.start for e in plan.activity_events),
            *(e.end for e in plan.activity_events),
        }
    )
    maximum = max(
        (
            len(
                {
                    e.motion_group or e.channel
                    for e in plan.activity_events
                    if e.start <= (a + b) / 2 < e.end
                }
            )
            for a, b in zip(boundaries, boundaries[1:])
        ),
        default=0,
    )
    return {
        "scene_id": plan.scene_id,
        "scene_duration": plan.duration,
        "meaningful_activity_event_count": len(plan.activity_events),
        "meaningful_activity_events": [e.model_dump(mode="json") for e in plan.activity_events],
        "pose_change_count": 0 if plan.micro_scene else len(plan.character_pose_sequence) - 1,
        "prop_motion_events": sum(len(p.events) for p in plan.prop_tracks),
        "keyword_event_count": len(plan.keyword_emphasis_events),
        "ambient_track_count": len(plan.ambient_tracks),
        "camera_motion": plan.camera_track.behavior,
        "camera_track": plan.camera_track.model_dump(mode="json"),
        "outro_phase": plan.outro_phases,
        "max_simultaneous_major_motion": maximum,
    }


def validate_motion(
    plan: SceneMotionPlan,
    alignment: AudioAlignment,
    vocabulary: tuple[str, ...],
    animation: CharacterAnimation,
    canvas: tuple[int, int],
) -> dict[str, Any]:
    diagnostics = activity_diagnostics(plan)
    minimum = 2 if plan.duration > 4.5 else 1 if plan.duration > 2.5 else 0
    if diagnostics["meaningful_activity_event_count"] < minimum:
        raise ValueError("stagnant long scene")
    if diagnostics["max_simultaneous_major_motion"] > MAX_MAJOR_SIMULTANEOUS_MOTIONS:
        raise ValueError("major motion budget exceeded")
    # The plan cannot increase its own budget to bypass the renderer policy.
    if plan.max_major_simultaneous_motions != MAX_MAJOR_SIMULTANEOUS_MOTIONS:
        raise ValueError("unsupported motion budget")
    vocabulary_keys = {canonical_word(v) for v in vocabulary}
    primary = next((p for p in plan.prop_tracks if p.primary), None)
    expected_indices = tuple(
        i
        for i, word in enumerate(alignment.words)
        if primary
        and canonical_word(word.text) in vocabulary_keys
        and plan.scene_start <= word.start < plan.scene_start + plan.duration
    )
    if tuple(e.word_index for e in plan.keyword_emphasis_events) != expected_indices:
        raise ValueError("keyword occurrence coverage differs from admitted alignment")
    for event in plan.keyword_emphasis_events:
        if event.word_index >= len(alignment.words):
            raise ValueError("keyword does not bind admitted word")
        word = alignment.words[event.word_index]
        if (
            (event.word_start, event.word_end) != (word.start, word.end)
            or canonical_word(word.text) != canonical_word(event.vocabulary_item)
            or canonical_word(word.text) not in vocabulary_keys
        ):
            raise ValueError("keyword does not bind admitted target timing")
    w, h = canvas
    times = {plan.duration * i / 60 for i in range(61)}
    for activity in plan.activity_events:
        times.update((activity.start, (activity.start + activity.end) / 2, activity.end))
    from tovitunes.render.character import position

    for t in sorted(times):
        clock = t + plan.clock_offset
        z, px, py = camera_state(plan.camera_track, clock)
        if any(abs(p) > (z - 1) / 2 + 1e-9 for p in (px, py)):
            raise ValueError("camera exposes borders")
        ax, ay = position(animation, t)
        feet = ay + animation.size[1]
        center = ax + animation.size[0] / 2
        sizes = [p.size for p in animation.pose_sequence] or [animation.size]
        char_boxes = []
        for sw, sh in sizes:
            box = ((center - sw / 2) / w, (feet - sh) / h, (center + sw / 2) / w, feet / h)
            transformed = camera_box(box, plan.camera_track, clock)
            # The explicitly declared arrival may cross the edge for <=0.85s.
            if not (animation.motion_type == "enter" and t < animation.enter_seconds) and not (
                0.01 <= transformed[0] < transformed[2] <= 0.99
                and 0.01 <= transformed[1] < transformed[3] <= 0.99
            ):
                raise ValueError("pose/camera character envelope exceeds frame")
            char_boxes.append(box)
        prop_boxes: list[tuple[float, float, float, float]] = []
        for prop in plan.prop_tracks:
            x, bottom, scale, angle = prop_state(prop, plan, t)
            pw, ph = prop.bbox[2] - prop.bbox[0], prop.bbox[3] - prop.bbox[1]
            # Rotation's complete rectangular envelope; grounded rolling is bottom anchored.
            theta = math.radians(angle)
            rw = (abs(pw * math.cos(theta)) + abs(ph * h / w * math.sin(theta))) * scale
            rh = (abs(ph * math.cos(theta)) + abs(pw * w / h * math.sin(theta))) * scale
            box = (x - rw / 2, bottom - rh, x + rw / 2, bottom)
            transformed = camera_box(box, plan.camera_track, clock)
            intentional_arrival = any(
                e.motion == "roll_in" and e.start == 0 and e.end <= 0.95 and t < e.end
                for e in prop.events
            )
            if not intentional_arrival and not (
                0.01 <= transformed[0] < transformed[2] <= 0.99
                and 0.01 <= transformed[1] < transformed[3] <= 0.99
            ):
                raise ValueError("prop/camera envelope exceeds frame")
            if (
                prop.grounded
                and any(e.motion == "roll_in" and e.start <= t <= e.end for e in prop.events)
                and abs(bottom - prop.bbox[3]) > 1e-9
            ):
                raise ValueError("rolling object loses ground contact")
            for cx0, cy0, cx1, cy1 in char_boxes:
                if box[0] < cx1 and box[2] > cx0 and box[1] < cy1 and box[3] > cy0:
                    raise ValueError("prop motion occludes Tovi")
            for ox0, oy0, ox1, oy1 in prop_boxes:
                if box[0] < ox1 and box[2] > ox0 and box[1] < oy1 and box[3] > oy0:
                    raise ValueError("prop motion obscures another lesson object")
            prop_boxes.append(box)
        for ambient in plan.ambient_tracks:
            clock = plan.ambient_time_offset + t
            dx = ambient.drift * math.sin(2 * math.pi * clock / ambient.period + ambient.phase)
            dy = 0.004 * math.sin(2 * math.pi * clock / ambient.period + ambient.phase)
            cx, cy = ambient.center[0] + dx, ambient.center[1] + dy
            box = (
                cx - ambient.width / 2,
                cy - ambient.width * w / h / 2,
                cx + ambient.width / 2,
                cy + ambient.width * w / h / 2,
            )
            guarded = char_boxes + prop_boxes if ambient.plane == "foreground" else prop_boxes
            for ox0, oy0, ox1, oy1 in guarded:
                if box[0] < ox1 and box[2] > ox0 and box[1] < oy1 and box[3] > oy0:
                    raise ValueError("environmental accent obscures lesson plane")
    diagnostics["motion_envelope_passed"] = True
    diagnostics["keyword_binding_passed"] = True
    return diagnostics
