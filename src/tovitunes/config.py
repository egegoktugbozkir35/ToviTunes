"""Strict runtime configuration with paths relative to the config file."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OllamaCreativeConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )

    base_url: str = "http://127.0.0.1:11434"
    model: str = Field(default="qwen3.8:27b-q4_K_M", pattern=r"^\S+$")
    timeout_seconds: float = Field(default=240, gt=0)
    temperature: float = Field(default=0.7, ge=0, le=2)

    @field_validator("base_url")
    @classmethod
    def local_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
        ):
            raise ValueError("Ollama requires a local HTTP(S) endpoint without credentials")
        return value


class TopicEmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    enabled: bool = False
    provider: Literal["ollama"] = "ollama"
    model: str | None = Field(default=None, pattern=r"^\S+$")
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = Field(default=30, gt=0, le=120)

    @field_validator("base_url")
    @classmethod
    def local_url(cls, value: str) -> str:
        return OllamaCreativeConfig.local_url(value)

    @model_validator(mode="after")
    def explicit_model(self) -> "TopicEmbeddingConfig":
        if self.enabled and not self.model:
            raise ValueError("enabled topic embeddings require an explicitly configured model")
        return self


class CreativeTopicsConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )
    candidate_batch_size: int = Field(default=15, ge=1, le=30)
    recent_history_count: int = Field(default=100, ge=1, le=500)
    max_generation_rounds: int = Field(default=3, ge=1, le=10)
    lexical_similarity_threshold: float = Field(default=0.82, gt=0, le=1)
    treatment_similarity_threshold: float = Field(default=0.80, gt=0, le=1)
    semantic_similarity_threshold: float = Field(default=0.88, gt=0, le=1)
    banned_topics: tuple[str, ...] = Field(default=(), max_length=100)
    embedding: TopicEmbeddingConfig = Field(default_factory=TopicEmbeddingConfig)


class CreativeLLMConfig(BaseModel):
    """Explicit NIM configuration; credentials are read only from the named environment."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )

    provider: Literal["nvidia"] = "nvidia"
    model: str = Field(default="moonshotai/kimi-k3", pattern=r"^[\w.-]+/[\w.-]+$")
    base_url: str = "https://integrate.api.nvidia.com/v1"
    api_key_env: str = Field(default="NVIDIA_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=1800, gt=0)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=16384, ge=1, le=131072)
    fallback_models: tuple[str, ...] = Field(
        default=(
            "z-ai/glm-5.3",
            "nvidia/nemotron-3-ultra-550b-a55b",
            "deepseek-ai/deepseek-v4.1-flash",
        ),
        max_length=8,
    )
    fallback_to_ollama_on_endpoint_failure: bool = False
    ollama: OllamaCreativeConfig = Field(default_factory=OllamaCreativeConfig)

    @model_validator(mode="after")
    def valid_chain(self) -> "CreativeLLMConfig":
        import re

        chain = (self.model, *self.fallback_models)
        if any(re.fullmatch(r"[\w.-]+/[\w.-]+", model) is None for model in chain):
            raise ValueError("creative model names must be namespace/model identifiers")
        if len({model.casefold() for model in chain}) != len(chain):
            raise ValueError("creative model chain must not contain duplicates")
        return self

    def generation_budget_seconds(self, stages: int) -> float:
        budget = self.timeout_seconds * 2 * (1 + len(self.fallback_models))
        if self.fallback_to_ollama_on_endpoint_failure:
            budget += self.ollama.timeout_seconds * 2
        return stages * budget + 600

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

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    provider: Literal["google", "qwen_comfyui"] = "qwen_comfyui"
    model: str = "qwen-image-2.1-q8"
    location: Literal["global", "us", "eu"] = "global"
    image_size: Literal["1K", "2K", "4K"] = "1K"
    base_url: str = "http://127.0.0.1:8188"
    workflow_path: Path = Path("workflows/qwen_image_2_1_t2i_api.json")
    width: int = Field(default=768, ge=64)
    height: int = Field(default=1376, ge=64)
    steps: int = Field(default=20, ge=1)
    cfg: float = Field(default=1.0, gt=0)
    sampler: str = "euler"
    scheduler: str = "simple"
    timeout_seconds: float = Field(default=600, gt=0)
    poll_interval_seconds: float = Field(default=1, gt=0)

    @model_validator(mode="before")
    @classmethod
    def legacy_google_defaults(cls, value: object) -> object:
        if isinstance(value, dict):
            raw = dict(value)
            if "provider" not in raw and str(raw.get("model", "")).startswith("gemini-"):
                raw["provider"] = "google"
            if raw.get("provider") == "google" and "model" not in raw:
                raw["model"] = "gemini-3.1-flash-image"
            return raw
        return value

    @model_validator(mode="after")
    def supported_contract(self) -> "EnvironmentGenerationConfig":
        if self.provider == "qwen_comfyui":
            if self.model != "qwen-image-2.1-q8" or not _local_http_url(self.base_url):
                raise ValueError("Qwen environments require qwen-image-2.1-q8 and local HTTP")
            if abs(self.width / self.height - 9 / 16) > 0.025:
                raise ValueError("Qwen environment source must be portrait 9:16")
        else:
            if self.model not in {"gemini-3.1-flash-image", "gemini-3-pro-image"}:
                raise ValueError("unsupported Google environment model")
            if self.model == "gemini-3-pro-image" and self.location != "global":
                raise ValueError("gemini-3-pro-image is available only at the global location")
            if self.model == "gemini-3.1-flash-image" and self.image_size != "1K":
                raise ValueError("the admitted Flash environment contract is pinned to 1K")
        return self


def _local_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.port is not None
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path in {"", "/"}
    )


class LessonObjectGenerationConfig(BaseModel):
    """Provider settings for reviewed lesson objects only."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    provider: Literal["google", "qwen_comfyui"] = "qwen_comfyui"
    model: str = "qwen-image-2.1-q8"
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

    @model_validator(mode="before")
    @classmethod
    def legacy_google_defaults(cls, value: object) -> object:
        if isinstance(value, dict):
            raw = dict(value)
            if "provider" not in raw and str(raw.get("model", "")).startswith("gemini-"):
                raw["provider"] = "google"
            if raw.get("provider") == "google" and "model" not in raw:
                raw["model"] = "gemini-3-pro-image"
            return raw
        return value

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
            if self.model != "qwen-image-2.1-q8" or not _local_http_url(self.base_url):
                raise ValueError(
                    "Qwen lesson objects require qwen-image-2.1-q8 and a local HTTP base URL"
                )
            if self.width != self.height:
                raise ValueError("Qwen lesson objects require a square source")
        return self


class MusicGenerationConfig(BaseModel):
    """External local ACE-Step service; Vertex remains available to older requests."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    provider: Literal["ace_step_local", "google"] = "ace_step_local"
    base_url: str = "http://127.0.0.1:8001"
    model: str = "acestep-v15-turbo"
    lm_model: str = "acestep-5Hz-lm-0.6B"
    lm_backend: Literal["pt"] = "pt"
    thinking: Literal[True] = True
    inference_steps: int = Field(default=8, ge=1, le=20)
    audio_format: Literal["wav"] = "wav"
    batch_size: Literal[1] = 1
    timeout_seconds: float = Field(default=1800, gt=0)
    poll_interval_seconds: float = Field(default=2, gt=0)

    @model_validator(mode="before")
    @classmethod
    def legacy_model_default(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("provider") == "google":
            return {"model": "lyria-3-pro-preview", **value}
        return value

    @model_validator(mode="after")
    def supported_contract(self) -> "MusicGenerationConfig":
        if self.provider == "ace_step_local" and (
            self.model != "acestep-v15-turbo"
            or self.lm_model != "acestep-5Hz-lm-0.6B"
            or not _local_http_url(self.base_url)
        ):
            raise ValueError("ACE-Step requires the pinned models and local HTTP")
        if self.provider == "google" and self.model != "lyria-3-pro-preview":
            raise ValueError("Google music requires lyria-3-pro-preview")
        return self


class YouTubeConfig(BaseModel):
    """Local OAuth files and immutable upload policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    credentials_file: Path = Path("secrets/client_secret.json")
    token_file: Path = Path("secrets/youtube_token.json")
    category_id: str = Field(default="27", pattern=r"^[0-9]+$")
    upload_chunk_size: int = -1
    max_retries: int = Field(default=5, ge=0, le=10)
    contains_synthetic_media: bool = True

    @field_validator("upload_chunk_size")
    @classmethod
    def valid_chunk_size(cls, value: int) -> int:
        if value != -1 and (value <= 0 or value % (256 * 1024)):
            raise ValueError("upload chunk size must be -1 or a positive multiple of 256 KiB")
        return value


class PublicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)


class ProductionAutomationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    auto_publish: bool = False
    publish_visibility: Literal["private", "public"] = "private"
    require_human_review: bool = True
    asr_model: str = "small.en"
    analysis_device: Literal["auto", "cpu", "cuda"] = "auto"
    allow_model_download: bool = False
    analysis_version: int = Field(default=1, gt=0)


class LocalServiceLaunchConfig(BaseModel):
    """Executable argument arrays only; never shell command strings or credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    command: tuple[str, ...] = Field(default=(), max_length=40)
    cwd: Path | None = None
    startup_timeout_seconds: float = Field(default=120, gt=0, le=600)

    @field_validator("command")
    @classmethod
    def safe_command(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values and Path(values[0]).stem.casefold() in {
            "cmd",
            "powershell",
            "pwsh",
            "sh",
            "bash",
            "wscript",
            "cscript",
        }:
            raise ValueError("launcher commands must be executables, not command shells")
        for value in values:
            if not value or len(value) > 2000 or any(ord(c) < 32 for c in value):
                raise ValueError("invalid launcher argument")
            if any(
                word in value.casefold()
                for word in ("api-key", "api_key", "token=", "password", "bearer ", "secret")
            ):
                raise ValueError("credentials must not be supplied in launcher arguments")
        return values


class LocalServicesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ace_step: LocalServiceLaunchConfig = Field(default_factory=LocalServiceLaunchConfig)
    comfyui: LocalServiceLaunchConfig = Field(default_factory=LocalServiceLaunchConfig)
    ollama: LocalServiceLaunchConfig = Field(default_factory=LocalServiceLaunchConfig)
    auto_discover: bool = True


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    database_path: Path
    data_root: Path
    brand_root: Path
    publication_enabled: bool = False
    expected_youtube_channel_id: str | None = None
    publication: PublicationConfig = Field(default_factory=PublicationConfig)
    creative_llm: CreativeLLMConfig = Field(default_factory=CreativeLLMConfig)
    creative_topics: CreativeTopicsConfig = Field(default_factory=CreativeTopicsConfig)
    environment_generation: EnvironmentGenerationConfig = Field(
        default_factory=EnvironmentGenerationConfig
    )
    lesson_object_generation: LessonObjectGenerationConfig = Field(
        default_factory=LessonObjectGenerationConfig
    )
    music_generation: MusicGenerationConfig = Field(default_factory=MusicGenerationConfig)
    automation: ProductionAutomationConfig = Field(default_factory=ProductionAutomationConfig)
    local_services: LocalServicesConfig = Field(default_factory=LocalServicesConfig)

    @model_validator(mode="before")
    @classmethod
    def migrate_publication_flag(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("publication_enabled") is True:
            raw = dict(value)
            publication = dict(raw.get("publication") or {})
            youtube = dict(publication.get("youtube") or {})
            youtube["enabled"] = True
            publication["youtube"] = youtube
            raw["publication"] = publication
            return raw
        return value

    @model_validator(mode="after")
    def publishing_requires_channel(self) -> "RuntimeConfig":
        if (
            self.publication_enabled or self.publication.youtube.enabled
        ) and not self.expected_youtube_channel_id:
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
    for section in ("environment_generation", "lesson_object_generation"):
        generation = raw.setdefault(section, {})
        if (
            isinstance(generation, dict)
            and generation.get("provider", "qwen_comfyui") == "qwen_comfyui"
        ):
            candidate = Path(
                generation.get("workflow_path", "workflows/qwen_image_2_1_t2i_api.json")
            )
            generation["workflow_path"] = (
                (config_file.parent / candidate).resolve()
                if not candidate.is_absolute()
                else candidate.resolve()
            )
    publication = raw.setdefault("publication", {})
    if isinstance(publication, dict):
        youtube = publication.setdefault("youtube", {})
        if isinstance(youtube, dict):
            youtube.setdefault("credentials_file", "secrets/client_secret.json")
            youtube.setdefault("token_file", "secrets/youtube_token.json")
            for key in ("credentials_file", "token_file"):
                value = youtube.get(key)
                if isinstance(value, str):
                    candidate = Path(value)
                    youtube[key] = (
                        (config_file.parent / candidate).resolve()
                        if not candidate.is_absolute()
                        else candidate.resolve()
                    )
    services = raw.get("local_services", {})
    if isinstance(services, dict):
        for name in ("ace_step", "comfyui", "ollama"):
            launch = services.get(name, {})
            if isinstance(launch, dict) and launch.get("cwd"):
                launch["cwd"] = (config_file.parent / Path(launch["cwd"]).expanduser()).resolve()
            if isinstance(launch, dict) and isinstance(launch.get("command"), list):
                # Resolve relative executable/script paths against the configuration file.
                launch["command"] = [
                    str((config_file.parent / Path(arg).expanduser()).resolve())
                    if ("/" in arg or "\\" in arg) and not arg.startswith("--")
                    else arg
                    for arg in launch["command"]
                ]
    return RuntimeConfig.model_validate(raw)
