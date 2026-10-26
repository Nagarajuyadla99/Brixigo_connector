"""Background maintenance worker — stale detection, command expiry, alerts."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Connector, ConnectorStatus, Device, DeviceHealthState, Notification
from app.services.commands import expire_stale_commands
from app.services.common import raise_notification

logger = logging.getLogger("brixigo.cloud.worker")

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()
_redis_client = None


def _get_redis():
    global _redis_client
    settings = get_settings()
    if not settings.enable_redis or not settings.redis_url:
        return None
    if _redis_client is None:
        try:
            import redis

            _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            _redis_client.ping()
        except Exception as exc:
            logger.warning("Redis unavailable, using in-process worker: %s", exc)
            _redis_client = False
    return _redis_client if _redis_client is not False else None


def _recent_notification_exists(db, org_id: str, event_type: str, resource_id: str, minutes: int = 30) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    existing = db.scalar(
        select(Notification.id).where(
            Notification.organisation_id == org_id,
            Notification.event_type == event_type,
            Notification.connector_id == resource_id,
            Notification.created_at >= cutoff,
            Notification.acknowledged.is_(False),
        ).limit(1)
    )
    return existing is not None


def _tick() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        connector_stale = timedelta(seconds=settings.connector_offline_seconds)

        connectors = db.scalars(select(Connector).where(Connector.is_active.is_(True))).all()
        for c in connectors:
            if c.last_heartbeat_at and (now - c.last_heartbeat_at.replace(tzinfo=timezone.utc)) > connector_stale:
                if c.status != ConnectorStatus.OFFLINE.value:
                    c.status = ConnectorStatus.OFFLINE.value
                    if not _recent_notification_exists(db, c.organisation_id, "connector_offline", c.id):
                        raise_notification(
                            db,
                            organisation_id=c.organisation_id,
                            connector_id=c.id,
                            event_type="connector_offline",
                            severity="critical",
                            title=f"Connector offline: {c.name}",
                            message=f"No heartbeat since {c.last_heartbeat_at}",
                        )
            if c.queue_size >= settings.queue_overflow_threshold:
                if not _recent_notification_exists(db, c.organisation_id, "queue_overflow", c.id):
                    raise_notification(
                        db,
                        organisation_id=c.organisation_id,
                        connector_id=c.id,
                        event_type="queue_overflow",
                        severity="warning",
                        title=f"Queue overflow: {c.name}",
                        message=f"Queue size {c.queue_size} exceeds threshold {settings.queue_overflow_threshold}",
                        details={"queue_size": c.queue_size},
                    )

        devices = db.scalars(select(Device).where(Device.enabled.is_(True))).all()
        connectors_by_id = {c.id: c for c in connectors}
        for d in devices:
            connector = connectors_by_id.get(d.connector_id or "")
            connector_alive = bool(
                connector
                and connector.last_heartbeat_at
                and (now - connector.last_heartbeat_at.replace(tzinfo=timezone.utc)) <= connector_stale
            )
            if not connector_alive:
                # Connector itself is stale — device state is unknown, not disconnected.
                if d.health_status != DeviceHealthState.UNKNOWN.value:
                    d.health_status = DeviceHealthState.UNKNOWN.value
                continue
            # Connector is reporting. Do not infer device offline from stale health
            # snapshots (those can fail independently due to TLS/API delay).
            if d.status == "offline" and d.health_status != DeviceHealthState.OFFLINE.value:
                d.health_status = DeviceHealthState.OFFLINE.value

        expire_stale_commands(db, settings)
        try:
            from app.services.hrms_outbound import sync_pending

            sync_pending(db, settings)
        except Exception:
            logger.exception("HRMS outbound sync failed")
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Background worker tick failed")
    finally:
        db.close()


def _worker_loop() -> None:
    settings = get_settings()
    interval = 60
    redis = _get_redis()
    if redis:
        lock_key = "brixigo:cloud:worker:lock"
        while not _stop_event.is_set():
            acquired = redis.set(lock_key, "1", nx=True, ex=interval)
            if acquired:
                _tick()
            _stop_event.wait(interval)
    else:
        while not _stop_event.is_set():
            _tick()
            _stop_event.wait(interval)


def start_background_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="brixigo-cloud-worker", daemon=True)
    _worker_thread.start()
    logger.info("Background worker started")


def stop_background_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=5)
    logger.info("Background worker stopped")
