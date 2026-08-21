from __future__ import annotations

import os
import secrets


def get_runtime_secret(env_name: str, fallback_name: str) -> str:
    explicit = os.getenv(env_name)
    if explicit:
        return explicit

    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env == "production":
        raise RuntimeError(f"Missing required environment variable: {env_name}")

    current = os.getenv(fallback_name)
    if current and len(current.encode("utf-8")) >= 32:
        return current

    generated = secrets.token_urlsafe(48)
    os.environ[fallback_name] = generated
    return generated
