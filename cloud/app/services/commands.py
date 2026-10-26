"""Remote command creation and lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CommandState, RemoteCommand, new_id, utcnow
from app.security.hmac_signing import build_signed_command
from app.services.common import parse_iso, write_audit


def create_command(
    db: Session,
    *,
    organisation_id: str,
    connector_id: str,
    command_type: str,
    device_id: str = "",
    payload: dict | None = None,
    created_by: str = "",
    ttl_seconds: int | None = None,
    settings: Settings,
) -> RemoteCommand:
    cmd_id = new_id()
    ttl = ttl_seconds or settings.default_command_ttl_seconds
    signed = build_signed_command(
        command_id=cmd_id,
        command_type=command_type,
        device_id=device_id,
        payload=payload or {},
        secret=settings.command_hmac_secret,
        ttl_seconds=ttl,
    )
    expires = parse_iso(signed["expires_at"])
    issued = parse_iso(signed["issued_at"]) or utcnow()
    # Preserve exact ISO strings used for HMAC so connector verification matches
    meta = {
        "issued_at": signed["issued_at"],
        "expires_at": signed["expires_at"],
    }
    cmd = RemoteCommand(
        id=cmd_id,
        organisation_id=organisation_id,
        connector_id=connector_id,
        device_id=device_id or "",
        command_type=command_type,
        payload_json=signed["payload"],
        status=CommandState.QUEUED.value,
        signature=signed["signature"],
        nonce=signed["nonce"],
        issued_at=issued,
        expires_at=expires,
        created_by=created_by,
        result_json={"_signing": meta},
    )
    db.add(cmd)
    db.flush()
    write_audit(
        db,
        organisation_id=organisation_id,
        actor_id=created_by,
        action="command.create",
        resource_type="remote_command",
        resource_id=cmd.id,
        message=f"Queued {command_type} for connector {connector_id}",
        details={"device_id": device_id, "command_type": command_type},
    )
    return cmd


def list_commands(
    db: Session,
    *,
    organisation_id: str | None = None,
    connector_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[RemoteCommand]:
    q = select(RemoteCommand).order_by(RemoteCommand.created_at.desc())
    if organisation_id:
        q = q.where(RemoteCommand.organisation_id == organisation_id)
    if connector_id:
        q = q.where(RemoteCommand.connector_id == connector_id)
    if status:
        q = q.where(RemoteCommand.status == status)
    return list(db.scalars(q.offset(offset).limit(limit)))


def ack_command(
    db: Session,
    command_id: str,
    *,
    status: str,
    result_message: str = "",
    result_json: dict | None = None,
) -> RemoteCommand | None:
    cmd = db.get(RemoteCommand, command_id)
    if not cmd:
        return None
    cmd.status = status
    cmd.result_message = result_message[:2000]
    cmd.result_json = result_json or {}
    if status in (CommandState.COMPLETED.value, CommandState.FAILED.value, CommandState.CANCELLED.value):
        cmd.completed_at = utcnow()
    db.flush()
    return cmd


def expire_stale_commands(db: Session, settings: Settings) -> int:
    now = utcnow()
    stale = db.scalars(
        select(RemoteCommand).where(
            RemoteCommand.status == CommandState.QUEUED.value,
            RemoteCommand.expires_at.isnot(None),
            RemoteCommand.expires_at < now,
        )
    ).all()
    count = 0
    for cmd in stale:
        cmd.status = CommandState.EXPIRED.value
        cmd.result_message = "Command expired"
        cmd.completed_at = now
        count += 1
    if count:
        db.flush()
    return count
