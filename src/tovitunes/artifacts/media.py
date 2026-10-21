"""Cheap file validation before deeper media QA becomes available."""

import json
from pathlib import Path

from PIL import Image


class InvalidMedia(ValueError):
    pass


_MIME_AND_EXTENSION = {
    ".json": "application/json",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
}


def validate_media(path: Path) -> tuple[str, str, dict[str, str | int]]:
    suffix = path.suffix.lower()
    mime = _MIME_AND_EXTENSION.get(suffix)
    if mime is None:
        raise InvalidMedia(f"unsupported media extension: {suffix}")
    size = path.stat().st_size
    if size <= 0:
        raise InvalidMedia("empty media file")
    with path.open("rb") as stream:
        header = stream.read(16)
    if suffix == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise InvalidMedia("invalid PNG signature")
    if suffix in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
        raise InvalidMedia("invalid JPEG signature")
    if suffix == ".webp" and not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
        raise InvalidMedia("invalid WebP signature")
    if suffix == ".wav" and not (header.startswith(b"RIFF") and header[8:12] == b"WAVE"):
        raise InvalidMedia("invalid WAV signature")
    mp3_frame_headers = {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}
    if suffix == ".mp3" and not (header.startswith(b"ID3") or header[:2] in mp3_frame_headers):
        raise InvalidMedia("invalid MP3 signature")
    if suffix == ".mp4" and header[4:8] != b"ftyp":
        raise InvalidMedia("invalid MP4 signature")
    facts: dict[str, str | int] = {"size": size, "signature_check": "passed"}
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        # A valid header alone can admit a truncated, unrenderable provider result.
        # Verify the container, then reopen for a full pixel decode (verify cannot
        # be followed by load on the same Pillow object). Never rewrite the source.
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                frames = getattr(image, "n_frames", 1)
                facts.update(width=image.width, height=image.height, image_decode="passed")
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise InvalidMedia("image cannot be fully decoded; retain and inspect source") from exc
        if frames != 1:
            raise InvalidMedia("animated image is unsupported for a static art asset")
    if suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidMedia("invalid UTF-8 JSON") from exc
    if suffix == ".txt":
        try:
            path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise InvalidMedia("invalid UTF-8 text") from exc
    return mime, suffix, facts

