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
    primary: NvidiaNIMClient,
) -> Iterator[ResilientStructuredGenerator]:
    fallbacks: list[NvidiaNIMClient] = []
    emergency: OllamaClient | None = None
    try:
        for model in config.fallback_models:
            fallbacks.append(NvidiaNIMClient(config.model_copy(update={"model": model})))
        if config.fallback_to_ollama_on_endpoint_failure:
            emergency = OllamaClient(config.ollama)
        yield ResilientStructuredGenerator(database, [primary, *fallbacks], emergency=emergency)
    finally:
        for transport in fallbacks:
            transport.close()
        if emergency is not None:
            emergency.close()
