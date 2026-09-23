"""Strict runtime configuration with paths relative to the config file."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    database_path: Path
    data_root: Path
    brand_root: Path
    publication_enabled: bool = False
    expected_youtube_channel_id: str | None = None

    @model_validator(mode="after")
    def publishing_requires_channel(self) -> "RuntimeConfig":
        if self.publication_enabled and not self.expected_youtube_channel_id:
            raise ValueError("publishing requires an expected YouTube channel ID")
        return self


def load_config(path: Path) -> RuntimeConfig:
    config_file = path.resolve(strict=True)
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a YAML mapping")
    channel = os.environ.get("TOVITUNES_EXPECTED_CHANNEL_ID")
    if channel is not None:
        raw["expected_youtube_channel_id"] = channel or None
    for key in ("database_path", "data_root", "brand_root"):
        value = raw.get(key)
        if isinstance(value, str):
            candidate = Path(value)
            raw[key] = (
                (config_file.parent / candidate).resolve()
                if not candidate.is_absolute()
                else candidate.resolve()
            )
    return RuntimeConfig.model_validate(raw)

