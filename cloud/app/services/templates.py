"""Biometric template storage with encryption and version history."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BiometricTemplate, new_id
from app.services.common import decrypt_str, encrypt_str, write_audit


def _checksum(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def create_template(
    db: Session,
    *,
    organisation_id: str,
    employee_id: str,
    employee_code: str,
    template_type: str,
    template_data: str,
    finger_index: int = 0,
    vendor: str = "",
    provided_checksum: str = "",
    actor_id: str = "",
) -> BiometricTemplate:
    checksum = provided_checksum or _checksum(template_data)
    existing = db.scalar(
        select(BiometricTemplate).where(
            BiometricTemplate.organisation_id == organisation_id,
            BiometricTemplate.employee_id == employee_id,
            BiometricTemplate.template_type == template_type,
            BiometricTemplate.finger_index == finger_index,
        ).order_by(BiometricTemplate.version.desc())
    )
    previous_id = None
    version = 1
    if existing:
        if existing.checksum == checksum:
            return existing
        previous_id = existing.id
        version = existing.version + 1

    tpl = BiometricTemplate(
        id=new_id(),
        organisation_id=organisation_id,
        employee_id=employee_id,
        employee_code=employee_code,
        template_type=template_type,
        finger_index=finger_index,
        template_enc=encrypt_str(template_data),
        checksum=checksum,
        vendor=vendor,
        size=len(template_data),
        version=version,
        previous_version_id=previous_id,
    )
    db.add(tpl)
    db.flush()
    write_audit(
        db,
        organisation_id=organisation_id,
        actor_id=actor_id,
        action="template.create",
        resource_type="biometric_template",
        resource_id=tpl.id,
        message=f"Stored {template_type} template v{version} for {employee_code}",
    )
    return tpl


def get_template(db: Session, template_id: str) -> BiometricTemplate | None:
    return db.get(BiometricTemplate, template_id)


def list_templates(
    db: Session,
    *,
    organisation_id: str | None = None,
    employee_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[BiometricTemplate]:
    q = select(BiometricTemplate).order_by(BiometricTemplate.updated_at.desc())
    if organisation_id:
        q = q.where(BiometricTemplate.organisation_id == organisation_id)
    if employee_id:
        q = q.where(BiometricTemplate.employee_id == employee_id)
    return list(db.scalars(q.offset(offset).limit(limit)))


def compare_checksums(db: Session, template_id_a: str, template_id_b: str) -> dict:
    a = db.get(BiometricTemplate, template_id_a)
    b = db.get(BiometricTemplate, template_id_b)
    if not a or not b:
        return {"match": False, "error": "Template not found"}
    return {
        "match": a.checksum == b.checksum,
        "checksum_a": a.checksum,
        "checksum_b": b.checksum,
        "version_a": a.version,
        "version_b": b.version,
    }


def restore_template_version(db: Session, template_id: str, *, actor_id: str = "") -> BiometricTemplate | None:
    current = db.get(BiometricTemplate, template_id)
    if not current or not current.previous_version_id:
        return None
    previous = db.get(BiometricTemplate, current.previous_version_id)
    if not previous:
        return None
    restored = create_template(
        db,
        organisation_id=current.organisation_id,
        employee_id=current.employee_id,
        employee_code=current.employee_code,
        template_type=current.template_type,
        template_data=decrypt_str(previous.template_enc),
        finger_index=current.finger_index,
        vendor=previous.vendor,
        provided_checksum=previous.checksum,
        actor_id=actor_id,
    )
    write_audit(
        db,
        organisation_id=current.organisation_id,
        actor_id=actor_id,
        action="template.restore",
        resource_type="biometric_template",
        resource_id=restored.id,
        message=f"Restored template from version {previous.version}",
    )
    return restored


def delete_template(db: Session, template_id: str, *, actor_id: str = "") -> bool:
    tpl = db.get(BiometricTemplate, template_id)
    if not tpl:
        return False
    org_id = tpl.organisation_id
    db.delete(tpl)
    db.flush()
    write_audit(
        db,
        organisation_id=org_id,
        actor_id=actor_id,
        action="template.delete",
        resource_type="biometric_template",
        resource_id=template_id,
    )
    return True
