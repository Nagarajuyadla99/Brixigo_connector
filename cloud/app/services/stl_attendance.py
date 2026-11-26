"""Silicon Wireless STL040 / BAMS URL-push attendance ingest (Port 80 / HTTPS).

Devices call a configured host URL with either:
  - HTTP GET query payload:  ?$SID&MID&RFID&DOT,RFID&DOT...*
  - HTTP POST JSON body:     {"MachineID":"...","Attendance":[...]}

Successful device acknowledgements match the partner manual:
  - GET  → plain text ``$RFID=0#``
  - POST → JSON ``{"DeviceResponse": 0}``
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AttendancePunch, Device, Organisation, new_id, utcnow
from app.services.employees import ensure_employee_from_device
from app.services.punch_classifier import reclassify_inferred_punches
from app.services.punch_time import format_punch_iso

logger = logging.getLogger("brixigo.cloud.stl")

SOURCE = "cloud-mission"
MANUFACTURER = "silicon-wireless"
COMM_TYPE = "http"

GET_ACK_OK = "$RFID=0#"
POST_ACK_OK = {"DeviceResponse": 0}
POST_ACK_ERR = {"DeviceResponse": 1}

_GET_PAYLOAD_RE = re.compile(
    r"^\$?(?P<body>.+?)\*?$",
    re.DOTALL,
)


@dataclass
class ParsedPunch:
    employee_code: str
    punch_time_iso: str
    event_type: str
    raw_punch_time: str
    punch_status: str | None = None


@dataclass
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    organisation_id: str = ""
    terminal_code: str = ""
    device_id: str = ""
    error: str = ""


def parse_stl_datetime(value: str) -> datetime | None:
    """Parse DDMMYYYYHHMMSS → datetime (naive local wall clock)."""
    text = str(value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%d%m%Y%H%M%S")
    except ValueError:
        return None


def punch_status_to_event(status: str | int | None) -> str:
    raw = str(status if status is not None else "0").strip()
    if raw == "1":
        return "OUT"
    return "IN"


def parse_get_payload(raw_query: str) -> tuple[str, str, list[ParsedPunch]]:
    """Parse ``$SID&MID&RFID&DOT,...*`` from the raw URL query string.

    Returns (sid, mid, punches).
    """
    query = (raw_query or "").strip()
    if not query:
        return "", "", []

    # Devices send the payload as the entire query (no key=value).
    # Also tolerate accidental wrapping like attendance=$SID&...
    if "=" in query and not query.lstrip().startswith("$") and "$" in query:
        # e.g. unused=1&$99999&99&...  → take from first $
        query = query[query.index("$") :]
    if query.startswith("attendance="):
        query = query.split("=", 1)[1]

    match = _GET_PAYLOAD_RE.match(query)
    body = match.group("body") if match else query
    body = body.strip().lstrip("$").rstrip("*")
    if not body:
        return "", "", []

    parts = body.split("&")
    if len(parts) < 4:
        return "", "", []

    sid = parts[0].strip()
    mid = parts[1].strip()
    remainder = "&".join(parts[2:])
    records = [r for r in remainder.split(",") if r.strip()]

    punches: list[ParsedPunch] = []
    for record in records:
        fields = [f.strip() for f in record.split("&") if f.strip() != ""]
        if len(fields) < 2:
            continue
        rfid = fields[0]
        dot = fields[1]
        dt = parse_stl_datetime(dot)
        if not rfid or dt is None:
            continue
        punches.append(
            ParsedPunch(
                employee_code=str(rfid).lstrip("0") or "0",
                punch_time_iso=format_punch_iso(dt),
                event_type="IN",
                raw_punch_time=dot,
                punch_status="0",
            )
        )
    return sid, mid, punches


def parse_post_json(body: dict[str, Any]) -> tuple[str, list[ParsedPunch]]:
    """Parse JSON POST body. Returns (machine_id, punches)."""
    machine_id = str(body.get("MachineID") or body.get("machine_id") or "").strip()
    rows = body.get("Attendance") or body.get("attendance") or []
    if not isinstance(rows, list):
        rows = []

    punches: list[ParsedPunch] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        punch_id = str(row.get("PunchID") or row.get("punch_id") or "").strip()
        punch_time = str(row.get("PunchTime") or row.get("punch_time") or "").strip()
        status = row.get("PunchStatus", row.get("punch_status", "0"))
        dt = parse_stl_datetime(punch_time)
        if not punch_id or dt is None:
            continue
        punches.append(
            ParsedPunch(
                employee_code=str(punch_id).lstrip("0") or "0",
                punch_time_iso=format_punch_iso(dt),
                event_type=punch_status_to_event(status),
                raw_punch_time=punch_time,
                punch_status=str(status),
            )
        )
    return machine_id, punches


def resolve_organisation_by_sid(db: Session, sid: str) -> Organisation | None:
    sid = str(sid or "").strip()
    if not sid:
        return None
    # Prefer explicit Cloud Missions SID binding on the organisation.
    for org in db.scalars(select(Organisation)).all():
        settings = org.settings_json or {}
        bound = str(settings.get("stl_sid") or settings.get("cloud_mission_sid") or "").strip()
        if bound and bound == sid:
            return org
    # Fallback: organisation.code equals the device SID.
    org = db.scalar(select(Organisation).where(Organisation.code == sid))
    if org:
        return org
    return None


def resolve_organisation_for_machine(db: Session, machine_id: str) -> tuple[Organisation | None, str, str]:
    """Resolve org + (sid, mid) from a 7-digit MachineID or registered device.

    Returns (org, sid, mid_or_machine).
    """
    machine_id = str(machine_id or "").strip()
    if not machine_id:
        return None, "", ""

    device = db.scalar(
        select(Device).where(
            (Device.terminal_code == machine_id)
            | (Device.serial_number == machine_id)
        ).limit(1)
    )
    if device:
        org = db.get(Organisation, device.organisation_id)
        sid = ""
        settings = (org.settings_json or {}) if org else {}
        sid = str(settings.get("stl_sid") or settings.get("cloud_mission_sid") or "").strip()
        if not sid and len(machine_id) >= 7 and machine_id.isdigit():
            sid = machine_id[:5]
        mid = machine_id[5:] if len(machine_id) >= 7 and machine_id.isdigit() else machine_id
        return org, sid, mid or machine_id

    if len(machine_id) >= 7 and machine_id[:5].isdigit():
        sid = machine_id[:5]
        mid = machine_id[5:]
        org = resolve_organisation_by_sid(db, sid)
        return org, sid, mid

    return None, "", machine_id


def upsert_cloud_mission_device(
    db: Session,
    *,
    organisation_id: str,
    terminal_code: str,
    sid: str = "",
    mid: str = "",
) -> Device:
    terminal = str(terminal_code or "").strip() or f"{sid}{mid}".strip() or "STL"
    device = db.scalar(
        select(Device).where(
            Device.organisation_id == organisation_id,
            Device.terminal_code == terminal,
        )
    )
    if not device:
        device = Device(
            id=new_id(),
            organisation_id=organisation_id,
            connector_id=None,
            name=f"Cloud Mission {mid or terminal}",
            manufacturer=MANUFACTURER,
            model="STL040",
            serial_number=terminal,
            terminal_code=terminal,
            communication_type=COMM_TYPE,
            port=80,
            status="online",
            inventory_json={
                "source": SOURCE,
                "stl_sid": sid,
                "stl_mid": mid,
            },
        )
        db.add(device)
        db.flush()
    else:
        device.manufacturer = device.manufacturer or MANUFACTURER
        device.communication_type = COMM_TYPE
        device.port = 80
        device.status = "online"
        inv = dict(device.inventory_json or {})
        inv.setdefault("source", SOURCE)
        if sid:
            inv["stl_sid"] = sid
        if mid:
            inv["stl_mid"] = mid
        device.inventory_json = inv
    return device


def ingest_punches(
    db: Session,
    *,
    organisation: Organisation,
    device: Device,
    punches: list[ParsedPunch],
    protocol: str,
) -> IngestResult:
    result = IngestResult(
        organisation_id=organisation.id,
        terminal_code=device.terminal_code,
        device_id=device.id,
    )
    created: list[AttendancePunch] = []
    touched: list[str] = []

    for punch in punches:
        emp = str(punch.employee_code or "").strip()
        if not emp or not punch.punch_time_iso:
            result.rejected += 1
            continue
        event_type = punch.event_type if punch.event_type in {"IN", "OUT"} else "IN"
        idem = hashlib.sha256(
            f"{device.terminal_code}|{emp}|{punch.punch_time_iso}|{event_type}|{SOURCE}".encode()
        ).hexdigest()
        existing = db.scalar(
            select(AttendancePunch).where(
                AttendancePunch.organisation_id == organisation.id,
                AttendancePunch.idempotency_key == idem,
            )
        )
        if existing:
            result.duplicates += 1
            continue

        ensure_employee_from_device(
            db,
            organisation_id=organisation.id,
            employee_code=emp,
            name="",
            branch_id=device.branch_id,
            device_id=device.id,
            source=SOURCE,
            extra={"device_name": device.name, "protocol": protocol},
        )
        meta = {
            "source": SOURCE,
            "manufacturer": MANUFACTURER,
            "protocol": protocol,
            "device_punch_time": punch.raw_punch_time,
            "punch_status": punch.punch_status,
            "device_name": device.name,
            "contract_version": "stl040-1.9",
            "payroll_ready": True,
            "direction_reported": punch.punch_status is not None and protocol == "json_post",
        }
        row = AttendancePunch(
            id=new_id(),
            organisation_id=organisation.id,
            connector_id="",
            device_id=device.id,
            terminal_code=device.terminal_code,
            device_employee_code=emp,
            punch_time=punch.punch_time_iso,
            event_type=event_type,
            verification_method="fingerprint",
            biometric_reference="",
            idempotency_key=idem,
            metadata_json=meta,
        )
        db.add(row)
        created.append(row)
        touched.append(emp)
        result.accepted += 1
        device.last_attendance_at = utcnow()
        device.attendance_count = int(device.attendance_count or 0) + 1
        device.last_sync_at = utcnow()

    if touched:
        reclassify_inferred_punches(
            db,
            organisation_id=organisation.id,
            employee_codes=touched,
        )
    db.flush()

    try:
        from app.services.hrms_outbound import enqueue_punch

        for row in created:
            enqueue_punch(db, row, organisation)
        db.flush()
    except Exception:
        logger.exception("HRMS outbox enqueue failed for cloud mission punch; storage intact")

    return result


def ingest_get_query(db: Session, raw_query: str) -> IngestResult:
    sid, mid, punches = parse_get_payload(raw_query)
    if not sid or not mid:
        return IngestResult(error="missing_sid_or_mid", rejected=len(punches))
    org = resolve_organisation_by_sid(db, sid)
    if not org:
        return IngestResult(error="organisation_not_found", rejected=len(punches))
    terminal = f"{sid}{mid}"
    device = upsert_cloud_mission_device(
        db,
        organisation_id=org.id,
        terminal_code=terminal,
        sid=sid,
        mid=mid,
    )
    if not punches:
        return IngestResult(
            organisation_id=org.id,
            terminal_code=terminal,
            device_id=device.id,
            error="no_records",
        )
    return ingest_punches(
        db,
        organisation=org,
        device=device,
        punches=punches,
        protocol="http_get",
    )


def ingest_post_json(db: Session, body: dict[str, Any]) -> IngestResult:
    machine_id, punches = parse_post_json(body if isinstance(body, dict) else {})
    if not machine_id:
        return IngestResult(error="missing_machine_id", rejected=len(punches))
    org, sid, mid = resolve_organisation_for_machine(db, machine_id)
    if not org:
        return IngestResult(error="organisation_not_found", rejected=len(punches))
    device = upsert_cloud_mission_device(
        db,
        organisation_id=org.id,
        terminal_code=machine_id,
        sid=sid,
        mid=mid or machine_id,
    )
    if not punches:
        return IngestResult(
            organisation_id=org.id,
            terminal_code=machine_id,
            device_id=device.id,
            error="no_records",
        )
    return ingest_punches(
        db,
        organisation=org,
        device=device,
        punches=punches,
        protocol="json_post",
    )
