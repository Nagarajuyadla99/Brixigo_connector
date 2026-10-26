"""Firmware package registry and upgrade jobs."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Connector, Device, FirmwareJob, FirmwarePackage, new_id
from app.services.commands import create_command
from app.services.common import write_audit


def _file_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def register_package(
    db: Session,
    *,
    manufacturer: str,
    version: str,
    file_obj: BinaryIO,
    filename: str,
    organisation_id: str | None = None,
    model: str = "",
    release_notes: str = "",
    settings: Settings,
    actor_id: str = "",
) -> FirmwarePackage:
    dest_name = f"{manufacturer}_{model}_{version}_{new_id()[:8]}{Path(filename).suffix}"
    dest_path = settings.firmware_dir / dest_name
    with dest_path.open("wb") as out:
        shutil.copyfileobj(file_obj, out)

    checksum = _file_checksum(dest_path)
    size = dest_path.stat().st_size

    if organisation_id:
        existing = db.scalars(
            select(FirmwarePackage).where(
                FirmwarePackage.organisation_id == organisation_id,
                FirmwarePackage.manufacturer == manufacturer,
                FirmwarePackage.model == model,
            )
        ).all()
        for pkg in existing:
            pkg.is_latest = False

    pkg = FirmwarePackage(
        id=new_id(),
        organisation_id=organisation_id,
        manufacturer=manufacturer,
        model=model,
        version=version,
        file_path=str(dest_path),
        checksum=checksum,
        size_bytes=size,
        release_notes=release_notes,
        is_latest=True,
    )
    db.add(pkg)
    db.flush()
    write_audit(
        db,
        organisation_id=organisation_id or "",
        actor_id=actor_id,
        action="firmware.package_register",
        resource_type="firmware_package",
        resource_id=pkg.id,
        message=f"Registered firmware {manufacturer} {version}",
    )
    return pkg


def list_packages(
    db: Session,
    *,
    organisation_id: str | None = None,
    manufacturer: str | None = None,
) -> list[FirmwarePackage]:
    q = select(FirmwarePackage).order_by(FirmwarePackage.created_at.desc())
    if organisation_id:
        q = q.where(
            (FirmwarePackage.organisation_id == organisation_id)
            | (FirmwarePackage.organisation_id.is_(None))
        )
    if manufacturer:
        q = q.where(FirmwarePackage.manufacturer == manufacturer)
    return list(db.scalars(q))


def create_firmware_job(
    db: Session,
    *,
    device_id: str,
    package_id: str,
    scheduled_at: datetime | None = None,
    settings: Settings,
    actor_id: str = "",
) -> FirmwareJob | None:
    device = db.get(Device, device_id)
    pkg = db.get(FirmwarePackage, package_id)
    if not device or not pkg or not device.connector_id:
        return None

    job = FirmwareJob(
        id=new_id(),
        organisation_id=device.organisation_id,
        device_id=device.id,
        connector_id=device.connector_id,
        package_id=pkg.id,
        action="upgrade",
        from_version=device.firmware,
        to_version=pkg.version,
        status="queued" if not scheduled_at else "scheduled",
        scheduled_at=scheduled_at,
    )
    db.add(job)
    db.flush()

    if not scheduled_at:
        create_command(
            db,
            organisation_id=device.organisation_id,
            connector_id=device.connector_id,
            command_type="firmware_upgrade",
            device_id=device.id,
            payload={
                "package_id": pkg.id,
                "version": pkg.version,
                "checksum": pkg.checksum,
                "file_path": pkg.file_path,
                "job_id": job.id,
            },
            created_by=actor_id,
            settings=settings,
        )

    write_audit(
        db,
        organisation_id=device.organisation_id,
        actor_id=actor_id,
        action="firmware.job_create",
        resource_type="firmware_job",
        resource_id=job.id,
        message=f"Firmware upgrade {device.firmware} → {pkg.version}",
    )
    return job


def bulk_upgrade(
    db: Session,
    *,
    device_ids: list[str],
    package_id: str,
    scheduled_at: datetime | None = None,
    settings: Settings,
    actor_id: str = "",
) -> list[FirmwareJob]:
    jobs = []
    for did in device_ids:
        job = create_firmware_job(
            db,
            device_id=did,
            package_id=package_id,
            scheduled_at=scheduled_at,
            settings=settings,
            actor_id=actor_id,
        )
        if job:
            jobs.append(job)
    return jobs


def list_firmware_jobs(
    db: Session,
    *,
    organisation_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[FirmwareJob]:
    q = select(FirmwareJob).order_by(FirmwareJob.created_at.desc())
    if organisation_id:
        q = q.where(FirmwareJob.organisation_id == organisation_id)
    if status:
        q = q.where(FirmwareJob.status == status)
    return list(db.scalars(q.limit(limit)))
