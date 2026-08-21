from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import ipaddress

import pytesseract
import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException
from PIL import Image
from readability import Document


class MediaProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractedPageText:
    text: str
    source_domain: str


def extract_text_from_image(image_path: Path) -> str:
    try:
        with Image.open(image_path) as image:
            text = pytesseract.image_to_string(image)
    except Exception as error:
        raise MediaProcessingError("Failed to extract text from the image") from error

    normalized = normalize_text(text)
    if not normalized:
        raise MediaProcessingError("No readable text was found in the uploaded image")
    return normalized


@lru_cache(maxsize=1)
def _whisper_model() -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel("tiny", device="cpu", compute_type="int8")


def transcribe_audio_file(audio_path: Path) -> str:
    try:
        model = _whisper_model()
        segments, _info = model.transcribe(str(audio_path), beam_size=1, vad_filter=True)
        transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    except Exception as error:
        raise MediaProcessingError("Failed to transcribe the audio clip") from error

    normalized = normalize_text(transcript)
    if not normalized:
        raise MediaProcessingError("No speech could be transcribed from the audio clip")
    return normalized


def extract_text_from_url(raw_url: str) -> ExtractedPageText:
    parsed = validate_public_http_url(raw_url)
    try:
        response = requests.get(
            parsed.geturl(),
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
                )
            },
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise MediaProcessingError("Unable to fetch the linked page") from error

    extracted_text = _extract_main_article_text(response.text)
    normalized = normalize_text(extracted_text)
    if not normalized:
        raise MediaProcessingError("No readable article text could be extracted from the linked page")

    return ExtractedPageText(text=normalized, source_domain=(parsed.netloc or "").lower())


def _extract_main_article_text(html: str) -> str:
    try:
        document = Document(html)
        summary_html = document.summary(html_partial=True)
        soup = BeautifulSoup(summary_html, "html.parser")
        text = soup.get_text(" ", strip=True)
        if len(text) >= 160:
            return text
    except Exception:
        pass

    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return soup.get_text(" ", strip=True)


def validate_public_http_url(raw_url: str):
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MediaProcessingError("Please provide a valid http or https URL")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise MediaProcessingError("Please provide a valid URL")

    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost"):
        raise MediaProcessingError("Local URLs are not allowed for link analysis")

    try:
        ip_address = ipaddress.ip_address(hostname)
    except ValueError:
        ip_address = None

    if ip_address and (ip_address.is_private or ip_address.is_loopback or ip_address.is_link_local or ip_address.is_reserved):
        raise MediaProcessingError("Private network URLs are not allowed for link analysis")

    return parsed


def normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()