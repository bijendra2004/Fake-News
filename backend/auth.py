from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import OTPChallenge, RefreshTokenRecord
from .secrets_config import get_runtime_secret

JWT_SECRET = get_runtime_secret("JWT_SECRET", "APP_EPHEMERAL_JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
OTP_EMAIL_LIMIT_WINDOW_SECONDS = 600
ACCESS_TOKEN_EXPIRES_MINUTES = 15
REFRESH_TOKEN_EXPIRES_DAYS = 30
SLIDING_INACTIVITY_HOURS = 48
ABSOLUTE_MAX_SESSION_DAYS = 7
OTP_EXPIRES_MINUTES = 10

logger = logging.getLogger("sachlens.auth")
_otp_rate_limits: dict[str, list[datetime]] = {}
_otp_ip_rate_limits: dict[str, list[datetime]] = {}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def issue_otp(
    email: str,
    db: Session,
    remote_ip: str | None = None,
    *,
    email_limit: int = 3,
    ip_limit: int = 10,
) -> str:
    now = utcnow()
    history = _otp_rate_limits.setdefault(email, [])
    history[:] = [entry for entry in history if (now - entry).total_seconds() < OTP_EMAIL_LIMIT_WINDOW_SECONDS]
    if len(history) >= email_limit:
        raise ValueError("OTP rate limit exceeded")

    if remote_ip:
        ip_history = _otp_ip_rate_limits.setdefault(remote_ip, [])
        ip_history[:] = [entry for entry in ip_history if (now - entry).total_seconds() < OTP_EMAIL_LIMIT_WINDOW_SECONDS]
        if len(ip_history) >= ip_limit:
            raise ValueError("OTP rate limit exceeded for IP")
        ip_history.append(now)

    history.append(now)
    otp = f"{secrets.randbelow(1_000_000):06d}"
    otp_hash = hash_value(email, otp)
    db.execute(delete(OTPChallenge).where(OTPChallenge.email == email))
    challenge = OTPChallenge(
        email=email,
        otp_hash=otp_hash,
        expires_at=now + timedelta(minutes=OTP_EXPIRES_MINUTES),
        attempts=0,
    )
    db.add(challenge)
    db.commit()
    if os.getenv("APP_ENV", "development").lower() != "production":
        logger.info("OTP issued for %s (dev-only log)", email)
        logger.info("OTP value for %s: %s", email, otp)
    return otp


def ensure_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def verify_otp(email: str, otp: str, db: Session, max_attempts: int = 5) -> bool:
    challenge = db.execute(
        select(OTPChallenge).where(OTPChallenge.email == email).order_by(OTPChallenge.created_at.desc())
    ).scalar_one_or_none()
    if challenge is None:
        return False
    if ensure_utc(challenge.expires_at) < utcnow():
        db.execute(delete(OTPChallenge).where(OTPChallenge.id == challenge.id))
        db.commit()
        return False

    challenge.attempts += 1
    db.add(challenge)
    db.commit()

    if challenge.attempts > max_attempts:
        db.execute(delete(OTPChallenge).where(OTPChallenge.id == challenge.id))
        db.commit()
        return False

    is_valid = hmac.compare_digest(challenge.otp_hash, hash_value(email, otp))
    if is_valid:
        db.execute(delete(OTPChallenge).where(OTPChallenge.id == challenge.id))
        db.commit()
    elif challenge.attempts >= max_attempts:
        db.execute(delete(OTPChallenge).where(OTPChallenge.id == challenge.id))
        db.commit()
    return is_valid


def create_access_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": email,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TOKEN_EXPIRES_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(48)
    payload = {
        "sub": email,
        "jti": token,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> bool:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("type") == "access"
    except jwt.PyJWTError:
        return False


def get_access_token_email(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

    if payload.get("type") != "access":
        return None

    email = payload.get("sub")
    return str(email) if email else None


def rotate_refresh_token(
    email: str,
    db: Session,
    old_token: str | None = None,
    device_fingerprint: str | None = None,
) -> str:
    """Create a new refresh token or rotate an existing one.

    Preserves `issued_at` across rotations to enforce the 7-day absolute session ceiling.
    Updates `last_active_at` and associates the client's device fingerprint.
    """
    now = utcnow()
    issued_at = now
    stored_fingerprint = device_fingerprint

    if old_token:
        old_hash = hash_value(email, old_token)
        old_row = db.execute(
            select(RefreshTokenRecord).where(RefreshTokenRecord.token_hash == old_hash)
        ).scalar_one_or_none()
        if old_row:
            if old_row.issued_at:
                issued_at = old_row.issued_at
            if old_row.device_fingerprint and not stored_fingerprint:
                stored_fingerprint = old_row.device_fingerprint
            db.delete(old_row)

    token = create_refresh_token(email)
    token_hash = hash_value(email, token)
    db.add(
        RefreshTokenRecord(
            email=email,
            token_hash=token_hash,
            expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS),
            issued_at=issued_at,
            last_active_at=now,
            device_fingerprint=stored_fingerprint,
        )
    )
    db.commit()
    return token


def verify_refresh_token(
    token: str,
    db: Session,
    current_fingerprint: str | None = None,
) -> str | None:
    """Verify refresh token validity under sliding inactivity, absolute max lifetime, and device binding rules.

    Returns email if valid, or None if expired / mismatched.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

    if payload.get("type") != "refresh":
        return None

    email = payload.get("sub")
    if not email:
        return None

    token_hash = hash_value(email, token)
    row = db.execute(
        select(RefreshTokenRecord).where(RefreshTokenRecord.token_hash == token_hash)
    ).scalar_one_or_none()
    if row is None or ensure_utc(row.expires_at) < utcnow():
        return None

    now = utcnow()

    # Rule 1: Sliding Inactivity Timeout (48 hours)
    last_active = ensure_utc(row.last_active_at or row.created_at)
    if (now - last_active).total_seconds() > SLIDING_INACTIVITY_HOURS * 3600:
        logger.info("Session for %s expired due to 48h inactivity (last_active: %s)", email, last_active)
        db.delete(row)
        db.commit()
        return None

    # Rule 2: Absolute Max Session Lifetime (7 days from original login)
    issued_at = ensure_utc(row.issued_at or row.created_at)
    if (now - issued_at).total_seconds() > ABSOLUTE_MAX_SESSION_DAYS * 86400:
        logger.info("Session for %s reached 7-day absolute max lifetime (issued_at: %s)", email, issued_at)
        db.delete(row)
        db.commit()
        return None

    # Rule 3: Device Fingerprint Binding (Anti-Session Theft)
    stored_fp = (row.device_fingerprint or "").strip()
    received_fp = (current_fingerprint or "").strip()
    ignored_fps = {"", "anonymous", "server", "unknown"}

    if stored_fp and received_fp and stored_fp not in ignored_fps and received_fp not in ignored_fps:
        if stored_fp != received_fp:
            logger.warning(
                "Device fingerprint mismatch for %s (stored=%s, received=%s). Revoking session.",
                email, stored_fp, received_fp,
            )
            db.delete(row)
            db.commit()
            return None
    elif stored_fp in ignored_fps and received_fp and received_fp not in ignored_fps:
        row.device_fingerprint = received_fp

    # Session is active and valid: touch last_active_at
    row.last_active_at = now
    db.add(row)
    db.commit()
    return email


def touch_session_activity(email: str, db: Session, device_fingerprint: str | None = None) -> None:
    """Update last_active_at timestamp for active sessions of this user."""
    now = utcnow()
    query = select(RefreshTokenRecord).where(RefreshTokenRecord.email == email)
    if device_fingerprint and device_fingerprint != "server":
        query = query.where(RefreshTokenRecord.device_fingerprint == device_fingerprint)
    tokens = db.execute(query).scalars().all()
    for row in tokens:
        row.last_active_at = now
        db.add(row)
    if tokens:
        db.commit()


def revoke_refresh_token(token: str, db: Session, email: str | None = None) -> None:
    if not email:
        raise ValueError("Email is required to revoke a refresh token")
    token_hash = hash_value(email, token)
    db.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.token_hash == token_hash))
    db.commit()


def revoke_all_user_sessions(email: str, db: Session) -> int:
    """Revoke all refresh tokens for a user across all devices."""
    result = db.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.email == email))
    db.commit()
    return result.rowcount or 0


def hash_value(email: str, value: str) -> str:
    return hashlib.sha256(f"{email}:{value}:{JWT_SECRET}".encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
