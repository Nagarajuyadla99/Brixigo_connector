"""Authentication, JWT, password hashing, connector API-key auth."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Connector, Role, User
from app.services.names import normalize_identity, resolve_organisation

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return f"bxk_{secrets.token_urlsafe(32)}"


def create_access_token(
    *,
    subject: str,
    role: str,
    organisation_id: str | None,
    settings: Settings,
    extra: dict[str, Any] | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": subject,
        "role": role,
        "organisation_id": organisation_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token, settings)
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive or not found")
    request.state.user = user
    return user


def require_roles(*roles: Role):
    allowed = {r.value if isinstance(r, Role) else str(r) for r in roles}

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role == Role.SUPER_ADMIN.value:
            return user
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient role permissions")
        return user

    return _dep


def _resolve_connector_ref(db: Session, ref: str) -> Connector | None:
    key = (ref or "").strip()
    if not key:
        return None
    conn = db.get(Connector, key)
    if conn:
        return conn
    conn = db.scalar(select(Connector).where(Connector.name == key))
    if conn:
        return conn
    needle = normalize_identity(key)
    if not needle:
        return None
    for candidate in db.scalars(select(Connector).where(Connector.is_active)).all():
        if needle in (normalize_identity(candidate.id), normalize_identity(candidate.name)):
            return candidate
    return None


def get_connector_from_headers(
    request: Request,
    db: Session = Depends(get_db),
    x_connector_id: Optional[str] = Header(default=None, alias="X-Connector-Id"),
    x_organisation_id: Optional[str] = Header(default=None, alias="X-Organisation-Id"),
    x_device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    authorization: Optional[str] = Header(default=None),
) -> Connector:
    """Authenticate on-premise connector agent requests."""
    connector_ref = x_connector_id or ""
    token = x_device_token or ""
    if authorization and authorization.lower().startswith("bearer ") and not token:
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="X-Device-Token or Bearer token required")

    token_hash = hash_api_key(token)
    connector: Connector | None = None

    # API key is authoritative — allows connector name in X-Connector-Id field
    for candidate in db.scalars(select(Connector).where(Connector.is_active)).all():
        if hmac.compare_digest(candidate.api_key_hash, token_hash):
            connector = candidate
            break

    if not connector and connector_ref:
        connector = _resolve_connector_ref(db, connector_ref)
        if connector and not connector.is_active:
            connector = None
        if connector and not hmac.compare_digest(connector.api_key_hash, token_hash):
            connector = None

    if not connector:
        raise HTTPException(status_code=401, detail="Invalid connector credentials")

    # API key is identity. Header/name typos (spaces removed) are the same connector.
    if connector_ref:
        claimed = _resolve_connector_ref(db, connector_ref)
        same_label = normalize_identity(connector_ref) in (
            normalize_identity(connector.id),
            normalize_identity(connector.name),
        )
        if claimed and claimed.id != connector.id and not same_label:
            raise HTTPException(status_code=403, detail="Connector identity mismatch")
        if not claimed and not same_label:
            raise HTTPException(status_code=403, detail="Connector identity mismatch")

    if x_organisation_id:
        org = resolve_organisation(db, x_organisation_id)
        if org and connector.organisation_id != org.id:
            raise HTTPException(status_code=403, detail="Organisation mismatch")

    request.state.connector = connector
    return connector


def tenant_filter_org_id(user: User) -> Optional[str]:
    """Super/platform admins may span orgs; others are locked to their org."""
    if user.role in (Role.SUPER_ADMIN.value, Role.PLATFORM_ADMIN.value):
        return None
    return user.organisation_id
