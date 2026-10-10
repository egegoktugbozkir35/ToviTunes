"""Provider-original WAV container integrity, decoding and codec identity on Python 3.11."""

import io
import math
import struct
import wave
from hashlib import sha256

import miniaudio
import pytest

from tovitunes.music.audio import inspect_audio
from tovitunes.music.benchmark import inspect_wav, inspect_wav_metadata


def riff(chunks):
    body = b"WAVE" + b"".join(
        kind + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)
        for kind, payload in chunks
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def float_wav(*, frames=8000, channels=1, rate=8000, bits=32, extensible=False):
    width = bits // 8
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * channels * width, channels * width, bits)
    if extensible:
        fmt = struct.pack("<H", 0xFFFE) + fmt[2:]
        fmt += struct.pack("<HHI", 22, bits, 0)
        fmt += bytes.fromhex("0300000000001000800000aa00389b71")
    else:
        fmt += struct.pack("<H", 0)
    data = b"".join(
        struct.pack("<f" if bits == 32 else "<d", 0.05 * math.sin(2 * math.pi * 220 * i / rate))
        * channels
        for i in range(frames)
    )
    return riff([(b"fmt ", fmt), (b"fact", struct.pack("<I", frames)), (b"data", data)])


@pytest.mark.parametrize("bits", [32, 64])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("extensible", [False, True])
def test_original_float_wav_decodes_with_exact_source_metadata(bits, channels, extensible):
    data = float_wav(bits=bits, channels=channels, extensible=extensible)
    digest = sha256(data).hexdigest()
    if not extensible:
        with pytest.raises(wave.Error, match="unknown format: 3"):
            wave.open(io.BytesIO(data), "rb")
    info = inspect_audio(data, "audio/wav")
    assert info.codec == f"pcm_f{bits}le"
    assert info.sample_format == f"float{bits}"
    assert info.channel_count == channels and info.frame_count == 8000
    assert info.sample_rate_hz == 8000 and info.duration_seconds == 1
    assert info.bitrate_bps == 8000 * channels * bits
    assert info.mime_type == "audio/wav" and info.container == "wav" and info.extension == ".wav"
    decoded = miniaudio.wav_read_f32(data)
    assert len(decoded.samples) == 8000 * channels
    assert max(decoded.samples) == pytest.approx(0.05)
    assert inspect_wav_metadata(data) == (1, info.codec)
    assert inspect_wav(data) == 1
    assert sha256(data).hexdigest() == digest


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_integer_pcm_remains_correctly_identified(width):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((2, width, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0" * 8000 * width * 2)
    info = inspect_audio(buffer.getvalue(), "audio/wav")
    assert info.codec == {1: "pcm_u8", 2: "pcm_s16le", 3: "pcm_s24le", 4: "pcm_s32le"}[width]
    assert info.frame_count == 8000 and info.channel_count == 2


def corrupt(data, offset, value):
    return data[:offset] + value + data[offset + len(value) :]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d[:-1],
        lambda d: corrupt(d[:-4], 4, struct.pack("<I", len(d) - 12)),
        lambda d: d + b"extra",
        lambda d: corrupt(d, 20, struct.pack("<H", 7)),
        lambda d: corrupt(d, 22, struct.pack("<H", 0)),
        lambda d: corrupt(d, 24, struct.pack("<I", 0)),
        lambda d: corrupt(d, 28, struct.pack("<I", 1)),
        lambda d: corrupt(d, 32, struct.pack("<H", 2)),
        lambda d: corrupt(d, 34, struct.pack("<H", 16)),
        lambda d: corrupt(d, 36, struct.pack("<H", 1)),
        lambda d: corrupt(d, 46, struct.pack("<I", 8001)),
        lambda d: corrupt(d, 58, struct.pack("<f", float("nan"))),
        lambda d: corrupt(d, 58, struct.pack("<f", float("inf"))),
        lambda d: riff([(b"fmt ", d[20:38]), (b"data", b"\0" * 7)]),
        lambda d: riff([(b"fmt ", d[20:38]), (b"fmt ", d[20:38]), (b"data", d[58:])]),
        lambda d: riff([(b"fmt ", d[20:38]), (b"data", d[58:]), (b"data", d[58:])]),
    ],
)
def test_malformed_truncated_or_nonfinite_wav_is_rejected(mutate):
    with pytest.raises(ValueError):
        inspect_audio(mutate(float_wav()), "audio/wav")


def test_wav_requires_successful_actual_decoding(monkeypatch):
    def broken(data):
        raise miniaudio.DecodeError("decoder failed")

    monkeypatch.setattr(miniaudio, "wav_read_f32", broken)
    with pytest.raises(ValueError, match="invalid WAV audio"):
        inspect_audio(float_wav(), "audio/wav")
