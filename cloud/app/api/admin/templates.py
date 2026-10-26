"""Biometric template management API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas.admin import TemplateCreate, TemplateOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services import templates

router = APIRouter(prefix="/templates", tags=["Templates"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "templates"):
        raise HTTPException(status_code=403, detail="Template access denied")
    return user


@router.get("", response_model=list[TemplateOut])
def list_tpls(
    employee_id: str | None = None,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    return templates.list_templates(db, organisation_id=tenant_filter_org_id(user), employee_id=employee_id)


@router.post("", response_model=TemplateOut)
def create_tpl(body: TemplateCreate, user: User = Depends(_require), db: Session = Depends(get_db)):
    org = tenant_filter_org_id(user)
    if org and body.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    return templates.create_template(
        db,
        organisation_id=body.organisation_id,
        employee_id=body.employee_id,
        employee_code=body.employee_code,
        template_type=body.template_type,
        template_data=body.template_data,
        finger_index=body.finger_index,
        vendor=body.vendor,
        provided_checksum=body.checksum,
        actor_id=user.id,
    )


@router.get("/{template_id}", response_model=TemplateOut)
def get_tpl(template_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    tpl = templates.get_template(db, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    org = tenant_filter_org_id(user)
    if org and tpl.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    return tpl


@router.post("/{template_id}/restore", response_model=TemplateOut)
def restore_tpl(template_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    tpl = templates.restore_template_version(db, template_id, actor_id=user.id)
    if not tpl:
        raise HTTPException(status_code=404, detail="No previous version to restore")
    return tpl


@router.delete("/{template_id}")
def delete_tpl(template_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    if not templates.delete_template(db, template_id, actor_id=user.id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


@router.get("/compare/{id_a}/{id_b}")
def compare_tpls(id_a: str, id_b: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    return templates.compare_checksums(db, id_a, id_b)
