"""Report generation API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import User
from app.schemas.admin import ReportCreate, ReportJobOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services import reports
from app.services.names import resolve_organisation

router = APIRouter(prefix="/reports", tags=["Reports"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "reports"):
        raise HTTPException(status_code=403, detail="Reports access denied")
    return user


@router.post("", response_model=ReportJobOut)
def create_report(
    body: ReportCreate,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    org_id = tenant_filter_org_id(user) or user.organisation_id
    if body.organisation_name:
        org = resolve_organisation(db, body.organisation_name)
        if not org:
            raise HTTPException(status_code=404, detail=f"Organisation not found: {body.organisation_name}")
        org_id = org.id
    if not org_id:
        raise HTTPException(status_code=400, detail="organisation_name required")
    return reports.create_report_job(
        db,
        organisation_id=org_id,
        report_type=body.report_type,
        format=body.format,
        parameters=body.parameters,
        created_by=user.id,
        settings=settings,
    )


@router.get("", response_model=list[ReportJobOut])
def list_reports(user: User = Depends(_require), db: Session = Depends(get_db)):
    return reports.list_report_jobs(db, organisation_id=tenant_filter_org_id(user))


@router.get("/{job_id}/download")
def download_report(job_id: str, user: User = Depends(_require), db: Session = Depends(get_db)):
    job = reports.get_report_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report not found")
    org = tenant_filter_org_id(user)
    if org and job.organisation_id != org:
        raise HTTPException(status_code=403, detail="Access denied")
    if job.status != "completed" or not job.file_path:
        raise HTTPException(status_code=400, detail="Report not ready")
    path = Path(job.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report file missing")
    return FileResponse(path, filename=path.name)
