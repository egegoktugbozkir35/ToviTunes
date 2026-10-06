"""Optional local emergency inference, using the same durable structured contracts."""

from collections.abc import Callable, Sequence

import httpx

from tovitunes.config import OllamaCreativeConfig
from tovitunes.creative.provider import (
    ChatResponse,
    FailureCategory,
    Message,
    ProviderError,
    http_failure,
)


class OllamaClient:
    provider_name = "ollama"

    def __init__(self, config: OllamaCreativeConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self.model_name = config.model
        self._owns_client = client is None
        self._client = client

    @property
    def settings(self) -> dict[str, object]:
        return self.config.model_dump()

    def check_ready(self) -> None:
        pass

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def chat(
        self, messages: Sequence[Message], *, record_identity: Callable[[str], None]
    ) -> ChatResponse:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.config.timeout_seconds, transport=httpx.HTTPTransport(retries=0)
            )
        try:
            response = self._client.post(
                f"{self.config.base_url}/api/chat",
                json={
                    "model": self.model_name,
                    "messages": list(messages),
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "options": {"temperature": self.config.temperature},
                },
            )
            if response.status_code >= 400:
                category = http_failure(response.status_code)
                raise ProviderError(
                    "Ollama rejected generation",
                    ambiguous=category == FailureCategory.AMBIGUOUS,
                    category=category,
                )
            body = response.json()
            if body.get("done") is not True:
                raise ProviderError(
                    "Ollama response lacks completion; do not resend", ambiguous=True
                )
            content = body.get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ProviderError(
                    "Ollama returned empty answer", category=FailureCategory.EMPTY_ANSWER
                )
            return ChatResponse(content)
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Ollama transport outcome uncertain; do not resend", ambiguous=True
            ) from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProviderError("Ollama response malformed; do not resend", ambiguous=True) from exc
