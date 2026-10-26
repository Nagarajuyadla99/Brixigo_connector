"""Device provisioning wizard API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Connector, ConnectorStatus, Device, User
from app.schemas.admin import ProvisioningApprove, ProvisioningAssign, ProvisioningWizardStep
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.security.tenant import get_tenant_owned
from app.services.common import write_audit

router = APIRouter(prefix="/provisioning", tags=["Provisioning"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "connectors"):
        raise HTTPException(status_code=403, detail="Provisioning access denied")
    return user


@router.get("/discover", response_model=ProvisioningWizardStep)
def discover(user: User = Depends(_require), db: Session = Depends(get_db)):
    q = select(Connector).where(Connector.status == ConnectorStatus.UNKNOWN.value, Connector.is_active.is_(True))
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(Connector.organisation_id == org)
    pending = list(db.scalars(q))
    items = [
        {"id": c.id, "name": c.name, "hostname": c.hostname, "version": c.version, "created_at": c.created_at.isoformat()}
        for c in pending
    ]
    return ProvisioningWizardStep(
        step="discover",
        status="ready" if items else "empty",
        message=f"Found {len(items)} unapproved connectors",
        items=items,
    )


@router.post("/approve", response_model=ProvisioningWizardStep)
def approve(body: ProvisioningApprove, user: User = Depends(_require), db: Session = Depends(get_db)):
    c = get_tenant_owned(db, Connector, body.connector_id, user, not_found="Connector not found")
    if body.branch_id:
        c.branch_id = body.branch_id
    c.status = ConnectorStatus.OFFLINE.value
    db.flush()
    write_audit(db, organisation_id=c.organisation_id, actor_id=user.id, action="provisioning.approve", resource_type="connector", resource_id=c.id)
    return ProvisioningWizardStep(step="approve", status="completed", message=f"Approved connector {c.name}")


@router.post("/assign", response_model=ProvisioningWizardStep)
def assign(body: ProvisioningAssign, user: User = Depends(_require), db: Session = Depends(get_db)):
    d = get_tenant_owned(db, Device, body.device_id, user, not_found="Device not found")
    if body.branch_id is not None:
        d.branch_id = body.branch_id
    if body.department_id is not None:
        d.department_id = body.department_id
    if body.building_id is not None:
        d.building_id = body.building_id
    if body.floor_id is not None:
        d.floor_id = body.floor_id
    if body.gate_id is not None:
        d.gate_id = body.gate_id
    db.flush()
    write_audit(db, organisation_id=d.organisation_id, actor_id=user.id, action="provisioning.assign", resource_type="device", resource_id=d.id)
    return ProvisioningWizardStep(step="assign", status="completed", message=f"Assigned location for device {d.name}")


@router.get("/unassigned-devices", response_model=ProvisioningWizardStep)
def unassigned_devices(user: User = Depends(_require), db: Session = Depends(get_db)):
    q = select(Device).where(Device.branch_id.is_(None))
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(Device.organisation_id == org)
    devices = list(db.scalars(q))
    items = [{"id": d.id, "name": d.name, "terminal_code": d.terminal_code, "connector_id": d.connector_id} for d in devices]
    return ProvisioningWizardStep(step="assign", status="ready", message=f"{len(items)} devices need location assignment", items=items)
