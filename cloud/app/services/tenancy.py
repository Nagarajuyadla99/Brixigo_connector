"""Multi-tenant organisation hierarchy CRUD."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Branch, Building, Department, Floor, Gate, Organisation, new_id
from app.services.common import write_audit


def _check_org_access(org_id: str, tenant_org: str | None) -> None:
    if tenant_org and org_id != tenant_org:
        raise PermissionError("Organisation access denied")


# --- Organisations ---

def create_organisation(
    db: Session,
    *,
    name: str,
    code: str,
    timezone: str = "Asia/Kolkata",
    status: str = "active",
    settings_json: dict | None = None,
    actor_id: str = "",
) -> Organisation:
    if db.scalar(select(Organisation).where(Organisation.code == code)):
        raise ValueError(f"Organisation code {code} already exists")
    org = Organisation(
        id=new_id(),
        name=name,
        code=code,
        timezone=timezone,
        status=status,
        settings_json=settings_json or {},
    )
    db.add(org)
    db.flush()
    write_audit(db, organisation_id=org.id, actor_id=actor_id, action="org.create", resource_type="organisation", resource_id=org.id)
    return org


def list_organisations(db: Session, tenant_org: str | None = None) -> list[Organisation]:
    q = select(Organisation).order_by(Organisation.name)
    if tenant_org:
        q = q.where(Organisation.id == tenant_org)
    return list(db.scalars(q))


def get_organisation(db: Session, org_id: str, tenant_org: str | None = None) -> Organisation | None:
    _check_org_access(org_id, tenant_org)
    return db.get(Organisation, org_id)


def update_organisation(db: Session, org_id: str, tenant_org: str | None = None, actor_id: str = "", **fields) -> Organisation | None:
    _check_org_access(org_id, tenant_org)
    org = db.get(Organisation, org_id)
    if not org:
        return None
    for key in ("name", "timezone", "status", "settings_json"):
        if key in fields and fields[key] is not None:
            setattr(org, key, fields[key])
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=actor_id, action="org.update", resource_type="organisation", resource_id=org_id)
    return org


def delete_organisation(db: Session, org_id: str, tenant_org: str | None = None, actor_id: str = "") -> bool:
    _check_org_access(org_id, tenant_org)
    org = db.get(Organisation, org_id)
    if not org:
        return False
    db.delete(org)
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=actor_id, action="org.delete", resource_type="organisation", resource_id=org_id)
    return True


# --- Branches ---

def create_branch(
    db: Session,
    *,
    organisation_id: str,
    name: str,
    code: str,
    address: str = "",
    status: str = "active",
    tenant_org: str | None = None,
    actor_id: str = "",
) -> Branch:
    _check_org_access(organisation_id, tenant_org)
    branch = Branch(
        id=new_id(),
        organisation_id=organisation_id,
        name=name,
        code=code,
        address=address,
        status=status,
    )
    db.add(branch)
    db.flush()
    write_audit(db, organisation_id=organisation_id, actor_id=actor_id, action="branch.create", resource_type="branch", resource_id=branch.id)
    return branch


def list_branches(db: Session, organisation_id: str | None = None, tenant_org: str | None = None) -> list[Branch]:
    q = select(Branch).order_by(Branch.name)
    if tenant_org:
        q = q.where(Branch.organisation_id == tenant_org)
    elif organisation_id:
        q = q.where(Branch.organisation_id == organisation_id)
    return list(db.scalars(q))


def update_branch(db: Session, branch_id: str, tenant_org: str | None = None, actor_id: str = "", **fields) -> Branch | None:
    branch = db.get(Branch, branch_id)
    if not branch:
        return None
    _check_org_access(branch.organisation_id, tenant_org)
    for key in ("name", "address", "status"):
        if key in fields and fields[key] is not None:
            setattr(branch, key, fields[key])
    db.flush()
    write_audit(db, organisation_id=branch.organisation_id, actor_id=actor_id, action="branch.update", resource_type="branch", resource_id=branch_id)
    return branch


def delete_branch(db: Session, branch_id: str, tenant_org: str | None = None, actor_id: str = "") -> bool:
    branch = db.get(Branch, branch_id)
    if not branch:
        return False
    _check_org_access(branch.organisation_id, tenant_org)
    org_id = branch.organisation_id
    db.delete(branch)
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=actor_id, action="branch.delete", resource_type="branch", resource_id=branch_id)
    return True


# --- Departments ---

def create_department(
    db: Session,
    *,
    organisation_id: str,
    branch_id: str,
    name: str,
    code: str = "",
    tenant_org: str | None = None,
    actor_id: str = "",
) -> Department:
    _check_org_access(organisation_id, tenant_org)
    dept = Department(id=new_id(), organisation_id=organisation_id, branch_id=branch_id, name=name, code=code)
    db.add(dept)
    db.flush()
    write_audit(db, organisation_id=organisation_id, actor_id=actor_id, action="department.create", resource_type="department", resource_id=dept.id)
    return dept


def list_departments(
    db: Session,
    organisation_id: str | None = None,
    branch_id: str | None = None,
    tenant_org: str | None = None,
) -> list[Department]:
    q = select(Department).order_by(Department.name)
    if tenant_org:
        q = q.where(Department.organisation_id == tenant_org)
    elif organisation_id:
        q = q.where(Department.organisation_id == organisation_id)
    if branch_id:
        q = q.where(Department.branch_id == branch_id)
    return list(db.scalars(q))


def update_department(db: Session, dept_id: str, tenant_org: str | None = None, actor_id: str = "", **fields) -> Department | None:
    dept = db.get(Department, dept_id)
    if not dept:
        return None
    _check_org_access(dept.organisation_id, tenant_org)
    for key in ("name", "code"):
        if key in fields and fields[key] is not None:
            setattr(dept, key, fields[key])
    db.flush()
    write_audit(db, organisation_id=dept.organisation_id, actor_id=actor_id, action="department.update", resource_type="department", resource_id=dept_id)
    return dept


def delete_department(db: Session, dept_id: str, tenant_org: str | None = None, actor_id: str = "") -> bool:
    dept = db.get(Department, dept_id)
    if not dept:
        return False
    _check_org_access(dept.organisation_id, tenant_org)
    org_id = dept.organisation_id
    db.delete(dept)
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=actor_id, action="department.delete", resource_type="department", resource_id=dept_id)
    return True


# --- Buildings ---

def create_building(
    db: Session,
    *,
    organisation_id: str,
    branch_id: str,
    name: str,
    department_id: str | None = None,
    tenant_org: str | None = None,
    actor_id: str = "",
) -> Building:
    _check_org_access(organisation_id, tenant_org)
    b = Building(id=new_id(), organisation_id=organisation_id, branch_id=branch_id, name=name, department_id=department_id)
    db.add(b)
    db.flush()
    write_audit(db, organisation_id=organisation_id, actor_id=actor_id, action="building.create", resource_type="building", resource_id=b.id)
    return b


def list_buildings(
    db: Session,
    organisation_id: str | None = None,
    branch_id: str | None = None,
    tenant_org: str | None = None,
) -> list[Building]:
    q = select(Building).order_by(Building.name)
    if tenant_org:
        q = q.where(Building.organisation_id == tenant_org)
    elif organisation_id:
        q = q.where(Building.organisation_id == organisation_id)
    if branch_id:
        q = q.where(Building.branch_id == branch_id)
    return list(db.scalars(q))


def delete_building(db: Session, building_id: str, tenant_org: str | None = None, actor_id: str = "") -> bool:
    b = db.get(Building, building_id)
    if not b:
        return False
    _check_org_access(b.organisation_id, tenant_org)
    org_id = b.organisation_id
    db.delete(b)
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=actor_id, action="building.delete", resource_type="building", resource_id=building_id)
    return True


# --- Floors ---

def create_floor(
    db: Session,
    *,
    organisation_id: str,
    building_id: str,
    name: str,
    tenant_org: str | None = None,
    actor_id: str = "",
) -> Floor:
    _check_org_access(organisation_id, tenant_org)
    f = Floor(id=new_id(), organisation_id=organisation_id, building_id=building_id, name=name)
    db.add(f)
    db.flush()
    write_audit(db, organisation_id=organisation_id, actor_id=actor_id, action="floor.create", resource_type="floor", resource_id=f.id)
    return f


def list_floors(db: Session, building_id: str | None = None, tenant_org: str | None = None) -> list[Floor]:
    q = select(Floor).order_by(Floor.name)
    if tenant_org:
        q = q.where(Floor.organisation_id == tenant_org)
    if building_id:
        q = q.where(Floor.building_id == building_id)
    return list(db.scalars(q))


def delete_floor(db: Session, floor_id: str, tenant_org: str | None = None, actor_id: str = "") -> bool:
    f = db.get(Floor, floor_id)
    if not f:
        return False
    _check_org_access(f.organisation_id, tenant_org)
    org_id = f.organisation_id
    db.delete(f)
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=actor_id, action="floor.delete", resource_type="floor", resource_id=floor_id)
    return True


# --- Gates ---

def create_gate(
    db: Session,
    *,
    organisation_id: str,
    floor_id: str,
    name: str,
    code: str = "",
    tenant_org: str | None = None,
    actor_id: str = "",
) -> Gate:
    _check_org_access(organisation_id, tenant_org)
    g = Gate(id=new_id(), organisation_id=organisation_id, floor_id=floor_id, name=name, code=code)
    db.add(g)
    db.flush()
    write_audit(db, organisation_id=organisation_id, actor_id=actor_id, action="gate.create", resource_type="gate", resource_id=g.id)
    return g


def list_gates(db: Session, floor_id: str | None = None, tenant_org: str | None = None) -> list[Gate]:
    q = select(Gate).order_by(Gate.name)
    if tenant_org:
        q = q.where(Gate.organisation_id == tenant_org)
    if floor_id:
        q = q.where(Gate.floor_id == floor_id)
    return list(db.scalars(q))


def delete_gate(db: Session, gate_id: str, tenant_org: str | None = None, actor_id: str = "") -> bool:
    g = db.get(Gate, gate_id)
    if not g:
        return False
    _check_org_access(g.organisation_id, tenant_org)
    org_id = g.organisation_id
    db.delete(g)
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=actor_id, action="gate.delete", resource_type="gate", resource_id=gate_id)
    return True
