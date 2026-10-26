"""Phase 3 domain models — employee, templates, backup, access control, reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid4())


@dataclass
class Employee:
    """Canonical employee record synchronized from Brixigo HRMS."""

    id: str
    employee_code: str
    name: str
    department: str = ""
    designation: str = ""
    branch: str = ""
    organization: str = ""
    building: str = ""
    floor: str = ""
    gate: str = ""
    shift: str = ""
    card_number: str = ""
    pin: str = ""
    status: str = "active"
    device_ids: list[str] = field(default_factory=list)
    privilege: int = 0
    cloud_version: int = 0
    last_synced_at: Optional[str] = None
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BiometricTemplate:
    """Encrypted biometric template stored locally and synced to devices."""

    id: str
    employee_id: str
    employee_code: str
    template_type: str
    finger_index: int = 0
    template_enc: str = ""
    checksum: str = ""
    vendor: str = ""
    size: int = 0
    device_id: str = ""
    cloud_version: int = 0
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceUserRecord:
    """Cached mirror of a user residing on a biometric device."""

    id: str
    device_id: str
    user_id: str
    name: str = ""
    card: str = ""
    privilege: int = 0
    password: str = ""
    enabled: bool = True
    employee_id: str = ""
    last_synced_at: Optional[str] = None
    sync_status: str = "unknown"
    conflict: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=_utcnow_iso)


@dataclass
class DeviceBackup:
    id: str
    device_id: str
    backup_path: str
    backup_type: str = "full"
    includes_users: bool = True
    includes_templates: bool = True
    includes_attendance: bool = True
    includes_config: bool = True
    size_bytes: int = 0
    checksum: str = ""
    encrypted: bool = True
    created_at: str = field(default_factory=_utcnow_iso)
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FirmwareJob:
    id: str
    device_id: str
    action: str
    from_version: str = ""
    to_version: str = ""
    package_path: str = ""
    status: str = "pending"
    message: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    completed_at: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessLevel:
    """Future-ready access-control level (door controllers)."""

    id: str
    name: str
    organization: str = ""
    branch: str = ""
    schedule_json: str = "{}"
    door_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DoorController:
    """Future-ready door controller inventory with durable storage."""

    id: str
    name: str
    device_id: str = ""
    ip_address: str = ""
    port: int = 4370
    organization: str = ""
    branch: str = ""
    building: str = ""
    floor: str = ""
    gate: str = ""
    status: str = "unknown"
    created_at: str = field(default_factory=_utcnow_iso)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessSchedule:
    id: str
    name: str
    timezone: str = "Asia/Kolkata"
    rules_json: str = "[]"
    created_at: str = field(default_factory=_utcnow_iso)


@dataclass
class NotificationEvent:
    id: str
    event_type: str
    title: str
    message: str
    severity: str = "warning"
    device_id: str = ""
    channel: str = "cloud"
    delivered: bool = False
    created_at: str = field(default_factory=_utcnow_iso)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReportRequest:
    id: str
    report_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result_path: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    completed_at: Optional[str] = None
