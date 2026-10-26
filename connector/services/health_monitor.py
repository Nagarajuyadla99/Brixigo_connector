"""Per-device health monitoring worker."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from adapters.registry import create_adapter
from api.cloud_client import CloudAPIError, CloudClient
from models import Device, DeviceHealthSnapshot, DeviceStatus, new_id, utcnow
from services.connection_state import (
    ConnectionStateRegistry,
    DeviceNetworkStatus,
    is_tcp_connect_failure,
    live_heartbeat_connected,
    tcp_probe,
)
from services.device_session_manager import DeviceSessionManager
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.health_repo import DeviceHealthRepository
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.health")


class HealthMonitorService:
    """
    Collect device health every minute independently via ThreadPoolExecutor.
    One slow device does not block others.
    """

    def __init__(
        self,
        cloud: CloudClient,
        devices: DeviceRepository,
        health_repo: DeviceHealthRepository,
        sync_logs: SyncLogRepository,
        *,
        list_devices: Callable[[], list[Device]],
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        max_workers: int = 16,
        interval_seconds: int = 60,
        on_offline: Optional[Callable[[Device, DeviceHealthSnapshot], None]] = None,
        on_low_storage: Optional[Callable[[Device, DeviceHealthSnapshot], None]] = None,
        states: Optional[ConnectionStateRegistry] = None,
        sessions: Optional[DeviceSessionManager] = None,
    ) -> None:
        self._cloud = cloud
        self._devices = devices
        self._health_repo = health_repo
        self._sync_logs = sync_logs
        self._list_devices = list_devices
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_workers = max(2, max_workers)
        self.interval_seconds = max(30, interval_seconds)
        self.on_offline = on_offline
        self.on_low_storage = on_low_storage
        self._states = states
        self._sessions = sessions
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-health-monitor", daemon=True
        )
        self._thread.start()
        logger.info("Health monitor started interval=%ss", self.interval_seconds)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("Health monitor stopped")

    def collect_all(self) -> list[DeviceHealthSnapshot]:
        devices = self._list_devices()
        snapshots: list[DeviceHealthSnapshot] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self.collect_device, d): d for d in devices}
            for fut in as_completed(futures):
                device = futures[fut]
                try:
                    snap = fut.result()
                    snapshots.append(snap)
                except Exception as exc:
                    logger.error("Health collect failed device=%s: %s", device.name, exc)
        self._push_pending()
        self._health_repo.cleanup(keep_per_device=288)
        return snapshots

    def collect_device(self, device: Device) -> DeviceHealthSnapshot:
        link = self._states.device(device.id) if self._states else None
        if link is not None and link.network_status == DeviceNetworkStatus.OFFLINE:
            err = link.last_error or "timed out"
            if not is_tcp_connect_failure(err, failure_stage=link.failure_stage):
                err = f"TCP connect failed: {err}"
            metrics = {
                "online": False,
                "ping_ms": 0.0,
                "message": err,
            }
        else:
            try:
                if self._sessions is not None:
                    metrics = self._sessions.execute(
                        device,
                        lambda adapter: adapter.collect_health_metrics(),
                        retries=2,
                        owner="health",
                    )
                else:
                    adapter = create_adapter(
                        device,
                        connect_timeout=self.connect_timeout,
                        read_timeout=self.read_timeout,
                    )
                    metrics = adapter.collect_health_metrics()
            except Exception as exc:
                metrics = {
                    "online": False,
                    "ping_ms": 0.0,
                    "message": str(exc),
                }

        protocol_ok = bool(metrics.get("online"))
        tcp_ok = protocol_ok
        if not protocol_ok and not is_tcp_connect_failure(metrics.get("message")):
            tcp_ok, tcp_ms, tcp_msg = tcp_probe(
                device.ip_address, int(device.port or 4370)
            )
            metrics["tcp_open"] = tcp_ok
            metrics["tcp_ms"] = tcp_ms
            if tcp_ok and tcp_msg != "session-in-use":
                metrics["message"] = (
                    f"protocol health failed but TCP {device.ip_address}:{device.port} "
                    f"is open ({tcp_msg})"
                )
                logger.warning(
                    "Health protocol failed but TCP open device=%s",
                    device.name,
                    extra={"device_id": device.id},
                )

        snap = DeviceHealthSnapshot(
            id=new_id(),
            device_id=device.id,
            online=protocol_ok or tcp_ok,
            ping_ms=float(metrics.get("ping_ms") or 0),
            memory_usage_percent=float(metrics.get("memory_usage_percent") or 0),
            storage_usage_percent=float(metrics.get("storage_usage_percent") or 0),
            user_capacity=int(metrics.get("user_capacity") or 0),
            log_capacity=int(metrics.get("log_capacity") or 0),
            current_users=int(metrics.get("current_users") or 0),
            current_logs=int(metrics.get("current_logs") or 0),
            firmware_version=str(metrics.get("firmware_version") or ""),
            device_time=str(metrics.get("device_time") or ""),
            battery_level=metrics.get("battery_level"),
            cpu_temperature=metrics.get("cpu_temperature"),
            restart_count=int(metrics.get("restart_count") or 0),
            message=str(metrics.get("message") or ""),
            collected_at=utcnow().isoformat(),
            details=metrics if isinstance(metrics, dict) else {},
        )
        self._health_repo.save(snap)
        self._confirm_modern_device_clock(device.id, snap.device_time)

        if protocol_ok:
            self._devices.update_status(
                device.id,
                status=DeviceStatus.ONLINE.value,
                last_error="",
                serial_number=str(metrics.get("serial_number") or None),
            )
            self._devices.update_telemetry(
                device.id,
                firmware=snap.firmware_version or None,
                mac_address=str(metrics.get("mac_address") or "") or None,
                sdk_version=str(metrics.get("sdk_version") or "") or None,
                model=str(metrics.get("model") or "") or None,
                current_users=snap.current_users,
                current_logs=snap.current_logs,
                health_status="healthy",
                last_health_at=snap.collected_at,
            )
        elif tcp_ok and live_heartbeat_connected(link):
            # Health lost the single K90 session while poll/heartbeat already
            # proved the terminal is online. Do not stamp DEGRADED over that.
            logger.info(
                "Health protocol busy; live heartbeat still connected device=%s",
                device.name,
                extra={"device_id": device.id},
            )
            self._devices.update_telemetry(
                device.id,
                health_status="healthy",
                last_health_at=snap.collected_at,
            )
        elif tcp_ok:
            self._devices.update_status(
                device.id,
                status=DeviceStatus.DEGRADED.value,
                last_error=str(metrics.get("message") or ""),
            )
            self._devices.update_telemetry(
                device.id,
                health_status="warning",
                last_health_at=snap.collected_at,
            )
        else:
            self._devices.update_telemetry(
                device.id,
                last_health_at=snap.collected_at,
            )

        if not tcp_ok and self.on_offline:
            # Only alert after the independent heartbeat has marked DISCONNECTED.
            link = self._states.device(device.id) if self._states else None
            if link is None or link.status.value == "disconnected":
                self.on_offline(device, snap)
        if protocol_ok and snap.storage_usage_percent >= 90 and self.on_low_storage:
            self.on_low_storage(device, snap)

        return snap

    def _push_pending(self) -> None:
        pending = self._health_repo.list_unpushed(limit=200)
        if not pending:
            return
        if not self._cloud.settings.connector_id:
            return
        try:
            payload = []
            for s in pending:
                device = self._devices.get(s.device_id)
                row = {
                    "id": s.id,
                    "device_id": s.device_id,
                    "online": s.online,
                    "status": "online" if s.online else "offline",
                    "ping_ms": s.ping_ms,
                    "memory_usage_percent": s.memory_usage_percent,
                    "storage_usage_percent": s.storage_usage_percent,
                    "user_capacity": s.user_capacity,
                    "log_capacity": s.log_capacity,
                    "current_users": s.current_users,
                    "current_logs": s.current_logs,
                    "firmware_version": s.firmware_version,
                    "device_time": s.device_time,
                    "battery_level": s.battery_level,
                    "cpu_temperature": s.cpu_temperature,
                    "restart_count": s.restart_count,
                    "message": s.message,
                    "collected_at": s.collected_at,
                }
                if device is not None:
                    row.update(
                        {
                            "name": device.name,
                            "ip_address": device.ip_address,
                            "port": device.port,
                            "terminal_code": device.terminal_code,
                            "serial_number": device.serial_number,
                            "manufacturer": device.manufacturer,
                            "model": device.model,
                            "firmware": device.firmware or s.firmware_version,
                            "mac_address": device.mac_address,
                            "communication_type": device.communication_type,
                            "enabled": device.enabled,
                            "branch": device.branch,
                            "department": device.department,
                            "building": device.building,
                            "floor": device.floor,
                            "gate": device.gate,
                        }
                    )
                payload.append(row)
            self._cloud.push_device_health(payload)
            self._health_repo.mark_pushed([s.id for s in pending])
        except CloudAPIError as exc:
            logger.warning("Health push failed: %s", exc)
        except Exception as exc:
            logger.warning("Health push error: %s", exc)

    def _confirm_modern_device_clock(self, device_id: str, device_time: str) -> None:
        text = str(device_time or "").strip()
        if len(text) < 4 or not text[:4].isdigit() or int(text[:4]) < 2010:
            return
        try:
            self._devices.touch_field(device_id, "last_time_sync_at")
        except Exception:
            logger.debug(
                "last_time_sync_at update skipped device=%s",
                device_id,
                extra={"device_id": device_id},
            )

    def _loop(self) -> None:
        # First cycle after a short delay so the dashboard can paint from SQLite
        # before we take the device session lock for protocol health.
        self._stop.wait(2.0)
        while not self._stop.is_set():
            try:
                self.collect_all()
            except Exception as exc:
                logger.exception("Health monitor cycle failed: %s", exc)
            self._stop.wait(self.interval_seconds)
