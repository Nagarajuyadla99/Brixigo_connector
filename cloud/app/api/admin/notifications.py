"""Notification center API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notification, User, utcnow
from app.schemas.admin import NotificationOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "audit"):
        raise HTTPException(status_code=403, detail="Notification access denied")
    return user


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    acknowledged: bool | None = None,
    severity: str | None = None,
    limit: int = 100,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    q = select(Notification).order_by(Notification.created_at.desc())
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(Notification.organisation_id == org)
    if acknowledged is not None:
        q = q.where(Notification.acknowledged == acknowledged)
    if severity:
        q = q.where(Notification.severity == severity)
    return list(db.scalars(q.limit(limit)))


@router.post("/{notification_id}/ack", response_model=NotificationOut)
def ack_notification(notification_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    note = db.get(Notification, notification_id)
    if not note:
        raise HTTPException(status_code=404, detail="Notification not found")
    org = tenant_filter_org_id(user)
    if org and note.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    note.acknowledged = True
    db.flush()
    return note
