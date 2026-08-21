from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any


logger = logging.getLogger("sachlens.mailer")


class EmailDeliveryError(RuntimeError):
    pass


def send_otp_email(email: str, otp: str, expires_minutes: int = 10) -> None:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        if os.getenv("APP_ENV", "development").lower() == "production":
            raise EmailDeliveryError("SMTP is not configured")
        logger.info("SMTP not configured; OTP email skipped for %s in non-production", email)
        return

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
        "Loaded SMTP config host=%s port=%s tls=%s ssl=%s username=%s from=%s subject=%s",
        smtp_host,
        smtp_port,
        smtp_use_tls,
        smtp_use_ssl,
        smtp_user or "<empty>",
        sender_email,
        subject,
    )
    logger.info(
        "Sending OTP email via SMTP host=%s port=%s from=%s to=%s",
        smtp_host,
        smtp_port,
        sender_email,
        email,
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
                logger.info("SMTP SSL connection established")
                if smtp_user:
                    login_result = smtp.login(smtp_user, smtp_password)
                    logger.info("SMTP login response: %s", login_result)
                send_result: Any = smtp.send_message(message)
                logger.info("SMTP send_message response: %s", send_result)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
                ehlo_result = smtp.ehlo()
                logger.info("SMTP EHLO response: %s", ehlo_result)
                if smtp_use_tls:
                    starttls_result = smtp.starttls(context=ssl.create_default_context())
                    logger.info("SMTP STARTTLS response: %s", starttls_result)
                    post_tls_ehlo = smtp.ehlo()
                    logger.info("SMTP EHLO after STARTTLS response: %s", post_tls_ehlo)
                if smtp_user:
                    login_result = smtp.login(smtp_user, smtp_password)
                    logger.info("SMTP login response: %s", login_result)
                logger.info("About to call SMTP send_message for %s", email)
                send_result = smtp.send_message(message)
                logger.info("SMTP send_message response: %s", send_result)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.exception("SMTP OTP delivery failed")
        raise EmailDeliveryError("Failed to send OTP email") from exc


def _parse_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}