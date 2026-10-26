"""Fernet-based secret encryption for config and device credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def has_master_key_env() -> bool:
    """True when BRIXIGO_CONNECTOR_MASTER_KEY is explicitly configured."""
    return bool(os.environ.get("BRIXIGO_CONNECTOR_MASTER_KEY", "").strip())


def is_production_env() -> bool:
    """True when connector is running in production profile."""
    value = (os.environ.get("BRIXIGO_CONNECTOR_ENV") or "").strip().lower()
    return value in {"production", "prod", "ga"}


def derive_machine_key(*, app_id: str) -> bytes:
    """
    Derive a stable Fernet key from machine identity + optional env override.
    Prefer BRIXIGO_CONNECTOR_MASTER_KEY (url-safe base64 32-byte key) in production.
    """
    env_key = os.environ.get("BRIXIGO_CONNECTOR_MASTER_KEY", "").strip()
    if env_key:
        raw = env_key.encode("utf-8")
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)

    if is_production_env():
        raise RuntimeError(
            "BRIXIGO_CONNECTOR_MASTER_KEY is required when BRIXIGO_CONNECTOR_ENV=production. "
            "Set a strong secret before starting the connector."
        )

    node = uuid.getnode()
    host = platform.node() or "unknown-host"
    material = f"{app_id}|{host}|{node}|brixigo-connector-v1".encode("utf-8")
    digest = hashlib.pbkdf2_hmac("sha256", material, b"brixigo-salt-v1", 120_000)
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    """Encrypt/decrypt strings and JSON blobs."""

    def __init__(self, fernet_key: bytes) -> None:
        self._fernet = Fernet(fernet_key)
        self._key = fernet_key

    def encrypt_str(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("ascii")

    def decrypt_str(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Failed to decrypt secret — key mismatch or corrupt data") from exc

    def encrypt_json(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return self.encrypt_str(raw)

    def decrypt_json(self, token: str) -> dict[str, Any]:
        raw = self.decrypt_str(token)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Encrypted payload is not a JSON object")
        return data

    def hash_api_key(self, api_key: str) -> str:
        digest = hmac.new(self._key, api_key.encode("utf-8"), hashlib.sha256).hexdigest()
        return digest
