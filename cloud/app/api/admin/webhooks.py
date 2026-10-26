"""Webhook subscription API."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, WebhookSubscription, new_id
from app.schemas.admin import WebhookCreate, WebhookOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services.common import write_audit

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "settings"):
        raise HTTPException(status_code=403, detail="Webhook access denied")
    return user


@router.get("", response_model=list[WebhookOut])
def list_webhooks(user: User = Depends(_require), db: Session = Depends(get_db)):
    q = select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(WebhookSubscription.organisation_id == org)
    return list(db.scalars(q))


@router.post("", response_model=WebhookOut)
def create_webhook(body: WebhookCreate, user: User = Depends(_require), db: Session = Depends(get_db)):
    org = tenant_filter_org_id(user)
    if org and body.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    secret = body.secret or secrets.token_urlsafe(24)
    hook = WebhookSubscription(
        id=new_id(),
        organisation_id=body.organisation_id,
        url=body.url,
        secret=secret,
        event_types_json=body.event_types,
        is_active=body.is_active,
    )
    db.add(hook)
    db.flush()
    write_audit(db, organisation_id=body.organisation_id, actor_id=user.id, action="webhook.create", resource_type="webhook", resource_id=hook.id)
    return hook


@router.delete("/{webhook_id}")
def delete_webhook(webhook_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    hook = db.get(WebhookSubscription, webhook_id)
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    org = tenant_filter_org_id(user)
    if org and hook.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    db.delete(hook)
    db.flush()
    return {"ok": True}
