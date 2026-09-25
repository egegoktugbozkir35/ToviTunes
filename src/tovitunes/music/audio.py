"""Structural validation of original music bytes; no transcoding."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioInfo:
    duration_seconds: float
    mime_type: str
    container: str
    codec: str
    extension: str


def inspect_mp3(data: bytes) -> AudioInfo:
    """Walk every MPEG Layer III frame, rejecting truncated or stray bytes."""
    offset = 0
    if data.startswith(b"ID3"):
        if len(data) < 10 or any(byte & 0x80 for byte in data[6:10]):
            raise ValueError("invalid MP3 ID3 header")
        size = sum(byte << shift for byte, shift in zip(data[6:10], (21, 14, 7, 0)))
        offset = 10 + size + (10 if data[5] & 0x10 else 0)
        if offset > len(data):
            raise ValueError("truncated MP3 ID3 tag")
    end = len(data) - 128 if len(data) >= 128 and data[-128:-125] == b"TAG" else len(data)
    frames = 0
    duration = 0.0
    bitrate_v1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
    bitrate_v2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    rates = (44100, 48000, 32000)
    while offset < end:
        if offset + 4 > end:
            raise ValueError("truncated MP3 frame header")
        header = int.from_bytes(data[offset : offset + 4], "big")
        version = (header >> 19) & 3
        layer = (header >> 17) & 3
        bitrate_index = (header >> 12) & 15
        rate_index = (header >> 10) & 3
        if (
            (header >> 21) != 0x7FF
            or version == 1
            or layer != 1
            or bitrate_index in (0, 15)
            or rate_index == 3
        ):
            raise ValueError("invalid MP3 Layer III frame")
        bitrate = (bitrate_v1 if version == 3 else bitrate_v2)[bitrate_index] * 1000
        rate = rates[rate_index] // (1 if version == 3 else 2 if version == 2 else 4)
        samples = 1152 if version == 3 else 576
        size = (144 if version == 3 else 72) * bitrate // rate + ((header >> 9) & 1)
        if offset + size > end:
            raise ValueError("truncated MP3 frame")
        duration += samples / rate
        offset += size
        frames += 1
    if frames < 2 or offset != end:
        raise ValueError("MP3 requires complete audio frames")
    return AudioInfo(duration, "audio/mpeg", "mp3", "mp3", ".mp3")


def inspect_audio(data: bytes, mime_type: str) -> AudioInfo:
    if mime_type == "audio/mpeg":
        return inspect_mp3(data)
    if mime_type == "audio/wav":
        from tovitunes.music.benchmark import inspect_wav_metadata

        duration, codec = inspect_wav_metadata(data)
        return AudioInfo(duration, "audio/wav", "wav", codec, ".wav")
    raise ValueError("unsupported music audio MIME type")
