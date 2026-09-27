"""First-party donor NIM transport adapted without fallback or hidden retries."""

import json
import os
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from tovitunes.config import CreativeLLMConfig
from tovitunes.creative.provider import ChatResponse, Message, ProviderError


class NvidiaNIMClient:
    provider_name = "nvidia"

    def __init__(self, config: CreativeLLMConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self.model_name: str = config.model
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=config.timeout_seconds, transport=httpx.HTTPTransport(retries=0)
        )

    @property
    def settings(self) -> dict[str, object]:
        return self.config.model_dump(exclude={"api_key_env"})

    def close(self) -> None:
        if self._owns_client:
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
            raise ProviderError("NVIDIA NIM returned a provider error; model unavailable")

    @staticmethod
    def _text(content: object) -> str:
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
        remote_id: str | None = None

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
                for name in ("x-request-id", "request-id", "x-nvidia-request-id"):
                    if response.headers.get(name):
                        remote_id = response.headers[name]
                        record_identity(remote_id)
                        break
                if response.status_code >= 400:
                    raise ProviderError(
                        f"NVIDIA NIM HTTP {response.status_code} for {self.model_name}",
                        ambiguous=response.status_code >= 500,
                    )
                if "text/event-stream" not in response.headers.get("content-type", "").casefold():
                    response.read()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ProviderError("NVIDIA NIM returned a non-object response")
                    identity(body)
                    self._provider_error(body)
                    try:
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
                            complete = True
                            break
                        event = json.loads(data)
                        if not isinstance(event, dict):
                            raise ProviderError("NVIDIA NIM returned a non-object streaming event")
                        identity(event)
                        self._provider_error(event)
                        choices = event.get("choices")
                        # A usage-only chunk is legal; arbitrary malformed events are not.
                        if choices == [] and "usage" in event:
                            continue
                        if not isinstance(choices, list) or not choices:
                            raise ProviderError("NVIDIA NIM returned malformed streaming choices")
                        choice = choices[0]
                        if not isinstance(choice, dict):
                            raise ProviderError("NVIDIA NIM returned malformed streaming choice")
                        delta = choice.get("delta", choice.get("message"))
                        if not isinstance(delta, dict):
                            raise ProviderError("NVIDIA NIM returned malformed streaming delta")
                        if delta.get("content") is not None:
                            parts.append(self._text(delta["content"]))
                        # reasoning_content is deliberately never collected.
                        finish = choice.get("finish_reason")
                        if finish is not None:
                            if finish != "stop":
                                raise ProviderError(f"NVIDIA NIM incomplete answer: {finish}")
                            complete = True
                    if not complete:
                        raise ProviderError(
                            "NVIDIA NIM stream ended without completion", ambiguous=True
                        )
                    content = "".join(parts)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"NVIDIA NIM transport {type(exc).__name__}; do not resend", ambiguous=True
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("NVIDIA NIM returned malformed transport JSON") from exc
        if not content.strip():
            raise ProviderError("NVIDIA NIM returned empty answer content")
        return ChatResponse(content, remote_id)
