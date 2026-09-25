"""Music provider contract and deterministic offline test adapter."""

import io
import math
import struct
import wave
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import Field

from tovitunes.music.models import CanonicalMusicSpec, StrictModel


class MusicCapabilities(StrictModel):
    text_to_music: bool
    lyrics: bool
    instrumental_only: bool
    vocals: bool
    target_duration: bool
    bpm: bool
    style: bool
    seed: bool
    stems: bool
    output_formats: tuple[str, ...]
    usage_metadata: bool
    provider_request_id: bool
    rights_information: bool
    api_contract: str


class MusicResult(StrictModel):
    audio_bytes: bytes
    mime_type: str
    container: str | None = None
    codec: str | None = None
    provider_request_id: str | None = None
    usage: dict[str, Any] | None = None
    actual_cost_amount: float | None = Field(default=None, ge=0)
    cost_currency: str | None = None
    pricing_policy: str | None = None
    rights_evidence: dict[str, Any] | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)


class MusicFailure(Exception):
    def __init__(
        self,
        reason: str,
        outcome: Literal["retryable_failure", "terminal_failure", "ambiguous"],
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.outcome = outcome
        self.provider_request_id = provider_request_id


class MusicProvider(Protocol):
    provider: str
    model: str
    capabilities: MusicCapabilities

    def translate(self, spec: CanonicalMusicSpec) -> dict[str, Any]: ...

    def generate(
        self,
        spec: CanonicalMusicSpec,
        translated: dict[str, Any],
        on_remote_start: Callable[[], None],
    ) -> MusicResult: ...


class FakeMusicProvider:
    """Documented local simulator; it never opens a network connection."""

    provider = "offline_fake"
    model = "sine-v1"
    capabilities = MusicCapabilities(
        text_to_music=True,
        lyrics=True,
        instrumental_only=True,
        vocals=False,
        target_duration=True,
        bpm=True,
        style=True,
        seed=True,
        stems=False,
        output_formats=("audio/wav",),
        usage_metadata=False,
        provider_request_id=True,
        rights_information=False,
        api_contract="local deterministic simulator v1",
    )

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior
        self.calls = 0

    def translate(self, spec: CanonicalMusicSpec) -> dict[str, Any]:
        return {
            "mode": "local_synthesis",
            "brief_id": spec.brief.id,
            "lyrics": spec.lyrics.text(),
            "target_seconds": spec.brief.preferred_duration_seconds[0],
            "bpm": spec.brief.target_bpm,
            "style": spec.brief.style,
            "seed": spec.attempt,
        }

    def generate(
        self,
        spec: CanonicalMusicSpec,
        translated: dict[str, Any],
        on_remote_start: Callable[[], None],
    ) -> MusicResult:
        self.calls += 1
        if self.behavior == "preflight":
            raise MusicFailure("simulated local preflight", "retryable_failure")
        on_remote_start()
        if self.behavior == "retryable":
            raise MusicFailure(
                "simulated provider outcome", "retryable_failure", f"fake-{spec.attempt}"
            )
        if self.behavior == "ambiguous":
            raise MusicFailure("simulated provider outcome", "ambiguous", f"fake-{spec.attempt}")
        if self.behavior == "terminal":
            raise MusicFailure(
                "simulated provider outcome", "terminal_failure", f"fake-{spec.attempt}"
            )
        if self.behavior == "malformed":
            audio = b"not a wave"
        else:
            sample_rate = 8000
            frames = bytearray()
            for i in range(sample_rate * spec.brief.preferred_duration_seconds[0]):
                value = int(
                    800 * math.sin(2 * math.pi * (220 + spec.attempt * 10) * i / sample_rate)
                )
                frames.extend(struct.pack("<h", value))
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(sample_rate)
                stream.writeframes(frames)
            audio = buffer.getvalue()
        return MusicResult(
            audio_bytes=audio,
            mime_type="audio/wav",
            container="wav",
            codec="pcm_s16le",
            provider_request_id=f"fake-{spec.attempt}",
            response_metadata={"simulated": True},
        )
