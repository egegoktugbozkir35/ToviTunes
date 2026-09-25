"""Vertex AI Interactions adapter for Lyria 3 Pro Preview."""

import base64
import binascii
import json
import os
import re
from collections.abc import Callable
from typing import Any

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

from tovitunes.music.models import CanonicalMusicSpec
from tovitunes.music.providers import MusicCapabilities, MusicFailure, MusicResult

PROJECT_PATTERN = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
INTERACTION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
PENDING_STATUSES = {"queued", "in_progress", "requires_action"}
TERMINAL_STATUSES = {"failed", "cancelled", "incomplete", "budget_exceeded"}
SAFE_ERROR_STATUS = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
SENSITIVE_ERROR_TEXT = re.compile(
    r"authorization|bearer|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|"
    r"cookie|credential|secret|private[_ -]?key|ya29\.|AIza|-----BEGIN|[{}]",
    re.IGNORECASE,
)


def _default_credentials() -> Credentials:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials


class VertexLyriaProvider:
    provider = "google"
    model = "lyria-3-pro-preview"
    location = "global"
    backend = "vertex_ai_interactions"
    capabilities = MusicCapabilities(
        text_to_music=True,
        lyrics=True,
        instrumental_only=True,
        vocals=True,
        target_duration=True,
        bpm=True,
        style=True,
        seed=False,
        stems=False,
        output_formats=("audio/mpeg",),
        usage_metadata=True,
        provider_request_id=False,
        rights_information=False,
        api_contract="Vertex AI Interactions v1beta1; Lyria 3 Pro Preview",
        user_provided_lyrics=True,
        generated_lyrics=True,
        intensity_direction=True,
        word_timestamps=False,
        maximum_duration_seconds=184,
    )
    _sections = ("tiny_intro", "hook", "teaching_line", "reinforcement", "short_ending")

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        credentials_loader: Callable[[], Credentials] = _default_credentials,
    ) -> None:
        self._client = client
        self._credentials_loader = credentials_loader

    @staticmethod
    def _prompt(spec: CanonicalMusicSpec) -> str:
        brief = spec.brief
        if brief.id != "colors_red_v1" or brief.sections != VertexLyriaProvider._sections:
            raise ValueError("unsupported canonical Lyria brief")
        lyric_sections = tuple(line.section for line in spec.lyrics.lines)
        if lyric_sections != (
            "hook",
            "hook",
            "teaching_line",
            "teaching_line",
            "reinforcement",
            "reinforcement",
            "short_ending",
        ):
            raise ValueError("canonical lyric sections changed")
        if not 30 <= 34 <= min(brief.preferred_duration_seconds[1], brief.maximum_duration_seconds):
            raise ValueError("target duration is outside the canonical brief")
        return "\n".join(
            [
                "Create one original English preschool pop song for ages 3–6, "
                "approximately 34 seconds.",
                "Educational objective: Red is a color; a red apple and a red ball are examples.",
                "Bright, warm, catchy, simple and bouncy. Light percussion, gentle bass, "
                "simple pitched instruments, clear downbeats.",
                f"Tempo: approximately {brief.target_bpm} BPM, as a stylistic direction.",
                "Intensity: gentle and playful, not aggressive.",
                "Friendly clear English vocal with intelligible teaching words. "
                "No mature styling, melisma or dense harmony.",
                "No frightening sounds, aggressive instruments, named artist imitation "
                "or named song imitation.",
                "Structure:",
                "0:00–0:03 tiny instrumental intro.",
                "0:03–0:12 hook.",
                "0:12–0:22 teaching section.",
                "0:22–0:31 reinforcement.",
                "0:31–0:34 short ending.",
                "Sing the supplied lyrics exactly, in order: first two lines in the hook, "
                "next two in teaching, next two in reinforcement, final line in the ending.",
                "Lyrics:",
                spec.lyrics.text(),
            ]
        )

    def translate(self, spec: CanonicalMusicSpec) -> dict[str, Any]:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or "{GOOGLE_CLOUD_PROJECT}"
        quota_project = os.environ.get("GOOGLE_CLOUD_QUOTA_PROJECT", project)
        return {
            "backend": self.backend,
            "project": project,
            "quota_project": quota_project,
            "location": self.location,
            "endpoint": (
                "https://aiplatform.googleapis.com/v1beta1/projects/"
                f"{project}/locations/global/interactions"
            ),
            "body": {
                "model": self.model,
                "input": [{"type": "text", "text": self._prompt(spec)}],
            },
        }

    def _preflight(self, translated: dict[str, Any]) -> tuple[str, str, str]:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not PROJECT_PATTERN.fullmatch(project):
            raise MusicFailure("GOOGLE_CLOUD_PROJECT is missing or invalid", "retryable_failure")
        quota_project = os.environ.get("GOOGLE_CLOUD_QUOTA_PROJECT", project)
        if not PROJECT_PATTERN.fullmatch(quota_project):
            raise MusicFailure(
                "GOOGLE_CLOUD_QUOTA_PROJECT is missing or invalid", "retryable_failure"
            )
        if os.environ.get("GOOGLE_CLOUD_LOCATION", "global") != "global":
            raise MusicFailure("GOOGLE_CLOUD_LOCATION must be global", "retryable_failure")
        expected_endpoint = (
            "https://aiplatform.googleapis.com/v1beta1/projects/"
            f"{project}/locations/global/interactions"
        )
        if (
            translated.get("backend") != self.backend
            or translated.get("location") != self.location
            or translated.get("project") not in (project, "{GOOGLE_CLOUD_PROJECT}")
            or translated.get("quota_project") not in (quota_project, "{GOOGLE_CLOUD_PROJECT}")
            or translated.get("endpoint")
            not in (
                expected_endpoint,
                "https://aiplatform.googleapis.com/v1beta1/projects/"
                "{GOOGLE_CLOUD_PROJECT}/locations/global/interactions",
            )
        ):
            raise MusicFailure("stored Vertex project or endpoint differs", "retryable_failure")
        try:
            credentials = self._credentials_loader()
            if not credentials.valid:
                credentials.refresh(GoogleAuthRequest())  # type: ignore[no-untyped-call]
            token = credentials.token
            if not credentials.valid or not isinstance(token, str) or not token:
                raise ValueError("ADC produced no valid access token")
        except Exception as exc:
            raise MusicFailure(
                "Vertex Application Default Credentials unavailable", "retryable_failure"
            ) from exc
        return expected_endpoint, token, quota_project

    @staticmethod
    def _response_json(response: httpx.Response, expected_id: str | None = None) -> dict[str, Any]:
        try:
            value = response.json()
        except (ValueError, UnicodeError) as exc:
            raise MusicFailure(
                "malformed Vertex interaction JSON", "ambiguous", expected_id
            ) from exc
        if not isinstance(value, dict):
            raise MusicFailure("malformed Vertex interaction JSON", "ambiguous", expected_id)
        return value

    @staticmethod
    def _read_result(
        interaction: dict[str, Any], interaction_id: str | None, access_token: str
    ) -> MusicResult:
        outputs: list[dict[str, Any]] = []
        raw_outputs = interaction.get("outputs", [])
        if isinstance(raw_outputs, list):
            outputs.extend(item for item in raw_outputs if isinstance(item, dict))
        else:
            raise MusicFailure("invalid Vertex interaction outputs", "ambiguous", interaction_id)
        steps = interaction.get("steps", [])
        if not isinstance(steps, list):
            raise MusicFailure("invalid Vertex interaction steps", "ambiguous", interaction_id)
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            contents = step.get("content", [])
            if not isinstance(contents, list):
                raise MusicFailure("invalid Vertex model output", "ambiguous", interaction_id)
            outputs.extend(item for item in contents if isinstance(item, dict))
        audio_parts = [item for item in outputs if item.get("type") == "audio"]
        if len(audio_parts) != 1:
            raise MusicFailure(
                "Vertex interaction requires exactly one audio output", "ambiguous", interaction_id
            )
        audio_part = audio_parts[0]
        if audio_part.get("mime_type") != "audio/mpeg" or not isinstance(
            audio_part.get("data"), str
        ):
            raise MusicFailure("unsupported Vertex audio output", "ambiguous", interaction_id)
        try:
            audio = base64.b64decode(audio_part["data"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MusicFailure("invalid Vertex audio base64", "ambiguous", interaction_id) from exc
        texts = [
            item["text"]
            for item in outputs
            if item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        provider_lyrics = texts[0] if texts else None
        provider_description = texts[1] if len(texts) > 1 else None
        usage = interaction.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise MusicFailure("invalid Vertex usage metadata", "ambiguous", interaction_id)
        metadata = {
            "backend": VertexLyriaProvider.backend,
            "interaction_status": "completed",
            "provider_interaction_id_supplied": interaction_id is not None,
            "provider_lyrics_text": provider_lyrics,
            "provider_description_text": provider_description,
            "other_text_outputs": texts[2:],
            "created": interaction.get("created"),
            "updated": interaction.get("updated"),
        }
        if access_token in json.dumps({"usage": usage, "metadata": metadata}):
            raise MusicFailure("unsafe Vertex response metadata", "ambiguous", interaction_id)
        return MusicResult(
            audio_bytes=audio,
            mime_type="audio/mpeg",
            container="mp3",
            codec="mp3",
            provider_request_id=interaction_id,
            usage=usage,
            response_metadata=metadata,
        )

    def _interpret(
        self,
        interaction: dict[str, Any],
        *,
        access_token: str,
        expected_id: str | None = None,
        on_identity: Callable[[str], None] | None = None,
    ) -> MusicResult:
        interaction_id = interaction.get("id")
        if interaction_id is not None and (
            not isinstance(interaction_id, str) or not INTERACTION_PATTERN.fullmatch(interaction_id)
        ):
            raise MusicFailure("Vertex interaction ID missing or invalid", "ambiguous", expected_id)
        if expected_id is not None and interaction_id != expected_id:
            raise MusicFailure("Vertex interaction identity changed", "ambiguous", expected_id)
        if interaction_id is not None and access_token in interaction_id:
            raise MusicFailure("unsafe Vertex interaction identity", "ambiguous", expected_id)
        if on_identity is not None and interaction_id is not None:
            on_identity(interaction_id)
        if interaction.get("model") not in (None, self.model):
            raise MusicFailure("Vertex interaction model differs", "ambiguous", interaction_id)
        status = interaction.get("status")
        if status in PENDING_STATUSES:
            raise MusicFailure(f"Vertex interaction {status}", "ambiguous", interaction_id)
        if status in TERMINAL_STATUSES:
            raise MusicFailure(f"Vertex interaction {status}", "terminal_failure", interaction_id)
        if status != "completed":
            raise MusicFailure("unknown Vertex interaction status", "ambiguous", interaction_id)
        return self._read_result(interaction, interaction_id, access_token)

    @staticmethod
    def _safe_error_detail(response: httpx.Response, access_token: str) -> str | None:
        if len(response.content) > 4096:
            return None
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
            return None
        error = payload["error"]
        parts: list[str] = []
        code = error.get("code")
        if type(code) is int and 100 <= code <= 599:
            parts.append(f"Google code {code}")
        status = error.get("status")
        if isinstance(status, str) and SAFE_ERROR_STATUS.fullmatch(status):
            parts.append(f"Google status {status}")
        message = error.get("message")
        if isinstance(message, str):
            clean = " ".join(message.split())
            if (
                clean
                and len(clean) <= 300
                and access_token not in clean
                and not SENSITIVE_ERROR_TEXT.search(clean)
                and all(char.isprintable() for char in clean)
            ):
                parts.append(f"Google message {clean}")
        return "; ".join(parts) or None

    @staticmethod
    def _http_failure(
        response: httpx.Response, interaction_id: str | None, access_token: str
    ) -> None:
        code = response.status_code
        if code in (408, 429) or code >= 500:
            raise MusicFailure("Vertex interaction outcome uncertain", "ambiguous", interaction_id)
        if code >= 400:
            detail = VertexLyriaProvider._safe_error_detail(response, access_token)
            reason = f"Vertex rejected request (HTTP {code})"
            if detail is not None:
                reason += f": {detail}"
            raise MusicFailure(
                reason, "terminal_failure", interaction_id
            )
        if not 200 <= code < 300:
            raise MusicFailure(
                "unexpected Vertex interaction HTTP status", "ambiguous", interaction_id
            )

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
        if translated.get("body") != self.translate(spec)["body"]:
            raise MusicFailure("Vertex plan differs from canonical inputs", "retryable_failure")
        endpoint, token, quota_project = self._preflight(translated)
        try:
            body = json.dumps(translated["body"], sort_keys=True, separators=(",", ":")).encode()
            request = httpx.Request(
                "POST",
                endpoint,
                headers={
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json",
                    "x-goog-user-project": quota_project,
                },
                content=body,
            )
        except (TypeError, ValueError) as exc:
            raise MusicFailure(
                "invalid local Vertex interaction request", "retryable_failure"
            ) from exc
        client = self._client or httpx.Client(timeout=httpx.Timeout(180.0), follow_redirects=False)
        try:
            on_remote_start()
            try:
                response = client.send(request, follow_redirects=False)
            except httpx.RequestError as exc:
                raise MusicFailure("Vertex generation POST outcome unknown", "ambiguous") from exc
            self._http_failure(response, None, token)
            interaction = self._response_json(response)
            return self._interpret(
                interaction, access_token=token, on_identity=on_provider_identity
            )
        finally:
            if self._client is None:
                client.close()

    def retrieve(self, interaction_id: str, translated: dict[str, Any]) -> MusicResult:
        """Conditionally retrieve a known historical ID; new Lyria POSTs may have none."""
        if not INTERACTION_PATTERN.fullmatch(interaction_id):
            raise MusicFailure("stored Vertex interaction ID invalid", "retryable_failure")
        endpoint, token, quota_project = self._preflight(translated)
        try:
            request = httpx.Request(
                "GET",
                f"{endpoint}/{interaction_id}",
                headers={
                    "authorization": f"Bearer {token}",
                    "x-goog-user-project": quota_project,
                },
            )
        except (TypeError, ValueError) as exc:
            raise MusicFailure(
                "invalid local Vertex interaction request", "retryable_failure"
            ) from exc
        client = self._client or httpx.Client(timeout=httpx.Timeout(60.0), follow_redirects=False)
        try:
            try:
                response = client.send(request, follow_redirects=False)
            except httpx.RequestError as exc:
                raise MusicFailure(
                    "Vertex interaction GET outcome unknown", "ambiguous", interaction_id
                ) from exc
            self._http_failure(response, interaction_id, token)
            interaction = self._response_json(response, interaction_id)
            return self._interpret(interaction, access_token=token, expected_id=interaction_id)
        finally:
            if self._client is None:
                client.close()
