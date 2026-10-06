"""Construct explicit donor-style model chain; Ollama is instantiated only when enabled."""

from collections.abc import Iterator
from contextlib import contextmanager

from tovitunes.config import CreativeLLMConfig
from tovitunes.creative.nvidia import NvidiaNIMClient
from tovitunes.creative.ollama import OllamaClient
from tovitunes.creative.resilience import ResilientStructuredGenerator
from tovitunes.persistence.db import Database


@contextmanager
def creative_generator(
    database: Database,
    config: CreativeLLMConfig,
    primary: NvidiaNIMClient | None = None,
) -> Iterator[ResilientStructuredGenerator]:
    close_primary = primary is None
    bounded = config.model_copy(update={"timeout_seconds": min(config.timeout_seconds, 120.0)})
    primary = primary or NvidiaNIMClient(bounded)
    fallbacks: list[NvidiaNIMClient] = []
    emergency: OllamaClient | None = None
    try:
        for model in config.fallback_models:
            fallbacks.append(NvidiaNIMClient(bounded.model_copy(update={"model": model})))
        if config.fallback_to_ollama_on_endpoint_failure:
            emergency = OllamaClient(config.ollama)
        yield ResilientStructuredGenerator(database, [primary, *fallbacks], emergency=emergency)
    finally:
        if close_primary:
            primary.close()
        for transport in fallbacks:
            transport.close()
        if emergency is not None:
            emergency.close()
