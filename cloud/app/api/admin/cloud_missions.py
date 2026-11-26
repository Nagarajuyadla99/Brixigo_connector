"""Cloud Missions admin API — URL-push (Port 80) devices and punches."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AttendancePunch, Device, Organisation, User
from app.security.auth import get_current_user, tenant_filter_org_id
from app.security.rbac import can_access
from app.services.common import write_audit
from app.services.stl_attendance import MANUFACTURER, SOURCE

router = APIRouter(prefix="/cloud-missions", tags=["Cloud Missions"])


def _require(user: User = Depends(get_current_user)) -> User:
    if not (
        can_access(user.role, "attendance")
        or can_access(user.role, "devices")
        or can_access(user.role, "reports")
    ):
        raise HTTPException(status_code=403, detail="Cloud Missions access denied")
    return user


def _resolve_org(user: User, db: Session, organisation_id: str | None) -> Organisation:
    org_id = organisation_id or tenant_filter_org_id(user) or user.organisation_id
    if not org_id:
        raise HTTPException(status_code=400, detail="Organisation context required")
    org = db.get(Organisation, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    scoped = tenant_filter_org_id(user)
    if scoped and org.id != scoped:
        raise HTTPException(status_code=403, detail="Organisation access denied")
    return org


def _public_base(request: Request) -> str:
    # Prefer forwarded host when behind nginx for connector.brixigo.com
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or "").split(",")[0].strip()
    if not host:
        host = "connector.brixigo.com"
    return f"{proto}://{host}".rstrip("/")


class CloudMissionSettingsIn(BaseModel):
    stl_sid: str = Field(default="", max_length=16, description="5-digit Silicon Wireless Org ID (SID)")
    organisation_id: Optional[str] = None


class CloudMissionSettingsOut(BaseModel):
    organisation_id: str
    organisation_name: str
    organisation_code: str
    stl_sid: str
    get_url: str
    post_url: str
    aspx_url: str
    sms_http_example: str
    sms_https_example: str
    setup_hint: str


def _settings_out(request: Request, org: Organisation) -> CloudMissionSettingsOut:
    settings = org.settings_json or {}
    sid = str(settings.get("stl_sid") or settings.get("cloud_mission_sid") or "").strip()
    base = _public_base(request)
    get_url = f"{base}/attendance?"
    post_url = f"{base}/attservice?"
    aspx_url = f"{base}/rfid/attendance.aspx?"
    host_path = get_url.replace("https://", "").replace("http://", "")
    return CloudMissionSettingsOut(
        organisation_id=org.id,
        organisation_name=org.name,
        organisation_code=org.code,
        stl_sid=sid,
        get_url=get_url,
        post_url=post_url,
        aspx_url=aspx_url,
        sms_http_example=f"*555#001#{host_path}#80#",
        sms_https_example=f"*555#001#{host_path}#443#",
        setup_hint=(
            "Set STL SID (5 digits) here, then SMS the device host URL on Port 80. "
            "Each login punch is pushed to connector.brixigo.com and forwarded to a.brixigo.com."
        ),
    )


@router.get("/settings", response_model=CloudMissionSettingsOut)
def get_settings(
    request: Request,
    organisation_id: str | None = None,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    org = _resolve_org(user, db, organisation_id)
    return _settings_out(request, org)


@router.put("/settings", response_model=CloudMissionSettingsOut)
def save_settings(
    body: CloudMissionSettingsIn,
    request: Request,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    if not can_access(user.role, "tenancy") and not can_access(user.role, "settings"):
        # Org admins with attendance can still bind SID for their org.
        if user.role not in {
            "super_admin",
            "platform_admin",
            "organisation_admin",
            "it_administrator",
        }:
            raise HTTPException(status_code=403, detail="Cannot update Cloud Missions settings")
    org = _resolve_org(user, db, body.organisation_id)
    sid = str(body.stl_sid or "").strip()
    if sid and (not sid.isdigit() or len(sid) > 5):
        raise HTTPException(status_code=400, detail="STL SID must be up to 5 digits")
    if sid:
        sid = sid.zfill(5)
        # Prevent two orgs claiming the same SID.
        for other in db.scalars(select(Organisation).where(Organisation.id != org.id)).all():
            other_sid = str((other.settings_json or {}).get("stl_sid") or "").strip()
            if other_sid and other_sid == sid:
                raise HTTPException(status_code=400, detail=f"STL SID {sid} already used by another organisation")
    settings = dict(org.settings_json or {})
    settings["stl_sid"] = sid
    settings["cloud_mission_sid"] = sid
    org.settings_json = settings
    db.flush()
    write_audit(
        db,
        organisation_id=org.id,
        actor_id=user.id,
        action="cloud_missions.settings.update",
        resource_type="organisation",
        resource_id=org.id,
        details={"stl_sid": sid},
    )
    return _settings_out(request, org)


def _is_cloud_mission_punch(punch: AttendancePunch) -> bool:
    meta = punch.metadata_json or {}
    source = str(meta.get("source") or "").strip().lower()
    return source in {SOURCE, "stl", "silicon-wireless", "url-mission"}


def _is_cloud_mission_device(device: Device) -> bool:
    inv = device.inventory_json or {}
    if str(inv.get("source") or "").strip().lower() == SOURCE:
        return True
    if str(device.manufacturer or "").strip().lower() in {MANUFACTURER, "silicon", "stl"}:
        return True
    if str(device.communication_type or "").strip().lower() in {"http", "adms"} and int(device.port or 0) in {80, 443}:
        return True
    return False


@router.get("/punches")
def list_punches(
    organisation_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    terminal_code: str | None = None,
    employee_code: str | None = None,
    today: bool = False,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    org = _resolve_org(user, db, organisation_id)
    stmt = (
        select(AttendancePunch)
        .where(AttendancePunch.organisation_id == org.id)
        .order_by(AttendancePunch.created_at.desc())
        .limit(min(limit * 3, 2000))
    )
    if terminal_code:
        stmt = stmt.where(AttendancePunch.terminal_code == terminal_code.strip())
    if employee_code:
        code = employee_code.strip()
        stmt = stmt.where(
            (AttendancePunch.device_employee_code == code)
            | (AttendancePunch.device_employee_code.like(f"%{code}"))
        )
    rows = list(db.scalars(stmt).all())
    punches = [p for p in rows if _is_cloud_mission_punch(p)]
    if today:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        punches = [
            p
            for p in punches
            if str(p.punch_time or "").startswith(day) or str(p.created_at or "").startswith(day)
        ]
    punches = punches[:limit]

    device_ids = {p.device_id for p in punches if p.device_id}
    devices = {
        d.id: d
        for d in db.scalars(select(Device).where(Device.id.in_(device_ids))).all()
    } if device_ids else {}

    out: list[dict[str, Any]] = []
    for p in punches:
        meta = p.metadata_json or {}
        device = devices.get(p.device_id)
        out.append(
            {
                "id": p.id,
                "punch_time": p.punch_time,
                "event_type": p.event_type,
                "device_employee_code": p.device_employee_code,
                "terminal_code": p.terminal_code,
                "device_name": (device.name if device else None) or meta.get("device_name") or p.terminal_code,
                "manufacturer": meta.get("manufacturer") or (device.manufacturer if device else "") or MANUFACTURER,
                "protocol": meta.get("protocol") or "",
                "verification_method": p.verification_method,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "source": meta.get("source") or SOURCE,
            }
        )
    return {"punches": out, "count": len(out), "organisation_id": org.id}


@router.get("/devices")
def list_devices(
    organisation_id: str | None = None,
    user: User = Depends(_require),
    db: Session = Depends(get_db),
):
    org = _resolve_org(user, db, organisation_id)
    rows = list(
        db.scalars(
            select(Device)
            .where(Device.organisation_id == org.id)
            .order_by(Device.updated_at.desc())
        ).all()
    )
    devices = [d for d in rows if _is_cloud_mission_device(d)]
    return {
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "terminal_code": d.terminal_code,
                "serial_number": d.serial_number,
                "manufacturer": d.manufacturer,
                "model": d.model,
                "port": d.port,
                "communication_type": d.communication_type,
                "status": d.status,
                "attendance_count": d.attendance_count,
                "last_attendance_at": d.last_attendance_at.isoformat() if d.last_attendance_at else None,
                "stl_sid": (d.inventory_json or {}).get("stl_sid") or "",
                "stl_mid": (d.inventory_json or {}).get("stl_mid") or "",
            }
            for d in devices
        ],
        "count": len(devices),
        "organisation_id": org.id,
    }
