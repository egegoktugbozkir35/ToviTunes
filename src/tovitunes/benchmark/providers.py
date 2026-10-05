"""Narrow image-provider adapters with injectable, offline-testable transports."""

import base64
import copy
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlencode

import google.auth
import httpx
from google import genai
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field

from tovitunes.benchmark.models import CanonicalImageSpec


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_images: bool
    maximum_reference_images: int
    portrait_9_16: bool
    requested_size: str
    grounding_enabled: bool = False
    api_contract: str


class TranslatedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: str
    method: Literal["POST"] = "POST"
    body: dict[str, Any]
    supplied_reference_artifact_ids: tuple[str, ...]
    omitted_reference_artifact_ids: tuple[str, ...] = ()


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_bytes: bytes
    mime_type: str
    provider_request_id: str | None = None
    usage: dict[str, Any] | None = None
    actual_cost_amount: float | None = None
    cost_currency: str | None = None
    pricing_policy: str | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderFailure(Exception):
    def __init__(
        self,
        reason: str,
        *,
        outcome: Literal["retryable_failure", "terminal_failure", "ambiguous"],
        provider_request_id: str | None = None,
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason)
        self.outcome = outcome
        self.provider_request_id = provider_request_id
        self.diagnostics = dict(diagnostics or {})


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookies?|set-cookie|access[_ -]?token|refresh[_ -]?token|"
    r"id[_ -]?token|client[_ -]?secret|private[_ -]?key|credentials?)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\b")
_GOOGLE_TOKEN = re.compile(r"\b(?:ya29\.|1//|AIza)[A-Za-z0-9._~-]{12,}\b")


def _sanitize_provider_text(value: object, *, limit: int = 500) -> str | None:
    """Return one bounded line while removing common credential/token forms."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _JWT_TOKEN.sub("[REDACTED]", text)
    text = _GOOGLE_TOKEN.sub("[REDACTED]", text)
    return text[:limit]


def _safe_identifier(value: object, *, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^A-Za-z0-9._:/+@=-]", "_", value.strip())[:limit]
    return cleaned or None


def _vertex_error_diagnostics(
    exc: errors.APIError, *, model: str, location: str
) -> tuple[str, dict[str, object], str | None]:
    """Extract an allowlisted, sanitized subset of a Vertex SDK error."""
    status_code = int(exc.code or 0)
    canonical_status = _safe_identifier(exc.status, limit=80)
    provider_message = _sanitize_provider_text(exc.message)
    response_headers = getattr(exc.response, "headers", {}) if exc.response is not None else {}
    request_id = _safe_identifier(response_headers.get("x-request-id"))

    error_info_reason: str | None = None
    error_info_type: str | None = None
    details = exc.details
    if isinstance(details, dict):
        payload = details.get("error", details)
        structured = payload.get("details", ()) if isinstance(payload, dict) else ()
        if isinstance(structured, list):
            for item in structured:
                if not isinstance(item, dict):
                    continue
                candidate_reason = _safe_identifier(item.get("reason"), limit=120)
                candidate_type = _safe_identifier(item.get("@type"), limit=160)
                if candidate_reason:
                    error_info_reason = candidate_reason
                if candidate_type:
                    error_info_type = candidate_type.rsplit("/", 1)[-1]
                if error_info_reason:
                    break

    diagnostics: dict[str, object] = {
        "http_status": status_code,
        "model": model,
        "location": location,
        "endpoint_family": "Vertex AI Gemini generateContent",
    }
    for key, value in (
        ("canonical_status", canonical_status),
        ("provider_message", provider_message),
        ("provider_request_id", request_id),
        ("error_info_reason", error_info_reason),
        ("error_info_type", error_info_type),
    ):
        if value:
            diagnostics[key] = value

    summary = f"Vertex AI returned HTTP {status_code}"
    if canonical_status:
        summary += f" ({canonical_status})"
    if provider_message:
        summary += f": {provider_message}"
    if error_info_reason:
        summary += f" [reason={error_info_reason}]"
    return summary, diagnostics, request_id


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class Transport(Protocol):
    def send(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> HttpResponse: ...


class UrllibTransport:
    def send(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        if on_remote_start is not None:
            on_remote_start()
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status=response.status,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                headers={key.lower(): value for key, value in exc.headers.items()},
                body=exc.read(),
            )
        except (TimeoutError, urllib.error.URLError) as exc:
            raise ProviderFailure(
                "connection ended without a definitive provider response",
                outcome="ambiguous",
            ) from exc


class ImageProvider(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def translate(self, spec: CanonicalImageSpec) -> TranslatedRequest: ...

    def generate(
        self,
        spec: CanonicalImageSpec,
        reference_paths: tuple[Path, ...],
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> ProviderResult: ...


class QwenComfyUIImageProvider:
    """Shared local Qwen 2.1 text-to-image adapter for caller-owned prompts."""

    provider = "qwen_comfyui"
    model = "qwen-image-2.1-q8"
    location = "local"
    _nodes = {
        "459:451": "UnetLoaderGGUF",
        "459:452": "TextEncodeQwenImage21",
        "459:453": "CLIPLoader",
        "459:454": "VAELoader",
        "459:456": "EmptyLatentImage",
        "459:457": "VAEDecode",
        "459:458": "KSampler",
        "461": "SaveImageAdvanced",
    }

    def __init__(
        self,
        workflow_path: Path,
        *,
        base_url: str = "http://127.0.0.1:8188",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        cfg: float = 1.0,
        sampler: str = "euler",
        scheduler: str = "simple",
        purpose: Literal["general", "lesson_object", "environment"] = "general",
        timeout_seconds: float = 600,
        poll_interval_seconds: float = 1,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.workflow_path = workflow_path
        self.base_url = base_url.rstrip("/")
        self.width, self.height = width, height
        self.steps, self.cfg = steps, cfg
        self.sampler, self.scheduler = sampler, scheduler
        self.purpose = purpose
        self.image_size = f"{width}x{height}"
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._transport = transport
        self._clock, self._sleep = clock, sleep

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            reference_images=False,
            maximum_reference_images=0,
            portrait_9_16=abs(self.width / self.height - 9 / 16) <= 0.025,
            requested_size=self.image_size,
            api_contract="local ComfyUI /prompt, /history, /view",
        )

    def _workflow(self) -> tuple[dict[str, Any], str]:
        try:
            data = self.workflow_path.read_bytes()
            raw = json.loads(data)
            if not isinstance(raw, dict):
                raise ValueError("workflow root is not an API node mapping")
            for node_id, class_type in self._nodes.items():
                node = raw[node_id]
                if node["class_type"] != class_type or not isinstance(node["inputs"], dict):
                    raise ValueError(f"workflow node {node_id} must be {class_type}")
            if (
                raw["459:451"]["inputs"]["unet_name"] != "qwen_image_2.1_Q8_0.gguf"
                or raw["459:453"]["inputs"]["clip_name"] != "qwen3vl_8b_int8_convrot.safetensors"
                or raw["459:454"]["inputs"]["vae_name"] != "qwen_image_2.1_vae_bf16.safetensors"
                or raw["461"]["inputs"]["images"] != ["459:457", 0]
                or raw["459:456"]["inputs"]["batch_size"] != 1
            ):
                raise ValueError("workflow model stack, batch, or output differs from proven graph")
            for node_id, names in {
                "459:452": ("prompt", "negative_prompt", "resolution"),
                "459:456": ("width", "height"),
                "459:458": ("seed", "steps", "cfg", "sampler_name", "scheduler"),
                "461": ("filename_prefix", "format"),
            }.items():
                if not all(name in raw[node_id]["inputs"] for name in names):
                    raise ValueError(f"workflow node {node_id} has missing inputs")
            if raw["461"]["inputs"]["format"] != "png":
                raise ValueError("workflow output must be PNG")
            return copy.deepcopy(raw), hashlib.sha256(data).hexdigest()
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderFailure(
                f"Qwen API workflow unavailable or invalid at {self.workflow_path}: {exc}",
                outcome="terminal_failure",
            ) from exc

    def _translated(self, spec: CanonicalImageSpec) -> tuple[TranslatedRequest, str]:
        if spec.references:
            raise ProviderFailure(
                "Qwen text-to-image does not accept references", outcome="terminal_failure"
            )
        workflow, workflow_hash = self._workflow()
        seed = int.from_bytes(hashlib.sha256(spec.fingerprint().encode()).digest()[:8], "big")
        workflow["459:452"]["inputs"]["prompt"] = spec.prompt()
        workflow["459:452"]["inputs"]["resolution"] = max(self.width, self.height)
        workflow["459:456"]["inputs"].update(width=self.width, height=self.height)
        workflow["459:458"]["inputs"].update(
            seed=seed,
            steps=self.steps,
            cfg=self.cfg,
            sampler_name=self.sampler,
            scheduler=self.scheduler,
        )
        workflow["461"]["inputs"]["filename_prefix"] = f"ToviTunes_{self.purpose}"
        return (
            TranslatedRequest(
                endpoint=f"{self.base_url}/prompt",
                body={"prompt": workflow, "client_id": f"tovitunes-{self.purpose}"},
                supplied_reference_artifact_ids=(),
            ),
            workflow_hash,
        )

    def translate(self, spec: CanonicalImageSpec) -> TranslatedRequest:
        return self._translated(spec)[0]

    def health(self) -> dict[str, str]:
        """Read-only workflow and ComfyUI availability check."""
        try:
            self._workflow()
        except ProviderFailure:
            return {"status": "misconfigured", "provider": self.provider}
        try:
            with httpx.Client(transport=self._transport, timeout=5, trust_env=False) as client:
                response = client.get(f"{self.base_url}/system_stats")
        except httpx.HTTPError:
            return {"status": "unavailable", "provider": self.provider}
        if response.status_code != 200:
            return {"status": "unavailable", "provider": self.provider}
        try:
            body = response.json()
        except ValueError:
            return {"status": "misconfigured", "provider": self.provider}
        if not isinstance(body, dict) or "system" not in body:
            return {"status": "misconfigured", "provider": self.provider}
        return {"status": "available", "provider": self.provider}

    def generate(
        self,
        spec: CanonicalImageSpec,
        reference_paths: tuple[Path, ...],
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> ProviderResult:
        if reference_paths:
            raise ProviderFailure(
                "Qwen text-to-image does not accept references", outcome="terminal_failure"
            )
        translated, workflow_hash = self._translated(spec)
        seed = translated.body["prompt"]["459:458"]["inputs"]["seed"]
        prompt_id: str | None = None
        started = self._clock()
        try:
            with httpx.Client(transport=self._transport, timeout=30) as client:
                if on_remote_start is not None:
                    on_remote_start()
                response = client.post(translated.endpoint, json=translated.body)
                if response.status_code >= 400:
                    raise ProviderFailure(
                        f"ComfyUI rejected /prompt (HTTP {response.status_code})",
                        outcome="terminal_failure" if response.status_code < 500 else "ambiguous",
                    )
                payload = response.json()
                prompt_id = payload.get("prompt_id") if isinstance(payload, dict) else None
                if not isinstance(prompt_id, str) or not prompt_id:
                    raise ProviderFailure("ComfyUI /prompt omitted prompt_id", outcome="ambiguous")
                while self._clock() - started < self.timeout_seconds:
                    history = client.get(f"{self.base_url}/history/{prompt_id}")
                    if history.status_code >= 500:
                        raise ProviderFailure(
                            "ComfyUI history became unavailable",
                            outcome="ambiguous",
                            provider_request_id=prompt_id,
                        )
                    if history.status_code not in {200, 404}:
                        raise ProviderFailure(
                            f"ComfyUI history returned HTTP {history.status_code}",
                            outcome="ambiguous",
                            provider_request_id=prompt_id,
                        )
                    item = history.json().get(prompt_id, {}) if history.status_code == 200 else {}
                    if item:
                        status = item.get("status", {})
                        if (
                            status.get("status_str") == "error"
                            or status.get("completed") is True
                            and status.get("status_str") != "success"
                        ):
                            messages = status.get("messages", [])
                            detail = _sanitize_provider_text(
                                str(messages[-1]) if messages else "execution error", limit=180
                            )
                            raise ProviderFailure(
                                f"ComfyUI execution failed: {detail}",
                                outcome="terminal_failure",
                                provider_request_id=prompt_id,
                            )
                        images = item.get("outputs", {}).get("461", {}).get("images", [])
                        if status.get("status_str") == "success" and images:
                            output = images[0]
                            if not isinstance(output, dict) or not all(
                                k in output for k in ("filename", "subfolder", "type")
                            ):
                                raise ProviderFailure(
                                    "ComfyUI output metadata is invalid",
                                    outcome="terminal_failure",
                                    provider_request_id=prompt_id,
                                )
                            query = urlencode(
                                {key: output[key] for key in ("filename", "subfolder", "type")}
                            )
                            view = client.get(f"{self.base_url}/view?{query}")
                            if view.status_code != 200:
                                raise ProviderFailure(
                                    "ComfyUI image retrieval failed",
                                    outcome="ambiguous",
                                    provider_request_id=prompt_id,
                                )
                            return ProviderResult(
                                image_bytes=view.content,
                                mime_type="image/png",
                                provider_request_id=prompt_id,
                                response_metadata={
                                    "seed": seed,
                                    "width": self.width,
                                    "height": self.height,
                                    "steps": self.steps,
                                    "cfg": self.cfg,
                                    "sampler": self.sampler,
                                    "scheduler": self.scheduler,
                                    "workflow_sha256": workflow_hash,
                                    "workflow_file": self.workflow_path.name,
                                    "purpose": self.purpose,
                                    "output_filename": output["filename"],
                                },
                            )
                        if status.get("status_str") == "success":
                            raise ProviderFailure(
                                "ComfyUI completed without expected image output",
                                outcome="terminal_failure",
                                provider_request_id=prompt_id,
                            )
                    self._sleep(self.poll_interval_seconds)
        except ProviderFailure:
            raise
        except httpx.ConnectError as exc:
            raise ProviderFailure(
                "ComfyUI connection refused before submission"
                if prompt_id is None
                else "ComfyUI connection lost after submission",
                outcome="terminal_failure" if prompt_id is None else "ambiguous",
                provider_request_id=prompt_id,
            ) from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderFailure(
                "ComfyUI response became uncertain after submission",
                outcome="ambiguous",
                provider_request_id=prompt_id,
            ) from exc
        raise ProviderFailure(
            "ComfyUI generation timed out after submission",
            outcome="ambiguous",
            provider_request_id=prompt_id,
        )


def _api_failure(response: HttpResponse) -> ProviderFailure:
    request_id = response.headers.get("x-request-id")
    try:
        raw = json.loads(response.body)
        error = raw.get("error", raw) if isinstance(raw, dict) else raw
        message = error.get("message") if isinstance(error, dict) else None
        if request_id is None and isinstance(raw, dict):
            request_id = raw.get("id") or raw.get("request_id")
    except (json.JSONDecodeError, UnicodeDecodeError):
        message = None
    outcome: Literal["retryable_failure", "terminal_failure", "ambiguous"] = (
        "retryable_failure"
        if response.status == 429
        else "ambiguous"
        if response.status == 408 or response.status >= 500
        else "terminal_failure"
    )
    return ProviderFailure(
        message or f"provider returned HTTP {response.status}",
        outcome=outcome,
        provider_request_id=request_id,
    )


def _load_reference_bytes(
    spec: CanonicalImageSpec, paths: tuple[Path, ...]
) -> tuple[tuple[str, bytes, str], ...]:
    if len(paths) != len(spec.references):
        raise ValueError("reference paths differ from canonical references")
    loaded = []
    for reference, path in zip(spec.references, paths, strict=True):
        data = path.read_bytes()
        import hashlib

        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ValueError(f"reference bytes differ from lock: {reference.role}")
        loaded.append((f"{reference.role.replace('/', '-')}.png", data, reference.mime_type))
    return tuple(loaded)


class OpenAIImageProvider:
    provider = "openai"

    def __init__(
        self,
        model: str = "gpt-image-2.5-sunburst",
        *,
        transport: Transport | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        self.model = model
        self._transport = transport or UrllibTransport()
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            reference_images=True,
            maximum_reference_images=16,
            portrait_9_16=True,
            requested_size="1008x1792 (exact 9:16 custom dimensions)",
            grounding_enabled=False,
            api_contract="OpenAI Image API /v1/images/edits",
        )

    def translate(self, spec: CanonicalImageSpec) -> TranslatedRequest:
        ids = tuple(item.artifact_id for item in spec.references)
        return TranslatedRequest(
            endpoint="https://api.openai.com/v1/images/edits",
            body={
                "model": self.model,
                "prompt": spec.prompt(),
                "size": "1008x1792",
                "quality": "high",
                "output_format": "png",
                "reference_count": len(ids),
            },
            supplied_reference_artifact_ids=ids,
        )

    def generate(
        self,
        spec: CanonicalImageSpec,
        reference_paths: tuple[Path, ...],
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> ProviderResult:
        key = self._api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderFailure("OPENAI_API_KEY is required", outcome="terminal_failure")
        translated = self.translate(spec)
        boundary = "----tovitunes-" + secrets.token_hex(16)
        chunks: list[bytes] = []

        def field(name: str, value: str) -> None:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode(),
                    b"\r\n",
                ]
            )

        for name in ("model", "prompt", "size", "quality", "output_format"):
            field(name, str(translated.body[name]))
        for filename, data, mime_type in _load_reference_bytes(spec, reference_paths):
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    (
                        f'Content-Disposition: form-data; name="image[]"; filename="{filename}"\r\n'
                    ).encode(),
                    f"Content-Type: {mime_type}\r\n\r\n".encode(),
                    data,
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        response = self._transport.send(
            translated.endpoint,
            {
                "Authorization": f"Bearer {key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            b"".join(chunks),
            self._timeout_seconds,
            on_remote_start=on_remote_start,
        )
        if response.status >= 400:
            raise _api_failure(response)
        raw: Any = None
        try:
            raw = json.loads(response.body)
            if not isinstance(raw, dict):
                raise ValueError("success body is not an object")
            encoded = raw["data"][0]["b64_json"]
            image_bytes = base64.b64decode(encoded, validate=True)
            return ProviderResult(
                image_bytes=image_bytes,
                mime_type="image/png",
                provider_request_id=response.headers.get("x-request-id") or raw.get("id"),
                usage=raw.get("usage"),
                response_metadata={"created": raw.get("created"), "output_count": len(raw["data"])},
            )
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            AttributeError,
            json.JSONDecodeError,
        ) as exc:
            raise ProviderFailure(
                "malformed OpenAI image response",
                outcome="ambiguous",
                provider_request_id=response.headers.get("x-request-id")
                or (raw.get("id") if isinstance(raw, dict) else None),
            ) from exc


class _VertexBoundaryTransport(httpx.BaseTransport):
    """Persist remote start after SDK serialization, immediately before HTTP send."""

    def __init__(
        self, on_remote_start: Callable[[], None] | None, delegate: httpx.BaseTransport
    ) -> None:
        self._on_remote_start = on_remote_start
        self._delegate = delegate
        self.started = False
        self.response_request_id: str | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._on_remote_start is not None:
            self._on_remote_start()
        self.started = True
        response = self._delegate.handle_request(request)
        self.response_request_id = response.headers.get("x-request-id")
        return response

    def close(self) -> None:
        self._delegate.close()


def _vertex_credentials() -> Credentials:
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            credentials.refresh(GoogleAuthRequest())  # type: ignore[no-untyped-call]
        if not credentials.valid:
            raise ValueError("ADC did not produce valid credentials")
        return credentials
    except Exception as exc:
        raise ProviderFailure(
            "Vertex AI Application Default Credentials are unavailable or invalid",
            outcome="terminal_failure",
        ) from exc


class GeminiImageProvider:
    provider = "google"

    def __init__(
        self,
        model: str = "gemini-3.1-flash-image",
        *,
        transport: httpx.BaseTransport | None = None,
        credentials_loader: Callable[[], Credentials] = _vertex_credentials,
        project: str | None = None,
        location: str | None = None,
        image_size: Literal["1K", "2K", "4K"] = "1K",
        aspect_ratio: Literal["1:1", "9:16"] = "9:16",
        timeout_seconds: float = 180,
    ) -> None:
        if image_size not in {"1K", "2K", "4K"}:
            raise ValueError("unsupported Gemini image size")
        self.model = model
        self.image_size = image_size
        self.aspect_ratio = aspect_ratio
        self._transport = transport
        self._credentials_loader = credentials_loader
        self._project = project
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
        self._timeout_seconds = timeout_seconds

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            reference_images=True,
            maximum_reference_images=14,
            portrait_9_16=self.aspect_ratio == "9:16",
            requested_size=f"{self.image_size}, {self.aspect_ratio}",
            grounding_enabled=False,
            api_contract="Vertex AI Gemini generateContent via google-genai",
        )

    def translate(self, spec: CanonicalImageSpec) -> TranslatedRequest:
        ids = tuple(item.artifact_id for item in spec.references)
        host = (
            "aiplatform.googleapis.com"
            if self.location == "global"
            else f"aiplatform.{self.location}.rep.googleapis.com"
        )
        return TranslatedRequest(
            endpoint=(
                f"https://{host}/v1beta1/projects/{{GOOGLE_CLOUD_PROJECT}}/"
                f"locations/{self.location}/publishers/google/models/{self.model}:generateContent"
            ),
            body={
                "backend": "Vertex AI",
                "location": self.location,
                "model": self.model,
                "input": [
                    {"type": "text", "text": spec.prompt()},
                    *[
                        {
                            "type": "image",
                            "artifact_id": item.artifact_id,
                            "mime_type": item.mime_type,
                            "sha256": item.sha256,
                        }
                        for item in spec.references
                    ],
                ],
                "response_modalities": ["TEXT", "IMAGE"],
                "image_config": {
                    "aspect_ratio": self.aspect_ratio,
                    "image_size": self.image_size,
                },
                "tools": [],
            },
            supplied_reference_artifact_ids=ids,
        )

    def preflight(self) -> tuple[str, Credentials]:
        """Validate Vertex configuration before durable request preparation."""
        project = self._project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project or not project.strip():
            raise ProviderFailure(
                "GOOGLE_CLOUD_PROJECT is required for Vertex AI", outcome="terminal_failure"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", project.strip()):
            raise ProviderFailure("GOOGLE_CLOUD_PROJECT is invalid", outcome="terminal_failure")
        if not re.fullmatch(r"[a-z0-9-]+", self.location):
            raise ProviderFailure("GOOGLE_CLOUD_LOCATION is invalid", outcome="terminal_failure")
        if self.model == "gemini-3.1-flash-image" and self.location not in {"global", "us", "eu"}:
            raise ProviderFailure(
                "GOOGLE_CLOUD_LOCATION is unavailable for gemini-3.1-flash-image",
                outcome="terminal_failure",
            )
        if self.model == "gemini-3-pro-image" and self.location != "global":
            raise ProviderFailure(
                "gemini-3-pro-image is available only at the global location",
                outcome="terminal_failure",
            )
        credentials = self._credentials_loader()
        return project.strip(), credentials

    def generate(
        self,
        spec: CanonicalImageSpec,
        reference_paths: tuple[Path, ...],
        *,
        on_remote_start: Callable[[], None] | None = None,
    ) -> ProviderResult:
        project, credentials = self.preflight()
        references = _load_reference_bytes(spec, reference_paths)
        contents = types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=spec.prompt()),
                *(types.Part.from_bytes(data=data, mime_type=mime) for _, data, mime in references),
            ],
        )
        config = types.GenerateContentConfig(
            response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
            image_config=types.ImageConfig(
                aspect_ratio=self.aspect_ratio, image_size=self.image_size
            ),
            tools=[],
        )
        delegate = self._transport or httpx.HTTPTransport()
        boundary_transport = _VertexBoundaryTransport(on_remote_start, delegate)
        http_client = httpx.Client(transport=boundary_transport)
        try:
            with genai.Client(
                vertexai=True,
                project=project.strip(),
                location=self.location,
                credentials=credentials,
                http_options=types.HttpOptions(
                    httpx_client=http_client,
                    timeout=int(self._timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            ) as client:
                response = client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            images = [
                part.inline_data
                for candidate in response.candidates or []
                if candidate.content is not None
                for part in candidate.content.parts or []
                if part.inline_data is not None
            ]
            if len(images) != 1 or not images[0].data:
                raise ValueError("expected exactly one inline image")
            image = images[0]
            image_bytes = image.data
            if image_bytes is None:
                raise ValueError("missing image bytes")
            return ProviderResult(
                image_bytes=image_bytes,
                mime_type=image.mime_type or "image/png",
                provider_request_id=response.response_id or boundary_transport.response_request_id,
                usage=(
                    response.usage_metadata.model_dump(mode="json", exclude_none=True)
                    if response.usage_metadata
                    else None
                ),
                response_metadata={
                    "backend": "Vertex AI",
                    "location": self.location,
                    "project": project.strip(),
                    "requested_image_size": self.image_size,
                    "image_output_count": len(images),
                    "model_version": response.model_version,
                },
            )
        except errors.APIError as exc:
            status = exc.code
            outcome: Literal["retryable_failure", "terminal_failure", "ambiguous"] = (
                "retryable_failure"
                if status == 429
                else "ambiguous"
                if status == 408 or status >= 500
                else "terminal_failure"
            )
            summary, diagnostics, request_id = _vertex_error_diagnostics(
                exc, model=self.model, location=self.location
            )
            raise ProviderFailure(
                summary,
                outcome=outcome,
                provider_request_id=request_id,
                diagnostics=diagnostics,
            ) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderFailure(
                "Vertex AI transport outcome is uncertain", outcome="ambiguous"
            ) from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProviderFailure(
                "malformed Vertex AI image response"
                if boundary_transport.started
                else "invalid Vertex AI request preparation",
                outcome="ambiguous" if boundary_transport.started else "terminal_failure",
            ) from exc
        except Exception as exc:
            raise ProviderFailure(
                "Vertex AI generation outcome is uncertain"
                if boundary_transport.started
                else "Vertex AI request preparation failed",
                outcome="ambiguous" if boundary_transport.started else "terminal_failure",
            ) from exc
        finally:
            http_client.close()
