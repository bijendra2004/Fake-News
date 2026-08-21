from __future__ import annotations

import base64
import html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response


@dataclass(frozen=True)
class SecuritySettings:
    frontend_origins: list[str]
    enforce_https: bool
    cookie_secure: bool
    cookie_samesite: str
    max_request_bytes: int
    public_rate_limit_per_minute: int
    public_device_limit_per_minute: int
    otp_email_limit_per_10_minutes: int
    otp_ip_limit_per_10_minutes: int
    otp_max_attempts: int
    captcha_abuse_threshold: int
    captcha_free_abuse_threshold: int
    captcha_provider: str | None
    captcha_site_key: str | None
    captcha_secret_key: str | None
    captcha_bypass_token: str | None
    csrf_cookie_name: str
    csrf_header_name: str
    hsts_max_age: int
    request_timeout_seconds: int


def load_security_settings() -> SecuritySettings:
    frontend_origins = parse_origins(os.getenv("FRONTEND_ORIGINS") or os.getenv("FRONTEND_ORIGIN"))
    cookie_secure = parse_bool(os.getenv("COOKIE_SECURE", "false"))
    return SecuritySettings(
        frontend_origins=frontend_origins,
        enforce_https=parse_bool(os.getenv("ENFORCE_HTTPS", "false")),
        cookie_secure=cookie_secure,
        cookie_samesite=os.getenv("COOKIE_SAMESITE", "lax").lower(),
        max_request_bytes=int(os.getenv("MAX_REQUEST_BYTES", str(10 * 1024 * 1024))),
        public_rate_limit_per_minute=int(os.getenv("PUBLIC_RATE_LIMIT_PER_MINUTE", "60")),
        public_device_limit_per_minute=int(os.getenv("PUBLIC_DEVICE_LIMIT_PER_MINUTE", "30")),
        otp_email_limit_per_10_minutes=int(os.getenv("OTP_EMAIL_LIMIT_PER_10_MINUTES", "3")),
        otp_ip_limit_per_10_minutes=int(os.getenv("OTP_IP_LIMIT_PER_10_MINUTES", "10")),
        otp_max_attempts=int(os.getenv("OTP_MAX_ATTEMPTS", "5")),
        captcha_abuse_threshold=int(os.getenv("CAPTCHA_ABUSE_THRESHOLD", "2")),
        captcha_free_abuse_threshold=int(os.getenv("CAPTCHA_FREE_ABUSE_THRESHOLD", "2")),
        captcha_provider=os.getenv("CAPTCHA_PROVIDER") or None,
        captcha_site_key=os.getenv("CAPTCHA_SITE_KEY") or None,
        captcha_secret_key=os.getenv("CAPTCHA_SECRET_KEY") or None,
        captcha_bypass_token=os.getenv("CAPTCHA_BYPASS_TOKEN") or None,
        csrf_cookie_name=os.getenv("CSRF_COOKIE_NAME", "csrf_token"),
        csrf_header_name=os.getenv("CSRF_HEADER_NAME", "x-csrf-token"),
        hsts_max_age=int(os.getenv("HSTS_MAX_AGE", str(60 * 60 * 24 * 365))),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
    )


def parse_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_origins(value: str | None) -> list[str]:
    origins = [origin.strip() for origin in (value or "").split(",") if origin.strip()]
    if not origins:
        raise RuntimeError("FRONTEND_ORIGINS must be set")
    normalized = list(dict.fromkeys(origins))
    if os.getenv("APP_ENV", "development").lower() == "production":
        if "*" in normalized:
            raise RuntimeError("CORS wildcard '*' is not allowed in production")
        for origin in normalized:
            if origin == "*":
                raise RuntimeError("CORS wildcard is not allowed in production")
    return normalized


def is_local_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.lower()
    return normalized in {"localhost", "127.0.0.1", "::1"} or normalized.endswith(".localhost")


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            event_queue = self._events[key]
            while event_queue and event_queue[0] < cutoff:
                event_queue.popleft()
            if len(event_queue) >= limit:
                return False
            event_queue.append(now)
            return True


rate_limiter = SlidingWindowRateLimiter()


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def get_device_fingerprint(request: Request) -> str:
    return request.headers.get("x-device-fingerprint", "anonymous")


def request_scheme(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower()
    return request.url.scheme.lower()


def should_redirect_to_https(request: Request, settings: SecuritySettings) -> bool:
    if not settings.enforce_https:
        return False
    if request_scheme(request) == "https":
        return False
    return True


def is_public_api_path(path: str) -> bool:
    return path.startswith("/api/")


def is_state_changing_method(method: str) -> bool:
    return method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


def build_https_redirect_url(request: Request) -> str:
    url = request.url.replace(scheme="https")
    return str(url)


def security_headers(settings: SecuritySettings) -> dict[str, str]:
    csp = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none';"
    return {
        "Strict-Transport-Security": f"max-age={settings.hsts_max_age}; includeSubDomains",
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(self), microphone=(self)",
    }


def apply_security_headers(response: Response, settings: SecuritySettings) -> Response:
    for key, value in security_headers(settings).items():
        response.headers[key] = value
    return response


def make_generic_error(status_code: int = 500, detail: str = "Internal server error") -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def sanitize_display_text(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    return html.escape(cleaned, quote=True)


def create_csrf_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


def validate_csrf(request: Request, settings: SecuritySettings) -> bool:
    header_value = request.headers.get(settings.csrf_header_name)
    cookie_value = request.cookies.get(settings.csrf_cookie_name)
    return bool(header_value and cookie_value and hmac_compare(header_value, cookie_value))


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def verify_captcha_token(settings: SecuritySettings, token: str | None, remote_ip: str | None = None) -> bool:
    if not token:
        return False

    if settings.captcha_bypass_token and token == settings.captcha_bypass_token:
        return True

    if not settings.captcha_provider or not settings.captcha_secret_key:
        return False

    provider = settings.captcha_provider.lower()
    if provider in {"turnstile", "cloudflare"}:
        endpoint = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    elif provider in {"hcaptcha", "h-captcha"}:
        endpoint = "https://hcaptcha.com/siteverify"
    else:
        return False

    form_body = urllib.parse.urlencode(
        {
            "secret": settings.captcha_secret_key,
            "response": token,
            **({"remoteip": remote_ip} if remote_ip else {}),
        }
    ).encode("utf-8")
    request = urllib.request.Request(endpoint, data=form_body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return bool(payload.get("success"))


def validate_captcha_token(settings: SecuritySettings, token: str | None, remote_ip: str | None = None) -> bool:
    return verify_captcha_token(settings, token, remote_ip)


def body_length(request: Request) -> int:
    header_value = request.headers.get("content-length")
    if not header_value:
        return 0
    try:
        return int(header_value)
    except ValueError:
        return 0


async def enforce_body_size_limit(request: Request, max_bytes: int) -> JSONResponse | None:
    if request.method not in {"POST", "PUT", "PATCH"}:
        return None

    header_length = body_length(request)
    if header_length > max_bytes:
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})

    body = await request.body()
    if len(body) > max_bytes:
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]
    return None
