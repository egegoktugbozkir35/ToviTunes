"""ACE-Step 1.5 local REST adapter; no ACE-Step package or model dependency."""

import json
import time
from collections.abc import Callable
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from tovitunes.config import MusicGenerationConfig
from tovitunes.music.audio import inspect_audio
from tovitunes.music.models import CanonicalMusicSpec
from tovitunes.music.providers import MusicCapabilities, MusicFailure, MusicResult


class AceStepLocalProvider:
    provider = "ace_step_local"
    model = "acestep-v15-turbo"
    capabilities = MusicCapabilities(
        text_to_music=True,
        lyrics=True,
        instrumental_only=False,
        vocals=True,
        target_duration=True,
        bpm=True,
        style=True,
        seed=True,
        stems=False,
        output_formats=("audio/wav",),
        usage_metadata=False,
        provider_request_id=True,
        rights_information=True,
        api_contract="ACE-Step 1.5 local /release_task, /query_result, /v1/audio",
        user_provided_lyrics=True,
        generated_lyrics=False,
    )

    def __init__(
        self,
        config: MusicGenerationConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or MusicGenerationConfig()
        if self.config.provider != self.provider:
            raise ValueError("ACE-Step provider requires ace_step_local configuration")
        self.base_url = self.config.base_url.rstrip("/")
        self._transport = transport
        self._clock = clock
        self._sleep = sleep

    def _client(self) -> httpx.Client:
        return httpx.Client(
            transport=self._transport,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        )

    def health(self) -> dict[str, str]:
        """Read-only service status, safe when ACE-Step is not installed."""
        try:
            with self._client() as client:
                response = client.get(f"{self.base_url}/health")
        except httpx.HTTPError:
            return {"status": "unavailable", "provider": self.provider}
        if response.status_code != 200:
            return {"status": "unavailable", "provider": self.provider}
        try:
            body = response.json()
        except ValueError:
            return {"status": "misconfigured", "provider": self.provider}
        if (
            not isinstance(body, dict)
            or body.get("code") != 200
            or not isinstance(body.get("data"), dict)
            or body["data"].get("status") != "ok"
        ):
            return {"status": "misconfigured", "provider": self.provider}
        return {"status": "available", "provider": self.provider}

    def translate(self, spec: CanonicalMusicSpec) -> dict[str, Any]:
        brief = spec.brief
        style = " ".join(
            (
                brief.objective,
                brief.style,
                brief.arrangement,
                brief.vocal_direction,
                "Avoid: " + "; ".join(brief.avoid),
                "Sing only the supplied lyrics in the supplied line order, each line once. "
                "Add no extra sung words, lyrical ad-libs, or repeated chorus unless explicitly "
                "written. Clear English preschool diction for ages 3–6. Keep the music simple "
                "and suitable for children. No named artist or song imitation.",
            )
        )
        seed_input = json.dumps(
            spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        seed = int.from_bytes(sha256(seed_input).digest()[:4], "big") & 0x7FFF_FFFF
        low, high = brief.preferred_duration_seconds
        return {
            "task_type": "text2music",
            "model": self.config.model,
            "lm_model_path": self.config.lm_model,
            "lm_backend": self.config.lm_backend,
            "thinking": self.config.thinking,
            "use_format": False,
            "sample_mode": False,
            "use_cot_caption": False,
            "use_cot_language": False,
            "vocal_language": "en",
            "audio_format": self.config.audio_format,
            "inference_steps": self.config.inference_steps,
            "batch_size": self.config.batch_size,
            "bpm": brief.target_bpm,
            "audio_duration": (low + high) // 2,
            "use_random_seed": False,
            "seed": seed,
            "prompt": style,
            "lyrics": spec.lyrics.text(),
        }

    def _validate_request(self, spec: CanonicalMusicSpec, request: dict[str, Any]) -> None:
        if request != self.translate(spec):
            raise MusicFailure(
                "ACE-Step translated request differs from canonical inputs", "terminal_failure"
            )

    @staticmethod
    def _data(response: httpx.Response, task_id: str | None = None) -> Any:
        if response.status_code >= 500 or response.status_code in {408, 429}:
            raise MusicFailure("ACE-Step service response is uncertain", "ambiguous", task_id)
        if response.status_code != 200:
            raise MusicFailure("ACE-Step rejected the request", "terminal_failure", task_id)
        try:
            body = response.json()
        except ValueError as exc:
            raise MusicFailure("ACE-Step returned malformed JSON", "ambiguous", task_id) from exc
        if not isinstance(body, dict) or body.get("code") != 200 or "data" not in body:
            raise MusicFailure("ACE-Step returned an invalid API envelope", "ambiguous", task_id)
        return body["data"]

    def generate(
        self,
        spec: CanonicalMusicSpec,
        translated: dict[str, Any],
        on_remote_start: Callable[[], None],
    ) -> MusicResult:
        return self.generate_with_identity(spec, translated, on_remote_start, lambda _id: None)

    def generate_with_identity(
        self,
        spec: CanonicalMusicSpec,
        translated: dict[str, Any],
        on_remote_start: Callable[[], None],
        on_provider_identity: Callable[[str], None],
    ) -> MusicResult:
        self._validate_request(spec, translated)
        status = self.health()
        if status["status"] != "available":
            raise MusicFailure("ACE-Step local service is " + status["status"], "retryable_failure")
        task_id: str | None = None
        with self._client() as client:
            on_remote_start()
            try:
                response = client.post(f"{self.base_url}/release_task", json=translated)
            except httpx.ConnectError as exc:
                raise MusicFailure(
                    "ACE-Step connection failed before task creation", "retryable_failure"
                ) from exc
            except httpx.HTTPError as exc:
                raise MusicFailure("ACE-Step submission outcome is unknown", "ambiguous") from exc
            data = self._data(response)
            task_id = data.get("task_id") if isinstance(data, dict) else None
            if not isinstance(task_id, str) or not task_id:
                raise MusicFailure("ACE-Step submission omitted task_id", "ambiguous")
            on_provider_identity(task_id)
            started = self._clock()
            while self._clock() - started < self.config.timeout_seconds:
                try:
                    return self._retrieve_with_client(client, task_id, translated)
                except MusicFailure as exc:
                    if exc.outcome != "retryable_failure":
                        raise
                self._sleep(self.config.poll_interval_seconds)
        raise MusicFailure("ACE-Step task timed out", "ambiguous", task_id)

    def retrieve(self, task_id: str, translated_request: dict[str, Any]) -> MusicResult:
        """Query one known task; never call /release_task."""
        if not task_id or translated_request.get("model") != self.model:
            raise MusicFailure("ACE-Step recovery identity is invalid", "terminal_failure", task_id)
        with self._client() as client:
            return self._retrieve_with_client(client, task_id, translated_request)

    def _retrieve_with_client(
        self, client: httpx.Client, task_id: str, request: dict[str, Any]
    ) -> MusicResult:
        try:
            response = client.post(
                f"{self.base_url}/query_result", json={"task_id_list": [task_id]}
            )
            data = self._data(response, task_id)
        except httpx.HTTPError as exc:
            raise MusicFailure("ACE-Step task query failed", "ambiguous", task_id) from exc
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise MusicFailure(
                "ACE-Step task query returned unexpected tasks", "ambiguous", task_id
            )
        task = data[0]
        if task.get("task_id") != task_id:
            raise MusicFailure("ACE-Step task identity mismatch", "terminal_failure", task_id)
        if task.get("status") == 0:
            raise MusicFailure("ACE-Step task is still running", "retryable_failure", task_id)
        if task.get("status") == 2:
            raise MusicFailure("ACE-Step task failed", "terminal_failure", task_id)
        if task.get("status") != 1:
            raise MusicFailure("ACE-Step task status is unknown", "ambiguous", task_id)
        try:
            outputs = json.loads(task["result"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MusicFailure(
                "ACE-Step result JSON is malformed", "terminal_failure", task_id
            ) from exc
        if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict):
            raise MusicFailure(
                "ACE-Step returned an unexpected output count", "terminal_failure", task_id
            )
        output = outputs[0]
        if output.get("status") not in {None, 1}:
            raise MusicFailure("ACE-Step output did not succeed", "terminal_failure", task_id)
        if (
            output.get("dit_model", self.model) != self.model
            or output.get("lm_model", self.config.lm_model) != self.config.lm_model
        ):
            raise MusicFailure(
                "ACE-Step output model differs from request", "terminal_failure", task_id
            )
        if output.get("lyrics", request.get("lyrics")) != request.get("lyrics"):
            raise MusicFailure("ACE-Step returned altered lyrics", "terminal_failure", task_id)
        metas = output.get("metas", {})
        if not isinstance(metas, dict):
            raise MusicFailure("ACE-Step output metadata is malformed", "terminal_failure", task_id)
        audio_url = self._audio_url(output.get("file"), task_id)
        try:
            audio_response = client.get(audio_url)
        except httpx.HTTPError as exc:
            raise MusicFailure("ACE-Step audio download failed", "ambiguous", task_id) from exc
        if audio_response.status_code != 200:
            raise MusicFailure("ACE-Step audio download failed", "ambiguous", task_id)
        audio = audio_response.content
        try:
            info = inspect_audio(audio, "audio/wav")
        except ValueError as exc:
            raise MusicFailure(
                "ACE-Step returned invalid WAV audio", "terminal_failure", task_id
            ) from exc
        return MusicResult(
            audio_bytes=audio,
            mime_type="audio/wav",
            container="wav",
            codec=info.codec,
            provider_request_id=task_id,
            rights_evidence={
                "provider_repository": "https://github.com/ace-step/ACE-Step-1.5",
                "repository_license": "MIT",
                "commercial_clearance": "review_required",
            },
            response_metadata={
                "requested_seed": request.get("seed"),
                "reported_seed": output.get("seed_value"),
                "reported_bpm": metas.get("bpm"),
                "reported_duration": metas.get("duration"),
                "reported_model": output.get("dit_model"),
                "reported_lm_model": output.get("lm_model"),
                "actual_duration_seconds": info.duration_seconds,
                "sample_rate_hz": info.sample_rate_hz,
                "audio_sha256": sha256(audio).hexdigest(),
                "audio_byte_count": len(audio),
            },
        )

    def _audio_url(self, value: object, task_id: str) -> str:
        if not isinstance(value, str):
            raise MusicFailure("ACE-Step result omitted audio path", "terminal_failure", task_id)
        candidate = urlsplit(value)
        origin = urlsplit(self.base_url)
        if candidate.scheme or candidate.netloc:
            if (candidate.scheme, candidate.netloc) != (origin.scheme, origin.netloc):
                raise MusicFailure(
                    "ACE-Step audio URL has an external origin", "terminal_failure", task_id
                )
        if candidate.path != "/v1/audio" or candidate.fragment:
            raise MusicFailure(
                "ACE-Step audio URL uses an unexpected endpoint", "terminal_failure", task_id
            )
        query = parse_qs(candidate.query, keep_blank_values=True)
        if set(query) != {"path"} or len(query["path"]) != 1 or not query["path"][0]:
            raise MusicFailure(
                "ACE-Step audio URL has an invalid path", "terminal_failure", task_id
            )
        return f"{self.base_url}{candidate.path}?{candidate.query}"
