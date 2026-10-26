"""Employee management API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas.admin import EmployeeBulkAssign, EmployeeCreate, EmployeeOut, EmployeeTransfer, EmployeeUpdate
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.security.tenant import assert_org_access
from app.services import employees
from app.services.employees import reported_person_name
from app.services.names import NameResolver, enrich_employee, resolve_branch, resolve_organisation

router = APIRouter(prefix="/employees", tags=["Employees"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "employees"):
        raise HTTPException(status_code=403, detail="Employee access denied")
    return user


def _out(emp, names: NameResolver, stats: dict | None = None) -> EmployeeOut:
    row = enrich_employee(EmployeeOut.model_validate(emp).model_dump(), names)
    extra = emp.extra_json if isinstance(emp.extra_json, dict) else {}
    public_name = reported_person_name(emp.name, emp.employee_code)
    row["name"] = public_name
    row["source"] = str(extra.get("source") or "")
    row["device_user_id"] = str(emp.employee_code or "")
    row["name_reported"] = bool(public_name)
    key = (emp.organisation_id, str(emp.employee_code))
    punch = (stats or {}).get(key) or {}
    row["punch_count"] = int(punch.get("punch_count") or 0)
    row["last_punch_at"] = str(punch.get("last_punch_at") or "")
    return EmployeeOut(**row)


def _require_employee(db: Session, user: User, employee_id: str):
    emp = employees.get_employee(db, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    assert_org_access(user, emp.organisation_id)
    return emp


@router.get("", response_model=list[EmployeeOut])
def list_emps(
    search: str | None = None,
    branch_id: str | None = None,
    limit: int = 500,
    offset: int = 0,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    names = NameResolver(db)
    employees.sanitize_generated_employee_names(db, organisation_id=tenant_filter_org_id(user))
    employees.import_employees_from_punches(db, organisation_id=tenant_filter_org_id(user))
    stats = employees.punch_stats_by_code(db, organisation_id=tenant_filter_org_id(user))
    rows = employees.list_employees(
        db, organisation_id=tenant_filter_org_id(user), branch_id=branch_id, search=search, limit=limit, offset=offset,
    )
    return [_out(e, names, stats) for e in rows]


@router.post("/sync-from-attendance")
def sync_from_attendance(user: User = Depends(_require), db: Session = Depends(get_db)):
    result = employees.import_employees_from_punches(db, organisation_id=tenant_filter_org_id(user))
    return {"ok": True, **result}


@router.post("", response_model=EmployeeOut)
def create_emp(body: EmployeeCreate, user: User = Depends(_require), db: Session = Depends(get_db)):
    org = resolve_organisation(db, body.organisation_name)
    if not org:
        raise HTTPException(status_code=404, detail=f"Organisation not found: {body.organisation_name}")
    tenant_org = tenant_filter_org_id(user)
    if tenant_org and org.id != tenant_org:
        raise HTTPException(status_code=403, detail="Access denied")
    branch_id = None
    if body.branch_name:
        branch = resolve_branch(db, org.id, body.branch_name)
        if not branch:
            raise HTTPException(status_code=404, detail=f"Branch not found: {body.branch_name}")
        branch_id = branch.id
    try:
        emp = employees.create_employee(
            db,
            organisation_id=org.id,
            employee_code=body.employee_code,
            name=body.name,
            branch_id=branch_id,
            department_id=body.department_id,
            designation=body.designation,
            shift=body.shift,
            card_number=body.card_number,
            pin=body.pin,
            status=body.status,
            privilege=body.privilege,
            device_ids=body.device_ids,
            extra_json=body.extra_json,
            actor_id=user.id,
        )
        return _out(emp, NameResolver(db))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_emp(employee_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    emp = _require_employee(db, user, employee_id)
    return _out(emp, NameResolver(db))


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_emp(
    employee_id: str,
    body: EmployeeUpdate,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    _require_employee(db, user, employee_id)
    emp = employees.update_employee(
        db, employee_id, actor_id=user.id,
        name=body.name, branch_id=body.branch_id, department_id=body.department_id,
        designation=body.designation, shift=body.shift, card_number=body.card_number,
        pin=body.pin, status=body.status, privilege=body.privilege,
        device_ids=body.device_ids, extra_json=body.extra_json,
    )
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _out(emp, NameResolver(db))


@router.delete("/{employee_id}")
def delete_emp(employee_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    _require_employee(db, user, employee_id)
    if not employees.delete_employee(db, employee_id, actor_id=user.id):
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"ok": True}


@router.post("/{employee_id}/sync")
def sync_emp(employee_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    emp = employees.get_employee(db, employee_id)
    if not emp:
        return {"events_queued": 0}
    assert_org_access(user, emp.organisation_id)
    events = employees.sync_employee(db, employee_id, actor_id=user.id)
    return {"events_queued": len(events)}


@router.post("/bulk-assign", response_model=list[EmployeeOut])
def bulk_assign(body: EmployeeBulkAssign, user: User = Depends(_require), db: Session = Depends(get_db)):
    tenant_org = tenant_filter_org_id(user)
    for eid in body.employee_ids:
        emp = employees.get_employee(db, eid)
        if not emp:
            continue
        assert_org_access(user, emp.organisation_id)
    names = NameResolver(db)
    rows = employees.bulk_assign_devices(
        db,
        employee_ids=body.employee_ids,
        device_ids=body.device_ids,
        actor_id=user.id,
        organisation_id=tenant_org,
    )
    return [_out(e, names) for e in rows]


@router.post("/{employee_id}/transfer", response_model=EmployeeOut)
def transfer_emp(
    employee_id: str,
    body: EmployeeTransfer,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    _require_employee(db, user, employee_id)
    emp = employees.transfer_employee(
        db, employee_id, branch_id=body.branch_id, department_id=body.department_id, actor_id=user.id,
    )
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _out(emp, NameResolver(db))
