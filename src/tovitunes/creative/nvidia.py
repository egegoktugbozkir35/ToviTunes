"""First-party donor NIM transport adapted with typed terminal failures and no hidden retries."""

import json
import os
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from tovitunes.config import CreativeLLMConfig
from tovitunes.creative.provider import (
    ChatResponse,
    FailureCategory,
    Message,
    ProviderError,
    http_failure,
)


class NvidiaNIMClient:
    provider_name = "nvidia"

    def __init__(self, config: CreativeLLMConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self.model_name: str = config.model
        self._owns_client = client is None
        self._client = client

    @property
    def settings(self) -> dict[str, object]:
        # Preserve pre-fallback fingerprints so existing receipts remain reusable.
        return self.config.model_dump(
            exclude={
                "api_key_env",
                "fallback_models",
                "fallback_to_ollama_on_endpoint_failure",
                "ollama",
            }
        )

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def check_ready(self) -> None:
        self._headers()

    def _headers(self) -> dict[str, str]:
        token = os.getenv(self.config.api_key_env)
        if not token or not token.strip():
            raise ProviderError(f"NVIDIA NIM requires {self.config.api_key_env}")
        return {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token.strip()}",
        }

    @staticmethod
    def _provider_error(body: dict[str, Any]) -> None:
        if body.get("error") is not None:
            # Do not echo arbitrary remote error bodies (they may contain request secrets).
            error = body["error"]
            codes = (
                {
                    str(error[key]).casefold()
                    for key in ("code", "type")
                    if isinstance(error.get(key), (str, int))
                }
                if isinstance(error, dict)
                else set()
            )
            category = (
                FailureCategory.AUTHENTICATION
                if codes
                & {
                    "401",
                    "403",
                    "invalid_api_key",
                    "authentication_error",
                    "unauthorized",
                    "forbidden",
                }
                else FailureCategory.RATE_LIMITED
                if codes & {"429", "rate_limit_exceeded", "rate_limit_error", "insufficient_quota"}
                else FailureCategory.CONFIGURATION
                if codes & {"400", "invalid_request", "invalid_parameter"}
                else FailureCategory.AMBIGUOUS
                if codes
                & {"408", "500", "502", "503", "504", "server_error", "timeout", "request_timeout"}
                else FailureCategory.MODEL_UNAVAILABLE
                if codes & {"model_not_found", "model_unavailable", "model_not_supported"}
                else FailureCategory.CONFIGURATION
                if "invalid_request_error" in codes
                else FailureCategory.PROVIDER_REJECTED
            )
            raise ProviderError(
                "NVIDIA NIM returned a provider error",
                category=category,
                ambiguous=category == FailureCategory.AMBIGUOUS,
            )

    @staticmethod
    def _finish(finish: object) -> None:
        if not isinstance(finish, str) or finish not in {
            "stop",
            "length",
            "content_filter",
            "tool_calls",
            "function_call",
        }:
            raise ProviderError(
                "NVIDIA NIM unknown completion marker; do not resend", ambiguous=True
            )
        if finish != "stop":
            raise ProviderError(
                "NVIDIA NIM completed with unusable finish reason",
                category=FailureCategory.INCOMPLETE_ANSWER,
            )

    @staticmethod
    def _text(content: object) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list) and all(
            isinstance(p, dict) and isinstance(p.get("text"), str) for p in content
        ):
            return "".join(p["text"] for p in content)
        raise ProviderError("NVIDIA NIM returned malformed answer content")

    def chat(
        self, messages: Sequence[Message], *, record_identity: Callable[[str], None]
    ) -> ChatResponse:
        headers = self._headers()
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.config.timeout_seconds, transport=httpx.HTTPTransport(retries=0)
            )
        remote_id: str | None = None
        response_started = False

        def identity(body: dict[str, Any]) -> None:
            nonlocal remote_id
            value = body.get("id")
            if isinstance(value, str) and value.strip():
                remote_id = value
                record_identity(value)

        try:
            with self._client.stream(
                "POST",
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model_name,
                    "messages": list(messages),
                    "stream": True,
                    "temperature": self.config.temperature,
                    "max_tokens": self.config.max_tokens,
                },
                timeout=self.config.timeout_seconds,
            ) as response:
                response_started = True
                for name in ("x-request-id", "request-id", "x-nvidia-request-id"):
                    if response.headers.get(name):
                        remote_id = response.headers[name]
                        record_identity(remote_id)
                        break
                if response.status_code >= 400:
                    category = http_failure(response.status_code)
                    if response.status_code in {400, 404, 422}:
                        response.read()
                        try:
                            error_body = response.json()
                        except ValueError:
                            error_body = None
                        if isinstance(error_body, dict):
                            self._provider_error(error_body)
                    raise ProviderError(
                        f"NVIDIA NIM HTTP {response.status_code} for {self.model_name}",
                        ambiguous=category == FailureCategory.AMBIGUOUS,
                        category=category,
                    )
                if "text/event-stream" not in response.headers.get("content-type", "").casefold():
                    response.read()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ProviderError("NVIDIA NIM returned a non-object response")
                    identity(body)
                    self._provider_error(body)
                    try:
                        finish = body["choices"][0].get("finish_reason")
                        if finish is not None:
                            self._finish(finish)
                        content = self._text(body["choices"][0]["message"]["content"])
                    except (KeyError, IndexError, TypeError) as exc:
                        raise ProviderError("NVIDIA NIM response has no answer content") from exc
                else:
                    parts: list[str] = []
                    complete = False
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        event = json.loads(data)
                        if not isinstance(event, dict):
                            raise ProviderError(
                                "NVIDIA NIM returned a non-object streaming event", ambiguous=True
                            )
                        identity(event)
                        self._provider_error(event)
                        choices = event.get("choices")
                        # A usage-only chunk is legal; arbitrary malformed events are not.
                        if choices == [] and "usage" in event:
                            continue
                        if not isinstance(choices, list) or not choices:
                            raise ProviderError(
                                "NVIDIA NIM returned malformed streaming choices", ambiguous=True
                            )
                        choice = choices[0]
                        if not isinstance(choice, dict):
                            raise ProviderError(
                                "NVIDIA NIM returned malformed streaming choice", ambiguous=True
                            )
                        delta = choice.get("delta", choice.get("message"))
                        if not isinstance(delta, dict):
                            raise ProviderError(
                                "NVIDIA NIM returned malformed streaming delta", ambiguous=True
                            )
                        if delta.get("content") is not None:
                            try:
                                parts.append(self._text(delta["content"]))
                            except ProviderError as exc:
                                raise ProviderError(
                                    "malformed streaming content", ambiguous=True
                                ) from exc
                        # reasoning_content is deliberately never collected.
                        finish = choice.get("finish_reason")
                        if finish is not None:
                            self._finish(finish)
                            complete = True
                            break
                    if not complete:
                        raise ProviderError(
                            "NVIDIA NIM stream ended without completion", ambiguous=True
                        )
                    content = "".join(parts)
        except httpx.ConnectError as exc:
            # Only a typed DNS/refused cause proves no remote interaction could begin.
            import socket

            cause: BaseException | None = exc
            safe = False
            seen: set[int] = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, (socket.gaierror, ConnectionRefusedError)):
                    safe = not response_started
                    break
                cause = cause.__cause__
            raise ProviderError(
                "NVIDIA endpoint unreachable before interaction"
                if safe
                else "NVIDIA connection outcome uncertain; do not resend",
                ambiguous=not safe,
                category=FailureCategory.ENDPOINT_UNREACHABLE,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"NVIDIA NIM transport {type(exc).__name__}; do not resend", ambiguous=True
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise ProviderError(
                "NVIDIA NIM returned malformed transport JSON", ambiguous=True
            ) from exc
        if not content.strip():
            raise ProviderError(
                "NVIDIA NIM returned empty answer content", category=FailureCategory.EMPTY_ANSWER
            )
        return ChatResponse(content, remote_id)
