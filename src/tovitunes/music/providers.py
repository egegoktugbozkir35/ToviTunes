"""Music provider contract and deterministic offline test adapter."""

import io
import json
import math
import os
import struct
import wave
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from typing import Any, Literal, Protocol

import httpx
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
    structured_sections: bool = False
    requested_section_durations: bool = False
    word_timestamps: bool = False


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


class ElevenMusicProvider:
    """One request to Eleven Music detailed; transport is injectable for offline tests."""

    provider = "elevenlabs"
    model = "music_v2_5"
    endpoint = "https://api.elevenlabs.io/v1/music/detailed"
    output_format = "mp3_48000_192"
    capabilities = MusicCapabilities(
        text_to_music=True,
        lyrics=True,
        instrumental_only=False,
        vocals=True,
        target_duration=True,
        bpm=False,
        style=True,
        seed=True,
        stems=False,
        output_formats=("audio/mpeg",),
        usage_metadata=False,
        provider_request_id=True,
        rights_information=False,
        api_contract="Eleven Music detailed v2.5; section durations enforced, BPM stylistic",
        structured_sections=True,
        requested_section_durations=True,
        word_timestamps=True,
    )
    _sections = ("tiny_intro", "hook", "teaching_line", "reinforcement", "short_ending")
    _durations = (3000, 9000, 10000, 9000, 3000)
    _labels = ("Intro", "Chorus", "Verse", "Refrain", "Outro")

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def translate(self, spec: CanonicalMusicSpec) -> dict[str, Any]:
        if spec.brief.id != "colors_red_v1" or spec.brief.sections != self._sections:
            raise ValueError("unsupported Eleven music brief structure")
        if not 30 <= sum(self._durations) / 1000 <= min(40, spec.brief.maximum_duration_seconds):
            raise ValueError("composition plan duration is outside the brief")
        if any(line.section not in self._sections[1:] for line in spec.lyrics.lines):
            raise ValueError("lyric section is outside the brief")
        chunks: list[dict[str, Any]] = []
        for section, label, duration in zip(self._sections, self._labels, self._durations):
            lines = [line.text for line in spec.lyrics.lines if line.section == section]
            if section != "tiny_intro" and not lines:
                raise ValueError("canonical lyric section is empty")
            styles = [
                "bright warm bouncy simple preschool pop",
                "friendly clear intelligible English vocals",
                "light percussion, gentle bass and simple pitched instruments",
                "clear downbeats",
                f"approximately {spec.brief.target_bpm} BPM",
                "catchy uncluttered melody",
                "age appropriate gentle delivery",
            ]
            if section == "tiny_intro":
                styles = [*styles, "brief instrumental introduction"]
            chunks.append(
                {
                    "text": f"[{label}]" + ("\n" + "\n".join(lines) if lines else ""),
                    "duration_ms": duration,
                    "positive_styles": styles,
                    "negative_styles": [
                        "frightening sounds",
                        "aggressive instrumentation",
                        "dense harmony",
                        "mature vocal styling",
                        "melisma",
                        "artist or song imitation",
                    ],
                    "context_adherence": "high",
                }
            )
        if (
            "\n".join(line for chunk in chunks for line in chunk["text"].splitlines()[1:])
            != spec.lyrics.text()
        ):
            raise ValueError("composition plan changes canonical lyrics")
        return {
            "endpoint": self.endpoint,
            "query": {"output_format": self.output_format},
            "body": {
                "model_id": self.model,
                "composition_plan": {"chunks": chunks},
                "seed": 1000 + spec.attempt,
                "with_timestamps": True,
            },
        }

    @staticmethod
    def _parse_multipart(response: httpx.Response) -> tuple[bytes, dict[str, Any]]:
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/mixed;"):
            raise ValueError("expected multipart/mixed music response")
        envelope = (
            b"MIME-Version: 1.0\r\nContent-Type: "
            + content_type.encode("ascii")
            + b"\r\n\r\n"
            + response.content
        )
        message = BytesParser(policy=policy.default).parsebytes(envelope)
        if not message.is_multipart() or message.defects:
            raise ValueError("invalid music multipart response")
        audio: list[bytes] = []
        metadata: list[dict[str, Any]] = []
        for part in message.iter_parts():
            if part.is_multipart() or part.defects:
                raise ValueError("nested music multipart response")
            body = part.get_payload(decode=True)
            if not isinstance(body, bytes):
                raise ValueError("invalid music multipart payload")
            mime = part.get_content_type().lower()
            if mime == "application/json":
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError("invalid music metadata")
                metadata.append(value)
            elif mime in {"audio/mpeg", "application/octet-stream"}:
                audio.append(body)
            else:
                raise ValueError("unexpected music multipart part")
        if len(audio) != 1 or len(metadata) != 1:
            raise ValueError("music response requires one audio and one metadata part")
        return audio[0], metadata[0]

    def generate(
        self,
        spec: CanonicalMusicSpec,
        translated: dict[str, Any],
        on_remote_start: Callable[[], None],
    ) -> MusicResult:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise MusicFailure("ELEVENLABS_API_KEY is required", "retryable_failure")
        if translated != self.translate(spec):
            raise MusicFailure(
                "Eleven music request differs from canonical plan", "retryable_failure"
            )
        try:
            body = json.dumps(translated["body"], sort_keys=True, separators=(",", ":")).encode()
            request = httpx.Request(
                "POST",
                translated["endpoint"],
                params=translated["query"],
                headers={"xi-api-key": key, "content-type": "application/json"},
                content=body,
            )
        except (TypeError, ValueError) as exc:
            raise MusicFailure("invalid local Eleven music request", "retryable_failure") from exc
        client = self._client or httpx.Client(timeout=httpx.Timeout(180.0), follow_redirects=False)
        try:
            on_remote_start()
            try:
                response = client.send(request, follow_redirects=False)
            except httpx.RequestError as exc:
                raise MusicFailure("Eleven music transport outcome unknown", "ambiguous") from exc
            song_id = response.headers.get("song-id")
            if song_id and key in song_id:
                raise MusicFailure("unsafe Eleven music response identity", "ambiguous")
            if response.status_code == 408 or response.status_code >= 500:
                raise MusicFailure("Eleven music server outcome unknown", "ambiguous", song_id)
            if response.status_code >= 400:
                raise MusicFailure(
                    f"Eleven music rejected request (HTTP {response.status_code})",
                    "terminal_failure",
                    song_id,
                )
            if response.status_code != 200:
                raise MusicFailure("unexpected Eleven music response", "ambiguous", song_id)
            try:
                audio, metadata = self._parse_multipart(response)
            except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
                raise MusicFailure("malformed Eleven music response", "ambiguous", song_id) from exc
            safe_metadata = {
                field: metadata[field]
                for field in ("composition_plan", "song_metadata", "words_timestamps", "song_id")
                if field in metadata
            }
            safe_metadata["response_content_type"] = response.headers.get("content-type")
            if key in json.dumps(safe_metadata):
                raise MusicFailure("unsafe Eleven music response metadata", "ambiguous", song_id)
            return MusicResult(
                audio_bytes=audio,
                mime_type="audio/mpeg",
                container="mp3",
                codec="mp3",
                provider_request_id=song_id,
                response_metadata=safe_metadata,
            )
        finally:
            if self._client is None:
                client.close()
