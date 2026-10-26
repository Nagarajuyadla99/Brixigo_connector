"""Admin authentication endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import User, utcnow
from app.schemas.admin import LoginRequest, LoginResponse, MeResponse
from app.security.auth import create_access_token, get_current_user, verify_password
from app.services.common import write_audit
from app.services.names import NameResolver

router = APIRouter(prefix="/auth", tags=["Admin Auth"])


def _org_name(db, org_id: str | None) -> str:
    if not org_id:
        return ""
    return NameResolver(db).org(org_id)


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")

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
