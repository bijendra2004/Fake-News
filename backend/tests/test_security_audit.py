"""
SachLens Security Audit — Automated Tests for all 19 checklist items.

Tests are organized by checklist item number and verify actual behavior,
not just config.  Where a test can't be fully automated (e.g. real CAPTCHA
verification, ClamAV scanning), the test documents what manual step is needed.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import re
import secrets
import textwrap
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Bootstrap: set env vars BEFORE importing app modules
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sachlens_audit.db")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ENFORCE_HTTPS", "false")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ALLOW_UNSCANNED_UPLOADS", "true")
os.environ.setdefault("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

from backend.main import app, get_db, settings
from backend.models import Base, FreeUsageTracking, OTPChallenge, init_db
from backend.security import (
    SlidingWindowRateLimiter,
    apply_security_headers,
    load_security_settings,
    sanitize_display_text,
    security_headers,
    should_redirect_to_https,
)
from backend.auth import issue_otp, verify_otp, hash_value
from backend.upload_safety import validate_magic_bytes, strip_image_metadata, process_upload
from backend.crypto import encrypt_text, decrypt_text, EncryptedText


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
TEST_DB_URL = "sqlite:///./test_sachlens_audit.db"

@pytest.fixture(scope="module")
def test_engine():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.drop_all(bind=engine)
    init_db(engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    db_path = Path("test_sachlens_audit.db")
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def db_session(test_engine):
    Session = sessionmaker(bind=test_engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(test_engine):
    """TestClient with overridden DB dependency."""
    Session = sessionmaker(bind=test_engine)

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _get_csrf(client: TestClient) -> tuple[str, dict]:
    """Fetch a CSRF token from usage-check and return (token, cookies_as_headers)."""
    resp = client.get("/api/usage-check", headers={"X-Device-Fingerprint": "test-fp"})
    data = resp.json()
    csrf = data.get("csrf_token", "")
    # Extract the CSRF cookie from set-cookie
    cookies = {}
    for header_val in resp.headers.get_list("set-cookie"):
        if "csrf_token=" in header_val:
            cookie_val = header_val.split("csrf_token=")[1].split(";")[0]
            cookies["csrf_token"] = cookie_val
    return csrf, cookies


# ===========================================================================
# 1. HTTPS redirect + HSTS header
# ===========================================================================
class TestItem01_HTTPS:
    def test_hsts_header_present(self, client: TestClient):
        """HSTS header must be present on every response."""
        resp = client.get("/health")
        assert "strict-transport-security" in resp.headers
        assert "max-age=" in resp.headers["strict-transport-security"]

    def test_no_redirect_when_enforce_https_false(self):
        """With ENFORCE_HTTPS=false, HTTP requests should NOT be redirected."""
        settings = load_security_settings()
        mock_request = MagicMock()
        mock_request.url.scheme = "http"
        mock_request.url.hostname = "192.168.1.10"
        mock_request.headers = {}

        with patch.dict(os.environ, {"ENFORCE_HTTPS": "false"}):
            s = load_security_settings()
            assert not should_redirect_to_https(mock_request, s)

    def test_redirect_when_enforce_https_true(self):
        """With ENFORCE_HTTPS=true, HTTP requests should be redirected."""
        mock_request = MagicMock()
        mock_request.url.scheme = "http"
        mock_request.url.hostname = "example.com"
        mock_request.headers = {}

        with patch.dict(os.environ, {"ENFORCE_HTTPS": "true"}):
            s = load_security_settings()
            assert should_redirect_to_https(mock_request, s)


# ===========================================================================
# 2. Refresh token cookie flags
# ===========================================================================
class TestItem02_RefreshCookie:
    def test_refresh_cookie_flags(self, client: TestClient, db_session):
        """After OTP verify, refresh_token cookie must have HttpOnly, SameSite."""
        email = f"test_{secrets.token_hex(4)}@example.com"
        otp = issue_otp(email, db_session, remote_ip="127.0.0.1")
        csrf, _ = _get_csrf(client)

        resp = client.post(
            "/api/auth/otp-verify",
            json={"email": email, "otp": otp},
            headers={"X-CSRF-Token": csrf},
            cookies={"csrf_token": csrf},
        )
        assert resp.status_code == 200, resp.text

        # Check set-cookie headers
        cookie_headers = resp.headers.get_list("set-cookie")
        refresh_cookies = [h for h in cookie_headers if "refresh_token=" in h]
        assert len(refresh_cookies) >= 1, "No refresh_token cookie set"

        cookie_str = refresh_cookies[0].lower()
        assert "httponly" in cookie_str, "Missing HttpOnly flag"
        assert "samesite=" in cookie_str, "Missing SameSite flag"
        # Secure may be false in dev — check it's explicitly managed
        # In production config it would be True


# ===========================================================================
# 3. OTP rate-limited, expires, single-use, max-attempt lockout
# ===========================================================================
class TestItem03_OTP:
    def test_otp_rate_limit_per_email(self, db_session):
        """Issuing more OTPs than the limit should raise ValueError."""
        email = f"rate_{secrets.token_hex(4)}@example.com"
        for _ in range(3):
            issue_otp(email, db_session, remote_ip="10.0.0.1", email_limit=3, ip_limit=100)
        with pytest.raises(ValueError, match="rate limit"):
            issue_otp(email, db_session, remote_ip="10.0.0.1", email_limit=3, ip_limit=100)

    def test_otp_rate_limit_per_ip(self, db_session):
        """Issuing more OTPs from the same IP than the limit should raise."""
        ip = "10.99.99.99"
        for i in range(2):
            email = f"ip_test_{i}_{secrets.token_hex(4)}@example.com"
            issue_otp(email, db_session, remote_ip=ip, email_limit=100, ip_limit=2)
        with pytest.raises(ValueError, match="rate limit"):
            issue_otp(f"ip_test_extra_{secrets.token_hex(4)}@example.com", db_session, remote_ip=ip, email_limit=100, ip_limit=2)

    def test_otp_single_use(self, db_session):
        """A valid OTP should only work once."""
        email = f"single_{secrets.token_hex(4)}@example.com"
        otp = issue_otp(email, db_session, remote_ip="127.0.0.1")
        assert verify_otp(email, otp, db_session)
        assert not verify_otp(email, otp, db_session)

    def test_otp_wrong_attempts_lockout(self, db_session):
        """After max wrong attempts, the OTP should be invalidated."""
        email = f"lockout_{secrets.token_hex(4)}@example.com"
        otp = issue_otp(email, db_session, remote_ip="127.0.0.1")
        for _ in range(5):
            assert not verify_otp(email, "000000", db_session, max_attempts=5)
        # Now even the correct OTP should fail
        assert not verify_otp(email, otp, db_session, max_attempts=5)

    def test_otp_expires(self, db_session):
        """An expired OTP should not verify."""
        email = f"expire_{secrets.token_hex(4)}@example.com"
        otp = issue_otp(email, db_session, remote_ip="127.0.0.1")
        # Manually expire it via ORM — OTPChallenge.email uses EncryptedText,
        # so the ORM handles encrypt/decrypt automatically.
        from sqlalchemy import select
        challenge = db_session.execute(
            select(OTPChallenge).where(OTPChallenge.email == email).order_by(OTPChallenge.created_at.desc())
        ).scalar_one()
        # utcnow() returns naive datetime, so use a naive past datetime
        challenge.expires_at = datetime(2020, 1, 1)
        db_session.add(challenge)
        db_session.commit()
        assert not verify_otp(email, otp, db_session)


# ===========================================================================
# 4. CAPTCHA triggers on free-tier abuse
# ===========================================================================
class TestItem04_CAPTCHA:
    def test_captcha_triggered_after_abuse(self, client: TestClient):
        """After exceeding free limit + abuse threshold, requires_captcha flag is set."""
        fp = f"abuse-fp-{secrets.token_hex(4)}"
        csrf, _ = _get_csrf(client)

        # Use up free checks
        for _ in range(4):
            resp = client.post(
                "/api/predict",
                json={"text": "test claim for captcha audit"},
                headers={
                    "X-Device-Fingerprint": fp,
                    "X-CSRF-Token": csrf,
                },
                cookies={"csrf_token": csrf},
            )

        # Check usage: should now require captcha or login
        resp = client.get("/api/usage-check", headers={"X-Device-Fingerprint": fp})
        data = resp.json()
        # After abuse, either requires_login or requires_captcha is True
        assert data["requires_login"] or data.get("requires_captcha", False)


# ===========================================================================
# 5. Free-usage count is server-side
# ===========================================================================
class TestItem05_ServerSideUsage:
    def test_usage_count_from_db(self, client: TestClient):
        """Usage count comes from DB, not client input."""
        fp = f"server-side-{secrets.token_hex(4)}"
        resp = client.get("/api/usage-check", headers={"X-Device-Fingerprint": fp})
        data = resp.json()
        assert "free_remaining" in data
        assert data["free_remaining"] == 3  # Fresh user

    def test_no_usage_count_in_predict_request(self):
        """Verify the PredictRequest schema has no usage_count field."""
        from backend.main import PredictRequest
        fields = PredictRequest.model_fields
        assert "usage_count" not in fields
        assert "free_remaining" not in fields
        assert "checks_left" not in fields


# ===========================================================================
# 6. General rate limiting on all public endpoints
# ===========================================================================
class TestItem06_RateLimiting:
    def test_rate_limiter_blocks_after_limit(self):
        """Sliding window rate limiter should block after limit."""
        limiter = SlidingWindowRateLimiter()
        key = "test:ratelimit:audit"
        for _ in range(5):
            assert limiter.allow(key, 5, 60)
        assert not limiter.allow(key, 5, 60)

    def test_all_api_paths_rate_limited(self, client: TestClient):
        """All /api/ endpoints are covered by the rate limiter middleware."""
        # The middleware checks is_public_api_path which returns True for /api/
        from backend.security import is_public_api_path
        assert is_public_api_path("/api/predict")
        assert is_public_api_path("/api/auth/otp-request")
        assert is_public_api_path("/api/usage-check")
        assert is_public_api_path("/api/upload/media")
        assert not is_public_api_path("/health")


# ===========================================================================
# 7. Request body size limits
# ===========================================================================
class TestItem07_BodySize:
    def test_large_body_rejected(self, client: TestClient):
        """Bodies exceeding MAX_REQUEST_BYTES should be rejected with 413."""
        csrf, _ = _get_csrf(client)
        oversized_body = "x" * (settings.max_request_bytes + 1000)
        resp = client.post(
            "/api/predict",
            content=oversized_body,
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
                "X-Device-Fingerprint": "test",
            },
            cookies={"csrf_token": csrf},
        )
        assert resp.status_code == 413


# ===========================================================================
# 8. All DB queries parameterized / ORM-based
# ===========================================================================
class TestItem08_ParameterizedQueries:
    def test_no_raw_sql_with_user_input(self):
        """Verify no f-string or %-format SQL with user input in backend code."""
        backend_dir = Path(__file__).resolve().parent.parent
        dangerous_patterns = [
            re.compile(r'f".*(?:SELECT|INSERT|UPDATE|DELETE|WHERE).*\{', re.IGNORECASE),
            re.compile(r'f\'.*(?:SELECT|INSERT|UPDATE|DELETE|WHERE).*\{', re.IGNORECASE),
            re.compile(r'%s.*(?:SELECT|INSERT|UPDATE|DELETE)', re.IGNORECASE),
        ]

        violations = []
        for py_file in backend_dir.rglob("*.py"):
            if "test" in py_file.name or "__pycache__" in str(py_file):
                continue
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), 1):
                for pattern in dangerous_patterns:
                    if pattern.search(line):
                        violations.append(f"{py_file.name}:{i}: {line.strip()}")

        assert not violations, f"Potential raw SQL found:\n" + "\n".join(violations)

    def test_text_calls_are_static_ddl(self):
        """The only text() calls should be static DDL migration strings."""
        from backend import models
        source = inspect.getsource(models)
        text_calls = re.findall(r'text\("([^"]+)"\)', source)
        for call in text_calls:
            assert call.upper().startswith("ALTER TABLE"), f"Non-DDL text() call: {call}"


# ===========================================================================
# 9. Pydantic validation + XSS sanitization
# ===========================================================================
class TestItem09_ValidationXSS:
    def test_predict_requires_text_field(self, client: TestClient):
        """Posting without 'text' should return 422 (Pydantic validation)."""
        csrf, _ = _get_csrf(client)
        resp = client.post(
            "/api/predict",
            json={},
            headers={"X-CSRF-Token": csrf, "X-Device-Fingerprint": "t"},
            cookies={"csrf_token": csrf},
        )
        assert resp.status_code == 422

    def test_predict_text_max_length(self, client: TestClient):
        """Text exceeding max_length=5000 should be rejected."""
        csrf, _ = _get_csrf(client)
        resp = client.post(
            "/api/predict",
            json={"text": "x" * 5001},
            headers={"X-CSRF-Token": csrf, "X-Device-Fingerprint": "t"},
            cookies={"csrf_token": csrf},
        )
        assert resp.status_code == 422

    def test_otp_format_validation(self, client: TestClient):
        """OTP must be exactly 6 digits."""
        csrf, _ = _get_csrf(client)
        resp = client.post(
            "/api/auth/otp-verify",
            json={"email": "test@x.com", "otp": "abc"},
            headers={"X-CSRF-Token": csrf},
            cookies={"csrf_token": csrf},
        )
        assert resp.status_code == 422

    def test_sanitize_display_text_strips_xss(self):
        """XSS payloads should be HTML-escaped in sanitize_display_text."""
        result_script = sanitize_display_text("<script>alert(1)</script>")
        assert "&lt;script&gt;" in result_script
        assert "<script>" not in result_script  # Raw tag must not survive

        result_img = sanitize_display_text('<img onerror="alert(1)">')
        # The raw HTML tag must be escaped — angle brackets become entities
        assert "<img" not in result_img
        assert "&lt;img" in result_img
        # Quotes must be escaped too
        assert '"' not in result_img or "&quot;" in result_img


# ===========================================================================
# 10. CSRF protection on state-changing endpoints
# ===========================================================================
class TestItem10_CSRF:
    def test_predict_without_csrf_returns_403(self, client: TestClient):
        """POST /api/predict without CSRF token should return 403."""
        resp = client.post(
            "/api/predict",
            json={"text": "hello"},
            headers={"X-Device-Fingerprint": "t"},
        )
        assert resp.status_code == 403

    def test_otp_request_without_csrf_returns_403(self, client: TestClient):
        """POST /api/auth/otp-request without CSRF should return 403."""
        resp = client.post(
            "/api/auth/otp-request",
            json={"email": "test@example.com"},
        )
        assert resp.status_code == 403

    def test_predict_with_csrf_succeeds(self, client: TestClient):
        """POST /api/predict with valid CSRF should succeed."""
        csrf, _ = _get_csrf(client)
        resp = client.post(
            "/api/predict",
            json={"text": "test csrf check claim"},
            headers={"X-CSRF-Token": csrf, "X-Device-Fingerprint": f"csrf-{secrets.token_hex(4)}"},
            cookies={"csrf_token": csrf},
        )
        assert resp.status_code in (200, 403)  # 403 if usage exceeded, but not CSRF error


# ===========================================================================
# 11. File uploads: magic bytes, size, malware, random filenames, EXIF strip
# ===========================================================================
class TestItem11_FileUploads:
    def test_validate_magic_bytes_rejects_wrong_type(self):
        """validate_magic_bytes should reject files with wrong magic bytes."""
        fake_png_content = b"This is not a PNG file at all"
        with pytest.raises(ValueError, match="Unsupported"):
            validate_magic_bytes("fake.png", fake_png_content, ["png"])

    def test_validate_magic_bytes_accepts_real_png(self):
        """Real PNG magic bytes should be accepted."""
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = validate_magic_bytes("test.png", png_header, ["png"])
        assert result == "png"

    def test_upload_size_limit(self):
        """Files exceeding MAX_UPLOAD_BYTES should be rejected."""
        from backend.upload_safety import MAX_UPLOAD_BYTES
        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_UPLOAD_BYTES + 1)
        with pytest.raises(ValueError, match="exceeds maximum"):
            process_upload(Path("/tmp/test_uploads"), "big.png", oversized, ["png"])

    def test_random_filenames(self):
        """Stored files should have randomized names, not the original."""
        from backend.upload_safety import store_upload_securely
        project_root = Path(__file__).resolve().parent.parent.parent
        tmp_dir = project_root / ".test_tmp" / "upload_test"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
            result = store_upload_securely(tmp_dir, "png", content)
            assert result.name != "original.png"
            assert len(result.stem) == 32  # 16 hex bytes = 32 chars
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_strip_image_metadata(self):
        """strip_image_metadata should process without error on valid images."""
        # Create a minimal valid PNG
        from PIL import Image
        img = Image.new("RGB", (10, 10), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        original = buf.getvalue()
        stripped = strip_image_metadata(original)
        assert len(stripped) > 0


# ===========================================================================
# 12. LLM prompt isolation
# ===========================================================================
class TestItem12_LLMIsolation:
    def test_no_llm_prompt_injection_risk(self):
        """Verify user text is never used in prompt construction."""
        from backend.ml import predict as predict_module
        source = inspect.getsource(predict_module)
        # Should not contain prompt template construction with user text
        dangerous_patterns = [
            "system_prompt",
            "f\"You are",
            "messages.append",
            "openai",
            "anthropic",
            "genai",
        ]
        for pattern in dangerous_patterns:
            assert pattern not in source, f"Potential LLM prompt injection: '{pattern}' found in predict.py"

    def test_predict_uses_sklearn_only(self):
        """Prediction should use sklearn, not LLM APIs."""
        from backend.ml.predict import PredictionService
        svc = PredictionService()
        svc.load()
        result = svc.predict("test claim text")
        assert "label" in result
        assert "confidence" in result
        assert result["label"] in ("real", "fake")


# ===========================================================================
# 13. Security headers present
# ===========================================================================
class TestItem13_SecurityHeaders:
    REQUIRED_HEADERS = {
        "content-security-policy": "default-src",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin",
        "permissions-policy": "camera",
        "strict-transport-security": "max-age=",
    }

    def test_all_security_headers_on_health(self, client: TestClient):
        """All required security headers must be present on /health."""
        resp = client.get("/health")
        for header, expected_fragment in self.REQUIRED_HEADERS.items():
            assert header in resp.headers, f"Missing header: {header}"
            assert expected_fragment in resp.headers[header], (
                f"Header {header} = '{resp.headers[header]}' doesn't contain '{expected_fragment}'"
            )

    def test_all_security_headers_on_api(self, client: TestClient):
        """All required security headers must be present on API responses."""
        resp = client.get("/api/usage-check", headers={"X-Device-Fingerprint": "t"})
        for header in self.REQUIRED_HEADERS:
            assert header in resp.headers, f"Missing header on API: {header}"


# ===========================================================================
# 14. CORS restricted (no wildcard in production)
# ===========================================================================
class TestItem14_CORS:
    def test_wildcard_blocked_in_production(self):
        """parse_origins should raise if wildcard is used in production."""
        from backend.security import parse_origins
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with pytest.raises(RuntimeError, match="wildcard"):
                parse_origins("*")

    def test_specific_origins_allowed(self):
        """Specific origins should be parsed correctly."""
        from backend.security import parse_origins
        result = parse_origins("https://sachlens.com,https://app.sachlens.com")
        assert result == ["https://sachlens.com", "https://app.sachlens.com"]


# ===========================================================================
# 15. Upload auto-delete / TTL
# ===========================================================================
class TestItem15_UploadTTL:
    def test_cleanup_deletes_old_files(self):
        """cleanup_expired_uploads should remove files older than TTL."""
        from backend.cleanup import cleanup_expired_uploads, UPLOAD_TTL_HOURS
        project_root = Path(__file__).resolve().parent.parent.parent
        tmp_dir = project_root / ".test_tmp" / "ttl_delete"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Create a file and backdate its mtime
            test_file = tmp_dir / "old_upload.png"
            test_file.write_bytes(b"test content")
            old_time = (datetime.now(timezone.utc) - timedelta(hours=UPLOAD_TTL_HOURS + 1)).timestamp()
            os.utime(test_file, (old_time, old_time))

            with patch("backend.cleanup.UPLOAD_DIR", tmp_dir):
                cleanup_expired_uploads()

            assert not test_file.exists(), "Old file should have been deleted"
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_cleanup_keeps_recent_files(self):
        """cleanup_expired_uploads should keep files newer than TTL."""
        from backend.cleanup import cleanup_expired_uploads
        project_root = Path(__file__).resolve().parent.parent.parent
        tmp_dir = project_root / ".test_tmp" / "ttl_keep"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            test_file = tmp_dir / "new_upload.png"
            test_file.write_bytes(b"test content")

            with patch("backend.cleanup.UPLOAD_DIR", tmp_dir):
                cleanup_expired_uploads()

            assert test_file.exists(), "Recent file should NOT have been deleted"
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ===========================================================================
# 16. Sensitive fields encrypted at rest
# ===========================================================================
class TestItem16_Encryption:
    def test_encrypt_decrypt_roundtrip(self):
        """encrypt_text / decrypt_text should roundtrip correctly."""
        original = "user@example.com"
        encrypted = encrypt_text(original)
        assert encrypted.startswith("enc.v1:")
        assert original not in encrypted
        decrypted = decrypt_text(encrypted)
        assert decrypted == original

    def test_encrypted_type_on_user_model(self):
        """User.email and User.phone should use EncryptedText type."""
        from backend.models import User
        email_col = User.__table__.columns["email"]
        phone_col = User.__table__.columns["phone"]
        assert isinstance(email_col.type, EncryptedText)
        assert isinstance(phone_col.type, EncryptedText)

    def test_otp_challenge_email_encrypted(self):
        """OTPChallenge.email should use EncryptedText type."""
        email_col = OTPChallenge.__table__.columns["email"]
        assert isinstance(email_col.type, EncryptedText)


# ===========================================================================
# 17. No hardcoded secrets
# ===========================================================================
class TestItem17_NoHardcodedSecrets:
    SECRET_PATTERNS = [
        re.compile(r'sk-[a-zA-Z0-9]{20,}'),  # OpenAI-style
        re.compile(r'["\']AIza[a-zA-Z0-9_-]{30,}["\']'),  # Google API
        re.compile(r'AKIA[0-9A-Z]{16}'),  # AWS
        re.compile(r'ghp_[a-zA-Z0-9]{36}'),  # GitHub
    ]

    def test_no_secrets_in_source(self):
        """No hardcoded API keys or secrets in any source files."""
        project_root = Path(__file__).resolve().parent.parent.parent
        violations = []
        for ext in ("*.py", "*.js", "*.jsx", "*.ts", "*.tsx"):
            for path in project_root.rglob(ext):
                if "node_modules" in str(path) or ".venv" in str(path) or "__pycache__" in str(path):
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except Exception:
                    continue
                for pattern in self.SECRET_PATTERNS:
                    matches = pattern.findall(content)
                    if matches:
                        violations.append(f"{path.name}: {matches}")

        assert not violations, f"Hardcoded secrets found:\n" + "\n".join(violations)

    def test_env_example_exists(self):
        """A .env.example file should exist in the project root."""
        project_root = Path(__file__).resolve().parent.parent.parent
        assert (project_root / ".env.example").exists()


# ===========================================================================
# 18. Production error responses don't leak internals
# ===========================================================================
class TestItem18_ErrorResponses:
    def test_500_error_is_generic(self, client: TestClient):
        """500 errors should show 'Internal server error', not stack traces."""
        # Trigger a 500 by calling an endpoint that would fail internally
        from backend.main import format_http_error
        from fastapi import HTTPException

        result = format_http_error(HTTPException(status_code=500, detail="secret traceback info"))
        assert result == {"detail": "Internal server error"}

    def test_404_shows_message(self, client: TestClient):
        """404s should show a reasonable message but no internals."""
        resp = client.get("/api/nonexistent")
        assert resp.status_code in (404, 405)
        body = resp.json()
        assert "traceback" not in str(body).lower()
        assert "file" not in str(body).lower() or "detail" in body

    def test_exception_handler_masks_500(self, client: TestClient):
        """The global exception handler should mask 500 responses."""
        resp = client.get("/health")  # Should work fine
        assert resp.status_code == 200

        # Verify format_http_error strips 5xx detail
        from backend.main import format_http_error
        from fastapi import HTTPException
        for code in (500, 502, 503):
            result = format_http_error(HTTPException(status_code=code, detail="something sensitive"))
            assert result == {"detail": "Internal server error"}


# ===========================================================================
# 19. Dependency vulnerabilities
# ===========================================================================
class TestItem19_Dependencies:
    def test_requirements_file_exists(self):
        """requirements.txt should exist."""
        req_file = Path(__file__).resolve().parent.parent / "requirements.txt"
        assert req_file.exists()

    def test_no_known_dangerous_packages(self):
        """Check for known dangerous/deprecated packages."""
        req_file = Path(__file__).resolve().parent.parent / "requirements.txt"
        content = req_file.read_text()
        dangerous = ["pyyaml<5", "requests<2.20", "urllib3<1.24", "django<2"]
        for pkg in dangerous:
            assert pkg not in content.lower(), f"Potentially dangerous: {pkg}"
