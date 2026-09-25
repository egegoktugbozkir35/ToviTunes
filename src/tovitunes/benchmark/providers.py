"""Narrow image-provider adapters with injectable, offline-testable transports."""

import base64
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

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
    ) -> None:
        super().__init__(reason)
        self.outcome = outcome
        self.provider_request_id = provider_request_id


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
        timeout_seconds: float = 180,
    ) -> None:
        self.model = model
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
            portrait_9_16=True,
            requested_size="1K, 9:16",
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
                "image_config": {"aspect_ratio": "9:16", "image_size": "1K"},
                "tools": [],
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
        credentials = self._credentials_loader()
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
            image_config=types.ImageConfig(aspect_ratio="9:16", image_size="1K"),
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
                    if response.usage_metadata else None
                ),
                response_metadata={
                    "backend": "Vertex AI", "location": self.location, "project": project.strip(),
                    "image_output_count": len(images), "model_version": response.model_version,
                },
            )
        except errors.APIError as exc:
            status = exc.code
            outcome: Literal["retryable_failure", "terminal_failure", "ambiguous"] = (
                "retryable_failure" if status == 429 else "ambiguous"
                if status == 408 or status >= 500 else "terminal_failure"
            )
            headers = exc.response.headers if exc.response is not None else {}
            raise ProviderFailure(
                f"Vertex AI returned HTTP {status}", outcome=outcome,
                provider_request_id=headers.get("x-request-id"),
            ) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderFailure(
                "Vertex AI transport outcome is uncertain", outcome="ambiguous"
            ) from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProviderFailure(
                "malformed Vertex AI image response" if boundary_transport.started
                else "invalid Vertex AI request preparation",
                outcome="ambiguous" if boundary_transport.started else "terminal_failure",
            ) from exc
        except Exception as exc:
            raise ProviderFailure(
                "Vertex AI generation outcome is uncertain" if boundary_transport.started
                else "Vertex AI request preparation failed",
                outcome="ambiguous" if boundary_transport.started else "terminal_failure",
            ) from exc
        finally:
            http_client.close()
