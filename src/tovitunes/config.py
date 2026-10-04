"""Strict runtime configuration with paths relative to the config file."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreativeLLMConfig(BaseModel):
    """Explicit NIM configuration; credentials are read only from the named environment."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    provider: Literal["nvidia"] = "nvidia"
    model: Literal["moonshotai/kimi-k3"] = "moonshotai/kimi-k3"
    base_url: str = "https://integrate.api.nvidia.com/v1"
    api_key_env: str = Field(default="NVIDIA_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=1800, gt=0)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=8192, ge=1, le=131072)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith("/v1")
        ):
            raise ValueError("NIM base URL must be an HTTP(S) /v1 endpoint without credentials")
        return value


class EnvironmentGenerationConfig(BaseModel):
    """Approved production image-model choices for reviewed environment sets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["google"] = "google"
    model: Literal["gemini-3.1-flash-image", "gemini-3-pro-image"] = "gemini-3.1-flash-image"
    location: Literal["global", "us", "eu"] = "global"
    image_size: Literal["1K", "2K", "4K"] = "1K"

    @model_validator(mode="after")
    def supported_contract(self) -> "EnvironmentGenerationConfig":
        if self.model == "gemini-3-pro-image" and self.location != "global":
            raise ValueError("gemini-3-pro-image is available only at the global location")
        if self.model == "gemini-3.1-flash-image" and self.image_size != "1K":
            raise ValueError("the admitted Flash environment contract is pinned to 1K")
        return self


class LessonObjectGenerationConfig(BaseModel):
    """Provider settings for reviewed lesson objects only."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    provider: Literal["google", "qwen_comfyui"] = "google"
    model: str = "gemini-3-pro-image"
    location: str = "global"
    image_size: Literal["2K"] = "2K"
    base_url: str = "http://127.0.0.1:8188"
    workflow_path: Path = Path("workflows/qwen_image_2_1_t2i_api.json")
    width: int = Field(default=1024, ge=64)
    height: int = Field(default=1024, ge=64)
    steps: int = Field(default=20, ge=1)
    cfg: float = Field(default=1.0, gt=0)
    sampler: str = "euler"
    scheduler: str = "simple"
    timeout_seconds: float = Field(default=600, gt=0)
    poll_interval_seconds: float = Field(default=1, gt=0)

    @model_validator(mode="after")
    def supported_contract(self) -> "LessonObjectGenerationConfig":
        if self.provider == "google":
            if (self.model, self.location, self.image_size) != (
                "gemini-3-pro-image",
                "global",
                "2K",
            ):
                raise ValueError("Google lesson objects require gemini-3-pro-image/global/2K")
        else:
            parsed = urlsplit(self.base_url)
            if (
                self.model != "qwen-image-2.1-q8"
                or parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError(
                    "Qwen lesson objects require qwen-image-2.1-q8 and a local HTTP base URL"
                )
        return self


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    database_path: Path
    data_root: Path
    brand_root: Path
    publication_enabled: bool = False
    expected_youtube_channel_id: str | None = None
    creative_llm: CreativeLLMConfig = Field(default_factory=CreativeLLMConfig)
    environment_generation: EnvironmentGenerationConfig = Field(
        default_factory=EnvironmentGenerationConfig
    )
    lesson_object_generation: LessonObjectGenerationConfig = Field(
        default_factory=LessonObjectGenerationConfig
    )

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
    lesson_generation = raw.get("lesson_object_generation")
    if isinstance(lesson_generation, dict) and isinstance(
        lesson_generation.get("workflow_path"), str
    ):
        candidate = Path(lesson_generation["workflow_path"])
        lesson_generation["workflow_path"] = (
            (config_file.parent / candidate).resolve()
            if not candidate.is_absolute()
            else candidate.resolve()
        )
    return RuntimeConfig.model_validate(raw)
