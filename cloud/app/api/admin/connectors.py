"""Connector management API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Connector, ConnectorStatus, Device, User, new_id, utcnow
from app.schemas.admin import ConnectorAdminOut, ConnectorRegister, ConnectorRegisterResponse, ConnectorUpdate
from app.security.auth import generate_api_key, get_current_user, hash_api_key, tenant_filter_org_id
from app.security.rbac import can_access
from app.security.tenant import get_tenant_owned
from app.services.commands import create_command
from app.services.common import write_audit
from app.services.names import NameResolver, enrich_connector, resolve_branch, resolve_organisation

router = APIRouter(prefix="/connectors", tags=["Connectors"])

_ONLINE_DEVICE = {"online", "connected", "healthy"}


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "connectors"):
        raise HTTPException(status_code=403, detail="Connector access denied")
    return user


def _get_tenant_connector(db: Session, user: User, connector_id: str) -> Connector:
    return get_tenant_owned(
        db, Connector, connector_id, user, not_found="Connector not found"
    )


def _tenant_q(db: Session, user: User):
    q = select(Connector).order_by(Connector.name)
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(Connector.organisation_id == org)
    return q


def _live_device_counts(db: Session, connector_id: str) -> tuple[int, int]:
    """Return (total_devices, offline_devices) from the devices table."""
    devices = list(db.scalars(select(Device).where(Device.connector_id == connector_id)))
    online = sum(1 for d in devices if str(d.status or "").lower() in _ONLINE_DEVICE)
    total = len(devices)
    return total, max(0, total - online)


def _out(connector: Connector, names: NameResolver, db: Session | None = None) -> ConnectorAdminOut:
    data = enrich_connector(ConnectorAdminOut.model_validate(connector).model_dump(), names)
    if db is not None:
        total, offline = _live_device_counts(db, connector.id)
        data["connected_devices"] = total
        data["offline_devices"] = offline
    return ConnectorAdminOut(**data)


@router.get("", response_model=list[ConnectorAdminOut])
def list_connectors(user: User = Depends(_require), db: Session = Depends(get_db)):
    names = NameResolver(db)
    rows = list(db.scalars(_tenant_q(db, user)))
    return [_out(c, names, db) for c in rows]


@router.get("/{connector_id}", response_model=ConnectorAdminOut)
def get_connector(connector_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    c = _get_tenant_connector(db, user, connector_id)
    return _out(c, NameResolver(db), db)


@router.patch("/{connector_id}", response_model=ConnectorAdminOut)
def update_connector(
    connector_id: str,
    body: ConnectorUpdate,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    c = _get_tenant_connector(db, user, connector_id)

    data = body.model_dump(exclude_unset=True)
    if "branch_name" in data:
        branch_name = data.pop("branch_name")
        if branch_name:
            branch = resolve_branch(db, c.organisation_id, branch_name)
            if not branch:
                raise HTTPException(status_code=404, detail=f"Branch not found: {branch_name}")
            c.branch_id = branch.id
        else:
            c.branch_id = None

    for field, value in data.items():
        if hasattr(c, field) and value is not None:
            setattr(c, field, value)
        elif field == "is_active" and value is not None:
            c.is_active = bool(value)
            if not c.is_active:
                c.status = ConnectorStatus.DEACTIVATED.value

    db.flush()
    write_audit(
        db,
        organisation_id=c.organisation_id,
        actor_id=user.id,
        actor_email=user.email,
        action="connector.update",
        resource_type="connector",
        resource_id=c.id,
        message=f"Updated connector {c.name}",
        details=body.model_dump(exclude_unset=True),
    )
    return _out(c, NameResolver(db), db)


@router.post("/register", response_model=ConnectorRegisterResponse)
def register_connector(
    body: ConnectorRegister,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    org = resolve_organisation(db, body.organisation_name)
    if not org:
        raise HTTPException(status_code=404, detail=f"Organisation not found: {body.organisation_name}")

    tenant_org = tenant_filter_org_id(user)
    if tenant_org and org.id != tenant_org:
        raise HTTPException(status_code=403, detail="Cannot register for other organisation")

    branch_id = None
    if body.branch_name:
        branch = resolve_branch(db, org.id, body.branch_name)
        if not branch:
            raise HTTPException(status_code=404, detail=f"Branch not found: {body.branch_name}")
        branch_id = branch.id

    api_key = generate_api_key()
    connector = Connector(
        id=new_id(),
        organisation_id=org.id,
        branch_id=branch_id,
        name=body.name,
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=api_key[:12],
        hostname=body.hostname,
        version=body.version,
        status=ConnectorStatus.UNKNOWN.value,
        installation_date=utcnow(),
        is_active=True,
    )
    db.add(connector)
    db.flush()
    write_audit(
        db,
        organisation_id=org.id,
        actor_id=user.id,
        actor_email=user.email,
        action="connector.register",
        resource_type="connector",
        resource_id=connector.id,
        message=f"Registered connector {body.name} for {org.name}",
    )
    names = NameResolver(db)
    return ConnectorRegisterResponse(connector=_out(connector, names, db), api_key=api_key)


@router.post("/{connector_id}/deactivate", response_model=ConnectorAdminOut)
def deactivate_connector(connector_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    c = _get_tenant_connector(db, user, connector_id)
    c.is_active = False
    c.status = ConnectorStatus.DEACTIVATED.value
    db.flush()
    write_audit(db, organisation_id=c.organisation_id, actor_id=user.id, action="connector.deactivate", resource_type="connector", resource_id=c.id)
    return _out(c, NameResolver(db), db)


@router.delete("/{connector_id}")
def delete_connector(connector_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    c = _get_tenant_connector(db, user, connector_id)
    org_id = c.organisation_id
    db.delete(c)
    db.flush()
    write_audit(db, organisation_id=org_id, actor_id=user.id, action="connector.delete", resource_type="connector", resource_id=connector_id)
    return {"ok": True}


def _action_command(
    db: Session,
    connector: Connector,
    command_type: str,
    user: User,
    settings: Settings,
    payload: dict | None = None,
):
    return create_command(
        db,
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        command_type=command_type,
        payload=payload or {},
        created_by=user.id,
        settings=settings,
    )


@router.post("/{connector_id}/restart")
def restart_connector(
    connector_id: str,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    c = _get_tenant_connector(db, user, connector_id)
    cmd = _action_command(db, c, "connector_restart", user, settings)
    return {"command_id": cmd.id, "status": cmd.status}


@router.post("/{connector_id}/upgrade")
def upgrade_connector(
    connector_id: str,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    c = _get_tenant_connector(db, user, connector_id)
    cmd = _action_command(db, c, "connector_upgrade", user, settings, {"target_version": "latest"})
    return {"command_id": cmd.id, "status": cmd.status}


@router.post("/{connector_id}/reconnect")
def reconnect_connector(
    connector_id: str,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    c = _get_tenant_connector(db, user, connector_id)
    cmd = _action_command(db, c, "connector_reconnect", user, settings)
    return {"command_id": cmd.id, "status": cmd.status}
