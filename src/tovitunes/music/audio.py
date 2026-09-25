"""Inspect original audio bytes without replacing or transcoding them."""

import io
import math
import wave
from dataclasses import dataclass

import miniaudio
from mutagen import MutagenError
from mutagen.mp3 import MP3


@dataclass(frozen=True)
class AudioInfo:
    duration_seconds: float
    mime_type: str
    container: str
    codec: str
    extension: str
    sample_rate_hz: int
    bitrate_bps: int | None


def inspect_audio(data: bytes, mime_type: str) -> AudioInfo:
    if mime_type == "audio/wav":
        try:
            with wave.open(io.BytesIO(data), "rb") as stream:
                if stream.getcomptype() != "NONE" or not stream.getnframes():
                    raise ValueError("unsupported or empty WAV")
                rate = stream.getframerate()
                channels = stream.getnchannels()
                width = stream.getsampwidth()
                frames = stream.getnframes()
                if rate <= 0 or channels <= 0 or width not in (1, 2, 3, 4):
                    raise ValueError("invalid PCM WAV format")
                if len(stream.readframes(frames)) != frames * channels * width:
                    raise ValueError("truncated WAV")
                codec = {1: "pcm_u8", 2: "pcm_s16le", 3: "pcm_s24le", 4: "pcm_s32le"}[width]
                return AudioInfo(
                    frames / rate,
                    mime_type,
                    "wav",
                    codec,
                    ".wav",
                    rate,
                    rate * channels * width * 8,
                )
        except (wave.Error, EOFError) as exc:
            raise ValueError("invalid WAV") from exc
    if mime_type != "audio/mpeg":
        raise ValueError("unsupported music audio MIME type")
    if not data:
        raise ValueError("empty MP3")
    try:
        parsed = MP3(io.BytesIO(data))  # type: ignore[no-untyped-call]
        if parsed.info is None:
            raise ValueError("MP3 has no stream information")
        decoded = miniaudio.mp3_read_f32(data)
        length = float(parsed.info.length)
        rate = int(parsed.info.sample_rate)
        bitrate = int(parsed.info.bitrate)
        if (
            not math.isfinite(length)
            or not 0.5 <= length <= 184
            or decoded.num_frames <= 0
            or decoded.sample_rate != rate
            or abs(decoded.duration - length) > 0.25
            or not 8000 <= rate <= 96000
            or not 8000 <= bitrate <= 512000
            or parsed.info.sketchy
        ):
            raise ValueError("inconsistent MP3 audio metadata")
        return AudioInfo(decoded.duration, mime_type, "mp3", "mp3", ".mp3", rate, bitrate)
    except (MutagenError, miniaudio.DecodeError, ValueError, OSError) as exc:
        raise ValueError("invalid or corrupt MP3") from exc
