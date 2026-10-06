"""Stop boundaries and real milestones for the single production workflow."""

from enum import StrEnum


class ProductionTarget(StrEnum):
    DRAFT = "draft"
    RENDER = "render"
    PUBLISH = "publish"


STAGES = (
    "CREATIVE",
    "MUSIC",
    "AUDIO_ANALYSIS",
    "VISUAL_PLAN",
    "VISUAL_ASSETS",
    "STORYBOARD",
    "RENDER",
    "MEDIA_QA",
    "METADATA",
    "RELEASE",
    "YOUTUBE",
)
CREATIVE_STEPS = ("TOPIC", "BRIEF", "EPISODE_SPEC", "LYRICS", "MUSIC_SPEC")


def stages_for(target: ProductionTarget) -> tuple[str, ...]:
    return (
        STAGES[:1]
        if target == ProductionTarget.DRAFT
        else (STAGES[:9] if target == ProductionTarget.RENDER else STAGES)
    )


def steps_for(target: ProductionTarget) -> tuple[str, ...]:
    return (*CREATIVE_STEPS, *stages_for(target)[1:])
