"""Connector engine — orchestrates poll, upload, heartbeat, and Phase 2/3 workers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from api.cloud_client import CloudClient
from app.constants import (
    DEFAULT_DASHBOARD_REFRESH_MS,
    DEVICE_TCP_PROBE_TIMEOUT,
    DISCOVERY_CONNECT_TIMEOUT,
    FAST_TCP_PROBE_TIMEOUT,
)
from app.container import Container
from models import AlertType, utcnow
from scheduler.poll_scheduler import PollScheduler
from security.command_security import CommandSecurityGate
from services.alert_service import AlertService
from services.api_health_monitor import ApiHealthMonitor
from services.cleanup_service import CleanupService
from services.command_service import CommandService
from services.connection_state import (
    ConnectionStateRegistry,
    ConnectorRuntimeStatus,
    SyncStatus,
    operator_device_badge,
    public_cloud_status,
)
from services.device_heartbeat_service import DeviceHeartbeatService
from services.device_manager import DeviceManager
from services.device_session_manager import DeviceSessionManager
from services.diagnostics import NetworkDiagnostics
from services.employee_sync_service import EmployeeSyncService
from services.health_monitor import HealthMonitorService
from services.health_score import compute_health_score
from services.heartbeat_service import HeartbeatService
from services.network_scanner import NetworkScanner
from services.phase3_command_dispatcher import Phase3CommandDispatcher
from services.phase3_platform import (
    AccessControlService,
    NotificationEngine,
    ProvisioningService,
    RemoteSupportService,
    ReportingService,
)
from services.phase3_services import (
    BackupRestoreService,
    DeviceUserService,
    FirmwareService,
    InventoryService,
    TemplateService,
    TimeSyncService,
)
from services.poll_service import PollService
from services.remote_config_service import RemoteConfigService
from services.upload_engine import UploadEngine

logger = logging.getLogger("brixigo.service")


class ConnectorEngine:
    """Long-running coordinator used by Windows Service and Tray UI."""

    def __init__(self, container: Container) -> None:
        self.container = container
        settings = container.settings.as_model()
        self.cloud = CloudClient(settings)
        self.states = ConnectionStateRegistry()
        self.cloud.on_credentials_updated = lambda s: container.settings.update(
            jwt_token=s.jwt_token
        )
        self.sessions = DeviceSessionManager(
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
        )

        self.alerts = AlertService(
            container.alerts,
            container.audit,
            self.cloud,
            queue_threshold=settings.queue_alert_threshold,
        )
        self.device_manager = DeviceManager(
            container.devices,
            container.sync_logs,
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            audit=container.audit,
            discovery=container.discovery,
            sessions=self.sessions,
            states=self.states,
            cloud=self.cloud,
        )
        self.poll_service = PollService(
            container.devices,
            container.attendance,
            container.sync_logs,
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            sessions=self.sessions,
            states=self.states,
            device_users=container.device_users,
            on_clock_mismatch=self._on_clock_mismatch,
        )
        self.upload_engine = UploadEngine(
            self.cloud,
            container.attendance,
            container.devices,
            container.sync_logs,
            batch_size=settings.upload_batch_size,
            max_retries=settings.max_upload_retries,
            retry_attempts=settings.retry_count,
            metrics=container.upload_metrics,
            on_upload_failed=lambda msg, device_id: self.alerts.upload_failed(msg, device_id),
            on_auth_failed=lambda msg: self.alerts.api_auth_failed(msg),
            states=self.states,
            resolve_employee_name=self._resolve_employee_name,
        )
        self.heartbeat = HeartbeatService(
            self.cloud,
            container.devices,
            container.attendance,
            container.heartbeat_repo,
            data_path=container.paths.data,
            engine_status="stopped",
            metrics=container.upload_metrics,
            db=container.db,
            on_internet_lost=lambda: self.alerts.internet_lost(),
        )
        self.heartbeat._states = self.states
        self.scheduler = PollScheduler(
            self.poll_service,
            self.upload_engine,
            list_devices=lambda: container.devices.list_all(enabled_only=True),
            max_workers=settings.max_worker_threads,
            states=self.states,
        )
        self.health_monitor = HealthMonitorService(
            self.cloud,
            container.devices,
            container.health_repo,
            container.sync_logs,
            list_devices=lambda: container.devices.list_all(enabled_only=True),
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            max_workers=settings.max_worker_threads,
            interval_seconds=settings.health_interval,
            on_offline=lambda d, s: self.alerts.device_offline(d, s),
            on_low_storage=lambda d, s: self.alerts.low_storage(d, s),
            states=self.states,
            sessions=self.sessions,
        )
        self.device_heartbeat = DeviceHeartbeatService(
            container.devices,
            self.states,
            list_devices=lambda: container.devices.list_all(enabled_only=True),
            interval_seconds=int(getattr(settings, "device_heartbeat_interval", 12) or 12),
            probe_timeout=max(
                3.0,
                float(
                    getattr(settings, "device_tcp_probe_timeout", None)
                    or DEVICE_TCP_PROBE_TIMEOUT
                ),
            ),
            on_disconnected=self._on_device_heartbeat_disconnected,
            on_connected=self._on_device_heartbeat_connected,
            sessions=self.sessions,
        )
        self._user_name_refresh_lock = threading.Lock()
        self._user_name_refresh_started: set[str] = set()
        self.api_health = ApiHealthMonitor(
            self.cloud,
            self.states,
            interval_seconds=int(getattr(settings, "api_health_interval", 30) or 30),
        )
        self.security_gate = CommandSecurityGate(
            container.nonces,
            container.audit,
            signing_secret=settings.command_signing_secret,
        )
        self.command_service = CommandService(
            self.cloud,
            container.commands,
            container.devices,
            container.sync_logs,
            container.audit,
            self.security_gate,
            self.poll_service,
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            interval_seconds=settings.command_poll_interval,
            sessions=self.sessions,
        )
        self.remote_config = RemoteConfigService(
            self.cloud,
            container.settings,
            container.audit,
            interval_seconds=settings.remote_config_interval,
            on_applied=self.reload_settings,
        )
        self.scanner = NetworkScanner(
            container.discovery,
            container.devices,
            connect_timeout=DISCOVERY_CONNECT_TIMEOUT,
        )
        self.device_manager.bind_scanner(self.scanner)
        self.poll_service.set_relocator(self.device_manager.relocate_if_unreachable)
        self.device_heartbeat.set_relocator(self.device_manager.relocate_if_unreachable)
        self.diagnostics = NetworkDiagnostics(self.cloud, sessions=self.sessions, states=self.states)
        self.cleanup = CleanupService(
            container.db,
            container.health_repo,
            container.paths.logs,
            uploaded_retention_days=settings.cleanup_uploaded_days,
            log_retention_days=settings.cleanup_log_days,
        )

        # ---- Phase 3 enterprise device management ----
        self.notifications = NotificationEngine(
            container.notifications, self.alerts, self.cloud, container.audit
        )
        self.template_service = TemplateService(
            container.templates,
            container.secrets,
            container.devices,
            container.sync_logs,
            container.audit,
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            on_failure=lambda msg, did: self.notifications.notify(
                event_type=AlertType.TEMPLATE_FAILURE.value,
                title="Template failure",
                message=msg,
                device_id=did,
            ),
        )
        self.device_user_service = DeviceUserService(
            container.device_users,
            container.devices,
            container.sync_logs,
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            max_workers=settings.max_worker_threads,
        )
        self.employee_sync = EmployeeSyncService(
            self.cloud,
            container.employees,
            container.employee_sync_queue,
            container.devices,
            container.sync_logs,
            container.audit,
            list_devices=lambda: container.devices.list_all(enabled_only=True),
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            max_workers=settings.max_worker_threads,
            interval_seconds=settings.employee_sync_interval,
            on_sync_failure=lambda msg, did: self.notifications.notify(
                event_type=AlertType.SYNC_FAILURE.value,
                title="Employee sync failure",
                message=msg,
                device_id=did,
            ),
            device_users=container.device_users,
        )
        self.heartbeat._directory_provider = self.employee_sync.collect_directory
        self.time_sync = TimeSyncService(
            self.cloud,
            container.devices,
            container.time_sync_logs,
            container.sync_logs,
            list_devices=lambda: container.devices.list_all(enabled_only=True),
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            interval_seconds=settings.time_sync_interval,
            drift_threshold_seconds=settings.time_drift_threshold_seconds,
            max_workers=settings.max_worker_threads,
            sessions=self.sessions,
            on_clock_mismatch=self._on_clock_mismatch,
            states=self.states,
        )
        self.backup_service = BackupRestoreService(
            container.backups,
            container.devices,
            self.template_service,
            self.device_user_service,
            container.secrets,
            container.audit,
            container.sync_logs,
            container.paths.backups,
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            interval_seconds=settings.backup_interval,
            list_devices=lambda: container.devices.list_all(enabled_only=True),
            on_failure=lambda msg, did: self.notifications.notify(
                event_type=AlertType.BACKUP_FAILED.value,
                title="Backup failed",
                message=msg,
                device_id=did,
            ),
        )
        self.firmware_service = FirmwareService(
            container.firmware_jobs,
            container.devices,
            container.audit,
            container.sync_logs,
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
        )
        self.inventory_service = InventoryService(
            self.cloud,
            container.devices,
            list_devices=lambda: container.devices.list_all(enabled_only=True),
            connect_timeout=settings.device_connect_timeout,
            read_timeout=settings.device_read_timeout,
            interval_seconds=settings.inventory_push_interval,
            max_workers=settings.max_worker_threads,
        )
        self.access_control = AccessControlService(
            container.access_control,
            container.audit,
            enabled=bool(settings.enable_access_control),
        )
        self.reporting = ReportingService(
            container.db, container.reports, container.paths.reports, self.cloud
        )
        self.provisioning = ProvisioningService(
            self.cloud,
            container.devices,
            self.device_manager,
            self.employee_sync,
            self.template_service,
            self.inventory_service,
            container.audit,
            container.sync_logs,
        )
        self.remote_support = RemoteSupportService(
            self.cloud,
            self.diagnostics,
            self.cleanup,
            self.backup_service,
            container.audit,
            container.paths.logs,
            container.paths.data,
            container.db,
            engine_restart=self.restart,
        )
        self.command_service.phase3_dispatcher = Phase3CommandDispatcher(
            employee_sync=self.employee_sync,
            templates=self.template_service,
            users=self.device_user_service,
            backup=self.backup_service,
            provisioning=self.provisioning,
            firmware=self.firmware_service,
            inventory=self.inventory_service,
            reporting=self.reporting,
            remote_support=self.remote_support,
            devices=container.devices,
            enable_firmware_flash=bool(settings.enable_firmware_flash),
            enable_access_control=bool(settings.enable_access_control),
        )

        self._hb_thread: Optional[threading.Thread] = None
        self._hb_stop = threading.Event()
        self._running = False
        self._started_at: Optional[str] = None
        self._lock = threading.RLock()
        self._clock_autofix_lock = threading.Lock()
        self._clock_autofix_ids: set[str] = set()

    def _resolve_employee_name(self, device_id: str, employee_code: str) -> str:
        code = str(employee_code or "").strip()
        if not code:
            return ""
        name = ""
        try:
            name = str(self.container.device_users.find_name(code, device_id=device_id) or "").strip()
        except Exception:
            name = ""
        if not name:
            try:
                emp = self.container.employees.get_by_code(code)
                name = str(getattr(emp, "name", "") or "").strip()
            except Exception:
                name = ""
        if name and name.lower() == code.lower():
            return ""
        return name

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self.reload_settings()
            try:
                fixed = self.container.attendance.rewrite_unset_clock_punch_times()
                if fixed:
                    logger.info("Rewrote %s unset device-clock punches onto the connector clock", fixed)
            except Exception as exc:
                logger.warning("Punch date rewrite skipped: %s", exc)
            try:
                healed = self.container.attendance.reclassify_attendance_events()
                if healed:
                    logger.info("Reclassified %s attendance events", healed)
            except Exception as exc:
                logger.warning("Attendance reclassify skipped: %s", exc)
            from security.production_guard import validate_connector_settings

            guard = validate_connector_settings(self.container.settings.as_model())
            guard.raise_if_blocking()
            self.states.set_connector(ConnectorRuntimeStatus.STARTING)
            self.states.reset_device_probe_state()
            # Device connectivity is established independently of Brixigo API health.
            self.device_heartbeat.start()
            self.api_health.start()
            self.scheduler.start()
            try:
                self.upload_engine.process_queue()
            except Exception as exc:
                logger.warning("Startup queue flush deferred: %s", exc)
                self.states.set_sync(SyncStatus.PENDING)
            self.health_monitor.start()
            self.command_service.start()
            self.remote_config.start()
            self.cleanup.start()
            self.employee_sync.start()
            self.time_sync.start()
            self.backup_service.start()
            self.inventory_service.start()
            self._start_heartbeat()
            self._running = True
            self._started_at = utcnow().isoformat()
            self.heartbeat.engine_status = "running"
            self.heartbeat.last_restart_at = self._started_at
            self.states.set_connector(ConnectorRuntimeStatus.RUNNING)
            self.alerts.connector_restarted()
            self._refresh_device_user_names_async()
            try:
                synced = self.device_manager.sync_all_devices_to_cloud()
                if synced:
                    logger.info("Synced %s local devices to cloud", synced)
            except Exception as exc:
                logger.warning("Startup device cloud sync deferred: %s", exc)
            logger.info("Connector engine started (Phase 3)")

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self.heartbeat.engine_status = "stopping"
            self.states.set_connector(ConnectorRuntimeStatus.STOPPING)
            self._hb_stop.set()
            if self._hb_thread and self._hb_thread.is_alive():
                self._hb_thread.join(timeout=5)
            self._hb_thread = None
            self.cleanup.stop()
            self.inventory_service.stop()
            self.backup_service.stop()
            self.time_sync.stop()
            self.employee_sync.stop()
            self.remote_config.stop()
            self.command_service.stop()
            self.health_monitor.stop()
            self.api_health.stop()
            self.device_heartbeat.stop()
            self.scheduler.stop()
            self.cloud.close()
            self._running = False
            self.heartbeat.engine_status = "stopped"
            self.states.set_connector(ConnectorRuntimeStatus.STOPPED)
            logger.info("Connector engine stopped")

    def restart(self) -> None:
        self.stop()
        self.start()

    def check_and_restart_connections(self) -> dict:
        """Restart the connectivity plane and re-check cloud + every device.

        Clears offline backoff / stale DISCONNECTED flags, restarts engine
        workers, then immediately probes API health and each terminal.
        """
        logger.info("CONNECTION_RESTART_ALL begin")
        report: dict = {
            "ok": False,
            "restarted": False,
            "api": {},
            "devices": [],
            "sync": [],
            "message": "",
        }
        try:
            self.states.reset_device_probe_state()
            self.device_heartbeat.reset_reconnect_state()
        except Exception as exc:
            logger.warning("Pre-restart state reset skipped: %s", exc)

        try:
            if self._running:
                self.restart()
            else:
                self.start()
            report["restarted"] = True
        except Exception as exc:
            report["message"] = f"Engine restart failed: {exc}"
            logger.exception("CONNECTION_RESTART_ALL engine failed")
            return report

        try:
            self.device_heartbeat.reset_reconnect_state()
            self.api_health.probe_once()
            snap = self.states.snapshot()
            report["api"] = {
                "server_status": snap.server_status,
                "internet_status": snap.internet_status,
                "cloud_status": snap.server_status,
            }
        except Exception as exc:
            report["api"] = {"error": str(exc)}
            logger.warning("CONNECTION_RESTART_ALL api probe failed: %s", exc)

        try:
            # Wake neighbors before the first post-restart SYN storm settles.
            from adapters.protocol.zk_transport import wake_neighbor

            for device in self.container.devices.list_all(enabled_only=True):
                try:
                    wake_neighbor(device.ip_address, wait_ms=600)
                except Exception:
                    pass
            report["devices"] = self.device_heartbeat.probe_all()
        except Exception as exc:
            report["devices"] = [{"error": str(exc)}]
            logger.warning("CONNECTION_RESTART_ALL device probe failed: %s", exc)

        try:
            online_ids = [
                str(item.get("device_id") or "")
                for item in report["devices"]
                if isinstance(item, dict)
                and (item.get("network_ok") or item.get("protocol_ok") or item.get("ok"))
            ]
            online_ids = [did for did in online_ids if did]
            report["sync"] = self.sync_now(online_ids or None)
        except Exception as exc:
            report["sync"] = [{"error": str(exc)}]
            logger.warning("CONNECTION_RESTART_ALL sync failed: %s", exc)

        device_ok = sum(
            1
            for item in report["devices"]
            if isinstance(item, dict)
            and (item.get("network_ok") or item.get("protocol_ok") or item.get("ok"))
        )
        api_ok = str((report.get("api") or {}).get("server_status") or "").lower() in {
            "connected",
            "acceptable",
            "slow",
        }
        report["ok"] = bool(report["restarted"] and api_ok)
        report["message"] = (
            f"Restarted connections. Cloud={'ok' if api_ok else 'down'}; "
            f"devices online={device_ok}/{len([d for d in report['devices'] if isinstance(d, dict) and d.get('device_id')])}."
        )
        logger.info("CONNECTION_RESTART_ALL done %s", report["message"])
        return report

    def reload_settings(self) -> None:
        settings = self.container.settings.load()
        self.cloud.update_settings(settings)
        self.upload_engine.batch_size = settings.upload_batch_size
        self.upload_engine.max_retries = settings.max_upload_retries
        self.upload_engine.retry_attempts = settings.retry_count
        self.device_manager.connect_timeout = settings.device_connect_timeout
        self.device_manager.read_timeout = settings.device_read_timeout
        self.sessions.connect_timeout = settings.device_connect_timeout
        self.sessions.read_timeout = settings.device_read_timeout
        self.poll_service.connect_timeout = settings.device_connect_timeout
        self.poll_service.read_timeout = settings.device_read_timeout
        self.health_monitor.connect_timeout = settings.device_connect_timeout
        self.health_monitor.read_timeout = settings.device_read_timeout
        self.health_monitor.interval_seconds = max(30, settings.health_interval)
        self.health_monitor.max_workers = max(2, settings.max_worker_threads)
        self.device_heartbeat.interval_seconds = max(
            10, min(15, int(getattr(settings, "device_heartbeat_interval", 12) or 12))
        )
        self.api_health.interval_seconds = max(
            15, int(getattr(settings, "api_health_interval", 30) or 30)
        )
        self.command_service.connect_timeout = settings.device_connect_timeout
        self.command_service.read_timeout = settings.device_read_timeout
        self.command_service.interval_seconds = max(10, settings.command_poll_interval)
        self.security_gate.signing_secret = settings.command_signing_secret
        self.remote_config.interval_seconds = max(60, settings.remote_config_interval)
        self.alerts.queue_threshold = settings.queue_alert_threshold
        self.cleanup.uploaded_retention_days = settings.cleanup_uploaded_days
        self.cleanup.log_retention_days = settings.cleanup_log_days
        self.scheduler.configure_workers(settings.max_worker_threads)
        self.employee_sync.interval_seconds = max(30, settings.employee_sync_interval)
        self.employee_sync.max_workers = max(2, settings.max_worker_threads)
        self.time_sync.interval_seconds = max(60, settings.time_sync_interval)
        self.time_sync.drift_threshold_seconds = max(1, settings.time_drift_threshold_seconds)
        self.backup_service.interval_seconds = max(3600, settings.backup_interval)
        self.inventory_service.interval_seconds = max(60, settings.inventory_push_interval)
        self.inventory_service.max_workers = max(2, settings.max_worker_threads)
        if self.command_service.phase3_dispatcher is not None:
            self.command_service.phase3_dispatcher.enable_firmware_flash = bool(
                settings.enable_firmware_flash
            )
            self.command_service.phase3_dispatcher.enable_access_control = bool(
                settings.enable_access_control
            )
        self.access_control.enabled = bool(settings.enable_access_control)
        self._apply_logging_level(settings.logging_level)

    def sync_now(self, device_ids: list[str] | None = None) -> list[dict]:
        return self.scheduler.sync_now(device_ids)

    def dashboard(self) -> dict:
        settings = self.container.settings.as_model()
        summary = self.device_manager.status_summary()
        queue_count = self.container.attendance.pending_count()
        failed_count = self.container.attendance.failed_count()
        self.states.set_sync(
            SyncStatus.PENDING if queue_count else SyncStatus.SYNCED,
            pending=queue_count,
            failed=failed_count,
        )
        last_sync = self.container.attendance.last_uploaded_at()
        self.states.set_last_sync(last_sync)
        conn = self.states.snapshot()
        hb = self.container.heartbeat_repo.get_latest() or {}
        metrics = self.container.upload_metrics.get()
        self.alerts.queue_growing(queue_count)
        recent_errors = [
            {
                "id": s.id,
                "device_id": s.device_id,
                "action": s.action,
                "message": s.message,
                "created_at": s.created_at,
            }
            for s in self.container.sync_logs.recent(50)
            if not s.success
        ][:20]
        fully_connected = sum(
            1
            for d in conn.devices
            if d.get("status") == "connected" or d.get("protocol_status") == "connected"
        )
        connecting_devices = self.states.connecting_device_count()
        devices = self.container.devices.list_all()
        db_online = sum(
            1
            for d in devices
            if d.enabled and str(d.status or "").lower() in {"online", "connected"}
        )
        connected_devices = max(fully_connected, db_online)
        tcp_open = sum(
            1
            for d in conn.devices
            if str(d.get("network_status") or "").lower() in {"connected", "online"}
        )
        # Sync Now / poll may mark DB online a moment before heartbeat; keep UI honest.
        tcp_open = max(tcp_open, db_online)
        offline_devices = self.states.disconnected_device_count()
        if offline_devices == 0 and not conn.devices:
            offline_devices = summary["offline"]
        primary = (conn.devices[0] if conn.devices else {}) or {}
        if not primary and devices:
            primary = {
                "ip_address": devices[0].ip_address,
                "port": devices[0].port,
                "network_status": "connected" if db_online else "unknown",
                "protocol_status": "connected" if db_online else "unknown",
            }
        device_network = str(
            primary.get("display_network") or primary.get("network_status") or "unknown"
        )
        device_protocol = str(
            primary.get("display_protocol") or primary.get("protocol_status") or "unknown"
        )
        health_summary = {
            "healthy": sum(
                1
                for d in conn.devices
                if d.get("protocol_status") == "connected"
            ),
            "unhealthy": offline_devices,
            "unknown": connecting_devices or summary["unknown"],
            "connecting": connecting_devices,
        }
        if not conn.devices:
            health_summary = {
                "healthy": connected_devices,
                "unhealthy": offline_devices,
                "unknown": summary["unknown"],
                "connecting": connecting_devices,
            }
        cloud_status = public_cloud_status(conn.server_status)
        today_punch_list = self.container.attendance.list_today(limit=200)
        engine_running = bool(self._running)
        enabled_devices = sum(1 for d in devices if d.enabled)
        device_punch_counts = {
            d.id: self.container.attendance.count_for_device(d.id) for d in devices
        }
        device_pending_counts = {
            d.id: self.container.attendance.pending_count_for_device(d.id) for d in devices
        }
        device_today_counts = {
            d.id: self.container.attendance.count_today_for_device(d.id) for d in devices
        }
        link_by_id = {item.get("device_id"): item for item in (conn.devices or [])}
        device_snapshots = [
            {
                "id": d.id,
                "name": d.name,
                "enabled": d.enabled,
                "ip_address": d.ip_address,
                "port": d.port,
                "model": d.model,
                "serial_number": d.serial_number,
                "firmware": d.firmware,
                "mac_address": d.mac_address,
                "status": (
                    "disabled"
                    if not d.enabled
                    else (
                        # Prefer live heartbeat; if missing, trust last successful poll DB status.
                        operator_device_badge(
                            link_by_id.get(d.id),
                            fallback=str(d.status or "unknown"),
                        )
                        if link_by_id.get(d.id)
                        else (
                            "online"
                            if str(d.status or "").lower() in {"online", "connected"}
                            else str(d.status or "unknown")
                        )
                    )
                ),
                "health_status": d.health_status,
                "last_error": d.last_error,
                "last_sync_at": d.last_sync_at,
                "last_attendance_at": d.last_attendance_at,
                "terminal_code": d.terminal_code,
                "manufacturer": d.manufacturer,
                "branch": d.branch,
                "organization": d.organization,
                "communication_type": d.communication_type,
                "last_time_sync_at": d.last_time_sync_at,
            }
            for d in devices
        ]
        clock = self.container.attendance.clock_mismatch_summary()
        self._maybe_autofix_clocks(clock)
        uploaded_today = self.container.attendance.uploaded_today_count()
        avg_ms = float(metrics.get("average_upload_ms") or 0)
        p95_ms = self.container.attendance.p95_upload_latency_ms()
        health = compute_health_score(
            internet_status=str(conn.internet_status or ""),
            cloud_status=str(cloud_status or ""),
            online_devices=connected_devices,
            enabled_devices=enabled_devices,
            total_devices=summary["total"],
            uploaded_today=uploaded_today,
            failed_uploads=failed_count,
            pending_uploads=queue_count,
            average_upload_ms=avg_ms or None,
            clock_mismatch_count=int(clock.get("count") or 0),
            critical_clock_mismatch=bool(clock.get("critical")),
        )
        status_counts = self.container.attendance.count_by_status()
        branches = sorted({d.branch for d in devices if d.branch})
        uploading_count = int(status_counts.get("uploading") or 0)
        telemetry_at = hb.get("updated_at") or hb.get("created_at")
        telemetry_age_seconds = None
        telemetry_stale = False
        if telemetry_at:
            try:
                from datetime import datetime, timezone

                text = str(telemetry_at).replace("Z", "+00:00")
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                telemetry_age_seconds = max(
                    0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
                )
                telemetry_stale = bool(engine_running and telemetry_age_seconds > 300)
            except ValueError:
                telemetry_age_seconds = None
        return {
            "connector_status": "running" if engine_running else "stopped",
            "cloud_configured": bool(settings.organisation_id and settings.api_key),
            "cloud_base_url": settings.cloud_base_url,
            "cloud_status": cloud_status,
            "internet_status": conn.internet_status,
            "device_network_status": device_network,
            "device_protocol_status": device_protocol,
            "connected_devices": connected_devices,
            "tcp_open_devices": tcp_open,
            "connecting_devices": connecting_devices,
            "offline_devices": offline_devices,
            "enabled_devices": enabled_devices,
            "total_devices": summary["total"],
            "queue_count": queue_count,
            "uploading_count": uploading_count,
            "failed_uploads": failed_count,
            "uploaded_count": status_counts.get("uploaded", 0),
            "queue_alert_threshold": int(settings.queue_alert_threshold or 5000),
            "today_punches": self.container.attendance.today_count(),
            "today_punch_list": today_punch_list,
            "punch_history": today_punch_list,
            "device_punch_counts": device_punch_counts,
            "device_pending_counts": device_pending_counts,
            "device_today_counts": device_today_counts,
            "device_snapshots": device_snapshots,
            "uploaded_today": uploaded_today,
            "average_upload_ms": avg_ms,
            "p95_upload_ms": p95_ms,
            "last_sync": last_sync,
            "started_at": self._started_at,
            "version": self.container.version,
            "connector_id": settings.connector_id,
            "organisation_id": settings.organisation_id,
            "role": settings.role,
            "heartbeat": hb,
            "health_summary": health_summary,
            "health": health.to_dict(),
            "punch_trend": self.container.attendance.punch_trend(7),
            "punch_trend_30": self.container.attendance.punch_trend(30),
            "punch_trend_hourly": self.container.attendance.punch_trend_hourly(24),
            "last_telemetry_at": telemetry_at,
            "telemetry_age_seconds": telemetry_age_seconds,
            "telemetry_stale": telemetry_stale,
            "clock_mismatch": clock,
            "upload_status_counts": status_counts,
            "branches": branches,
            "connection": conn.to_public_dict(),
            "diagnostics_lines": conn.diagnostics_lines(),
            "status_headline": conn.resolved.title if conn.resolved else "",
            "status_detail": conn.resolved.body if conn.resolved else "",
            "status_tone": conn.resolved.tone if conn.resolved else "info",
            "status_code": conn.resolved.code if conn.resolved else "",
            "recent_errors": recent_errors,
            "alerts": [
                {
                    "id": a.id,
                    "alert_type": a.alert_type,
                    "severity": a.severity,
                    "title": a.title,
                    "message": a.message,
                    "device_id": a.device_id,
                    "acknowledged": a.acknowledged,
                    "created_at": a.created_at,
                    "details": a.details,
                }
                for a in self.alerts.recent(40)
            ],
            "cloud_dashboard": {},
            "recent_activity": [
                {
                    "id": s.id,
                    "device_id": s.device_id,
                    "action": s.action,
                    "success": s.success,
                    "message": s.message,
                    "records_count": s.records_count,
                    "created_at": s.created_at,
                }
                for s in self.container.sync_logs.recent(80)
            ],
            "live": True,
            "auto_refresh_ms": DEFAULT_DASHBOARD_REFRESH_MS,
        }

    def live_punches(self, limit: int = 200) -> dict:
        rows = self.container.attendance.list_today(limit=limit)
        return {"punches": rows, "today": self.container.attendance.today_count()}

    def list_attendance(self, **filters) -> dict:
        return self.container.attendance.list_filtered(**filters)

    def daily_attendance(self, day: str) -> list[dict]:
        from datetime import date as date_cls

        from services.attendance_analytics import resolve_daily_day

        return self.container.attendance.daily_attendance(
            resolve_daily_day(day, today=date_cls.today().isoformat())
        )

    def monthly_attendance(self, year: int, month: int) -> dict:
        return self.container.attendance.monthly_attendance(year, month)

    def list_upload_queue(self, *, status: str = "", limit: int = 200) -> list[dict]:
        return self.container.attendance.list_queue(status=status, limit=limit)

    def is_admin(self) -> bool:
        role = str(self.container.settings.as_model().role or "admin").strip().lower()
        return role in {"admin", "owner", "superadmin", ""}

    def sync_device_time(self, device_id: str) -> dict:
        device = self.device_manager.get(device_id)
        if not device:
            return {"success": False, "message": "Device not found"}
        result = self.time_sync.sync_device(device)
        self.container.audit.add(
            action="sync_device_time",
            actor="admin",
            success=bool(result.get("success")),
            message=str(result.get("message") or ""),
            resource_type="device",
            resource_id=device_id,
        )
        return result

    def _maybe_autofix_clocks(self, clock: dict) -> None:
        """Write the PC clock to any terminal still stuck in year 2000."""
        if not self._running or not clock or not clock.get("critical"):
            return
        samples = clock.get("samples") or []
        device_ids = [
            str(sample.get("device_id") or "")
            for sample in samples
            if isinstance(sample, dict) and sample.get("device_id")
        ]
        if not device_ids:
            device_ids = [d.id for d in self.container.devices.list_all(enabled_only=True)]
        with self._clock_autofix_lock:
            pending = [did for did in device_ids if did and did not in self._clock_autofix_ids]
            self._clock_autofix_ids.update(pending)
        if not pending:
            return

        def _run() -> None:
            for device_id in pending:
                try:
                    result = self.sync_device_time(device_id)
                    if result.get("success"):
                        logger.info(
                            "Auto-corrected device clock device=%s message=%s",
                            device_id,
                            result.get("message") or "",
                            extra={"device_id": device_id},
                        )
                    else:
                        with self._clock_autofix_lock:
                            self._clock_autofix_ids.discard(device_id)
                except Exception as exc:
                    logger.warning(
                        "Auto clock sync failed device=%s: %s", device_id, exc
                    )
                    with self._clock_autofix_lock:
                        self._clock_autofix_ids.discard(device_id)

        threading.Thread(target=_run, name="brixigo-clock-autofix", daemon=True).start()

    def _on_clock_mismatch(self, device, clock) -> None:
        self.alerts.clock_mismatch(
            device_name=getattr(device, "name", "") or "Device",
            device_id=getattr(device, "id", "") or "",
            device_time=getattr(clock, "device_timestamp", "") or "",
            server_time=getattr(clock, "server_received_at", "") or "",
            difference_label=getattr(clock, "difference_label", "") or "",
            details=clock.to_dict() if hasattr(clock, "to_dict") else {},
        )

    def _on_device_heartbeat_disconnected(self, device) -> None:
        from models import DeviceHealthSnapshot, new_id

        snap = DeviceHealthSnapshot(
            id=new_id(),
            device_id=device.id,
            online=False,
            message=f"{device.ip_address}:{device.port} unreachable after consecutive heartbeats",
        )
        self.alerts.device_offline(device, snap)

    def _apply_logging_level(self, level_name: str) -> None:
        import logging as _logging

        level = getattr(_logging, str(level_name or "INFO").upper(), _logging.INFO)
        _logging.getLogger("brixigo").setLevel(level)

    def _on_device_heartbeat_connected(self, device) -> None:
        """Pull terminal user names once when the device comes online."""
        with self._user_name_refresh_lock:
            if device.id in self._user_name_refresh_started:
                return
            self._user_name_refresh_started.add(device.id)

        def _run() -> None:
            time.sleep(1.5)
            if not self._running:
                return
            try:
                cached = self.container.device_users.list_for_device(device.id)
                if not any(str(getattr(u, "name", "") or "").strip() for u in cached):
                    self.poll_service.refresh_device_user_names(device)
                self.employee_sync.push_directory_to_cloud()
            except Exception as exc:
                logger.warning(
                    "Device user name refresh on connect failed device=%s: %s",
                    device.name,
                    exc,
                    extra={"device_id": device.id},
                )
            finally:
                with self._user_name_refresh_lock:
                    self._user_name_refresh_started.discard(device.id)

        threading.Thread(
            target=_run, name=f"brixigo-users-{device.id[:8]}", daemon=True
        ).start()

    def _refresh_device_user_names_async(self) -> None:
        """Fill Daily/Monthly employee names from the terminal user table."""

        def _run() -> None:
            time.sleep(5.0)
            for device in self.container.devices.list_all(enabled_only=True):
                if not self._running:
                    return
                try:
                    cached = self.container.device_users.list_for_device(device.id)
                    if not any(str(getattr(u, "name", "") or "").strip() for u in cached):
                        self.poll_service.refresh_device_user_names(device)
                except Exception as exc:
                    logger.warning(
                        "Device user name refresh failed device=%s: %s",
                        device.name,
                        exc,
                        extra={"device_id": device.id},
                    )
            try:
                self.employee_sync.push_directory_to_cloud()
            except Exception as exc:
                logger.warning("Employee directory push after name refresh failed: %s", exc)

        threading.Thread(
            target=_run, name="brixigo-user-names", daemon=True
        ).start()

    def _start_heartbeat(self) -> None:
        self._hb_stop.clear()

        def _loop() -> None:
            while not self._hb_stop.is_set():
                try:
                    self.heartbeat.beat()
                except Exception as exc:
                    logger.warning("Heartbeat loop error: %s", exc)
                interval = max(
                    15, int(self.container.settings.as_model().heartbeat_interval or 60)
                )
                self._hb_stop.wait(interval)

        self._hb_thread = threading.Thread(
            target=_loop, name="brixigo-heartbeat", daemon=True
        )
        self._hb_thread.start()
