"""Payroll punch feed — read-only attendance export for HRMS / website consumers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AttendancePunch, Employee, HrmsAttendanceOutbox, HrmsSyncStatus, Organisation, User
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services.employees import reported_person_name
from app.services.hrms_outbound import outbox_for_punches, summarize_outbox, sync_pending
from app.services.hrms_identity import resolve_hrms_person_name
from app.services.hrms_payload import organisation_external_code
from app.services.punch_classifier import reclassify_inferred_punches
from app.services.punch_time import correct_unset_device_clock, format_punch_iso, rewrite_unset_clock_punches
from pydantic import BaseModel, Field

router = APIRouter(prefix="/payroll", tags=["Payroll Feed"])


def _utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _created_at_lower_bound(dt: datetime) -> str:
    """SQLite stores DateTime as 'YYYY-MM-DD HH:MM:SS[.fff]' — avoid ISO 'T' compares."""
    return _utc_naive(dt).strftime("%Y-%m-%d %H:%M:%S.%f")


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "reports") and not can_access(user.role, "employees"):
        raise HTTPException(status_code=403, detail="Payroll feed access denied")
    return user


class HrmsMappingIn(BaseModel):
    hrms_external_code: str = Field(min_length=1, max_length=64)


def _resolve_mapping_org(user: User, db: Session, organisation_id: str | None) -> Organisation:
    org_id = organisation_id or tenant_filter_org_id(user) or user.organisation_id
    if not org_id:
        raise HTTPException(status_code=400, detail="Organisation context required")
    org = db.get(Organisation, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    scoped = tenant_filter_org_id(user)
    if scoped and org.id != scoped:
        raise HTTPException(status_code=403, detail="Organisation access denied")
    return org


def _employee_map(db: Session, org_ids: set[str]) -> dict[tuple[str, str], Employee]:
    """Map (organisation_id, employee_code) → Employee for punch enrichment."""
    if not org_ids:
        return {}
    rows = db.scalars(
        select(Employee).where(Employee.organisation_id.in_(sorted(org_ids)))
    ).all()
    return {(e.organisation_id, str(e.employee_code)): e for e in rows}


def _serialize(
    punch: AttendancePunch,
    *,
    employee: Employee | None = None,
    hrms=None,
) -> dict[str, Any]:
    meta = punch.metadata_json or {}
    punch_time = punch.punch_time
    source = meta.get("device_punch_time") or punch.punch_time
    fixed = correct_unset_device_clock(source, punch.created_at)
    if fixed is not None:
        punch_time = format_punch_iso(fixed)
    matched = employee is not None
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
    classification = str(meta.get("classification_method") or "").strip()
    employee_payload = {
        "id": employee.id if employee else None,
        "name": person_name,
        "code": code,
        "device_user_id": code,
        "name_reported": bool(person_name),
    }
    return {
        "id": punch.id,
        "contract_version": meta.get("contract_version") or "1.0",
        "organisation_id": punch.organisation_id,
        "connector_id": punch.connector_id,
        "device_id": punch.device_id,
        "terminal_code": punch.terminal_code,
        "device_employee_code": punch.device_employee_code,
        "employee_code": punch.device_employee_code,
        "device_user_id": punch.device_employee_code,
        "employee_id": employee.id if employee else None,
        "employee_name": person_name,
        "employee_display_name": person_name,
        "name_reported": bool(person_name),
        "employee_matched": matched,
        "employee": employee_payload,
        "classification_method": classification,
        "punch_time": punch_time,
        "event_type": punch.event_type,
        "verification_method": punch.verification_method,
        "biometric_reference": punch.biometric_reference or None,
        "idempotency_key": punch.idempotency_key,
        "payroll_ready": bool(meta.get("payroll_ready", True)),
        "manufacturer": meta.get("manufacturer"),
        "branch": meta.get("branch")
        or ((employee.extra_json or {}).get("branch") if employee else None),
        "device_name": meta.get("device_name"),
        "source": meta.get("source") or "brixigo-connector",
        "created_at": punch.created_at.isoformat() if punch.created_at else None,
        "metadata": meta,
        "hrms_sync_status": getattr(hrms, "status", None) or "pending",
        "hrms_sync_error": getattr(hrms, "last_error", "") or "",
        "hrms_event_id": getattr(hrms, "hrms_event_id", "") or None,
        "hrms_employee_id": getattr(hrms, "hrms_employee_id", "") or None,
        "hrms_organisation_id": getattr(hrms, "hrms_organisation_id", "") or None,
    }


@router.get("/punches")
def list_payroll_punches(
    since: Optional[str] = Query(default=None, description="ISO timestamp lower bound"),
    terminal_code: Optional[str] = Query(default=None),
    employee_code: Optional[str] = Query(default=None),
    today: bool = Query(default=False, description="Restrict to punches created today (UTC)"),
    order: str = Query(default="asc", description="created_at sort: asc|desc"),
    limit: int = Query(default=500, ge=1, le=5000),
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    """
    Stable punch feed for Brixigo website / Payroll / HRMS.

    Joins each punch to the organisation employee roster on
    ``(organisation_id, device_employee_code = employee_code)`` so consumers
    receive employee_name without a second request.
    """
    rewrite_unset_clock_punches(db)
    org_id = tenant_filter_org_id(user) or user.organisation_id
    if not org_id and user.role not in {"super_admin", "platform_admin"}:
        raise HTTPException(status_code=400, detail="Organisation context required")
    if org_id:
        reclassify_inferred_punches(db, organisation_id=org_id)

    sort_desc = str(order or "").strip().lower() == "desc"
    order_clause = AttendancePunch.created_at.desc() if sort_desc else AttendancePunch.created_at.asc()
    stmt = select(AttendancePunch).order_by(order_clause).limit(limit)
    if org_id:
        stmt = stmt.where(AttendancePunch.organisation_id == org_id)
    if terminal_code:
        stmt = stmt.where(AttendancePunch.terminal_code == terminal_code)
    search = str(employee_code or "").strip()
    if search and search.replace(" ", "").isalnum() and search.isdigit():
        stmt = stmt.where(AttendancePunch.device_employee_code == search)
    if today and not since:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(AttendancePunch.created_at >= _created_at_lower_bound(start))
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            stmt = stmt.where(AttendancePunch.created_at >= _created_at_lower_bound(since_dt))
        except ValueError:
            stmt = stmt.where(AttendancePunch.punch_time >= since)

    rows = list(db.scalars(stmt).all())
    emp_by_key = _employee_map(db, {r.organisation_id for r in rows if r.organisation_id})
    hrms_by_id = outbox_for_punches(db, [r.id for r in rows])
    punches = [
        _serialize(
            r,
            employee=emp_by_key.get((r.organisation_id, str(r.device_employee_code))),
            hrms=hrms_by_id.get(r.id),
        )
        for r in rows
    ]
    if search:
        needle = search.lower()
        punches = [
            p
            for p in punches
            if needle in str(p.get("device_employee_code") or "").lower()
            or needle in str(p.get("employee_name") or "").lower()
            or needle in str(p.get("employee_display_name") or "").lower()
        ]
    matched = sum(1 for p in punches if p.get("employee_matched"))
    return {
        "ok": True,
        "contract_version": "1.0",
        "count": len(punches),
        "matched_employees": matched,
        "unmatched_employees": len(punches) - matched,
        "punches": punches,
    }


@router.get("/hrms-sync")
def hrms_sync_status(user: User = Depends(_require), db: Session = Depends(get_db)):
    org_id = tenant_filter_org_id(user) or user.organisation_id
    if not org_id and user.role not in {"super_admin", "platform_admin"}:
        raise HTTPException(status_code=400, detail="Organisation context required")
    return {"ok": True, **summarize_outbox(db, organisation_id=org_id if user.role not in {"super_admin", "platform_admin"} else None)}


@router.post("/hrms-sync/run")
def run_hrms_sync(user: User = Depends(_require), db: Session = Depends(get_db)):
    org_id = tenant_filter_org_id(user)
    stats = sync_pending(db, organisation_id=org_id)
    return {"ok": True, **stats}


@router.get("/hrms-mapping")
def get_hrms_mapping(
    organisation_id: str | None = Query(default=None),
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    org = _resolve_mapping_org(user, db, organisation_id)
    explicit = str((org.settings_json or {}).get("hrms_external_code") or "").strip()
    return {
        "ok": True,
        "cloud_organisation_id": org.id,
        "cloud_organisation_code": org.code,
        "cloud_organisation_name": org.name,
        "hrms_external_code": organisation_external_code(org),
        "mapped_explicitly": bool(explicit),
        "note": "Brixigo.com must set the same organisations.external_code. Display names are not used.",
    }


@router.put("/hrms-mapping")
def set_hrms_mapping(
    body: HrmsMappingIn,
    organisation_id: str | None = Query(default=None),
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    org = _resolve_mapping_org(user, db, organisation_id)
    code = "".join(ch for ch in body.hrms_external_code.strip() if ch.isalnum() or ch in "-_.").lower()
    if not code:
        raise HTTPException(status_code=400, detail="hrms_external_code is required")
    settings = dict(org.settings_json or {})
    settings["hrms_external_code"] = code
    org.settings_json = settings
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(HrmsAttendanceOutbox).where(HrmsAttendanceOutbox.organisation_id == org.id)
    ).all()
    for row in rows:
        row.organisation_code = code
        if row.status in {
            HrmsSyncStatus.ORGANISATION_UNMATCHED.value,
            HrmsSyncStatus.EMPLOYEE_UNMATCHED.value,
            HrmsSyncStatus.FAILED.value,
            HrmsSyncStatus.PENDING.value,
        }:
            row.status = HrmsSyncStatus.PENDING.value
            row.next_attempt_at = now
            row.last_error = ""
    return {
        "ok": True,
        "cloud_organisation_id": org.id,
        "cloud_organisation_code": org.code,
        "hrms_external_code": code,
        "outbox_reset": len(rows),
    }

