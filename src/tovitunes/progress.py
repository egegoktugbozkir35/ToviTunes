"""MPT-derived observational milestones; reporting cannot affect production."""

import logging
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class PipelineProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    stage: str
    percent: int = Field(ge=0, le=100)
    detail: str
    episode_key: str | None = None


ProgressReporter = Callable[[PipelineProgress], None]


def report_progress(reporter: ProgressReporter | None, progress: PipelineProgress) -> None:
    if reporter:
        try:
            reporter(progress)
        except Exception:
            logger.exception("pipeline progress reporter failed; production continues")
