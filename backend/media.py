from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from readability import Document
except ImportError:
    Document = None


logger = logging.getLogger("sachlens.media")


class MediaProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractedPageText:
    text: str
    source_domain: str


# Common boilerplate phrases that indicate scraping got a login/cookie/footer wall instead of content
_BOILERPLATE_PATTERNS = [
    r"instagram from meta",
    r"create an account or log in to instagram",
    r"sign up to see photos and videos",
    r"javascript is not available",
    r"enable javascript",
    r"terms privacy copyright",
    r"language selection footer",
    r"español français deutsch",
    r"select your language",
    r"login • instagram",
    r"see more on instagram",
    r"switch accounts or sign up",
]

_DYNAMIC_DOMAINS = {
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch", "fb.me", "web.facebook.com",
    "instagram.com", "www.instagram.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "tiktok.com", "www.tiktok.com",
    "threads.net", "www.threads.net",
    "youtube.com", "www.youtube.com", "youtu.be",
    "reddit.com", "www.reddit.com", "old.reddit.com",
    "linkedin.com", "www.linkedin.com",
}


def extract_text_from_image(image_path: Path, user_context: str | None = None) -> str:
    """Extract text and claims from an image using Gemini Vision (primary) or Tesseract OCR (fallback).

    Handles Hindi/multilingual text, WhatsApp forwards, news screenshots, and memes.
    Integrates user-supplied question/context to guide verification.
    """
    clean_context = user_context.strip() if user_context else ""

    # 1. Try Gemini Vision first (best quality for Hindi, screenshots, WhatsApp forwards, combined context)
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            extracted = _analyze_image_gemini(image_path, gemini_key, user_context=clean_context)
            if extracted and len(extracted.strip()) >= 5:
                logger.info("Gemini Vision extracted claim (%d chars): %s", len(extracted), extracted[:100])
                return extracted
        except Exception as error:
            logger.warning("Gemini Vision analysis failed: %s, falling back to OCR", error)

    # 2. Fallback to Tesseract OCR
    if pytesseract is None:
        if clean_context:
            return clean_context
        raise MediaProcessingError("Could not extract readable text from the image. Please enter the claim as text.")

    try:
        with Image.open(image_path) as image:
            # Try Hindi + English if available, else standard
            try:
                text = pytesseract.image_to_string(image, lang="hin+eng")
            except Exception:
                text = pytesseract.image_to_string(image)
    except Exception as error:
        if clean_context:
            return clean_context
        raise MediaProcessingError("Failed to extract text from the image. Please enter the claim as text.") from error

    normalized = normalize_text(text)
    if not normalized or len(normalized) < 5:
        if clean_context:
            return clean_context
        raise MediaProcessingError("No readable text was found in the uploaded image")

    if clean_context:
        return f"{clean_context}\n\n[Text in image: {normalized}]"

    return normalized


def _analyze_image_gemini(image_path: Path, api_key: str, user_context: str = "") -> str:
    """Send image to Gemini Multimodal Vision to extract text and describe the claim."""
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    suffix = image_path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime_type = mime_map.get(suffix, "image/jpeg")

    models_to_try = [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.7-flash",
        "gemini-flash-latest",
    ]

    context_prompt = ""
    if user_context:
        context_prompt = (
            f"\nUSER QUESTION / CONTEXT:\n\"{user_context}\"\n"
            "Analyze the image specifically addressing the user's question or context above.\n"
        )

    prompt_text = (
        "You are an expert fact-checker analyzing a news claim, social media post, or screenshot.\n"
        f"{context_prompt}"
        "1. Extract visible text from the image word-for-word in its original language (Hindi, English, etc.).\n"
        "2. State the central claim, news event, or question conveyed by the image and user context.\n"
        "3. Output a clear, comprehensive statement of the claim that should be verified against live facts.\n"
        "Do not include conversational filler. Start directly with the claim or extracted text."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64_image,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        },
    }

    last_error: Exception | None = None
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "SachLens/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    return parts[0]["text"].strip()
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
            logger.warning("Gemini Vision model %s failed (HTTP %s): %s", model_name, exc.code, error_body[:200])
            last_error = exc
            if exc.code == 404:
                continue
            break
        except Exception as exc:
            logger.warning("Gemini Vision model %s error: %s", model_name, exc)
            last_error = exc
            break

    if last_error:
        raise last_error
    raise MediaProcessingError("Could not analyze image with vision model")


def _transcribe_audio_groq(audio_path: Path, groq_key: str) -> str:
    """Transcribe audio clip using Groq Whisper Large V3 Turbo in ~0.3-0.5s."""
    import mimetypes
    mime_type, _ = mimetypes.guess_type(str(audio_path))
    if not mime_type or not mime_type.startswith("audio/"):
        mime_type = "audio/webm"

    with open(audio_path, "rb") as f:
        file_bytes = f.read()

    boundary = "----SachLensBoundary" + os.urandom(8).hex()
    body = bytearray()

    # Model parameter
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    body.extend(b"whisper-large-v3-turbo\r\n")

    # Response format parameter
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="response_format"\r\n\r\n')
    body.extend(b"json\r\n")

    # File parameter
    filename = audio_path.name or "audio.webm"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {groq_key}",
            "User-Agent": "SachLens/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=12) as response:
        data = json.loads(response.read().decode("utf-8"))
        return data.get("text", "").strip()


@lru_cache(maxsize=1)
def _whisper_model() -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel("tiny", device="cpu", compute_type="int8")


def transcribe_audio_file(audio_path: Path) -> str:
    transcript = ""

    # 1. Primary: Lightning-fast cloud Whisper Large V3 Turbo on Groq (0.3-0.5s)
    groq_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if groq_key:
        try:
            transcript = _transcribe_audio_groq(audio_path, groq_key)
            if transcript:
                logger.info("Groq Whisper transcribed audio in ~0.3s: %s", transcript[:80])
        except Exception as error:
            logger.warning("Groq Whisper API call failed (%s) — falling back to local model", error)
            transcript = ""

    # 2. Fallback: Local faster-whisper CPU model
    if not transcript:
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
    """Extract article or post text from a URL across Facebook, Instagram, YouTube, X, Reddit, and web news."""
    import html as html_module

    parsed = validate_public_http_url(raw_url)
    hostname = (parsed.hostname or "").lower()
    is_dynamic_site = any(hostname == d or hostname.endswith("." + d) for d in _DYNAMIC_DOMAINS)

    # 1. Fetch page using social crawler User-Agent (triggers server-side rendering of OG tags on Facebook/Instagram/Twitter/etc.)
    page_html = ""
    try:
        req = urllib.request.Request(
            parsed.geturl(),
            headers={
                "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            page_html = resp.read().decode("utf-8", errors="ignore")
    except Exception as error:
        logger.warning("Crawler fetch failed for %s: %s", raw_url, error)

    # 2. Extract and clean Open Graph metadata
    og_text = _extract_og_meta_text(page_html) if page_html else ""

    # 3. For social media platforms, if we got clean caption/title, that is our primary post content
    if is_dynamic_site and og_text and not _is_boilerplate(og_text) and len(og_text) >= 15:
        logger.info("Extracted social post from OG tags (%d chars) for %s", len(og_text), hostname)
        return ExtractedPageText(text=normalize_text(og_text), source_domain=hostname)

    # 4. Extract article body from HTML for standard news/blog articles
    article_text = _extract_main_article_text(page_html) if page_html else ""
    if _is_boilerplate(article_text):
        article_text = ""

    candidate_text = article_text or og_text

    # 5. If crawler didn't get enough text (e.g. JS rendered, login wall), search with Tavily
    if not candidate_text or len(candidate_text) < 30 or _is_boilerplate(candidate_text):
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        tavily_text = _search_url_with_tavily(clean_url)
        if tavily_text and not _is_boilerplate(tavily_text):
            candidate_text = tavily_text

    normalized = normalize_text(candidate_text)
    if not normalized or len(normalized) < 15:
        if is_dynamic_site:
            raise MediaProcessingError(
                f"Could not automatically load the content from {hostname} due to privacy/login restrictions. "
                "Please copy and paste the text/caption directly into the TEXT tab for instant verification."
            )
        raise MediaProcessingError("No readable article text could be extracted from the linked page")

    return ExtractedPageText(text=normalized, source_domain=hostname)


def _search_url_with_tavily(url: str) -> str:
    """Use Tavily to search for indexed metadata and content of a URL."""
    tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not tavily_api_key:
        return ""

    payload = {
        "api_key": tavily_api_key,
        "query": url,
        "search_depth": "basic",
        "max_results": 3,
        "include_answer": True,
    }

    try:
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "SachLens/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = data.get("results", [])
        if not results:
            return data.get("answer", "")

        parts: list[str] = []
        for r in results:
            title = r.get("title", "").strip()
            content = r.get("content", "").strip()
            if title and title not in parts and not _is_boilerplate(title):
                parts.append(title)
            if content and content not in parts and not _is_boilerplate(content):
                parts.append(content)

        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("Tavily search for URL %s failed: %s", url, exc)
        return ""


def _is_boilerplate(text: str) -> bool:
    """Check if the text is generic site navigation/footer/login boilerplate."""
    if not text:
        return True
    lower = text.lower()
    matches = sum(1 for pattern in _BOILERPLATE_PATTERNS if re.search(pattern, lower))
    return matches >= 2


def _extract_og_meta_text(html: str) -> str:
    """Extract Open Graph and standard meta tags from HTML, decoding HTML entities."""
    import html as html_module

    if not html:
        return ""

    title_matches = re.findall(r'<meta\s+[^>]*(?:property|name)=["\'](?:og:title|twitter:title|title)["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    title_matches += re.findall(r'<meta\s+[^>]*content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\'](?:og:title|twitter:title|title)["\']', html, re.IGNORECASE)

    desc_matches = re.findall(r'<meta\s+[^>]*(?:property|name)=["\'](?:og:description|twitter:description|description)["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    desc_matches += re.findall(r'<meta\s+[^>]*content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\'](?:og:description|twitter:description|description)["\']', html, re.IGNORECASE)

    tag_title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)

    title = html_module.unescape(title_matches[0]).strip() if title_matches else (html_module.unescape(tag_title_m.group(1)).strip() if tag_title_m else "")
    desc = html_module.unescape(desc_matches[0]).strip() if desc_matches else ""

    # Clean social media prefix like "903 likes, 7 comments - username on date: "
    clean_desc = re.sub(r'^[0-9,KkMm\s]+likes?,?\s+[0-9,KkMm\s]+comments?\s+-\s+[^\:]+:\s*', '', desc, flags=re.IGNORECASE).strip()
    if clean_desc.startswith('"') and clean_desc.endswith('"'):
        clean_desc = clean_desc[1:-1].strip()

    # Clean title like "Username on Instagram / Facebook / X: \"...\""
    clean_title = re.sub(r'^[^\:]+on\s+(?:Instagram|Facebook|X|Twitter|Threads):\s*', '', title, flags=re.IGNORECASE).strip()
    if clean_title.startswith('"') and clean_title.endswith('"'):
        clean_title = clean_title[1:-1].strip()

    candidate = clean_desc or clean_title or desc or title
    return candidate


def _get_meta_content(tag: Any) -> str:
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return ""


def _extract_main_article_text(html: str) -> str:
    if not html:
        return ""
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
    for element in soup(["script", "style", "noscript", "nav", "footer", "header"]):
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