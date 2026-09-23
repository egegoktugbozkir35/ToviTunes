"""Artifact identity and provenance without premature file registration."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

SafeKey = str


class ArtifactIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    owner_scope: Literal["episode", "brand"]
    owner_id: str
    kind: SafeKey = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    slot_key: SafeKey = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")

    @classmethod
    def create(
        cls, owner_scope: Literal["episode", "brand"], owner_id: str, kind: str, slot_key: str
    ) -> "ArtifactIdentity":
        return cls(
            artifact_id=str(uuid4()),
            owner_scope=owner_scope,
            owner_id=owner_id,
            kind=kind,
            slot_key=slot_key,
        )


class ArtifactSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_scope: Literal["episode", "brand"]
    owner_id: str
    kind: str
    slot_key: str
    artifact_id: str


class ArtifactDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    consumer_artifact_id: str
    input_artifact_id: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: str


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: Literal["provider", "manual", "deterministic"]
    acquired_at: datetime
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    operator: str | None = None
    source_uri: str | None = None
    prompt_version: str | None = None
    input_artifact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def source_identity(self) -> "Provenance":
        if self.source_kind == "provider" and not self.provider:
            raise ValueError("provider provenance requires provider name")
        if self.source_kind == "manual" and not self.operator:
            raise ValueError("manual provenance requires operator")
        return self

    @classmethod
    def manual(cls, operator: str, source_uri: str) -> "Provenance":
        return cls(
            source_kind="manual",
            acquired_at=datetime.now(UTC),
            operator=operator,
            source_uri=source_uri,
        )

