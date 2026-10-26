"""Firmware package and job API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import User
from app.schemas.admin import FirmwareJobCreate, FirmwareJobOut, FirmwarePackageOut
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services import firmware

router = APIRouter(prefix="/firmware", tags=["Firmware"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not can_access(user.role, "firmware"):
        raise HTTPException(status_code=403, detail="Firmware access denied")
    return user


@router.get("/packages", response_model=list[FirmwarePackageOut])
def list_packages(
    manufacturer: str | None = None,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    return firmware.list_packages(db, organisation_id=tenant_filter_org_id(user), manufacturer=manufacturer)


@router.post("/packages", response_model=FirmwarePackageOut)
async def upload_package(
    manufacturer: str = Form(...),
    version: str = Form(...),
    model: str = Form(""),
    release_notes: str = Form(""),
    organisation_id: str | None = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    org = tenant_filter_org_id(user)
    if org:
        organisation_id = org
    return firmware.register_package(
        db,
        manufacturer=manufacturer,
        version=version,
        model=model,
        release_notes=release_notes,
        organisation_id=organisation_id,
        file_obj=file.file,
        filename=file.filename or "firmware.bin",
        settings=settings,
        actor_id=user.id,
    )


@router.get("/jobs", response_model=list[FirmwareJobOut])
def list_jobs(
    status: str | None = None,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    return firmware.list_firmware_jobs(db, organisation_id=tenant_filter_org_id(user), status=status)


@router.post("/jobs", response_model=list[FirmwareJobOut])
def create_jobs(
    body: FirmwareJobCreate,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return firmware.bulk_upgrade(
        db,
        device_ids=body.device_ids,
        package_id=body.package_id,
        scheduled_at=body.scheduled_at,
        settings=settings,
        actor_id=user.id,
    )
