"""Device fleet management API."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Connector, Device, User, new_id
from app.schemas.admin import DeviceAdminOut, DeviceBulkAction, DeviceCreate, DeviceUpdate
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.security.tenant import get_tenant_owned
from app.services.common import write_audit
from app.services.names import NameResolver, enrich_device, resolve_branch, resolve_organisation

router = APIRouter(prefix="/devices", tags=["Devices"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "devices"):
        raise HTTPException(status_code=403, detail="Device access denied")
    return user


def _out(device: Device, names: NameResolver) -> DeviceAdminOut:
    return DeviceAdminOut(**enrich_device(DeviceAdminOut.model_validate(device).model_dump(), names))


def _get_tenant_device(db: Session, user: User, device_id: str) -> Device:
    return get_tenant_owned(db, Device, device_id, user, not_found="Device not found")


def _resolve_connector(db: Session, org_id: str, connector_id: str | None, connector_name: str | None) -> Connector | None:
    if connector_id:
        c = db.get(Connector, connector_id)
        if not c or c.organisation_id != org_id:
            raise HTTPException(status_code=404, detail="Connector not found")
        return c
    key = (connector_name or "").strip()
    if not key:
        return None
    c = db.scalar(select(Connector).where(Connector.organisation_id == org_id, Connector.name == key))
    if not c:
        raise HTTPException(status_code=404, detail=f"Connector not found: {key}")
    return c


def refresh_connector_device_counts(db: Session, connector_id: str | None) -> None:
    """Keep connector device counters aligned with the devices table."""
    if not connector_id:
        return
    connector = db.get(Connector, connector_id)
    if not connector:
        return
    devices = list(db.scalars(select(Device).where(Device.connector_id == connector_id)))
    online = sum(
        1
        for d in devices
        if str(d.status or "").lower() in {"online", "connected", "healthy"}
    )
    connector.connected_devices = len(devices)
    connector.offline_devices = max(0, len(devices) - online)


@router.get("", response_model=list[DeviceAdminOut])
def list_devices(
    search: str | None = None,
    status: str | None = None,
    health_status: str | None = None,
    branch_id: str | None = None,
    connector_id: str | None = None,
    enabled: bool | None = None,
    limit: int = 200,
    offset: int = 0,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    q = select(Device).order_by(Device.name)
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(Device.organisation_id == org)
    if search:
        pattern = f"%{search}%"
        q = q.where(
            or_(
                Device.name.ilike(pattern),
                Device.terminal_code.ilike(pattern),
                Device.serial_number.ilike(pattern),
                Device.ip_address.ilike(pattern),
            )
        )
    if status:
        q = q.where(Device.status == status)
    if health_status:
        q = q.where(Device.health_status == health_status)
    if branch_id:
        q = q.where(Device.branch_id == branch_id)
    if connector_id:
        q = q.where(Device.connector_id == connector_id)
    if enabled is not None:
        q = q.where(Device.enabled == enabled)
    names = NameResolver(db)
    return [_out(d, names) for d in db.scalars(q.offset(offset).limit(limit))]


@router.post("", response_model=DeviceAdminOut)
def create_device(body: DeviceCreate, user: User = Depends(_require), db: Session = Depends(get_db)):
    org = resolve_organisation(db, body.organisation_name)
    if not org:
        raise HTTPException(status_code=404, detail=f"Organisation not found: {body.organisation_name}")
    tenant_org = tenant_filter_org_id(user)
    if tenant_org and org.id != tenant_org:
        raise HTTPException(status_code=403, detail="Cannot create device for other organisation")

    branch_id = None
    if body.branch_name:
        branch = resolve_branch(db, org.id, body.branch_name)
        if not branch:
            raise HTTPException(status_code=404, detail=f"Branch not found: {body.branch_name}")
        branch_id = branch.id

    connector = _resolve_connector(db, org.id, body.connector_id, body.connector_name)
    terminal = (body.terminal_code or "").strip()
    if not terminal:
        terminal = f"DEV-{new_id()[:8].upper()}"

    existing = db.scalar(
        select(Device).where(Device.organisation_id == org.id, Device.terminal_code == terminal)
    )
    if existing:
        raise HTTPException(status_code=400, detail=f"Terminal code already exists: {terminal}")

    device = Device(
        id=new_id(),
        organisation_id=org.id,
        connector_id=connector.id if connector else None,
        branch_id=branch_id,
        name=body.name.strip(),
        terminal_code=terminal,
        ip_address=body.ip_address or "",
        port=body.port or 4370,
        manufacturer=body.manufacturer or "",
        model=body.model or "",
        serial_number=body.serial_number or "",
        firmware=body.firmware or "",
        enabled=body.enabled,
        status="offline",
        health_status="offline",
    )
    db.add(device)
    db.flush()
    refresh_connector_device_counts(db, device.connector_id)
    write_audit(
        db,
        organisation_id=org.id,
        actor_id=user.id,
        actor_email=user.email,
        action="device.create",
        resource_type="device",
        resource_id=device.id,
        message=f"Created device {device.name}",
    )
    return _out(device, NameResolver(db))


@router.get("/export/csv")
def export_csv(user: User = Depends(_require), db: Session = Depends(get_db)):
    q = select(Device).order_by(Device.name)
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(Device.organisation_id == org)
    devices = list(db.scalars(q))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["id", "name", "terminal_code", "status", "health_status", "health_score", "ip_address", "firmware", "enabled"]
    )
    for d in devices:
        writer.writerow(
            [d.id, d.name, d.terminal_code, d.status, d.health_status, d.health_score, d.ip_address, d.firmware, d.enabled]
        )

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=devices.csv"},
    )


@router.post("/bulk", response_model=list[DeviceAdminOut])
def bulk_action(
    body: DeviceBulkAction,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    updated = []
    connector_ids: set[str] = set()
    for did in body.device_ids:
        d = db.get(Device, did)
        if not d:
            continue
        org = tenant_filter_org_id(user)
        if org and d.organisation_id != org:
            continue
        if body.enabled is not None:
            d.enabled = body.enabled
        if d.connector_id:
            connector_ids.add(d.connector_id)
        updated.append(d)
    db.flush()
    for cid in connector_ids:
        refresh_connector_device_counts(db, cid)
    write_audit(
        db,
        organisation_id=updated[0].organisation_id if updated else "",
        actor_id=user.id,
        action="device.bulk",
        message=f"Bulk action on {len(updated)} devices",
        details={"device_ids": body.device_ids, "enabled": body.enabled},
    )
    names = NameResolver(db)
    return [_out(d, names) for d in updated]


@router.get("/{device_id}", response_model=DeviceAdminOut)
def get_device(device_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    d = _get_tenant_device(db, user, device_id)
    return _out(d, NameResolver(db))


@router.patch("/{device_id}", response_model=DeviceAdminOut)
def update_device(
    device_id: str,
    body: DeviceUpdate,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    d = _get_tenant_device(db, user, device_id)
    prev_connector = d.connector_id
    data = body.model_dump(exclude_unset=True)

    if "branch_name" in data:
        branch_name = data.pop("branch_name")
        if branch_name:
            branch = resolve_branch(db, d.organisation_id, branch_name)
            if not branch:
                raise HTTPException(status_code=404, detail=f"Branch not found: {branch_name}")
            d.branch_id = branch.id
        else:
            d.branch_id = None

    if "terminal_code" in data and data["terminal_code"] is not None:
        terminal = str(data["terminal_code"]).strip()
        if terminal and terminal != d.terminal_code:
            clash = db.scalar(
                select(Device).where(
                    Device.organisation_id == d.organisation_id,
                    Device.terminal_code == terminal,
                    Device.id != d.id,
                )
            )
            if clash:
                raise HTTPException(status_code=400, detail=f"Terminal code already exists: {terminal}")
            d.terminal_code = terminal
        data.pop("terminal_code", None)

    for field, value in data.items():
        if not hasattr(d, field):
            continue
        # Allow clearing optional foreign keys with null.
        if value is None and field in {"connector_id", "branch_id"}:
            setattr(d, field, None)
        elif value is not None:
            setattr(d, field, value)

    if d.connector_id:
        c = db.get(Connector, d.connector_id)
        if not c or c.organisation_id != d.organisation_id:
            raise HTTPException(status_code=404, detail="Connector not found")

    db.flush()
    refresh_connector_device_counts(db, prev_connector)
    if d.connector_id != prev_connector:
        refresh_connector_device_counts(db, d.connector_id)
    write_audit(
        db,
        organisation_id=d.organisation_id,
        actor_id=user.id,
        actor_email=user.email,
        action="device.update",
        resource_type="device",
        resource_id=d.id,
        message=f"Updated device {d.name}",
        details=body.model_dump(exclude_unset=True),
    )
    return _out(d, NameResolver(db))


@router.delete("/{device_id}")
def delete_device(device_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    d = _get_tenant_device(db, user, device_id)
    org_id = d.organisation_id
    name = d.name
    connector_id = d.connector_id
    db.delete(d)
    db.flush()
    refresh_connector_device_counts(db, connector_id)
    write_audit(
        db,
        organisation_id=org_id,
        actor_id=user.id,
        actor_email=user.email,
        action="device.delete",
        resource_type="device",
        resource_id=device_id,
        message=f"Deleted device {name}",
    )
    return {"ok": True}
