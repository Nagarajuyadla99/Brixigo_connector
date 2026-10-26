"""Support and diagnostics API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Connector, SupportArtifact, User
from app.schemas.admin import SupportArtifactOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.security.tenant import get_tenant_owned
from app.services.commands import create_command

router = APIRouter(prefix="/support", tags=["Support"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "support"):
        raise HTTPException(status_code=403, detail="Support access denied")
    return user


def _get_tenant_connector(db: Session, user: User, connector_id: str) -> Connector:
    return get_tenant_owned(
        db, Connector, connector_id, user, not_found="Connector not found"
    )


@router.post("/connectors/{connector_id}/download-logs")
def download_logs(
    connector_id: str,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    c = _get_tenant_connector(db, user, connector_id)
    cmd = create_command(
        db,
        organisation_id=c.organisation_id,
        connector_id=c.id,
        command_type="download_logs",
        created_by=user.id,
        settings=settings,
    )
    return {"command_id": cmd.id}


@router.post("/connectors/{connector_id}/diagnostics")
def run_diagnostics(
    connector_id: str,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    c = _get_tenant_connector(db, user, connector_id)
    cmd = create_command(
        db,
        organisation_id=c.organisation_id,
        connector_id=c.id,
        command_type="run_diagnostics",
        payload={"include_devices": True, "include_network": True},
        created_by=user.id,
        settings=settings,
    )
    return {"command_id": cmd.id, "connector_status": c.status}


@router.get("/artifacts", response_model=list[SupportArtifactOut])
def list_artifacts(
    connector_id: str | None = None,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    q = select(SupportArtifact).order_by(SupportArtifact.created_at.desc())
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(SupportArtifact.organisation_id == org)
    if connector_id:
        # Ensure tenant users cannot probe another org's connector via query param
        _get_tenant_connector(db, user, connector_id)
        q = q.where(SupportArtifact.connector_id == connector_id)
    return list(db.scalars(q.limit(100)))


@router.post("/connectors/{connector_id}/clear-queue")
def clear_queue(
    connector_id: str,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    c = _get_tenant_connector(db, user, connector_id)
    cmd = create_command(
        db,
        organisation_id=c.organisation_id,
        connector_id=c.id,
        command_type="clear_queue",
        created_by=user.id,
        settings=settings,
    )
    return {"command_id": cmd.id}
