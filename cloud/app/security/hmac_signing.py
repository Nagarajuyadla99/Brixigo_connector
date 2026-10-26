"""HMAC command signing compatible with connector Phase 2/3 security gate."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any


def canonical_payload(body: dict[str, Any]) -> str:
    return json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def sign_command(
    *,
    cloud_command_id: str,
    command_type: str,
    device_id: str,
    payload: dict[str, Any],
    nonce: str,
    issued_at: str,
    expires_at: str,
    secret: str,
) -> str:
    body = {
        "cloud_command_id": cloud_command_id,
        "command_type": command_type,
        "device_id": device_id or "",
        "expires_at": expires_at,
        "issued_at": issued_at,
        "nonce": nonce,
        "payload": payload or {},
    }
    digest = hmac.new(
        secret.encode("utf-8"),
        canonical_payload(body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def build_signed_command(
    *,
    command_id: str,
    command_type: str,
    device_id: str,
    payload: dict[str, Any],
    secret: str,
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    issued_at = now.isoformat()
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
    nonce = secrets.token_urlsafe(16)
    signature = sign_command(
        cloud_command_id=command_id,
        command_type=command_type,
        device_id=device_id,
        payload=payload,
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        secret=secret,
    )
    return {
        "id": command_id,
        "command_id": command_id,
        "command_type": command_type,
        "type": command_type,
        "device_id": device_id,
        "payload": payload or {},
        "signature": signature,
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
