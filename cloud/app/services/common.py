"""Shared service helpers — audit, notifications, encryption helpers."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog, Notification, new_id, utcnow


def _fernet() -> Fernet:
    settings = get_settings()
    digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_str(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_str(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def write_audit(
    db: Session,
    *,
    organisation_id: str = "",
    actor_id: str = "",
    actor_email: str = "",
    action: str,
    success: bool = True,
    message: str = "",
    resource_type: str = "",
    resource_id: str = "",
    ip_address: str = "",
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        id=new_id(),
        organisation_id=organisation_id or "",
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        success=success,
        message=message[:2000],
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        details_json=details or {},
        created_at=utcnow(),
    )
    db.add(entry)
    db.flush()
    return entry


def raise_notification(
    db: Session,
    *,
    organisation_id: str,
    event_type: str,
    title: str,
    message: str,
    severity: str = "warning",
    connector_id: str = "",
    device_id: str = "",
    details: dict | None = None,
) -> Notification:
    note = Notification(
        id=new_id(),
        organisation_id=organisation_id,
        connector_id=connector_id,
        device_id=device_id,
        event_type=event_type,
        severity=severity,
        title=title,
        message=message[:2000],
        details_json=details or {},
    )
    db.add(note)
    db.flush()
    return note


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def health_score_from_metrics(metrics: dict[str, Any]) -> tuple[float, str]:
    """Compute 0-100 health score and state label."""
    if not metrics.get("online", True):
        return 0.0, "offline"
    score = 100.0
    storage = float(metrics.get("storage_usage_percent") or 0)
    memory = float(metrics.get("memory_usage_percent") or 0)
    temp = metrics.get("cpu_temperature")
    battery = metrics.get("battery_level")
    ping = float(metrics.get("ping_ms") or 0)
    if storage >= 95:
        score -= 40
    elif storage >= 85:
        score -= 20
    if memory >= 95:
        score -= 25
    elif memory >= 85:
        score -= 10
    if temp is not None and float(temp) >= 80:
        score -= 20
    if battery is not None and float(battery) <= 15:
        score -= 15
    if ping >= 1000:
        score -= 15
    elif ping >= 500:
        score -= 5
    score = max(0.0, min(100.0, score))
    if score >= 80:
        state = "healthy"
    elif score >= 50:
        state = "warning"
    else:
        state = "critical"
    return score, state
