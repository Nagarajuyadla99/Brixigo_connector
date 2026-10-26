"""Employee CRUD and sync event propagation.

Name source of truth
--------------------
* ``source=directory`` (admin/HR create or rename) is authoritative.
  Device sync must not overwrite that display name.
* ``source`` in {device_punch, device_user, attendance_import} is
  device-managed. Later biometric names (Sai → Sai Kumar) update the row.
* Identity key is ``(organisation_id, employee_code)`` where
  ``employee_code`` is the device user id. Never match user id globally.
* Never persist ``Employee {code}`` as a name. Store the real name or blank.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AttendancePunch, Connector, Device, Employee, EmployeeSyncEvent, new_id
from app.services.common import encrypt_str, write_audit

DEVICE_ROSTER_SOURCES = frozenset({"device_punch", "attendance_import", "device_user"})


def placeholder_employee_name(code: str) -> str:
    """Deprecated display label — never persist this as employee.name."""
    text = str(code or "").strip() or "?"
    return f"Employee {text}"


def unnamed_employee_label(code: str) -> str:
    text = str(code or "").strip() or "?"
    return f"Device user {text}"


def is_generated_name(name: str | None, code: str | None) -> bool:
    person = str(name or "").strip()
    code_text = str(code or "").strip()
    if not person:
        return True
    if code_text and person.lower() == code_text.lower():
        return True
    if code_text and person.lower() == placeholder_employee_name(code_text).lower():
        return True
    return person.isdigit()


def reported_person_name(value: Any, code: str | None = None) -> str:
    """Return a real person name, ignoring blank values and device user IDs."""
    name = str(value or "").strip()
    if not name or is_generated_name(name, code):
        return ""
    return name


def _roster_source(emp: Employee) -> str:
    extra = emp.extra_json if isinstance(emp.extra_json, dict) else {}
    return str(extra.get("source") or "").strip()


def is_device_roster_employee(emp: Employee) -> bool:
    if _roster_source(emp) in DEVICE_ROSTER_SOURCES:
        return True
    return is_generated_name(emp.name, emp.employee_code)


def _connectors_for_devices(db: Session, org_id: str, device_ids: list[str]) -> list[Connector]:
    if not device_ids:
        return []
    devices = db.scalars(
        select(Device).where(
            Device.organisation_id == org_id,
            Device.id.in_(device_ids),
            Device.connector_id.isnot(None),
        )
    ).all()
    connector_ids = {d.connector_id for d in devices if d.connector_id}
    if not connector_ids:
        return []
    return list(
        db.scalars(
            select(Connector).where(
                Connector.id.in_(connector_ids),
                Connector.is_active.is_(True),
            )
        )
    )


def _enqueue_sync(
    db: Session,
    *,
    organisation_id: str,
    employee: Employee,
    action: str,
    connector: Connector,
) -> EmployeeSyncEvent:
    event = EmployeeSyncEvent(
        id=new_id(),
        organisation_id=organisation_id,
        connector_id=connector.id,
        employee_id=employee.id,
        action=action,
        payload_json={
            "employee_code": employee.employee_code,
            "name": employee.name,
            "branch_id": employee.branch_id,
            "department_id": employee.department_id,
            "designation": employee.designation,
            "shift": employee.shift,
            "card_number": employee.card_number,
            "status": employee.status,
            "privilege": employee.privilege,
            "device_ids": employee.device_ids_json,
            "version": employee.version,
        },
        status="pending",
    )
    db.add(event)
    return event


def _propagate_to_connectors(
    db: Session,
    employee: Employee,
    action: str,
) -> list[EmployeeSyncEvent]:
    connectors = _connectors_for_devices(db, employee.organisation_id, employee.device_ids_json or [])
    events = []
    for conn in connectors:
        events.append(
            _enqueue_sync(db, organisation_id=employee.organisation_id, employee=employee, action=action, connector=conn)
        )
    if events:
        db.flush()
    return events


def create_employee(
    db: Session,
    *,
    organisation_id: str,
    employee_code: str,
    name: str,
    branch_id: str | None = None,
    department_id: str | None = None,
    designation: str = "",
    shift: str = "",
    card_number: str = "",
    pin: str | None = None,
    status: str = "active",
    privilege: int = 0,
    device_ids: list[str] | None = None,
    extra_json: dict | None = None,
    actor_id: str = "",
) -> Employee:
    existing = db.scalar(
        select(Employee).where(
            Employee.organisation_id == organisation_id,
            Employee.employee_code == employee_code,
        )
    )
    if existing:
        if is_device_roster_employee(existing):
            extra = dict(existing.extra_json or {})
            extra["source"] = "directory"
            return update_employee(
                db,
                existing.id,
                actor_id=actor_id,
                name=name,
                branch_id=branch_id if branch_id is not None else existing.branch_id,
                department_id=department_id if department_id is not None else existing.department_id,
                designation=designation,
                shift=shift,
                card_number=card_number,
                pin=pin,
                status=status,
                privilege=privilege,
                device_ids=device_ids if device_ids is not None else existing.device_ids_json,
                extra_json=extra,
            )
        raise ValueError(f"Employee code {employee_code} already exists")

    extra_json = dict(extra_json or {})
    extra_json.setdefault("source", "directory")
    emp = Employee(
        id=new_id(),
        organisation_id=organisation_id,
        branch_id=branch_id,
        department_id=department_id,
        employee_code=employee_code,
        name=name,
        designation=designation,
        shift=shift,
        card_number=card_number,
        pin_enc=encrypt_str(pin or ""),
        status=status,
        privilege=privilege,
        device_ids_json=device_ids or [],
        extra_json=extra_json,
    )
    db.add(emp)
    db.flush()
    _propagate_to_connectors(db, emp, "create")
    write_audit(
        db,
        organisation_id=organisation_id,
        actor_id=actor_id,
        action="employee.create",
        resource_type="employee",
        resource_id=emp.id,
        message=f"Created employee {employee_code}",
    )
    return emp


def update_employee(
    db: Session,
    employee_id: str,
    *,
    actor_id: str = "",
    **fields,
) -> Employee | None:
    emp = db.get(Employee, employee_id)
    if not emp:
        return None

    if "name" in fields and fields["name"] is not None:
        emp.name = fields["name"]
    if "branch_id" in fields:
        emp.branch_id = fields["branch_id"]
    if "department_id" in fields:
        emp.department_id = fields["department_id"]
    if "designation" in fields and fields["designation"] is not None:
        emp.designation = fields["designation"]
    if "shift" in fields and fields["shift"] is not None:
        emp.shift = fields["shift"]
    if "card_number" in fields and fields["card_number"] is not None:
        emp.card_number = fields["card_number"]
    if "pin" in fields and fields["pin"] is not None:
        emp.pin_enc = encrypt_str(fields["pin"])
    if "status" in fields and fields["status"] is not None:
        emp.status = fields["status"]
    if "privilege" in fields and fields["privilege"] is not None:
        emp.privilege = fields["privilege"]
    if "device_ids" in fields and fields["device_ids"] is not None:
        emp.device_ids_json = fields["device_ids"]
    if "extra_json" in fields and fields["extra_json"] is not None:
        emp.extra_json = fields["extra_json"]

    emp.version += 1
    db.flush()
    _propagate_to_connectors(db, emp, "update")
    write_audit(
        db,
        organisation_id=emp.organisation_id,
        actor_id=actor_id,
        action="employee.update",
        resource_type="employee",
        resource_id=emp.id,
        message=f"Updated employee {emp.employee_code}",
    )
    return emp


def delete_employee(db: Session, employee_id: str, *, actor_id: str = "") -> bool:
    emp = db.get(Employee, employee_id)
    if not emp:
        return False
    _propagate_to_connectors(db, emp, "delete")
    org_id = emp.organisation_id
    code = emp.employee_code
    db.delete(emp)
    db.flush()
    write_audit(
        db,
        organisation_id=org_id,
        actor_id=actor_id,
        action="employee.delete",
        resource_type="employee",
        resource_id=employee_id,
        message=f"Deleted employee {code}",
    )
    return True


def get_employee(db: Session, employee_id: str) -> Employee | None:
    return db.get(Employee, employee_id)


def list_employees(
    db: Session,
    *,
    organisation_id: str | None = None,
    branch_id: str | None = None,
    search: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[Employee]:
    q = select(Employee).order_by(Employee.name)
    if organisation_id:
        q = q.where(Employee.organisation_id == organisation_id)
    if branch_id:
        q = q.where(Employee.branch_id == branch_id)
    if search:
        pattern = f"%{search}%"
        q = q.where((Employee.name.ilike(pattern)) | (Employee.employee_code.ilike(pattern)))
    return list(db.scalars(q.offset(offset).limit(limit)))


def bulk_assign_devices(
    db: Session,
    *,
    employee_ids: list[str],
    device_ids: list[str],
    actor_id: str = "",
    organisation_id: str | None = None,
) -> list[Employee]:
    updated = []
    for eid in employee_ids:
        emp = db.get(Employee, eid)
        if not emp:
            continue
        if organisation_id and emp.organisation_id != organisation_id:
            continue
        merged = list(set((emp.device_ids_json or []) + device_ids))
        emp.device_ids_json = merged
        emp.version += 1
        _propagate_to_connectors(db, emp, "assign")
        updated.append(emp)
    if updated:
        db.flush()
        write_audit(
            db,
            organisation_id=updated[0].organisation_id,
            actor_id=actor_id,
            action="employee.bulk_assign",
            message=f"Assigned {len(device_ids)} devices to {len(updated)} employees",
            details={"employee_ids": employee_ids, "device_ids": device_ids},
        )
    return updated


def transfer_employee(
    db: Session,
    employee_id: str,
    *,
    branch_id: str | None = None,
    department_id: str | None = None,
    actor_id: str = "",
) -> Employee | None:
    return update_employee(
        db,
        employee_id,
        actor_id=actor_id,
        branch_id=branch_id,
        department_id=department_id,
    )


def get_by_code(db: Session, organisation_id: str, employee_code: str) -> Employee | None:
    return db.scalar(
        select(Employee).where(
            Employee.organisation_id == organisation_id,
            Employee.employee_code == str(employee_code or "").strip(),
        )
    )


def ensure_employee_from_device(
    db: Session,
    *,
    organisation_id: str,
    employee_code: str,
    name: str = "",
    branch_id: str | None = None,
    device_id: str | None = None,
    source: str = "device_punch",
    extra: dict[str, Any] | None = None,
) -> Employee | None:
    """Create or refresh a roster row from a biometric punch / device user.

    Does not push the person back to connectors — they already exist on the terminal.
    """
    code = str(employee_code or "").strip()
    if not organisation_id or not code:
        return None
    person = reported_person_name(name, code)
    emp = get_by_code(db, organisation_id, code)
    payload = dict(extra or {})
    payload.setdefault("source", source)
    if emp:
        changed = False
        extra_json = dict(emp.extra_json or {})
        extra_json.setdefault("source", source)
        for key, value in payload.items():
            if key == "source" and extra_json.get("source") == "directory":
                continue
            if value in (None, ""):
                extra_json.setdefault(key, value)
                continue
            extra_json.setdefault(key, value)
        if extra_json != (emp.extra_json or {}):
            emp.extra_json = extra_json
            changed = True
        if person and emp.name != person:
            source_now = str(extra_json.get("source") or "")
            device_managed = source_now in DEVICE_ROSTER_SOURCES and source_now != "directory"
            if is_generated_name(emp.name, emp.employee_code) or device_managed:
                emp.name = person
                changed = True
        if branch_id and not emp.branch_id:
            emp.branch_id = branch_id
            changed = True
        if device_id:
            devices = list(emp.device_ids_json or [])
            if device_id not in devices:
                devices.append(device_id)
                emp.device_ids_json = devices
                changed = True
        if changed:
            emp.version += 1
            db.flush()
        return emp

    emp = Employee(
        id=new_id(),
        organisation_id=organisation_id,
        branch_id=branch_id,
        employee_code=code,
        name=person,
        status="active",
        device_ids_json=[device_id] if device_id else [],
        extra_json=payload,
    )
    db.add(emp)
    db.flush()
    write_audit(
        db,
        organisation_id=organisation_id,
        action="employee.import_device",
        resource_type="employee",
        resource_id=emp.id,
        message=f"Imported employee {code} from {source}",
    )
    return emp


def sanitize_generated_employee_names(
    db: Session,
    *,
    organisation_id: str | None = None,
) -> int:
    """Clear persisted 'Employee {code}' placeholders so they are not treated as names."""
    q = select(Employee)
    if organisation_id:
        q = q.where(Employee.organisation_id == organisation_id)
    changed = 0
    for emp in db.scalars(q):
        if not is_generated_name(emp.name, emp.employee_code):
            continue
        if str(emp.name or "") == "":
            continue
        emp.name = ""
        extra = dict(emp.extra_json or {})
        extra.setdefault("source", extra.get("source") or "device_punch")
        emp.extra_json = extra
        changed += 1
    if changed:
        db.flush()
    return changed


def import_employees_from_punches(
    db: Session,
    *,
    organisation_id: str | None = None,
) -> dict[str, int]:
    """Backfill the workforce roster from stored attendance punches."""
    stmt = select(AttendancePunch)
    if organisation_id:
        stmt = stmt.where(AttendancePunch.organisation_id == organisation_id)
    rows = list(db.scalars(stmt).all())
    created = 0
    updated = 0
    seen: set[tuple[str, str]] = set()
    for punch in rows:
        org_id = str(punch.organisation_id or "").strip()
        code = str(punch.device_employee_code or "").strip()
        if not org_id or not code:
            continue
        key = (org_id, code)
        meta = punch.metadata_json if isinstance(punch.metadata_json, dict) else {}
        person = reported_person_name(
            meta.get("employee_name") or meta.get("user_name") or meta.get("name"),
            code,
        )
        existing = get_by_code(db, org_id, code)
        before_id = existing.id if existing else None
        before_name = existing.name if existing else ""
        emp = ensure_employee_from_device(
            db,
            organisation_id=org_id,
            employee_code=code,
            name=person,
            device_id=punch.device_id or None,
            source="attendance_import",
            extra={"branch": meta.get("branch") or "", "device_name": meta.get("device_name") or ""},
        )
        if not emp:
            continue
        if before_id is None:
            created += 1
        elif emp.name != before_name:
            updated += 1
        seen.add(key)
    sanitized = sanitize_generated_employee_names(db, organisation_id=organisation_id)
    if created or updated or sanitized:
        db.flush()
    return {"created": created, "updated": updated, "people": len(seen), "sanitized": sanitized}


def _resolve_device_id(
    db: Session,
    *,
    organisation_id: str,
    device_id: str = "",
    terminal_code: str = "",
) -> str | None:
    code = str(terminal_code or "").strip()
    local_id = str(device_id or "").strip()
    if local_id:
        device = db.get(Device, local_id)
        if device and device.organisation_id == organisation_id:
            return device.id
    if code:
        device = db.scalar(
            select(Device).where(
                Device.organisation_id == organisation_id,
                Device.terminal_code == code,
            )
        )
        if device:
            return device.id
    return None


def import_employees_from_directory(
    db: Session,
    *,
    organisation_id: str,
    people: list[dict[str, Any]],
    branch_id: str | None = None,
) -> dict[str, int]:
    """Upsert organisation employees from connector device-user names."""
    created = 0
    updated = 0
    named = 0
    seen: set[str] = set()
    for item in people or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("employee_code") or item.get("user_id") or item.get("code") or "").strip()
        if not code or code in seen:
            continue
        person = reported_person_name(item.get("name") or item.get("employee_name"), code)
        seen.add(code)
        cloud_device = _resolve_device_id(
            db,
            organisation_id=organisation_id,
            device_id=str(item.get("device_id") or ""),
            terminal_code=str(item.get("terminal_code") or ""),
        )
        existing = get_by_code(db, organisation_id, code)
        before_id = existing.id if existing else None
        before_name = existing.name if existing else ""
        emp = ensure_employee_from_device(
            db,
            organisation_id=organisation_id,
            employee_code=code,
            name=person,
            branch_id=branch_id,
            device_id=cloud_device,
            source="device_user",
            extra={
                "branch": str(item.get("branch") or ""),
                "organization": str(item.get("organization") or item.get("organisation") or ""),
                "device_name": str(item.get("device_name") or ""),
                "card_number": str(item.get("card_number") or item.get("card") or ""),
            },
        )
        if not emp:
            continue
        if person:
            named += 1
        if before_id is None:
            created += 1
        elif emp.name != before_name:
            updated += 1
            if emp.card_number == "" and (item.get("card_number") or item.get("card")):
                emp.card_number = str(item.get("card_number") or item.get("card") or "")
    if created or updated:
        db.flush()
    return {"created": created, "updated": updated, "named": named, "people": len(seen)}


def punch_stats_by_code(
    db: Session,
    *,
    organisation_id: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    stmt = select(
        AttendancePunch.organisation_id,
        AttendancePunch.device_employee_code,
        func.count().label("punch_count"),
        func.max(AttendancePunch.punch_time).label("last_punch_at"),
    ).group_by(AttendancePunch.organisation_id, AttendancePunch.device_employee_code)
    if organisation_id:
        stmt = stmt.where(AttendancePunch.organisation_id == organisation_id)
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    for org_id, code, count, last_at in db.execute(stmt):
        stats[(str(org_id), str(code))] = {
            "punch_count": int(count or 0),
            "last_punch_at": last_at or "",
        }
    return stats


def sync_employee(db: Session, employee_id: str, *, actor_id: str = "") -> list[EmployeeSyncEvent]:
    emp = db.get(Employee, employee_id)
    if not emp:
        return []
    events = _propagate_to_connectors(db, emp, "sync")
    write_audit(
        db,
        organisation_id=emp.organisation_id,
        actor_id=actor_id,
        action="employee.sync",
        resource_type="employee",
        resource_id=emp.id,
        message=f"Manual sync for {emp.employee_code}",
    )
    return events
