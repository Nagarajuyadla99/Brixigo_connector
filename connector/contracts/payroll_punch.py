"""Canonical payroll punch contract shared by connector → cloud → HRMS."""

from __future__ import annotations

from typing import Any, Optional

from api.json_codec import format_api_datetime, to_json_value
from models import AttendanceRecord

PUNCH_CONTRACT_VERSION = "1.0"

# Stable verification vocabulary for Payroll / HRMS consumers
VERIFICATION_METHODS = frozenset(
    {
        "fingerprint",
        "face",
        "palm",
        "card",
        "pin",
        "password",
        "qr",
        "iris",
        "vein",
        "multi",
        "manual",
        "other",
        "not_reported",
        "unknown",
    }
)

EVENT_TYPES = frozenset({"IN", "OUT", "BREAK_OUT", "BREAK_IN", "OT_IN", "OT_OUT", "UNKNOWN"})


def normalize_event_type(value: Any, *, status: int | None = None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "CHECKIN": "IN",
        "CHECK_IN": "IN",
        "CHECKOUT": "OUT",
        "CHECK_OUT": "OUT",
    }
    raw = aliases.get(raw, raw)
    if raw in EVENT_TYPES:
        return raw
    # Vendor ``status`` bytes are not a reliable IN/OUT. Do not map 1 → OUT.
    _ = status
    return "UNKNOWN"


def normalize_verification_method(value: Any, *, punch: int | None = None) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "fp": "fingerprint",
        "finger": "fingerprint",
        "finger_print": "fingerprint",
        "rfid": "card",
        "proximity": "card",
        "password": "pin",
        "pwd": "pin",
        "faceid": "face",
        "facial": "face",
    }
    raw = aliases.get(raw, raw)
    if raw in VERIFICATION_METHODS and raw not in {"unknown", ""}:
        return raw
    # ZK punch field heuristics (varies by firmware)
    if punch is not None:
        mapping = {
            0: "password",
            1: "fingerprint",
            2: "card",
            15: "face",
        }
        if punch in mapping:
            return mapping[punch]
    # Do not invent a method. Empty / unmapped vendor values are "not reported".
    return "not_reported"


def build_idempotency_key(
    *,
    manufacturer: str,
    device_id: str,
    employee_code: str,
    punch_time: str,
    event_type: str,
) -> str:
    return f"{manufacturer}:{device_id}:{employee_code}:{punch_time}:{event_type}"


def build_payroll_punch(
    record: AttendanceRecord,
    *,
    terminal_code: str,
    manufacturer: str = "",
    device_name: str = "",
    organisation_id: str = "",
    connector_id: str = "",
    branch: str = "",
    timezone: str = "",
    employee_name: str = "",
) -> dict[str, Any]:
    """
    Build the stable JSON object consumed by Brixigo Cloud / Payroll.

    Payroll must NEVER recalculate from vendor raw blobs — only from these fields.
    """
    meta = dict(record.raw_payload or {})
    meta.setdefault("vendor", manufacturer)
    meta.setdefault("contract_version", PUNCH_CONTRACT_VERSION)

    event = normalize_event_type(record.event_type)
    method = normalize_verification_method(record.verification_method)
    tz_name = timezone or str(meta.get("device_timezone") or "") or None
    person_name = str(
        employee_name
        or meta.get("employee_name")
        or meta.get("user_name")
        or meta.get("name")
        or ""
    ).strip()
    if person_name:
        meta["employee_name"] = person_name

    payload = {
        "contract_version": PUNCH_CONTRACT_VERSION,
        "device_employee_code": str(record.device_employee_code),
        "punch_time": format_api_datetime(record.punch_time, default_timezone=tz_name),
        "event_type": event,
        "verification_method": method,
        "biometric_reference": record.biometric_reference or None,
        "idempotency_key": record.idempotency_key,
        "terminal_code": terminal_code,
        "organisation_id": organisation_id or None,
        "connector_id": connector_id or None,
        "device_id": record.device_id,
        "device_name": device_name or None,
        "branch": branch or None,
        "manufacturer": manufacturer or meta.get("vendor") or None,
        "payroll_ready": True,
        "source": "brixigo-connector",
        "metadata": meta,
    }
    if person_name:
        payload["employee_name"] = person_name
    return to_json_value(
        payload,
        default_timezone=tz_name,
    )


def validate_payroll_punch(punch: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(punch, dict):
        return False, "punch must be an object"
    emp = str(punch.get("device_employee_code") or punch.get("employee_code") or "").strip()
    if not emp:
        return False, "device_employee_code required"
    punch_time = str(punch.get("punch_time") or "").strip()
    if not punch_time:
        return False, "punch_time required"
    event = normalize_event_type(punch.get("event_type"))
    if event not in EVENT_TYPES:
        return False, "event_type invalid"
    return True, "ok"
