"""Tenancy hierarchy API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas.admin import (
    BranchCreate,
    BranchOut,
    BranchUpdate,
    BuildingCreate,
    BuildingOut,
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    FloorCreate,
    FloorOut,
    GateCreate,
    GateOut,
    OrganisationCreate,
    OrganisationOut,
    OrganisationUpdate,
)
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services import tenancy

router = APIRouter(prefix="/tenancy", tags=["Tenancy"])


def _require_tenancy(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "tenancy"):
        raise HTTPException(status_code=403, detail="Tenancy access denied")
    return user


@router.get("/organisations", response_model=list[OrganisationOut])
def list_orgs(user: User = Depends(_require_tenancy), db: Session = Depends(get_db)):
    return tenancy.list_organisations(db, tenant_filter_org_id(user))


@router.post("/organisations", response_model=OrganisationOut)
def create_org(
    body: OrganisationCreate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    try:
        return tenancy.create_organisation(
            db, name=body.name, code=body.code, timezone=body.timezone,
            status=body.status, settings_json=body.settings_json, actor_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/organisations/{org_id}", response_model=OrganisationOut)
def get_org(org_id: str, user: User = Depends(_require_tenancy), db: Session = Depends(get_db)):
    org = tenancy.get_organisation(db, org_id, tenant_filter_org_id(user))
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


@router.patch("/organisations/{org_id}", response_model=OrganisationOut)
def update_org(
    org_id: str,
    body: OrganisationUpdate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    org = tenancy.update_organisation(
        db, org_id, tenant_filter_org_id(user), user.id,
        name=body.name, timezone=body.timezone, status=body.status, settings_json=body.settings_json,
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


@router.get("/branches", response_model=list[BranchOut])
def list_branches(
    organisation_id: str | None = None,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.list_branches(db, organisation_id, tenant_filter_org_id(user))


@router.post("/branches", response_model=BranchOut)
def create_branch(
    body: BranchCreate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.create_branch(
        db, organisation_id=body.organisation_id, name=body.name, code=body.code,
        address=body.address, status=body.status, tenant_org=tenant_filter_org_id(user), actor_id=user.id,
    )


@router.patch("/branches/{branch_id}", response_model=BranchOut)
def update_branch(
    branch_id: str,
    body: BranchUpdate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    b = tenancy.update_branch(
        db, branch_id, tenant_filter_org_id(user), user.id,
        name=body.name, address=body.address, status=body.status,
    )
    if not b:
        raise HTTPException(status_code=404, detail="Branch not found")
    return b


@router.delete("/branches/{branch_id}")
def delete_branch(branch_id: str, user: User = Depends(_require_tenancy), db: Session = Depends(get_db)):
    if not tenancy.delete_branch(db, branch_id, tenant_filter_org_id(user), user.id):
        raise HTTPException(status_code=404, detail="Branch not found")
    return {"ok": True}


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(
    organisation_id: str | None = None,
    branch_id: str | None = None,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.list_departments(db, organisation_id, branch_id, tenant_filter_org_id(user))


@router.post("/departments", response_model=DepartmentOut)
def create_department(
    body: DepartmentCreate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.create_department(
        db, organisation_id=body.organisation_id, branch_id=body.branch_id,
        name=body.name, code=body.code, tenant_org=tenant_filter_org_id(user), actor_id=user.id,
    )


@router.patch("/departments/{dept_id}", response_model=DepartmentOut)
def update_department(
    dept_id: str,
    body: DepartmentUpdate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    d = tenancy.update_department(db, dept_id, tenant_filter_org_id(user), user.id, name=body.name, code=body.code)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    return d


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: str, user: User = Depends(_require_tenancy), db: Session = Depends(get_db)):
    if not tenancy.delete_department(db, dept_id, tenant_filter_org_id(user), user.id):
        raise HTTPException(status_code=404, detail="Department not found")
    return {"ok": True}


@router.get("/buildings", response_model=list[BuildingOut])
def list_buildings(
    organisation_id: str | None = None,
    branch_id: str | None = None,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.list_buildings(db, organisation_id, branch_id, tenant_filter_org_id(user))


@router.post("/buildings", response_model=BuildingOut)
def create_building(
    body: BuildingCreate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.create_building(
        db, organisation_id=body.organisation_id, branch_id=body.branch_id,
        name=body.name, department_id=body.department_id,
        tenant_org=tenant_filter_org_id(user), actor_id=user.id,
    )


@router.delete("/buildings/{building_id}")
def delete_building(building_id: str, user: User = Depends(_require_tenancy), db: Session = Depends(get_db)):
    if not tenancy.delete_building(db, building_id, tenant_filter_org_id(user), user.id):
        raise HTTPException(status_code=404, detail="Building not found")
    return {"ok": True}


@router.get("/floors", response_model=list[FloorOut])
def list_floors(
    building_id: str | None = None,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.list_floors(db, building_id, tenant_filter_org_id(user))


@router.post("/floors", response_model=FloorOut)
def create_floor(
    body: FloorCreate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.create_floor(
        db, organisation_id=body.organisation_id, building_id=body.building_id,
        name=body.name, tenant_org=tenant_filter_org_id(user), actor_id=user.id,
    )


@router.delete("/floors/{floor_id}")
def delete_floor(floor_id: str, user: User = Depends(_require_tenancy), db: Session = Depends(get_db)):
    if not tenancy.delete_floor(db, floor_id, tenant_filter_org_id(user), user.id):
        raise HTTPException(status_code=404, detail="Floor not found")
    return {"ok": True}


@router.get("/gates", response_model=list[GateOut])
def list_gates(
    floor_id: str | None = None,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.list_gates(db, floor_id, tenant_filter_org_id(user))


@router.post("/gates", response_model=GateOut)
def create_gate(
    body: GateCreate,
    user: User = Depends(_require_tenancy),
    db: Session = Depends(get_db),
):
    return tenancy.create_gate(
        db, organisation_id=body.organisation_id, floor_id=body.floor_id,
        name=body.name, code=body.code, tenant_org=tenant_filter_org_id(user), actor_id=user.id,
    )


@router.delete("/gates/{gate_id}")
def delete_gate(gate_id: str, user: User = Depends(_require_tenancy), db: Session = Depends(get_db)):
    if not tenancy.delete_gate(db, gate_id, tenant_filter_org_id(user), user.id):
        raise HTTPException(status_code=404, detail="Gate not found")
    return {"ok": True}
