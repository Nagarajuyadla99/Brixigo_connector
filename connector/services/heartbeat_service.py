"""Heartbeat service — collect host metrics and report to cloud."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

import psutil

from api.cloud_client import CloudAPIError, CloudClient
from app.constants import APP_VERSION
from models import DeviceStatus, HeartbeatSnapshot, new_id, utcnow
from storage.database import Database
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import UploadMetricsRepository
from storage.repositories.heartbeat_repo import HeartbeatRepository

logger = logging.getLogger("brixigo.heartbeat")


class HeartbeatService:
    def __init__(
        self,
        cloud: CloudClient,
        devices: DeviceRepository,
        attendance: AttendanceRepository,
        heartbeat_repo: HeartbeatRepository,
        *,
        data_path: Path,
        engine_status: str = "running",
        metrics: UploadMetricsRepository | None = None,
        db: Database | None = None,
        last_restart_at: Optional[str] = None,
        on_internet_lost: Optional[Callable[[], None]] = None,
        directory_provider: Optional[Callable[[], list]] = None,
    ) -> None:
        self._cloud = cloud
        self._devices = devices
        self._attendance = attendance
        self._heartbeat_repo = heartbeat_repo
        self._data_path = data_path
        self.engine_status = engine_status
        self._metrics = metrics
        self._db = db
        self.last_restart_at = last_restart_at
        self._states: Optional["ConnectionStateRegistry"] = None
        self.on_internet_lost = on_internet_lost
        self._directory_provider = directory_provider

    def collect(self) -> HeartbeatSnapshot:
        counts = self._devices.counts_by_status()
        if self._states is not None:
            connected = self._states.connected_device_count()
            offline = self._states.disconnected_device_count()
        else:
            connected = counts.get(DeviceStatus.ONLINE.value, 0)
            offline = (
                counts.get(DeviceStatus.OFFLINE.value, 0)
                + counts.get(DeviceStatus.ERROR.value, 0)
            )
        try:
            cpu = float(psutil.cpu_percent(interval=0.1))
        except Exception:
            cpu = 0.0
        try:
            ram = float(psutil.virtual_memory().percent)
        except Exception:
            ram = 0.0
        try:
            disk = float(psutil.disk_usage(str(self._data_path)).percent)
        except Exception:
            disk = 0.0

        metrics = self._metrics.get() if self._metrics else {}
        return HeartbeatSnapshot(
            connector_version=APP_VERSION,
            status=self.engine_status,
            cpu_percent=cpu,
            ram_percent=ram,
            disk_percent=disk,
            connected_devices=connected,
            offline_devices=offline,
            last_sync=self._attendance.last_uploaded_at(),
            pending_queue=self._attendance.pending_count(),
            failed_uploads=self._attendance.failed_count(),
            today_punches=self._attendance.today_count(),
            average_upload_ms=float(metrics.get("average_upload_ms") or 0),
            last_error=str(metrics.get("last_error") or ""),
            last_restart_at=self.last_restart_at,
        )

    def beat(self) -> HeartbeatSnapshot:
        snap = self.collect()
        sent = False
        extra = None
        if self._states is not None:
            cs = self._states.snapshot()
            connection_states = cs.to_public_dict()
            for item in connection_states.get("devices") or []:
                if not isinstance(item, dict):
                    continue
                device = self._devices.get(str(item.get("device_id") or ""))
                if device is None:
                    continue
                item["terminal_code"] = device.terminal_code
                item["serial_number"] = device.serial_number
                if not item.get("name"):
                    item["name"] = device.name
                if not item.get("ip_address"):
                    item["ip_address"] = device.ip_address
                item["port"] = device.port or item.get("port")
            extra = {
                "internet_status": cs.internet_status,
                "server_status": cs.server_status,
                "tls_status": cs.tls_status,
                "authentication_status": cs.authentication_status,
                "sync_status": cs.sync_status,
                "connection_states": connection_states,
            }
            snap.connected_devices = self._states.connected_device_count()
            snap.offline_devices = self._states.disconnected_device_count()
        if extra is None:
            extra = {}
        if self._directory_provider:
            try:
                people = self._directory_provider() or []
                if people:
                    extra["employees"] = people
            except Exception:
                logger.debug("Heartbeat employee directory skipped", exc_info=True)
        try:
            if self._cloud.settings.connector_id and self._cloud.settings.organisation_id:
                # Always attempt the heartbeat. Do not preflight with a short API check —
                # a slow TLS handshake must not skip reporting (that falsely marks devices
                # offline in the cloud).
                self._cloud.send_heartbeat(snap, extra=extra)
                self._cloud.push_monitoring_snapshot(
                    {
                        "connector_version": snap.connector_version,
                        "status": snap.status,
                        "queue_size": snap.pending_queue,
                        "failed_uploads": snap.failed_uploads,
                        "connected_devices": snap.connected_devices,
                        "offline_devices": snap.offline_devices,
                        "today_punches": snap.today_punches,
                        "average_upload_ms": snap.average_upload_ms,
                        "last_sync": snap.last_sync,
                        "last_error": snap.last_error,
                        "last_restart_at": snap.last_restart_at,
                        "cpu_percent": snap.cpu_percent,
                        "ram_percent": snap.ram_percent,
                        "disk_percent": snap.disk_percent,
                    }
                )
                sent = True
            else:
                logger.debug("Heartbeat skipped — connector/org not configured")
        except CloudAPIError as exc:
            logger.warning(
                "Heartbeat cloud error kind=%s duration_ms=%.0f: %s",
                exc.error_kind,
                exc.duration_ms,
                exc,
            )
            if getattr(exc, "status_code", None) in (401, 403) or exc.error_kind == "auth":
                snap.last_error = str(exc)
            elif exc.error_kind in {"network", "timeout", "tls", "unavailable"} and self.on_internet_lost:
                # Internet-lost alert only when a true network/TLS failure happens,
                # never because the device LAN is down.
                if self._states is None or self._states.internet.value == "offline":
                    self.on_internet_lost()
        except Exception as exc:
            logger.warning("Heartbeat failed: %s", exc)
        self._heartbeat_repo.save_local(snap, sent=sent)
        self._save_history(snap, sent=sent)
        return snap

    def _save_history(self, snap: HeartbeatSnapshot, *, sent: bool) -> None:
        if self._db is None:
            return
        try:
            self._db.execute(
                """
                INSERT INTO heartbeat_history (
                    id, connector_version, status, cpu_percent, ram_percent,
                    disk_percent, connected_devices, offline_devices,
                    pending_queue, sent, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    snap.connector_version,
                    snap.status,
                    snap.cpu_percent,
                    snap.ram_percent,
                    snap.disk_percent,
                    snap.connected_devices,
                    snap.offline_devices,
                    snap.pending_queue,
                    1 if sent else 0,
                    snap.created_at or utcnow().isoformat(),
                    json.dumps(
                        {
                            "failed_uploads": snap.failed_uploads,
                            "today_punches": snap.today_punches,
                            "average_upload_ms": snap.average_upload_ms,
                            "last_error": snap.last_error,
                        }
                    ),
                ),
            )
            self._db.execute(
                """
                DELETE FROM heartbeat_history WHERE id IN (
                    SELECT id FROM heartbeat_history
                    ORDER BY created_at DESC LIMIT -1 OFFSET 2000
                )
                """
            )
        except Exception as exc:
            logger.debug("Heartbeat history save skipped: %s", exc)
