"""Build the Cloud → Brixigo.com attendance payload from stored punches."""

from __future__ import annotations

from typing import Any

from app.models import AttendancePunch, Employee, Organisation
from app.services.employees import reported_person_name
from app.services.hrms_identity import canonical_hrms_employee_id, resolve_hrms_person_name

SOURCE_SYSTEM = "brixigo_cloud"

CLASSIFICATION_DEVICE = "DEVICE_REPORTED"
CLASSIFICATION_INFERRED = "SYSTEM_INFERRED"
CLASSIFICATION_MANUAL = "MANUAL"
CLASSIFICATION_UNKNOWN = "UNKNOWN"

_DEVICE = frozenset({"device_reported", "device", "DEVICE_REPORTED", "DEVICE"})
_INFERRED = frozenset(
    {"system_inference", "system_inferred", "SYSTEM_INFERRED", "SYSTEM_INFERENCE", "inferred"}
)


def organisation_external_code(org: Organisation) -> str:
    """Stable routing key. Display name is never used."""
    settings = org.settings_json if isinstance(org.settings_json, dict) else {}
    mapped = str(settings.get("hrms_external_code") or "").strip()
    if mapped:
        return mapped
    return str(org.code or "").strip()


def normalize_classification(value: Any, *, direction_reported: bool | None = None) -> str:
    raw = str(value or "").strip()
    if raw in _DEVICE:
        return CLASSIFICATION_DEVICE
    if raw in _INFERRED:
        return CLASSIFICATION_INFERRED
    if raw in {CLASSIFICATION_DEVICE, CLASSIFICATION_INFERRED, CLASSIFICATION_MANUAL}:
        return raw
    if raw.lower() == "manual":
        return CLASSIFICATION_MANUAL
    if direction_reported is True:
        return CLASSIFICATION_DEVICE
    if direction_reported is False:
        return CLASSIFICATION_INFERRED
    return raw.upper() if raw else CLASSIFICATION_UNKNOWN


def build_hrms_punch(
    punch: AttendancePunch,
    *,
    organisation: Organisation,
    employee: Employee | None = None,
    db=None,
    hrms_employee_id_hint: str = "",
) -> dict[str, Any]:
    meta = dict(punch.metadata_json or {})
    code = str(punch.device_employee_code or "").strip()
    roster_name = reported_person_name(employee.name, code) if employee else ""
    snapshot = reported_person_name(
        meta.get("employee_name") or meta.get("user_name") or meta.get("name"),
        code,
    )
    person_name = resolve_hrms_person_name(
        roster_name=roster_name,
        snapshot_name=snapshot,
        employee_code=code,
    )
    hint = str(hrms_employee_id_hint or "").strip()
    if not hint and db is not None:
        hint = canonical_hrms_employee_id(
            db,
            organisation_id=punch.organisation_id,
            employee_code=code,
            employee=employee,
        )
    direction = meta.get("direction_reported")
    classification = normalize_classification(
        meta.get("classification_method"),
        direction_reported=bool(direction) if direction is not None else None,
    )
    org_code = organisation_external_code(organisation)
    return {
        "source_system": SOURCE_SYSTEM,
        "source_punch_id": punch.id,
        "idempotency_key": punch.idempotency_key,
        "organisation_external_code": org_code,
        "cloud_organisation_id": punch.organisation_id,
        "employee_code": code,
        "device_user_id": code,
        "employee_name": person_name,
        "cloud_employee_id": employee.id if employee else None,
        "hrms_employee_id": hint or None,
        "punch_time": punch.punch_time,
        "timezone": organisation.timezone or meta.get("device_timezone") or "Asia/Kolkata",
        "event_type": str(punch.event_type or "UNKNOWN").upper(),
        "classification_method": classification,
        "direction_reported": bool(direction) if direction is not None else None,
        "verification_method": punch.verification_method,
        "biometric_reference": punch.biometric_reference or None,
        "device_id": punch.device_id or None,
        "device_name": meta.get("device_name"),
        "terminal_code": punch.terminal_code,
        "branch": meta.get("branch"),
        "source": meta.get("source") or "brixigo-connector",
        "raw_device_timestamp": meta.get("device_punch_time") or meta.get("timestamp"),
        "normalized_timestamp": punch.punch_time,
        "metadata": {
            "classification_method": classification,
            "classification_reason_code": meta.get("classification_reason_code"),
            "direction_reported": direction,
            "clock_corrected": meta.get("clock_corrected"),
            "contract_version": meta.get("contract_version") or "1.0",
            "manufacturer": meta.get("manufacturer"),
            "uid": meta.get("uid"),
        },
    }
