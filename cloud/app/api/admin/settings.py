"""System policy settings API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SystemPolicy, User, new_id
from app.schemas.admin import PolicyOut, PolicyUpdate
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services.common import write_audit

router = APIRouter(prefix="/settings", tags=["Settings"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "settings"):
        raise HTTPException(status_code=403, detail="Settings access denied")
    return user


@router.get("/policies", response_model=list[PolicyOut])
def list_policies(user: User = Depends(_require), db: Session = Depends(get_db)):
    q = select(SystemPolicy).order_by(SystemPolicy.policy_key)
    org = tenant_filter_org_id(user)
    if org:
        q = q.where(SystemPolicy.organisation_id == org)
    return list(db.scalars(q))


@router.put("/policies", response_model=PolicyOut)
def upsert_policy(body: PolicyUpdate, user: User = Depends(_require), db: Session = Depends(get_db)):
    org = body.organisation_id or tenant_filter_org_id(user) or ""
    existing = db.scalar(
        select(SystemPolicy).where(SystemPolicy.organisation_id == org, SystemPolicy.policy_key == body.policy_key)
    )
    if existing:
        existing.policy_json = body.policy_json
        db.flush()
        write_audit(db, organisation_id=org, actor_id=user.id, action="policy.update", resource_type="system_policy", resource_id=existing.id)
        return existing
    policy = SystemPolicy(id=new_id(), organisation_id=org, policy_key=body.policy_key, policy_json=body.policy_json)
    db.add(policy)
    db.flush()
    write_audit(db, organisation_id=org, actor_id=user.id, action="policy.create", resource_type="system_policy", resource_id=policy.id)
    return policy


@router.delete("/policies/{policy_id}")
def delete_policy(policy_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    policy = db.get(SystemPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    org = tenant_filter_org_id(user)
    if org and policy.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    db.delete(policy)
    db.flush()
    return {"ok": True}
