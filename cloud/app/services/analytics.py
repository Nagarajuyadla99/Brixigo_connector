"""Analytics aggregation queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AttendancePunch,
    CommandState,
    Connector,
    ConnectorStatus,
    Device,
    DeviceHealthState,
    RemoteCommand,
)
from app.schemas.admin import AnalyticsSummary, MonitoringDashboard
from app.services.punch_time import rewrite_unset_clock_punches

_LIVE_CONNECTOR_STATUSES = {"online", "running", "connected", "ok", "healthy"}
_LIVE_DEVICE_STATUSES = {"online", "connected", "healthy"}


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _is_fresh(ts: datetime | None, seconds: int) -> bool:
    aware = _aware(ts)
    if aware is None:
        return False
    return (datetime.now(timezone.utc) - aware).total_seconds() <= seconds


def _connector_is_online(connector: Connector, *, stale_seconds: int) -> bool:
    if str(connector.status or "").lower() in _LIVE_CONNECTOR_STATUSES:
        return True
    return _is_fresh(connector.last_heartbeat_at, stale_seconds)


def _device_is_healthy(device: Device, *, live_connector_ids: set[str]) -> bool:
    health = str(device.health_status or "").lower()
    status = str(device.status or "").lower()
    if health == DeviceHealthState.HEALTHY.value or status in _LIVE_DEVICE_STATUSES:
        return True
    if not device.enabled:
        return False
    if status in {"offline", "disabled"}:
        return False
    return bool(device.connector_id and device.connector_id in live_connector_ids)


def _attendance_on_day(db: Session, day: str, organisation_id: str | None) -> int:
    q = select(func.count(AttendancePunch.id)).where(
        (AttendancePunch.punch_time.startswith(day))
        | (func.date(AttendancePunch.created_at) == day)
    )
    if organisation_id:
        q = q.where(AttendancePunch.organisation_id == organisation_id)
    return int(db.scalar(q) or 0)


def get_monitoring_dashboard(
    db: Session,
    *,
    organisation_id: str | None = None,
    settings: Settings | None = None,
) -> MonitoringDashboard:
    cq = select(Connector)
    dq = select(Device)
    if organisation_id:
        cq = cq.where(Connector.organisation_id == organisation_id)
        dq = dq.where(Device.organisation_id == organisation_id)

    connectors = list(db.scalars(cq))
    devices = list(db.scalars(dq))
    stale = settings.connector_offline_seconds if settings else 300
    if settings and not settings.is_production:
        stale = max(stale, 24 * 3600)
    live_ids = {c.id for c in connectors if _connector_is_online(c, stale_seconds=stale)}

    online = sum(1 for d in devices if _device_is_healthy(d, live_connector_ids=live_ids))
    warning = sum(1 for d in devices if d.health_status == DeviceHealthState.WARNING.value)
    critical = sum(1 for d in devices if d.health_status == DeviceHealthState.CRITICAL.value)
    offline = sum(
        1
        for d in devices
        if d.health_status in (DeviceHealthState.OFFLINE.value, "offline")
        or str(d.status or "").lower() == "offline"
    )

    return MonitoringDashboard(
        online=online,
        offline=offline,
        warning=warning,
        critical=critical,
        degraded_connectors=sum(1 for c in connectors if c.status == ConnectorStatus.DEGRADED.value),
        total_devices=len(devices),
        total_connectors=len(connectors),
    )


def get_analytics_summary(
    db: Session,
    *,
    organisation_id: str | None = None,
    settings: Settings | None = None,
) -> AnalyticsSummary:
    cq = select(Connector)
    dq = select(Device)
    if organisation_id:
        cq = cq.where(Connector.organisation_id == organisation_id)
        dq = dq.where(Device.organisation_id == organisation_id)

    rewrite_unset_clock_punches(db)
    connectors = list(db.scalars(cq))
    devices = list(db.scalars(dq))
    stale = settings.connector_offline_seconds if settings else 300
    if settings and not settings.is_production:
        stale = max(stale, 24 * 3600)
    live_ids = {c.id for c in connectors if _connector_is_online(c, stale_seconds=stale)}

    conn_online = sum(1 for c in connectors if c.id in live_ids)
    conn_offline = len(connectors) - conn_online

    healthy = sum(1 for d in devices if _device_is_healthy(d, live_connector_ids=live_ids))
    warning = sum(1 for d in devices if d.health_status == DeviceHealthState.WARNING.value)
    critical = sum(1 for d in devices if d.health_status == DeviceHealthState.CRITICAL.value)
    offline = sum(
        1
        for d in devices
        if d.health_status in (DeviceHealthState.OFFLINE.value, "offline")
        or str(d.status or "").lower() == "offline"
    )

    avg_health = 0.0
    if devices:
        scores = [d.health_score or (80.0 if _device_is_healthy(d, live_connector_ids=live_ids) else 0.0) for d in devices]
        avg_health = sum(scores) / len(devices)

    uptime = 0.0
    if devices:
        uptime = (healthy / len(devices)) * 100.0

    today = datetime.now().strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    attendance_today = max(
        _attendance_on_day(db, today, organisation_id),
        _attendance_on_day(db, today_utc, organisation_id),
    )

    week_trend = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        count = _attendance_on_day(db, day, organisation_id)
        week_trend.append({"date": day, "count": count})

    queue_alerts = sum(1 for c in connectors if c.queue_size >= (settings.queue_overflow_threshold if settings else 5000))

    cmd_q = select(func.count(RemoteCommand.id)).where(RemoteCommand.status == CommandState.QUEUED.value)
    if organisation_id:
        cmd_q = cmd_q.where(RemoteCommand.organisation_id == organisation_id)
    pending_commands = db.scalar(cmd_q) or 0

    return AnalyticsSummary(
        connectors_online=conn_online,
        connectors_offline=conn_offline,
        devices_healthy=healthy,
        devices_warning=warning,
        devices_critical=critical,
        devices_offline=offline,
        attendance_today=attendance_today,
        attendance_week=week_trend,
        uptime_percent=round(uptime, 2),
        avg_health_score=round(avg_health, 2),
        queue_alerts=queue_alerts,
        pending_commands=pending_commands,
    )
