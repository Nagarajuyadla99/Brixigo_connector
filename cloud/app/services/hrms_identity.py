"""Stable Cloud ↔ HRMS employee identity helpers."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, HrmsAttendanceOutbox, HrmsSyncStatus
from app.services.employees import reported_person_name

logger = logging.getLogger("brixigo.cloud.hrms")


def resolve_hrms_person_name(
    *,
    roster_name: str,
    snapshot_name: str,
    employee_code: str,
) -> str:
    """Pick the person name sent to HRMS for a punch."""
    roster = reported_person_name(roster_name, employee_code)
    snapshot = reported_person_name(snapshot_name, employee_code)
    if roster and snapshot and roster.casefold() != snapshot.casefold():
        # The punch snapshot is authoritative for this event — roster may be stale.
        logger.warning(
            "Roster/punch name mismatch for employee_code=%s: roster=%r snapshot=%r — using snapshot",
            employee_code,
            roster,
            snapshot,
        )
        return snapshot
    return roster or snapshot


def _extra_hrms_employee_id(employee: Employee | None) -> str:
    if not employee:
        return ""
    extra = employee.extra_json if isinstance(employee.extra_json, dict) else {}
    return str(extra.get("hrms_employee_id") or "").strip()


def canonical_hrms_employee_id(
    db: Session,
    *,
    organisation_id: str,
    employee_code: str,
    employee: Employee | None = None,
) -> str:
    """Return the stable HRMS employee id previously accepted for this device code."""
    stored = _extra_hrms_employee_id(employee)
    if stored:
        return stored
    code = str(employee_code or "").strip()
    if not organisation_id or not code:
        return ""
    rows = db.scalars(
        select(HrmsAttendanceOutbox.hrms_employee_id).where(
            HrmsAttendanceOutbox.organisation_id == organisation_id,
            HrmsAttendanceOutbox.employee_code == code,
            HrmsAttendanceOutbox.status == HrmsSyncStatus.SYNCED.value,
            HrmsAttendanceOutbox.hrms_employee_id != "",
        )
    ).all()
    counts = Counter(str(row or "").strip() for row in rows if str(row or "").strip())
    if not counts:
        return ""
    canonical, votes = counts.most_common(1)[0]
    if len(counts) > 1:
        logger.warning(
            "Multiple HRMS employee ids seen for org=%s code=%s: %s — using %s (%s votes)",
            organisation_id,
            code,
            dict(counts),
            canonical,
            votes,
        )
    return canonical


def persist_hrms_mapping(
    db: Session,
    employee: Employee | None,
    *,
    hrms_employee_id: str,
    hrms_organisation_id: str = "",
) -> None:
    """Remember the HRMS ids accepted for a roster row."""
    if not employee:
        return
    hid = str(hrms_employee_id or "").strip()
    if not hid:
        return
    extra = dict(employee.extra_json or {})
    current = str(extra.get("hrms_employee_id") or "").strip()
    if current and current != hid:
        logger.warning(
            "Refusing to overwrite HRMS mapping for employee_code=%s: stored=%s incoming=%s",
            employee.employee_code,
            current,
            hid,
        )
        return
    changed = False
    if not current:
        extra["hrms_employee_id"] = hid
        changed = True
    org_id = str(hrms_organisation_id or "").strip()
    if org_id and extra.get("hrms_organisation_id") != org_id:
        extra["hrms_organisation_id"] = org_id
        changed = True
    if changed:
        employee.extra_json = extra
        employee.version += 1
        db.flush()


def validate_hrms_employee_id(
    *,
    employee_code: str,
    canonical_id: str,
    returned_id: str,
) -> tuple[bool, str]:
    """Reject HRMS responses that remap a known device code to a different employee."""
    canonical = str(canonical_id or "").strip()
    returned = str(returned_id or "").strip()
    if not canonical or not returned or canonical == returned:
        return True, ""
    return (
        False,
        f"HRMS employee_id mismatch for code {employee_code}: expected {canonical}, got {returned}",
    )


def backfill_employee_hrms_mappings(db: Session, *, organisation_id: str | None = None) -> int:
    """Populate employee.extra_json hrms ids from synced outbox history."""
    stmt = select(Employee)
    if organisation_id:
        stmt = stmt.where(Employee.organisation_id == organisation_id)
    updated = 0
    for employee in db.scalars(stmt).all():
        if _extra_hrms_employee_id(employee):
            continue
        canonical = canonical_hrms_employee_id(
            db,
            organisation_id=employee.organisation_id,
            employee_code=employee.employee_code,
            employee=employee,
        )
        if canonical:
            persist_hrms_mapping(db, employee, hrms_employee_id=canonical)
            updated += 1
    return updated
