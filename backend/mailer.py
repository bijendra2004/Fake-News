from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Any


logger = logging.getLogger("sachlens.mailer")


class EmailDeliveryError(RuntimeError):
    pass


def send_otp_email(email: str, otp: str, expires_minutes: int = 10) -> None:
    """Send OTP email using Resend HTTP API (primary) or SMTP (fallback).

    Resend works on Render's free tier because it uses HTTP, while SMTP
    ports are blocked by Render.
    """
    # Try Resend HTTP API first (works on Render free tier)
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    if resend_api_key:
        _send_via_resend(email, otp, expires_minutes, resend_api_key)
        return

    # Fall back to SMTP (works locally but NOT on Render free tier)
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        if os.getenv("APP_ENV", "development").lower() == "production":
            raise EmailDeliveryError(
                "Email delivery is not configured. Set RESEND_API_KEY or SMTP_HOST."
            )
        logger.info("Email not configured; OTP email skipped for %s in non-production", email)
        return

    _send_via_smtp(email, otp, expires_minutes)


def _send_via_resend(email: str, otp: str, expires_minutes: int, api_key: str) -> None:
    """Send OTP email via Resend HTTP API (https://resend.com)."""
    sender_name = os.getenv("SMTP_FROM_NAME", "SachLens").strip() or "SachLens"
    from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev").strip()
    subject = os.getenv("OTP_EMAIL_SUBJECT", "Your SachLens OTP Code")

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #333;">🔐 Your SachLens OTP Code</h2>
        <p style="font-size: 16px; color: #555;">Use the following code to verify your identity:</p>
        <div style="background: #f4f4f4; padding: 20px; text-align: center; border-radius: 8px; margin: 20px 0;">
            <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #222;">{otp}</span>
        </div>
        <p style="font-size: 14px; color: #777;">This code expires in {expires_minutes} minutes.</p>
        <p style="font-size: 14px; color: #777;">If you did not request this, you can safely ignore this email.</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="font-size: 12px; color: #aaa;">— {sender_name}</p>
    </div>
    """

    payload = json.dumps({
        "from": f"{sender_name} <{from_email}>",
        "to": [email],
        "subject": subject,
        "html": html_body,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8")
            logger.info("Resend API response: %s %s", resp.status, resp_body)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("Resend API error: %s %s", exc.code, error_body)
        raise EmailDeliveryError(f"Resend API error: {exc.code} {error_body}") from exc
    except Exception as exc:
        logger.exception("Resend API request failed")
        raise EmailDeliveryError("Failed to send OTP email via Resend") from exc


def _send_via_smtp(email: str, otp: str, expires_minutes: int) -> None:
    """Send OTP email via traditional SMTP (for local development)."""
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_use_tls = _parse_bool(os.getenv("SMTP_USE_TLS", "true"))
    smtp_use_ssl = _parse_bool(os.getenv("SMTP_USE_SSL", "false"))

    sender_email = os.getenv("SMTP_FROM_EMAIL", smtp_user).strip()
    sender_name = os.getenv("SMTP_FROM_NAME", "SachLens").strip() or "SachLens"
    if not sender_email:
        raise EmailDeliveryError("SMTP_FROM_EMAIL or SMTP_USERNAME must be configured")

    subject = os.getenv("OTP_EMAIL_SUBJECT", "Your SachLens OTP Code")

    logger.info(
        "Sending OTP email via SMTP host=%s port=%s from=%s to=%s",
        smtp_host, smtp_port, sender_email, email,
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = email
    message.set_content(
        (
            f"Your SachLens OTP is: {otp}\n\n"
            f"This OTP expires in {expires_minutes} minutes.\n"
            "If you did not request this, you can ignore this email.\n"
        )
    )

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15, context=ssl.create_default_context()) as smtp:
                if smtp_user:
                    smtp.login(smtp_user, smtp_password)
                send_result: Any = smtp.send_message(message)
                logger.info("SMTP send_message response: %s", send_result)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                if smtp_use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if smtp_user:
                    smtp.login(smtp_user, smtp_password)
                send_result = smtp.send_message(message)
                logger.info("SMTP send_message response: %s", send_result)
    except Exception as exc:
        logger.exception("SMTP OTP delivery failed")
        raise EmailDeliveryError("Failed to send OTP email") from exc


def _parse_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}