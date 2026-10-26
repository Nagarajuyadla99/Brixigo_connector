"""Connector agent APIs — exact contracts expected by on-premise Brixigo Connector."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import platform
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    AttendancePunch,
    CommandState,
    Connector,
    ConnectorStatus,
    Device,
    DeviceHealthSample,
    EmployeeSyncEvent,
    HeartbeatHistory,
    Organisation,
    ReportJob,
    SupportArtifact,
    SystemPolicy,
    new_id,
    utcnow,
)
from app.security.auth import (
    generate_api_key,
    get_connector_from_headers,
    hash_api_key,
)
from app.services.names import normalize_identity, resolve_organisation
from app.services.commands import ack_command, list_commands
from app.services.common import health_score_from_metrics, parse_iso, raise_notification, write_audit
from app.services.employees import (
    ensure_employee_from_device,
    import_employees_from_directory,
    reported_person_name,
)
from app.services.punch_classifier import reclassify_inferred_punches
from app.services.punch_time import correct_unset_device_clock, format_punch_iso

router = APIRouter(tags=["Connector Agent"])
logger = logging.getLogger("brixigo.cloud")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class AgentRegisterBody(BaseModel):
    connector_id: Optional[str] = None
    hostname: str = ""
    version: str = ""
    organisation_id: str = ""
    organisation_name: str = ""
    name: str = ""
    operating_system: str = ""
    ip_address: str = ""


class HeartbeatBody(BaseModel):
    connector_version: str = ""
    status: str = "online"
    cpu_percent: float = 0
    ram_percent: float = 0
    disk_percent: float = 0
    connected_devices: int = 0
    offline_devices: int = 0
    last_sync: Optional[str] = None
    pending_queue: int = 0
    failed_uploads: int = 0
    today_punches: int = 0
    average_upload_ms: float = 0
    last_error: str = ""
    last_restart_at: Optional[str] = None
    organisation_id: str = ""
    created_at: Optional[str] = None
    internet_status: str = ""
    server_status: str = ""
    tls_status: str = ""
    authentication_status: str = ""
    sync_status: str = ""
    connection_states: dict[str, Any] = Field(default_factory=dict)
    employees: list[dict[str, Any]] = Field(default_factory=list)


class PushBody(BaseModel):
    punches: list[dict[str, Any]] = Field(default_factory=list)
    employees: list[dict[str, Any]] = Field(default_factory=list)
    source: str = ""
    version: str = ""


class DeviceHealthBody(BaseModel):
    snapshots: list[dict[str, Any]] = Field(default_factory=list)


class DeviceStatusBody(BaseModel):
    devices: list[dict[str, Any]] = Field(default_factory=list)


class CommandAckBody(BaseModel):
    status: str
    message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    connector_version: str = ""


class EmployeeDirectoryBody(BaseModel):
    employees: list[dict[str, Any]] = Field(default_factory=list)
    users: list[dict[str, Any]] = Field(default_factory=list)


class InventoryBody(BaseModel):
    devices: list[dict[str, Any]] = Field(default_factory=list)


class EmployeeSyncAckBody(BaseModel):
    status: str = "applied"


class DeviceRegisterBody(BaseModel):
    model_config = {"extra": "allow"}

    id: str = ""
    device_id: str = ""
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial_number: str = ""
    firmware: str = ""
    sdk_version: str = ""
    mac_address: str = ""
    ip_address: str = ""
    port: int = 4370
    terminal_code: str = ""
    communication_type: str = "tcp"
    status: str = ""
    enabled: bool = True
    connector_id: str = ""
    organisation_id: str = ""
    branch_id: str = ""
    department: str = ""
    building: str = ""
    floor: str = ""
    gate: str = ""
    inventory: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    encoding = (request.headers.get("content-encoding") or "").lower()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _serialize_command(cmd) -> dict[str, Any]:
    meta = (cmd.result_json or {}).get("_signing") if isinstance(cmd.result_json, dict) else None
    if isinstance(meta, dict) and meta.get("issued_at") and meta.get("expires_at"):
        issued = str(meta["issued_at"])
        expires = str(meta["expires_at"])
    else:
        issued = cmd.issued_at.isoformat() if cmd.issued_at else ""
        expires = cmd.expires_at.isoformat() if cmd.expires_at else ""
    return {
        "id": cmd.id,
        "command_id": cmd.id,
        "command_type": cmd.command_type,
        "type": cmd.command_type,
        "device_id": cmd.device_id or "",
        "payload": cmd.payload_json or {},
        "signature": cmd.signature or "",
        "nonce": cmd.nonce or "",
        "issued_at": issued,
        "expires_at": expires,
        "status": cmd.status,
    }


def _normalize_device_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"connected", "online", "running", "healthy", "ok", "up"}:
        return "online"
    if value in {"disconnected", "offline", "error", "down", "unreachable"}:
        return "offline"
    return value or "unknown"


def _upsert_device_from_payload(
    db: Session,
    *,
    organisation_id: str,
    connector_id: str,
    payload: dict[str, Any],
) -> Device:
    terminal = str(payload.get("terminal_code") or payload.get("device_code") or "").strip()
    serial = str(payload.get("serial_number") or payload.get("serial") or "").strip()
    # Prefer stable connector-local ids; ignore ephemeral health-sample ids.
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        candidate = str(payload.get("id") or "").strip()
        # Health snapshots use their own sample id — only treat as device id when
        # terminal/name/ip are also present (registration / inventory payloads).
        if candidate and (
            terminal
            or payload.get("name")
            or payload.get("ip_address")
            or payload.get("serial_number")
        ):
            device_id = candidate
    ip_address = str(payload.get("ip_address") or "").strip()

    device: Device | None = None
    if device_id:
        device = db.get(Device, device_id)
        if device is not None and device.organisation_id != organisation_id:
            raise HTTPException(
                status_code=403,
                detail="Device belongs to another organisation",
            )
    if not device and terminal:
        device = db.scalar(
            select(Device).where(
                Device.organisation_id == organisation_id,
                Device.terminal_code == terminal,
            )
        )
    if not device and serial:
        device = db.scalar(
            select(Device).where(
                Device.organisation_id == organisation_id,
                Device.serial_number == serial,
            )
        )
    if not device and ip_address and connector_id:
        device = db.scalar(
            select(Device).where(
                Device.organisation_id == organisation_id,
                Device.connector_id == connector_id,
                Device.ip_address == ip_address,
            )
        )
    # Reclaim nameless stubs created by older health pushes (empty terminal).
    if not device and connector_id and (terminal or ip_address or device_id):
        stub = db.scalar(
            select(Device).where(
                Device.organisation_id == organisation_id,
                Device.connector_id == connector_id,
                Device.terminal_code == "",
            )
        )
        if stub is not None and (
            not stub.name
            or stub.name == "Device"
            or not stub.ip_address
            or (ip_address and stub.ip_address == ip_address)
        ):
            device = stub

    if not device:
        device = Device(
            id=device_id or new_id(),
            organisation_id=organisation_id,
            connector_id=connector_id,
            name=str(payload.get("name") or terminal or serial or ip_address or "Device"),
            terminal_code=terminal,
        )
        db.add(device)

    device.connector_id = connector_id or device.connector_id
    if payload.get("name"):
        device.name = str(payload["name"])
    for field, attr in (
        ("manufacturer", "manufacturer"),
        ("model", "model"),
        ("serial_number", "serial_number"),
        ("firmware", "firmware"),
        ("firmware_version", "firmware"),
        ("sdk_version", "sdk_version"),
        ("mac_address", "mac_address"),
        ("ip_address", "ip_address"),
        ("communication_type", "communication_type"),
        ("warranty_until", "warranty_until"),
        ("installation_date", "installation_date"),
    ):
        if payload.get(field) is not None:
            setattr(device, attr, str(payload.get(field) or ""))
    if terminal:
        device.terminal_code = terminal
    if payload.get("port") is not None:
        try:
            device.port = int(payload["port"])
        except (TypeError, ValueError):
            pass
    status = payload.get("status")
    if status is None and "online" in payload:
        status = "online" if payload.get("online") else "offline"
    if status is not None:
        device.status = _normalize_device_status(status)
    if "enabled" in payload:
        device.enabled = bool(payload["enabled"])

    inv = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    if inv:
        device.inventory_json = {**(device.inventory_json or {}), **inv}
    labels = {
        k: str(payload.get(k) or "")
        for k in ("department", "building", "floor", "gate", "branch")
        if payload.get(k)
    }
    if labels:
        device.location_labels = {**(device.location_labels or {}), **labels}

    db.flush()
    return device


# ---------------------------------------------------------------------------
# Public health + time (no auth)
# ---------------------------------------------------------------------------


@router.get("/time")
def server_time():
    now = datetime.now(timezone.utc)
    return {
        "utc": now.isoformat(),
        "unix": int(now.timestamp()),
        "timezone": "UTC",
    }


@router.get("/connectors/updates/latest")
def latest_update(version: str = Query(default="")):
    """Connector self-update check — cloud currently reports no mandatory update."""
    return {
        "update_available": False,
        "current_version": version,
        "latest_version": version or "3.0.0",
        "download_url": "",
        "release_notes": "",
        "mandatory": False,
    }


# ---------------------------------------------------------------------------
# Registration (bootstrap — authenticated by org existence + optional key)
# ---------------------------------------------------------------------------


@router.post("/connectors/register")
def register_connector(
    body: AgentRegisterBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    x_organisation_id: Optional[str] = Header(default=None, alias="X-Organisation-Id"),
):
    org_id = body.organisation_id or x_organisation_id or ""
    org = None
    if body.organisation_name:
        org = resolve_organisation(db, body.organisation_name)
    if not org and org_id:
        org = resolve_organisation(db, org_id)
    if not org:
        raise HTTPException(status_code=400, detail="organisation_name or organisation_id required")
    org_id = org.id

    connector: Connector | None = None
    if body.connector_id:
        connector = db.get(Connector, body.connector_id)

    api_key_out: str | None = None

    if connector:
        # Re-registration / heartbeat identity refresh
        if x_device_token:
            if not hmac_compare_api_key(connector.api_key_hash, x_device_token):
                raise HTTPException(status_code=401, detail="Invalid device token")
        connector.hostname = body.hostname or connector.hostname
        connector.version = body.version or connector.version
        connector.operating_system = body.operating_system or connector.operating_system or platform.system()
        connector.ip_address = body.ip_address or connector.ip_address
        connector.cloud_status = "online"
        connector.internet_status = "online"
        if body.name:
            connector.name = body.name
        connector.is_active = True
        if connector.status == ConnectorStatus.DEACTIVATED.value:
            raise HTTPException(status_code=403, detail="Connector deactivated")
        db.flush()
    else:
        api_key_out = generate_api_key()
        connector = Connector(
            id=body.connector_id or new_id(),
            organisation_id=org_id,
            name=body.name or body.hostname or f"Connector-{body.hostname or 'new'}",
            api_key_hash=hash_api_key(api_key_out),
            api_key_prefix=api_key_out[:12],
            hostname=body.hostname,
            version=body.version,
            operating_system=body.operating_system or platform.system(),
            ip_address=body.ip_address,
            status=ConnectorStatus.ONLINE.value,
            internet_status="online",
            cloud_status="online",
            certificate_status="valid",
            installation_date=utcnow(),
            is_active=True,
            config_json={
                "command_signing_secret": settings.command_hmac_secret,
                "poll_interval": 30,
            },
        )
        db.add(connector)
        db.flush()
        write_audit(
            db,
            organisation_id=org_id,
            action="connector.agent_register",
            resource_type="connector",
            resource_id=connector.id,
            message=f"Connector registered hostname={body.hostname}",
        )

    response: dict[str, Any] = {
        "ok": True,
        "connector_id": connector.id,
        "organisation_id": connector.organisation_id,
        "organisation_name": org.name,
        "name": connector.name,
        "status": connector.status,
        "command_signing_secret": settings.command_hmac_secret,
        "config": connector.config_json or {},
    }
    if api_key_out:
        response["api_key"] = api_key_out
        response["device_token"] = api_key_out
    return response


def hmac_compare_api_key(stored_hash: str, token: str) -> bool:
    import hmac as _hmac

    return _hmac.compare_digest(stored_hash, hash_api_key(token))


def _assert_connector_path(connector: Connector, connector_id: str) -> None:
    """Accept Cloud UUID or the human connector name in the URL path."""
    ref = (connector_id or "").strip()
    if ref in ("", "unregistered"):
        return
    if ref in (connector.id, connector.name):
        return
    if normalize_identity(ref) in (
        normalize_identity(connector.id),
        normalize_identity(connector.name),
    ):
        return
    raise HTTPException(status_code=403, detail="Connector id mismatch")


# ---------------------------------------------------------------------------
# Authenticated connector endpoints
# ---------------------------------------------------------------------------


@router.get("/connectors/{connector_id}/config")
def get_config(
    connector_id: str,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _assert_connector_path(connector, connector_id)
    policies = {
        p.policy_key: p.policy_json
        for p in db.scalars(
            select(SystemPolicy).where(
                (SystemPolicy.organisation_id == connector.organisation_id)
                | (SystemPolicy.organisation_id == "")
            )
        ).all()
    }
    return {
        "connector_id": connector.id,
        "organisation_id": connector.organisation_id,
        "branch_id": connector.branch_id,
        "name": connector.name,
        "config": connector.config_json or {},
        "policies": policies,
        "command_signing_secret": settings.command_hmac_secret,
        "heartbeat_interval": settings.heartbeat_stale_seconds // 3 or 60,
        "features": {
            "employee_sync": True,
            "commands": True,
            "health": True,
            "inventory": True,
            "support": True,
        },
    }


@router.post("/connectors/{terminal_code}/push")
async def push_attendance(
    terminal_code: str,
    request: Request,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    body = await _read_json_body(request)
    punches = body.get("punches") if isinstance(body.get("punches"), list) else []
    first = next((p for p in punches if isinstance(p, dict)), {}) or {}
    device = _upsert_device_from_payload(
        db,
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        payload={
            "device_id": str(
                body.get("device_id")
                or first.get("device_id")
                or ""
            ).strip(),
            "terminal_code": terminal_code,
            "name": str(
                body.get("device_name")
                or first.get("device_name")
                or ""
            ).strip(),
            "manufacturer": str(
                body.get("manufacturer")
                or first.get("manufacturer")
                or ""
            ).strip(),
            "branch": str(body.get("branch") or first.get("branch") or "").strip(),
            "status": "online",
        },
    )
    accepted = 0
    duplicates = 0
    rejected = 0
    reject_reasons: list[str] = []
    touched_codes: list[str] = []
    created_rows: list[AttendancePunch] = []
    for punch in punches:
        if not isinstance(punch, dict):
            rejected += 1
            continue
        emp = str(punch.get("device_employee_code") or punch.get("employee_code") or "").strip()
        punch_time = str(punch.get("punch_time") or "").strip()
        collected = punch.get("created_at") or body.get("created_at")
        source = (
            (punch.get("metadata") or {}).get("device_punch_time")
            if isinstance(punch.get("metadata"), dict)
            else None
        ) or punch_time
        fixed = correct_unset_device_clock(source, collected)
        if fixed is not None:
            punch_time = format_punch_iso(fixed)
        if not emp or not punch_time:
            rejected += 1
            if len(reject_reasons) < 5:
                reject_reasons.append("missing employee_code or punch_time")
            continue
        event_type = str(punch.get("event_type") or "UNKNOWN").upper()
        if event_type not in {
            "IN",
            "OUT",
            "BREAK_OUT",
            "BREAK_IN",
            "OT_IN",
            "OT_OUT",
            "UNKNOWN",
        }:
            event_type = "UNKNOWN"
        idem = str(
            punch.get("idempotency_key")
            or hashlib.sha256(f"{terminal_code}|{emp}|{punch_time}|{event_type}".encode()).hexdigest()
        )
        existing = db.scalar(
            select(AttendancePunch).where(
                AttendancePunch.organisation_id == connector.organisation_id,
                AttendancePunch.idempotency_key == idem,
            )
        )
        if existing:
            duplicates += 1
            continue
        touched_codes.append(emp)
        meta = dict(punch.get("metadata") or {})
        if punch.get("punch_time") and punch_time != str(punch.get("punch_time")):
            meta.setdefault("device_punch_time", punch.get("punch_time"))
            meta["clock_corrected"] = True
        meta["contract_version"] = str(punch.get("contract_version") or meta.get("contract_version") or "1.0")
        meta["payroll_ready"] = bool(punch.get("payroll_ready", True))
        meta["source"] = str(punch.get("source") or body.get("source") or "brixigo-connector")
        if punch.get("manufacturer"):
            meta["manufacturer"] = punch.get("manufacturer")
        if punch.get("branch"):
            meta["branch"] = punch.get("branch")
        if punch.get("device_name"):
            meta["device_name"] = punch.get("device_name")
        classification = str(
            punch.get("classification_method")
            or meta.get("classification_method")
            or ""
        ).strip()
        if classification:
            meta["classification_method"] = classification
        if punch.get("classification_reason"):
            meta.setdefault("classification_reason", punch.get("classification_reason"))
        if "direction_reported" in punch:
            meta["direction_reported"] = bool(punch.get("direction_reported"))
        elif "direction_reported" in meta:
            meta["direction_reported"] = bool(meta.get("direction_reported"))
        person_name = reported_person_name(
            punch.get("employee_name")
            or meta.get("employee_name")
            or meta.get("user_name")
            or meta.get("name"),
            emp,
        )
        if person_name:
            meta["employee_name"] = person_name
        ensure_employee_from_device(
            db,
            organisation_id=connector.organisation_id,
            employee_code=emp,
            name=person_name,
            branch_id=connector.branch_id,
            device_id=device.id,
            source="device_punch",
            extra={
                "branch": str(meta.get("branch") or ""),
                "device_name": str(meta.get("device_name") or ""),
            },
        )
        row = AttendancePunch(
            id=new_id(),
            organisation_id=connector.organisation_id,
            connector_id=connector.id,
            device_id=device.id,
            terminal_code=terminal_code,
            device_employee_code=emp,
            punch_time=punch_time,
            event_type=event_type,
            verification_method=str(punch.get("verification_method") or "not_reported"),
            biometric_reference=str(punch.get("biometric_reference") or ""),
            idempotency_key=idem,
            metadata_json=meta,
        )
        db.add(row)
        accepted += 1
        created_rows.append(row)
        device.last_attendance_at = utcnow()
        device.attendance_count = int(device.attendance_count or 0) + 1
    people = body.get("employees") if isinstance(body.get("employees"), list) else []
    if people:
        import_employees_from_directory(
            db,
            organisation_id=connector.organisation_id,
            people=people,
            branch_id=connector.branch_id,
        )
    reclassify_inferred_punches(
        db,
        organisation_id=connector.organisation_id,
        employee_codes=touched_codes,
    )
    db.flush()
    try:
        from app.models import Organisation as OrgModel
        from app.services.hrms_outbound import enqueue_punch

        org = db.get(OrgModel, connector.organisation_id)
        for row in created_rows:
            enqueue_punch(db, row, org)
        db.flush()
    except Exception:
        logger.exception("HRMS outbox enqueue failed; cloud punch storage is intact")
    return {
        "ok": True,
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "reject_reasons": reject_reasons,
        "terminal_code": terminal_code,
        "contract_version": "1.0",
        "payroll_ready": True,
    }


@router.post("/connectors/{connector_id}/heartbeat")
def heartbeat(
    connector_id: str,
    body: HeartbeatBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _assert_connector_path(connector, connector_id)

    connector.version = body.connector_version or connector.version
    raw_status = str(body.status or "").strip().lower()
    if raw_status in {"running", "connected", "ok", "healthy", "up"}:
        raw_status = ConnectorStatus.ONLINE.value
    connector.status = raw_status or ConnectorStatus.ONLINE.value
    connector.cpu_percent = body.cpu_percent
    connector.ram_percent = body.ram_percent
    connector.disk_percent = body.disk_percent
    connector.queue_size = body.pending_queue
    connector.pending_upload = body.pending_queue
    connector.last_error = body.last_error or ""
    connector.last_heartbeat_at = utcnow()
    connector.cloud_status = body.server_status or "online"
    connector.internet_status = body.internet_status or "online"
    if body.tls_status:
        connector.certificate_status = body.tls_status
    if body.connection_states:
        cfg = dict(connector.config_json or {})
        cfg["connection_states"] = body.connection_states
        connector.config_json = cfg
        for item in body.connection_states.get("devices") or []:
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("device_id") or "").strip()
            ip_address = str(item.get("ip_address") or "").strip()
            name = str(item.get("name") or "").strip()
            if not (device_id or ip_address or name):
                continue
            status = _normalize_device_status(
                item.get("display_status") or item.get("status")
            )
            device = _upsert_device_from_payload(
                db,
                organisation_id=connector.organisation_id,
                connector_id=connector.id,
                payload={
                    "device_id": device_id,
                    "name": name,
                    "ip_address": ip_address,
                    "port": item.get("port"),
                    "status": status,
                    "terminal_code": str(item.get("terminal_code") or "").strip(),
                    "serial_number": str(item.get("serial_number") or "").strip(),
                },
            )
            if status == "online":
                device.health_status = "healthy" if device.health_score >= 70 else (device.health_status or "healthy")
            elif status == "offline":
                device.health_status = "offline"
            if item.get("last_latency_ms") is not None:
                try:
                    device.response_time_ms = float(item["last_latency_ms"])
                except (TypeError, ValueError):
                    pass
            device.last_health_at = utcnow()
    # Prefer live device rows over agent-reported counters so admin pages stay aligned.
    devices = list(db.scalars(select(Device).where(Device.connector_id == connector.id)))
    if devices:
        online = sum(1 for d in devices if str(d.status or "").lower() in {"online", "connected", "healthy"})
        connector.connected_devices = len(devices)
        connector.offline_devices = max(0, len(devices) - online)
    else:
        connector.connected_devices = body.connected_devices
        connector.offline_devices = body.offline_devices
    if body.last_restart_at:
        connector.last_restart_at = parse_iso(body.last_restart_at) or connector.last_restart_at

    hist = HeartbeatHistory(
        id=new_id(),
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        payload_json=body.model_dump(),
    )
    db.add(hist)

    if body.employees:
        import_employees_from_directory(
            db,
            organisation_id=connector.organisation_id,
            people=body.employees,
            branch_id=connector.branch_id,
        )

    if body.pending_queue >= settings.queue_overflow_threshold:
        raise_notification(
            db,
            organisation_id=connector.organisation_id,
            event_type="queue_overflow",
            title="Connector queue overflow",
            message=f"Pending queue size {body.pending_queue}",
            severity="critical",
            connector_id=connector.id,
            details={"pending_queue": body.pending_queue},
        )
    db.flush()
    return {
        "ok": True,
        "connector_id": connector.id,
        "server_time": utcnow().isoformat(),
        "status": connector.status,
    }


@router.get("/connectors/{connector_id}/status")
def connector_status(
    connector_id: str,
    connector: Connector = Depends(get_connector_from_headers),
):
    _assert_connector_path(connector, connector_id)
    return {
        "connector_id": connector.id,
        "status": connector.status,
        "is_active": connector.is_active,
        "last_heartbeat_at": connector.last_heartbeat_at.isoformat() if connector.last_heartbeat_at else None,
        "connected_devices": connector.connected_devices,
        "offline_devices": connector.offline_devices,
        "queue_size": connector.queue_size,
        "version": connector.version,
    }


@router.post("/connectors/{connector_id}/device-health")
def device_health(
    connector_id: str,
    body: DeviceHealthBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    stored = 0
    for snap in body.snapshots:
        if not isinstance(snap, dict):
            continue
        device = _upsert_device_from_payload(
            db,
            organisation_id=connector.organisation_id,
            connector_id=connector.id,
            payload=snap,
        )
        online = bool(snap.get("online", snap.get("status") == "online"))
        ping = float(snap.get("ping_ms") or snap.get("response_time_ms") or 0)
        metrics = {
            "online": online,
            "storage_usage_percent": snap.get("storage_usage_percent") or snap.get("storage") or 0,
            "memory_usage_percent": snap.get("memory_usage_percent") or snap.get("memory") or 0,
            "cpu_temperature": snap.get("cpu_temperature") or snap.get("temperature"),
            "battery_level": snap.get("battery_level") or snap.get("battery"),
            "ping_ms": ping,
        }
        score, state = health_score_from_metrics(metrics)
        device.health_score = score
        device.health_status = state
        device.status = "online" if online else "offline"
        device.response_time_ms = ping
        device.memory_usage_percent = float(metrics["memory_usage_percent"] or 0)
        device.storage_usage_percent = float(metrics["storage_usage_percent"] or 0)
        if metrics["battery_level"] is not None:
            try:
                device.battery_level = float(metrics["battery_level"])
            except (TypeError, ValueError):
                pass
        if metrics["cpu_temperature"] is not None:
            try:
                device.cpu_temperature = float(metrics["cpu_temperature"])
            except (TypeError, ValueError):
                pass
        for count_key, attr in (
            ("attendance_count", "attendance_count"),
            ("user_count", "user_count"),
            ("template_count", "template_count"),
            ("restart_count", "restart_count"),
        ):
            if snap.get(count_key) is not None:
                try:
                    setattr(device, attr, int(snap[count_key]))
                except (TypeError, ValueError):
                    pass
        device.last_health_at = utcnow()
        sample = DeviceHealthSample(
            id=new_id(),
            organisation_id=connector.organisation_id,
            connector_id=connector.id,
            device_id=device.id,
            online=online,
            ping_ms=ping,
            payload_json=snap,
        )
        db.add(sample)
        stored += 1
        if state in ("warning", "critical", "offline"):
            raise_notification(
                db,
                organisation_id=connector.organisation_id,
                event_type="device_health",
                title=f"Device {state}: {device.name}",
                message=f"Health score {score}",
                severity="critical" if state in ("critical", "offline") else "warning",
                connector_id=connector.id,
                device_id=device.id,
            )
    db.flush()
    return {"ok": True, "stored": stored}


@router.post("/connectors/{connector_id}/devices/status")
def devices_status(
    connector_id: str,
    body: DeviceStatusBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    updated = 0
    for item in body.devices:
        if not isinstance(item, dict):
            continue
        device = _upsert_device_from_payload(
            db,
            organisation_id=connector.organisation_id,
            connector_id=connector.id,
            payload=item,
        )
        if item.get("status"):
            device.status = str(item["status"])
        if item.get("last_sync"):
            device.last_sync_at = parse_iso(str(item["last_sync"])) or device.last_sync_at
        updated += 1
    online = sum(1 for d in db.scalars(select(Device).where(Device.connector_id == connector.id)) if d.status == "online")
    total = list(db.scalars(select(Device).where(Device.connector_id == connector.id)).all())
    # Admin UI "Devices" column uses connected_devices as total registered devices.
    connector.connected_devices = len(total)
    connector.offline_devices = sum(1 for d in total if str(d.status or "").lower() not in {"online", "connected", "healthy"})
    db.flush()
    return {"ok": True, "updated": updated, "online": online}


@router.get("/connectors/{connector_id}/commands")
def fetch_commands(
    connector_id: str,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    cmds = list_commands(
        db,
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        status=CommandState.QUEUED.value,
        limit=50,
    )
    return {"commands": [_serialize_command(c) for c in cmds]}


@router.post("/connectors/{connector_id}/commands/{command_id}/ack")
def acknowledge_command(
    connector_id: str,
    command_id: str,
    body: CommandAckBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    status = body.status.lower()
    mapping = {
        "succeeded": CommandState.COMPLETED.value,
        "success": CommandState.COMPLETED.value,
        "completed": CommandState.COMPLETED.value,
        "failed": CommandState.FAILED.value,
        "rejected": CommandState.FAILED.value,
        "running": CommandState.RUNNING.value,
        "queued": CommandState.QUEUED.value,
    }
    normalized = mapping.get(status, status)
    cmd = ack_command(
        db,
        command_id,
        status=normalized,
        result_message=body.message,
        result_json=body.result,
    )
    if not cmd or cmd.connector_id != connector.id:
        raise HTTPException(status_code=404, detail="Command not found")
    return {"ok": True, "command_id": command_id, "status": cmd.status}


@router.post("/connectors/{connector_id}/alerts")
async def push_alert(
    connector_id: str,
    request: Request,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    alert = await _read_json_body(request)
    note = raise_notification(
        db,
        organisation_id=connector.organisation_id,
        event_type=str(alert.get("event_type") or alert.get("type") or "connector_alert"),
        title=str(alert.get("title") or "Connector alert"),
        message=str(alert.get("message") or alert.get("detail") or ""),
        severity=str(alert.get("severity") or "warning"),
        connector_id=connector.id,
        device_id=str(alert.get("device_id") or ""),
        details=alert,
    )
    return {"ok": True, "notification_id": note.id}


@router.post("/connectors/{connector_id}/monitoring")
async def push_monitoring(
    connector_id: str,
    request: Request,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    payload = await _read_json_body(request)
    connector.config_json = {**(connector.config_json or {}), "monitoring": payload}
    if payload.get("queue_size") is not None:
        try:
            connector.queue_size = int(payload["queue_size"])
        except (TypeError, ValueError):
            pass
    if payload.get("version"):
        connector.version = str(payload["version"])
    if payload.get("last_error") is not None:
        connector.last_error = str(payload.get("last_error") or "")
    connector.last_heartbeat_at = utcnow()
    db.flush()
    return {"ok": True}


@router.post("/devices")
def register_device(
    body: DeviceRegisterBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    payload = body.model_dump()
    payload["inventory"] = body.inventory
    if not payload.get("device_id") and payload.get("id"):
        payload["device_id"] = payload["id"]
    device = _upsert_device_from_payload(
        db,
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        payload=payload,
    )
    write_audit(
        db,
        organisation_id=connector.organisation_id,
        action="device.register",
        resource_type="device",
        resource_id=device.id,
        message=f"Device registered {device.name}",
    )
    return {
        "ok": True,
        "device_id": device.id,
        "terminal_code": device.terminal_code,
        "status": device.status,
    }


@router.get("/connectors/{connector_id}/employees/sync")
def employee_sync_events(
    connector_id: str,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    events = db.scalars(
        select(EmployeeSyncEvent)
        .where(
            EmployeeSyncEvent.connector_id == connector.id,
            EmployeeSyncEvent.status == "pending",
        )
        .order_by(EmployeeSyncEvent.created_at.asc())
        .limit(100)
    ).all()
    return {
        "events": [
            {
                "id": e.id,
                "event_id": e.id,
                "employee_id": e.employee_id,
                "action": e.action,
                "payload": e.payload_json,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
    }


@router.post("/connectors/{connector_id}/employees/directory")
def push_employee_directory(
    connector_id: str,
    body: EmployeeDirectoryBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    """Accept biometric user names from the on-premise connector."""
    _assert_connector_path(connector, connector_id)
    people = list(body.employees or []) + list(body.users or [])
    result = import_employees_from_directory(
        db,
        organisation_id=connector.organisation_id,
        people=people,
        branch_id=connector.branch_id,
    )
    return {"ok": True, **result}


@router.post("/connectors/{connector_id}/employees/sync/{event_id}/ack")
def employee_sync_ack(
    connector_id: str,
    event_id: str,
    body: EmployeeSyncAckBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    event = db.get(EmployeeSyncEvent, event_id)
    if not event or event.connector_id != connector.id:
        raise HTTPException(status_code=404, detail="Sync event not found")
    event.status = body.status or "applied"
    event.acknowledged_at = utcnow()
    db.flush()
    return {"ok": True, "event_id": event_id}


@router.get("/connectors/{connector_id}/policies")
def download_policies(
    connector_id: str,
    device_id: str = Query(default=""),
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    rows = db.scalars(
        select(SystemPolicy).where(
            (SystemPolicy.organisation_id == connector.organisation_id)
            | (SystemPolicy.organisation_id == "")
        )
    ).all()
    policies = {r.policy_key: r.policy_json for r in rows}
    return {"policies": policies, "device_id": device_id, "connector_id": connector.id}


@router.post("/connectors/{connector_id}/inventory")
def push_inventory(
    connector_id: str,
    body: InventoryBody,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    count = 0
    for item in body.devices:
        if not isinstance(item, dict):
            continue
        _upsert_device_from_payload(
            db,
            organisation_id=connector.organisation_id,
            connector_id=connector.id,
            payload=item,
        )
        count += 1
    db.flush()
    return {"ok": True, "devices": count}


@router.post("/connectors/{connector_id}/notifications")
async def push_notification(
    connector_id: str,
    request: Request,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    payload = await _read_json_body(request)
    note = raise_notification(
        db,
        organisation_id=connector.organisation_id,
        event_type=str(payload.get("event_type") or "notification"),
        title=str(payload.get("title") or "Notification"),
        message=str(payload.get("message") or ""),
        severity=str(payload.get("severity") or "info"),
        connector_id=connector.id,
        device_id=str(payload.get("device_id") or ""),
        details=payload,
    )
    return {"ok": True, "id": note.id}


@router.post("/connectors/{connector_id}/reports")
async def push_report(
    connector_id: str,
    request: Request,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    payload = await _read_json_body(request)
    job = ReportJob(
        id=new_id(),
        organisation_id=connector.organisation_id,
        report_type=str(payload.get("report_type") or "connector_report"),
        parameters_json=payload,
        status="received",
        format=str(payload.get("format") or "json"),
        created_by=connector.id,
        completed_at=utcnow(),
    )
    db.add(job)
    db.flush()
    return {"ok": True, "report_id": job.id}


@router.post("/connectors/{connector_id}/support/diagnostics")
async def support_diagnostics(
    connector_id: str,
    request: Request,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _assert_connector_path(connector, connector_id)
    suite = await _read_json_body(request)
    path = settings.artifact_dir / f"diag_{connector.id}_{new_id()}.json"
    path.write_text(json.dumps(suite, indent=2), encoding="utf-8")
    art = SupportArtifact(
        id=new_id(),
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        artifact_type="diagnostics",
        filename=path.name,
        file_path=str(path),
        size_bytes=path.stat().st_size,
        meta_json=suite if isinstance(suite, dict) else {},
    )
    db.add(art)
    db.flush()
    return {"ok": True, "artifact_id": art.id}


@router.post("/connectors/{connector_id}/support/artifacts")
async def support_artifacts(
    connector_id: str,
    request: Request,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    body = await _read_json_body(request)
    art = SupportArtifact(
        id=new_id(),
        organisation_id=connector.organisation_id,
        connector_id=connector.id,
        artifact_type=str(body.get("artifact_type") or "artifact"),
        filename=str(body.get("filename") or ""),
        file_path=str(body.get("local_path") or ""),
        size_bytes=int(body.get("size_bytes") or 0),
        meta_json=dict(body.get("meta") or {}),
    )
    db.add(art)
    db.flush()
    return {"ok": True, "artifact_id": art.id}


@router.get("/connectors/{connector_id}/dashboard")
def connector_dashboard(
    connector_id: str,
    connector: Connector = Depends(get_connector_from_headers),
    db: Session = Depends(get_db),
):
    _assert_connector_path(connector, connector_id)
    devices = list(db.scalars(select(Device).where(Device.connector_id == connector.id)).all())
    pending_cmds = list_commands(
        db, connector_id=connector.id, status=CommandState.QUEUED.value, limit=1000
    )
    pending_sync = db.scalars(
        select(EmployeeSyncEvent).where(
            EmployeeSyncEvent.connector_id == connector.id,
            EmployeeSyncEvent.status == "pending",
        )
    ).all()
    return {
        "connector_id": connector.id,
        "status": connector.status,
        "devices_total": len(devices),
        "devices_online": sum(1 for d in devices if d.status == "online"),
        "devices_offline": sum(1 for d in devices if d.status != "online"),
        "queue_size": connector.queue_size,
        "pending_commands": len(pending_cmds),
        "pending_employee_sync": len(pending_sync),
        "last_heartbeat_at": connector.last_heartbeat_at.isoformat() if connector.last_heartbeat_at else None,
    }
