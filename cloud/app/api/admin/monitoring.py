"""Live monitoring dashboard API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import User
from app.schemas.admin import MonitoringDashboard
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services.analytics import get_monitoring_dashboard

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "analytics"):
        raise HTTPException(status_code=403, detail="Monitoring access denied")
    return user


@router.get("/dashboard", response_model=MonitoringDashboard)
def dashboard(
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return get_monitoring_dashboard(
        db, organisation_id=tenant_filter_org_id(user), settings=settings
    )
