"""Audit log search API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, User
from app.schemas.admin import AuditOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access

router = APIRouter(prefix="/audit", tags=["Audit"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "audit"):
        raise HTTPException(status_code=403, detail="Audit access denied")
    return user


@router.get("", response_model=list[AuditOut])
def search_audit(
    search: str | None = None,
    action: str | None = None,
    actor_email: str | None = None,
    success: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.created_at.desc())
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(AuditLog.organisation_id == org)
    if action:
        q = q.where(AuditLog.action == action)
    if actor_email:
        q = q.where(AuditLog.actor_email.ilike(f"%{actor_email}%"))
    if success is not None:
        q = q.where(AuditLog.success == success)
    if search:
        pattern = f"%{search}%"
        q = q.where(
            or_(
                AuditLog.message.ilike(pattern),
                AuditLog.action.ilike(pattern),
                AuditLog.resource_id.ilike(pattern),
                AuditLog.actor_email.ilike(pattern),
            )
        )
    return list(db.scalars(q.offset(offset).limit(limit)))
