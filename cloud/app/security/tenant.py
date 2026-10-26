"""Organisation-scoped ownership checks for admin mutations."""

from __future__ import annotations

from typing import Any, Optional, TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import User
from app.security.auth import tenant_filter_org_id

T = TypeVar("T")


def assert_org_access(
    user: User,
    resource_organisation_id: Optional[str],
    *,
    detail: str = "Access denied",
) -> None:
    """Raise 403 when a tenant-scoped user targets another organisation's resource.

    Super/platform admins (`tenant_filter_org_id` → None) are unrestricted.
    """
    org = tenant_filter_org_id(user)
    if org and resource_organisation_id != org:
        raise HTTPException(status_code=403, detail=detail)


def get_tenant_owned(
    db: Session,
    model: type[T],
    resource_id: str,
    user: User,
    *,
    not_found: str = "Resource not found",
    detail: str = "Access denied",
) -> T:
    """Load by primary key then enforce organisation ownership."""
    obj: Any = db.get(model, resource_id)
    if not obj:
        raise HTTPException(status_code=404, detail=not_found)
    org_id = getattr(obj, "organisation_id", None)
    assert_org_access(user, org_id, detail=detail)
    return obj
