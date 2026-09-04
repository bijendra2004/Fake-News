from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.types import Text, TypeDecorator

from .secrets_config import get_runtime_secret

_ENCODING_PREFIX = "enc.v1:"


def _require_key() -> bytes:
    key = get_runtime_secret("DATA_ENCRYPTION_KEY", "APP_EPHEMERAL_DATA_KEY")
    return hashlib.sha256(key.encode("utf-8")).digest()


def encrypt_text(value: str) -> str:
    key = _require_key()
    plaintext = value.encode("utf-8")
    nonce = hmac.new(key, plaintext, hashlib.sha256).digest()[:12]
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=None)
    payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return f"{_ENCODING_PREFIX}{payload}"


def decrypt_text(value: str) -> str:
    if not value.startswith(_ENCODING_PREFIX):
        return value
    try:
        key = _require_key()
        raw = base64.urlsafe_b64decode(value[len(_ENCODING_PREFIX) :].encode("ascii"))
        nonce = raw[:12]
        ciphertext = raw[12:]
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
        return plaintext.decode("utf-8")
    except Exception:
        return "[encrypted]"


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect):
        if value is None:
            return None
        return encrypt_text(str(value))

    def process_result_value(self, value: Any, dialect):
        if value is None:
            return None
        return decrypt_text(str(value))
