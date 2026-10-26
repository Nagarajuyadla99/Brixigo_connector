"""Admin authentication endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Organisation, Role, User, new_id, utcnow
from app.schemas.admin import LoginRequest, LoginResponse, MeResponse, RegisterRequest
from app.security.auth import create_access_token, get_current_user, hash_password, verify_password
from app.services.common import write_audit
from app.services.names import NameResolver

router = APIRouter(prefix="/auth", tags=["Admin Auth"])


def _org_name(db, org_id: str | None) -> str:
    if not org_id:
        return ""
    return NameResolver(db).org(org_id)


def _slugify_org_code(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    slug = slug[:48] or "org"
    return slug


def _unique_org_code(db: Session, name: str) -> str:
    base = _slugify_org_code(name)
    code = base
    n = 2
    while db.scalar(select(Organisation.id).where(Organisation.code == code)):
        suffix = f"-{n}"
        code = f"{base[: 64 - len(suffix)]}{suffix}"
        n += 1
    return code


def _issue_login(db: Session, user: User, settings: Settings, request: Request) -> LoginResponse:
    user.last_login_at = utcnow()
    token = create_access_token(
        subject=user.id,
        role=user.role,
        organisation_id=user.organisation_id,
        settings=settings,
    )
    write_audit(
        db,
        organisation_id=user.organisation_id or "",
        actor_id=user.id,
        actor_email=user.email,
        action="auth.login",
        ip_address=request.client.host if request.client else "",
    )
    return LoginResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        organisation_id=user.organisation_id,
        organisation_name=_org_name(db, user.organisation_id),
    )


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    email = (body.email or "").strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")
    return _issue_login(db, user, settings, request)


@router.post("/register", response_model=LoginResponse)
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.allow_public_registration:
        raise HTTPException(status_code=403, detail="Public registration is disabled")

    email = (body.email or "").strip().lower()
    full_name = (body.full_name or "").strip()
    org_name = (body.organisation_name or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if len(org_name) < 2:
        raise HTTPException(status_code=400, detail="Organisation name is required")

    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    org = Organisation(
        id=new_id(),
        name=org_name,
        code=_unique_org_code(db, org_name),
        status="active",
        timezone="Asia/Kolkata",
        settings_json={"self_registered": True},
    )
    db.add(org)
    db.flush()

    user = User(
        id=new_id(),
        organisation_id=org.id,
        email=email,
        full_name=full_name,
        password_hash=hash_password(body.password),
        role=Role.ORG_ADMIN.value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    write_audit(
        db,
        organisation_id=org.id,
        actor_id=user.id,
        actor_email=user.email,
        action="auth.register",
        message=f"Self-registered organisation {org.name}",
        resource_type="user",
        resource_id=user.id,
        ip_address=request.client.host if request.client else "",
    )
    return _issue_login(db, user, settings, request)


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        organisation_id=user.organisation_id,
        organisation_name=_org_name(db, user.organisation_id),
        branch_id=user.branch_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
    )
