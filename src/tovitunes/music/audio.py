"""Inspect original audio bytes without replacing or transcoding them."""

import io
import math
import struct
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
    channel_count: int | None = None
    frame_count: int | None = None
    sample_format: str | None = None


def _inspect_wav(data: bytes) -> AudioInfo:
    """Validate every RIFF chunk and decode all frames without changing source bytes."""
    if (
        len(data) < 12
        or data[:4] != b"RIFF"
        or data[8:12] != b"WAVE"
        or struct.unpack_from("<I", data, 4)[0] + 8 != len(data)
    ):
        raise ValueError("invalid or incomplete WAV container")
    offset = 12
    chunks: dict[bytes, bytes] = {}
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated WAV chunk header")
        kind, size = struct.unpack_from("<4sI", data, offset)
        start, end = offset + 8, offset + 8 + size
        offset = end + size % 2
        if offset > len(data):
            raise ValueError("truncated WAV chunk payload")
        if kind in {b"fmt ", b"data", b"fact"}:
            if kind in chunks or (kind == b"data" and b"fmt " not in chunks):
                raise ValueError("ambiguous WAV chunk layout")
            chunks[kind] = data[start:end]
    fmt, audio = chunks.get(b"fmt ", b""), chunks.get(b"data", b"")
    if len(fmt) < 16 or not audio:
        raise ValueError("missing WAV format or frames")
    tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if len(fmt) != 16:
        if len(fmt) < 18 or len(fmt) != 18 + struct.unpack_from("<H", fmt, 16)[0]:
            raise ValueError("invalid WAV format extension")
    if tag == 0xFFFE:
        if len(fmt) != 40 or struct.unpack_from("<H", fmt, 18)[0] != bits:
            raise ValueError("unsupported extensible WAV sample width")
        mask = struct.unpack_from("<I", fmt, 20)[0]
        if mask and mask.bit_count() != channels:
            raise ValueError("invalid WAV channel mask")
        # KSDATAFORMAT_SUBTYPE_PCM / IEEE_FLOAT, little-endian Windows GUID.
        if fmt[28:40] != bytes.fromhex("00001000800000aa00389b71"):
            raise ValueError("unsupported WAV subformat")
        tag = struct.unpack_from("<I", fmt, 24)[0]
    codecs = {
        (1, 8): ("pcm_u8", miniaudio.SampleFormat.UNSIGNED8),
        (1, 16): ("pcm_s16le", miniaudio.SampleFormat.SIGNED16),
        (1, 24): ("pcm_s24le", miniaudio.SampleFormat.SIGNED24),
        (1, 32): ("pcm_s32le", miniaudio.SampleFormat.SIGNED32),
        (3, 32): ("pcm_f32le", miniaudio.SampleFormat.FLOAT32),
        (3, 64): ("pcm_f64le", miniaudio.SampleFormat.FLOAT32),
    }
    if (tag, bits) not in codecs or not 1 <= channels <= 32 or not 1 <= rate <= 384000:
        raise ValueError("unsupported WAV sample format")
    width = bits // 8
    if align != channels * width or byte_rate != rate * align or len(audio) % align:
        raise ValueError("inconsistent WAV frame layout")
    frames = len(audio) // align
    fact = chunks.get(b"fact")
    if fact is not None and (len(fact) != 4 or struct.unpack("<I", fact)[0] != frames):
        raise ValueError("inconsistent WAV fact frame count")
    codec, source_format = codecs[tag, bits]
    try:
        source = miniaudio.wav_get_info(data)
        decoded = miniaudio.wav_read_f32(data)
    except miniaudio.DecodeError as exc:
        raise ValueError("invalid WAV audio") from exc
    duration = frames / rate
    if (
        source.sample_format != source_format
        or (source.num_frames, source.nchannels, source.sample_rate) != (frames, channels, rate)
        or (decoded.num_frames, decoded.nchannels, decoded.sample_rate) != (frames, channels, rate)
        or source.duration != duration
        or decoded.duration != duration
        or decoded.sample_format != miniaudio.SampleFormat.FLOAT32
        or len(decoded.samples) != frames * channels
        or (tag == 3 and not all(math.isfinite(sample) for sample in decoded.samples))
    ):
        raise ValueError("inconsistent or nonfinite decoded WAV audio")
    return AudioInfo(
        duration,
        "audio/wav",
        "wav",
        codec,
        ".wav",
        rate,
        byte_rate * 8,
        channels,
        frames,
        f"{'float' if tag == 3 else 'integer'}{bits}",
    )


def inspect_audio(data: bytes, mime_type: str) -> AudioInfo:
    if mime_type == "audio/wav":
        return _inspect_wav(data)
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
