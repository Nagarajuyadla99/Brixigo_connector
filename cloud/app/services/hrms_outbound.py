"""Asynchronous outbound attendance sync: Cloud outbox → Brixigo.com.

Device ingest never waits on this path. Failures stay in the outbox and retry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AttendancePunch,
    Employee,
    HrmsAttendanceOutbox,
    HrmsSyncStatus,
    Organisation,
)
from app.services.employees import reported_person_name
from app.services.hrms_identity import (
    backfill_employee_hrms_mappings,
    canonical_hrms_employee_id,
    persist_hrms_mapping,
    validate_hrms_employee_id,
)
from app.services.hrms_payload import build_hrms_punch, organisation_external_code

logger = logging.getLogger("brixigo.cloud.hrms")

TERMINAL_STATUSES = {
    HrmsSyncStatus.SYNCED.value,
}
UNMATCHED_STATUSES = {
    HrmsSyncStatus.ORGANISATION_UNMATCHED.value,
    HrmsSyncStatus.EMPLOYEE_UNMATCHED.value,
}


def _backoff(attempts: int) -> datetime:
    minutes = min(60, 2 ** max(0, attempts - 1))
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def enqueue_punch(db: Session, punch: AttendancePunch, organisation: Organisation | None = None) -> HrmsAttendanceOutbox:
    existing = db.scalar(select(HrmsAttendanceOutbox).where(HrmsAttendanceOutbox.punch_id == punch.id))
    if existing:
        return existing
    org = organisation or db.get(Organisation, punch.organisation_id)
    emp = db.scalar(
        select(Employee).where(
            Employee.organisation_id == punch.organisation_id,
            Employee.employee_code == punch.device_employee_code,
        )
    )
    meta = punch.metadata_json or {}
    name = ""
    if emp:
        name = reported_person_name(emp.name, punch.device_employee_code)
    if not name:
        name = reported_person_name(meta.get("employee_name"), punch.device_employee_code)
    row = HrmsAttendanceOutbox(
        punch_id=punch.id,
        organisation_id=punch.organisation_id,
        organisation_code=organisation_external_code(org) if org else "",
        employee_code=str(punch.device_employee_code or ""),
        employee_name=name,
        status=HrmsSyncStatus.PENDING.value,
        next_attempt_at=datetime.now(timezone.utc),
        payload_json=build_hrms_punch(punch, organisation=org, employee=emp, db=db) if org else {},
    )
    db.add(row)
    db.flush()
    return row


def backfill_pending(db: Session, *, limit: int = 2000) -> int:
    existing_ids = set(db.scalars(select(HrmsAttendanceOutbox.punch_id)).all())
    punches = db.scalars(select(AttendancePunch).order_by(AttendancePunch.created_at.asc()).limit(limit * 2)).all()
    created = 0
    orgs = {o.id: o for o in db.scalars(select(Organisation)).all()}
    for punch in punches:
        if punch.id in existing_ids:
            continue
        enqueue_punch(db, punch, orgs.get(punch.organisation_id))
        created += 1
        if created >= limit:
            break
    return created


def _sign(api_key: str, timestamp: str, body: bytes) -> str:
    message = f"{timestamp}.".encode("utf-8") + body
    return hmac.new(api_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _apply_result(
    row: HrmsAttendanceOutbox,
    item: dict[str, Any],
    *,
    db: Session | None = None,
    employee: Employee | None = None,
) -> None:
    status = str(item.get("status") or "").strip().lower()
    row.last_reason = str(item.get("reason") or "")[:128]
    row.hrms_event_id = str(item.get("event_id") or row.hrms_event_id or "")
    returned_employee_id = str(item.get("employee_id") or "").strip()
    row.hrms_organisation_id = str(item.get("organisation_id") or row.hrms_organisation_id or "")
    row.last_error = ""
    canonical_id = ""
    if db is not None:
        canonical_id = canonical_hrms_employee_id(
            db,
            organisation_id=row.organisation_id,
            employee_code=row.employee_code,
            employee=employee,
        )
    ok, mismatch = validate_hrms_employee_id(
        employee_code=row.employee_code,
        canonical_id=canonical_id,
        returned_id=returned_employee_id,
    )
    if not ok and status in {"synced", "accepted", "duplicate", "already_synced"}:
        row.status = HrmsSyncStatus.FAILED.value
        row.last_error = mismatch[:2000]
        row.next_attempt_at = _backoff(max(row.attempts, 1))
        logger.error("HRMS identity mismatch: %s", mismatch)
        return
    row.hrms_employee_id = returned_employee_id or row.hrms_employee_id or canonical_id
    if status in {"synced", "accepted"}:
        row.status = HrmsSyncStatus.SYNCED.value
        row.synced_at = datetime.now(timezone.utc)
        row.next_attempt_at = None
        if db is not None and employee is not None and returned_employee_id:
            persist_hrms_mapping(
                db,
                employee,
                hrms_employee_id=returned_employee_id,
                hrms_organisation_id=row.hrms_organisation_id,
            )
    elif status in {"duplicate", "already_synced"}:
        row.status = HrmsSyncStatus.SYNCED.value
        row.synced_at = row.synced_at or datetime.now(timezone.utc)
        row.next_attempt_at = None
        row.last_reason = row.last_reason or "duplicate"
        if db is not None and employee is not None and returned_employee_id:
            persist_hrms_mapping(
                db,
                employee,
                hrms_employee_id=returned_employee_id,
                hrms_organisation_id=row.hrms_organisation_id,
            )
    elif status in {"organisation_unmatched", "org_unmatched"}:
        row.status = HrmsSyncStatus.ORGANISATION_UNMATCHED.value
        row.last_error = str(item.get("action") or item.get("reason") or "Organisation not mapped")
        row.next_attempt_at = _backoff(max(row.attempts, 3))
    elif status in {"employee_unmatched", "emp_unmatched"}:
        row.status = HrmsSyncStatus.EMPLOYEE_UNMATCHED.value
        row.last_error = str(item.get("action") or item.get("reason") or "Employee not mapped")
        row.next_attempt_at = _backoff(max(row.attempts, 3))
    elif status == "invalid":
        row.status = HrmsSyncStatus.INVALID.value
        row.last_error = str(item.get("reason") or "Invalid payload")
        row.next_attempt_at = None
    else:
        row.status = HrmsSyncStatus.FAILED.value
        row.last_error = str(item.get("reason") or item.get("detail") or "Unexpected HRMS response")
        row.next_attempt_at = _backoff(row.attempts)


def _mark_failed(rows: list[HrmsAttendanceOutbox], message: str, *, retry: bool = True) -> None:
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = HrmsSyncStatus.FAILED.value
        row.last_error = message[:2000]
        row.last_attempt_at = now
        row.next_attempt_at = _backoff(row.attempts) if retry else None


def _due_rows(db: Session, *, limit: int, organisation_id: str | None = None) -> list[HrmsAttendanceOutbox]:
    now = datetime.now(timezone.utc)
    stmt = select(HrmsAttendanceOutbox).where(
        HrmsAttendanceOutbox.status.in_(
            [
                HrmsSyncStatus.PENDING.value,
                HrmsSyncStatus.FAILED.value,
                HrmsSyncStatus.SENDING.value,
                HrmsSyncStatus.ORGANISATION_UNMATCHED.value,
                HrmsSyncStatus.EMPLOYEE_UNMATCHED.value,
            ]
        )
    ).order_by(HrmsAttendanceOutbox.created_at.asc()).limit(limit)
    if organisation_id:
        stmt = stmt.where(HrmsAttendanceOutbox.organisation_id == organisation_id)
    rows = list(db.scalars(stmt).all())
    due: list[HrmsAttendanceOutbox] = []
    for row in rows:
        if row.status == HrmsSyncStatus.SYNCED.value:
            continue
        if row.next_attempt_at and row.next_attempt_at.replace(tzinfo=timezone.utc) > now:
            continue
        due.append(row)
    return due


def sync_pending(
    db: Session,
    settings: Settings | None = None,
    *,
    organisation_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    settings = settings or get_settings()
    stats = {
        "queued": 0,
        "synced": 0,
        "duplicate": 0,
        "organisation_unmatched": 0,
        "employee_unmatched": 0,
        "failed": 0,
        "invalid": 0,
        "skipped": 0,
    }
    if not settings.hrms_enabled or not str(settings.hrms_base_url or "").strip():
        stats["skipped"] = 1
        return stats
    if not str(settings.hrms_api_key or "").strip():
        logger.warning("HRMS sync enabled but BRIXIGO_CLOUD_HRMS_API_KEY is empty")
        stats["skipped"] = 1
        return stats

    backfill_pending(db, limit=max(settings.hrms_batch_size * 5, 200))
    backfill_employee_hrms_mappings(db, organisation_id=organisation_id)
    rows = _due_rows(db, limit=settings.hrms_batch_size, organisation_id=organisation_id)
    stats["queued"] = len(rows)
    if not rows:
        return stats

    punches_by_id = {
        p.id: p
        for p in db.scalars(
            select(AttendancePunch).where(AttendancePunch.id.in_([r.punch_id for r in rows]))
        ).all()
    }
    orgs = {
        o.id: o
        for o in db.scalars(
            select(Organisation).where(Organisation.id.in_({r.organisation_id for r in rows}))
        ).all()
    }
    emps = list(
        db.scalars(
            select(Employee).where(Employee.organisation_id.in_({r.organisation_id for r in rows}))
        ).all()
    )
    emp_map = {(e.organisation_id, str(e.employee_code)): e for e in emps}

    payload_items: list[dict[str, Any]] = []
    send_rows: list[HrmsAttendanceOutbox] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        punch = punches_by_id.get(row.punch_id)
        org = orgs.get(row.organisation_id)
        if not punch or not org:
            row.status = HrmsSyncStatus.INVALID.value
            row.last_error = "Cloud punch or organisation missing"
            row.attempts += 1
            row.last_attempt_at = now
            stats["invalid"] += 1
            continue
        if row.attempts >= settings.hrms_max_attempts and row.status == HrmsSyncStatus.FAILED.value:
            stats["failed"] += 1
            continue
        emp = emp_map.get((punch.organisation_id, str(punch.device_employee_code)))
        hint = canonical_hrms_employee_id(
            db,
            organisation_id=punch.organisation_id,
            employee_code=str(punch.device_employee_code or ""),
            employee=emp,
        )
        item = build_hrms_punch(
            punch,
            organisation=org,
            employee=emp,
            db=db,
            hrms_employee_id_hint=hint,
        )
        row.payload_json = item
        row.organisation_code = item["organisation_external_code"]
        row.employee_name = item.get("employee_name") or row.employee_name
        row.status = HrmsSyncStatus.SENDING.value
        row.attempts += 1
        row.last_attempt_at = now
        payload_items.append(item)
        send_rows.append(row)
    db.flush()

    if not payload_items:
        return stats

    body = json.dumps({"punches": payload_items}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Brixigo-Cloud-Key": settings.hrms_api_key,
        "X-Brixigo-Cloud-Timestamp": timestamp,
        "X-Brixigo-Cloud-Signature": _sign(settings.hrms_api_key, timestamp, body),
    }
    url = settings.hrms_base_url.rstrip("/") + "/api/integrations/brixigo-cloud/attendance/batch"
    close_client = False
    http = client
    if http is None:
        http = httpx.Client(timeout=settings.hrms_timeout_seconds)
        close_client = True
    try:
        response = http.post(url, headers=headers, content=body)
    except httpx.TimeoutException:
        _mark_failed(send_rows, "HRMS timeout")
        stats["failed"] += len(send_rows)
        return stats
    except httpx.HTTPError as exc:
        _mark_failed(send_rows, f"HRMS unreachable: {exc}")
        stats["failed"] += len(send_rows)
        return stats
    finally:
        if close_client:
            http.close()

    if response.status_code in {401, 403}:
        _mark_failed(send_rows, f"HRMS authentication failed ({response.status_code})", retry=True)
        stats["failed"] += len(send_rows)
        return stats
    if response.status_code >= 500:
        _mark_failed(send_rows, f"HRMS server error ({response.status_code})")
        stats["failed"] += len(send_rows)
        return stats
    if response.status_code >= 400:
        _mark_failed(send_rows, f"HRMS rejected payload ({response.status_code}): {response.text[:400]}")
        stats["failed"] += len(send_rows)
        return stats

    try:
        data = response.json()
    except Exception:
        _mark_failed(send_rows, "HRMS returned non-JSON response")
        stats["failed"] += len(send_rows)
        return stats

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or len(results) != len(send_rows):
        # Partial / unexpected shape: apply by source_punch_id when possible
        by_id = {
            str(item.get("source_punch_id")): item
            for item in (results or [])
            if isinstance(item, dict)
        }
        for row, item in zip(send_rows, payload_items):
            matched = by_id.get(str(item.get("source_punch_id")))
            emp = emp_map.get((row.organisation_id, str(row.employee_code)))
            if matched:
                _apply_result(row, matched, db=db, employee=emp)
            else:
                _mark_failed([row], "Partial batch: punch missing from HRMS response")
    else:
        for row, item in zip(send_rows, results):
            emp = emp_map.get((row.organisation_id, str(row.employee_code)))
            if isinstance(item, dict):
                _apply_result(row, item, db=db, employee=emp)
            else:
                _mark_failed([row], "Invalid per-punch result")

    for row in send_rows:
        if row.status == HrmsSyncStatus.SYNCED.value:
            if row.last_reason == "duplicate":
                stats["duplicate"] += 1
            else:
                stats["synced"] += 1
        elif row.status == HrmsSyncStatus.ORGANISATION_UNMATCHED.value:
            stats["organisation_unmatched"] += 1
        elif row.status == HrmsSyncStatus.EMPLOYEE_UNMATCHED.value:
            stats["employee_unmatched"] += 1
        elif row.status == HrmsSyncStatus.INVALID.value:
            stats["invalid"] += 1
        else:
            stats["failed"] += 1
    return stats


def outbox_for_punches(db: Session, punch_ids: list[str]) -> dict[str, HrmsAttendanceOutbox]:
    if not punch_ids:
        return {}
    rows = db.scalars(
        select(HrmsAttendanceOutbox).where(HrmsAttendanceOutbox.punch_id.in_(punch_ids))
    ).all()
    return {r.punch_id: r for r in rows}


def summarize_outbox(db: Session, organisation_id: str | None = None) -> dict[str, Any]:
    stmt = select(HrmsAttendanceOutbox)
    if organisation_id:
        stmt = stmt.where(HrmsAttendanceOutbox.organisation_id == organisation_id)
    rows = list(db.scalars(stmt).all())
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    unmatched = [
        {
            "punch_id": r.punch_id,
            "organisation_id": r.organisation_id,
            "organisation_code": r.organisation_code,
            "employee_code": r.employee_code,
            "employee_name": r.employee_name,
            "status": r.status,
            "reason": r.last_reason,
            "error": r.last_error,
            "attempts": r.attempts,
        }
        for r in rows
        if r.status in UNMATCHED_STATUSES
    ][:200]
    return {
        "total": len(rows),
        "counts": counts,
        "unmatched": unmatched,
    }
