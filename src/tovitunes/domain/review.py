"""Rights and approvals are independent append-only decisions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class RightsDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    status: Literal["unknown", "review_required", "commercial_use_confirmed", "blocked"]
    actor: str
    evidence_uri: str | None = None
    policy_version: str
    decided_at: datetime

    @model_validator(mode="after")
    def clearance_requires_evidence(self) -> "RightsDecision":
        if self.status == "commercial_use_confirmed" and not self.evidence_uri:
            raise ValueError("commercial-use clearance requires evidence")
        return self


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str
    target_kind: Literal["episode", "artifact"]
    status: Literal["pending", "approved", "rejected", "needs_review"]
    actor: str
    reason: str | None = None
    policy_version: str
    decided_at: datetime

    @model_validator(mode="after")
    def rejection_requires_reason(self) -> "ApprovalDecision":
        if self.status == "rejected" and not self.reason:
            raise ValueError("rejection requires a reason")
        return self

