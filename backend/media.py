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
    "instagram.com", "www.instagram.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "tiktok.com", "www.tiktok.com",
    "threads.net", "www.threads.net",
    "youtube.com", "www.youtube.com", "youtu.be",
    "reddit.com", "www.reddit.com",
}


def extract_text_from_image(image_path: Path) -> str:
    """Extract text and claims from an image using Gemini Vision (primary) or Tesseract OCR (fallback).

    Handles Hindi/multilingual text, WhatsApp forwards, news screenshots, and memes.
    """
    # 1. Try Gemini Vision first (best quality for Hindi, screenshots, WhatsApp forwards)
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            extracted = _analyze_image_gemini(image_path, gemini_key)
            if extracted and len(extracted.strip()) >= 5:
                logger.info("Gemini Vision extracted claim (%d chars): %s", len(extracted), extracted[:100])
                return extracted
        except Exception as error:
            logger.warning("Gemini Vision analysis failed: %s, falling back to OCR", error)

    # 2. Fallback to Tesseract OCR
    if pytesseract is None:
        raise MediaProcessingError("Could not extract readable text from the image. Please enter the claim as text.")

    try:
        with Image.open(image_path) as image:
            # Try Hindi + English if available, else standard
            try:
                text = pytesseract.image_to_string(image, lang="hin+eng")
            except Exception:
                text = pytesseract.image_to_string(image)
    except Exception as error:
        raise MediaProcessingError("Failed to extract text from the image. Please enter the claim as text.") from error

    normalized = normalize_text(text)
    if not normalized or len(normalized) < 5:
        raise MediaProcessingError("No readable text was found in the uploaded image")
    return normalized


def _analyze_image_gemini(image_path: Path, api_key: str) -> str:
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

    prompt_text = (
        "You are an expert fact-checker analyzing a news claim, social media post, or screenshot.\n"
        "1. Extract ALL visible text from the image word-for-word in its original language (Hindi, English, etc.).\n"
        "2. State the central claim or news message conveyed by the image.\n"
        "3. Output a clear, concise statement of the claim that should be verified against live facts.\n"
        "Do not include conversational filler. Start directly with the claim or extracted text."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
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
    """Extract article or post text from a URL.

    Uses social media crawler headers (which causes Instagram, Twitter, Facebook, etc.
    to return server-rendered post captions in Open Graph metadata) and Readability for news articles.
    """
    import html as html_module

    parsed = validate_public_http_url(raw_url)
    hostname = (parsed.hostname or "").lower()
    is_dynamic_site = any(hostname == d or hostname.endswith("." + d) for d in _DYNAMIC_DOMAINS)

    # 1. Fetch page using social crawler User-Agent (triggers server-side rendering of OG tags on Instagram/Twitter/etc.)
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
        with urllib.request.urlopen(req, timeout=12) as resp:
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

    # 5. If crawler didn't get enough text, fallback to Tavily URL search
    if not candidate_text or len(candidate_text) < 30:
        # Strip tracking query params for cleaner search
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        tavily_text = _search_url_with_tavily(clean_url)
        if tavily_text and not _is_boilerplate(tavily_text):
            candidate_text = tavily_text

    normalized = normalize_text(candidate_text)
    if not normalized or len(normalized) < 15:
        if is_dynamic_site:
            raise MediaProcessingError(
                f"Could not load the content from {hostname}. Social media posts may require login. "
                "Please copy and paste the text/caption directly into the TEXT tab."
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
        with urllib.request.urlopen(req, timeout=12) as resp:
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

    # Regex search for OG tags (works without bs4)
    og_title_m = re.search(r'<meta[^>]*property=[\"\']og:title[\"\'][^>]*content=[\"\'](.*?)[\"\']', html, re.DOTALL | re.IGNORECASE)
    og_desc_m = re.search(r'<meta[^>]*property=[\"\']og:description[\"\'][^>]*content=[\"\'](.*?)[\"\']', html, re.DOTALL | re.IGNORECASE)
    tw_desc_m = re.search(r'<meta[^>]*name=[\"\']twitter:description[\"\'][^>]*content=[\"\'](.*?)[\"\']', html, re.DOTALL | re.IGNORECASE)

    title = html_module.unescape(og_title_m.group(1)).strip() if og_title_m else ""
    desc = html_module.unescape(og_desc_m.group(1)).strip() if og_desc_m else ""
    if not desc and tw_desc_m:
        desc = html_module.unescape(tw_desc_m.group(1)).strip()

    # Clean social media prefix like "903 likes, 7 comments - username on date: "
    clean_desc = re.sub(r'^[0-9,KkMm\s]+likes?,?\s+[0-9,KkMm\s]+comments?\s+-\s+[^\:]+:\s*', '', desc, flags=re.IGNORECASE).strip()
    if clean_desc.startswith('"') and clean_desc.endswith('"'):
        clean_desc = clean_desc[1:-1].strip()

    # Clean title like "Username on Instagram: \"...\""
    clean_title = re.sub(r'^[^\:]+on\s+Instagram:\s*', '', title, flags=re.IGNORECASE).strip()
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