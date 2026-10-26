"""Remote command center API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Connector, Device, User
from app.schemas.admin import CommandCreate, CommandOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services.commands import create_command, list_commands
from app.services.names import NameResolver, enrich_command

router = APIRouter(prefix="/commands", tags=["Commands"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "commands"):
        raise HTTPException(status_code=403, detail="Command access denied")
    return user


def _resolve_connector(db: Session, name: str) -> Connector:
    key = (name or "").strip()
    conn = db.scalar(select(Connector).where(Connector.name == key))
    if not conn:
        conn = db.get(Connector, key)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connector not found: {name}")
    return conn


def _resolve_device(db: Session, org_id: str, name: str) -> str:
    key = (name or "").strip()
    if not key:
        return ""
    dev = db.scalar(select(Device).where(Device.organisation_id == org_id, Device.name == key))
    if not dev:
        dev = db.get(Device, key)
    return dev.id if dev else ""


def _out(cmd, names: NameResolver) -> CommandOut:
    return CommandOut(**enrich_command(CommandOut.model_validate(cmd).model_dump(), names))


@router.post("", response_model=CommandOut)
def create_cmd(
    body: CommandCreate,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    connector = _resolve_connector(db, body.connector_name)
    org = tenant_filter_org_id(user)
    if org and connector.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    device_id = _resolve_device(db, connector.organisation_id, body.device_name)
    cmd = create_command(
        db,
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        command_type=body.command_type,
        device_id=device_id,
        payload=body.payload,
        created_by=user.id,
        ttl_seconds=body.ttl_seconds,
        settings=settings,
    )
    return _out(cmd, NameResolver(db))


@router.get("", response_model=list[CommandOut])
def list_cmds(
    status: str | None = None,
    connector_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    names = NameResolver(db)
    rows = list_commands(
        db,
        organisation_id=tenant_filter_org_id(user),
        connector_id=connector_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [_out(c, names) for c in rows]
