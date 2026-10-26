"""Bootstrap organisation and super-admin user on startup."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Organisation, Role, User, new_id
from app.security.auth import hash_password
from app.services.common import write_audit


def ensure_bootstrap(db: Session, settings: Settings) -> tuple[Organisation | None, User | None]:
    """Ensure default organisation and super-admin exist. Idempotent."""
    org = db.scalar(select(Organisation).where(Organisation.code == "brixigo-demo"))
    if not org:
        org = Organisation(
            id=new_id(),
            name=settings.bootstrap_org_name,
            code="brixigo-demo",
            status="active",
            timezone="Asia/Kolkata",
            settings_json={"bootstrapped": True},
        )
        db.add(org)
        db.flush()

    user = db.scalar(select(User).where(User.email == settings.bootstrap_admin_email))
    if not user:
        user = User(
            id=new_id(),
            organisation_id=org.id,
            email=settings.bootstrap_admin_email,
            full_name="Brixigo Super Admin",
            password_hash=hash_password(settings.bootstrap_admin_password),
            role=Role.SUPER_ADMIN.value,
            is_active=True,
        )
        db.add(user)
        db.flush()
        write_audit(
            db,
            organisation_id=org.id,
            actor_id=user.id,
            actor_email=user.email,
            action="bootstrap.admin_created",
            message="Bootstrap super-admin account created",
            resource_type="user",
            resource_id=user.id,
        )
    return org, user
