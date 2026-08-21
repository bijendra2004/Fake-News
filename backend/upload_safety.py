from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))

KIND_EXTENSIONS = {
    "png": ".png",
    "jpeg": ".jpg",
    "gif": ".gif",
    "webp": ".webp",
    "wav": ".wav",
    "mp3": ".mp3",
    "mp4": ".mp4",
    "webm": ".webm",
}

MAGIC_BYTES = {
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpeg": [b"\xff\xd8\xff"],
    "gif": [b"GIF87a", b"GIF89a"],
    "webp": [b"RIFF"],
    "wav": [b"RIFF"],
    "mp3": [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
    "mp4": [b"\x00\x00\x00", b"ftyp"],
    "webm": [b"\x1a\x45\xdf\xa3"],
}


@dataclass(frozen=True)
class UploadValidationResult:
    kind: str
    safe_filename: str


def validate_magic_bytes(filename: str, payload: bytes, allowed_kinds: Iterable[str]) -> str:
    allowed = set(allowed_kinds)
    if not allowed:
        raise ValueError("No file types are allowed")

    for kind in allowed:
        signatures = MAGIC_BYTES.get(kind.lower(), [])
        for signature in signatures:
            if kind.lower() == "webp" and payload.startswith(signature) and payload[8:12] == b"WEBP":
                return kind.lower()
            if kind.lower() == "mp4" and len(payload) > 12 and payload[4:8] == b"ftyp":
                return kind.lower()
            if payload.startswith(signature):
                return kind.lower()

    raise ValueError(f"Unsupported or mismatched file type for {filename}")


def strip_image_metadata(image_bytes: bytes) -> bytes:
    try:
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as image:
            output = BytesIO()
            image.save(output, format=image.format or "PNG")
            return output.getvalue()
    except Exception as error:  # pragma: no cover - optional safety path
        raise ValueError("Failed to strip image metadata") from error


def scan_for_malware(path: Path) -> None:
    clamscan = shutil.which(os.getenv("CLAMAV_SCAN_COMMAND", "clamscan"))
    if not clamscan:
        return

    result = subprocess.run([clamscan, "--no-summary", str(path)], capture_output=True, text=True)
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Malware scan failed: {result.stderr.strip() or result.stdout.strip()}")
    if result.returncode == 1:
        raise ValueError("Uploaded file failed malware scan")


def store_upload_securely(storage_dir: Path, kind: str, payload: bytes) -> Path:
    storage_dir.mkdir(parents=True, exist_ok=True)
    extension = KIND_EXTENSIONS.get(kind.lower(), ".bin")
    safe_name = f"{secrets.token_hex(16)}{extension}"
    destination = storage_dir / safe_name
    destination.write_bytes(payload)
    return destination


def process_upload(
    storage_dir: Path,
    original_filename: str,
    payload: bytes,
    allowed_kinds: Iterable[str],
) -> tuple[Path, UploadValidationResult]:
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds maximum size of {MAX_UPLOAD_BYTES} bytes")

    kind = validate_magic_bytes(original_filename, payload, allowed_kinds)
    if kind in {"png", "jpeg", "gif", "webp"}:
        payload = strip_image_metadata(payload)

    destination = store_upload_securely(storage_dir, kind, payload)
    scan_for_malware(destination)
    return destination, UploadValidationResult(kind=kind, safe_filename=destination.name)
