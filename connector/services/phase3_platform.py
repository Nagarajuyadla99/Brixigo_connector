"""Phase 3 platform services — provisioning, access control, reporting, notifications, remote support."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from api.cloud_client import CloudClient
from models import AlertSeverity, AlertType, Device, new_id, utcnow
from models.phase3 import (
    AccessLevel,
    AccessSchedule,
    DoorController,
    NotificationEvent,
    ReportRequest,
)
from services.alert_service import AlertService
from services.diagnostics import NetworkDiagnostics
from services.phase3_services import BackupRestoreService, InventoryService
from storage.database import Database
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import AuditRepository
from storage.repositories.phase3_repo import (
    AccessControlRepository,
    NotificationRepository,
    ReportRepository,
)
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.service")


class ProvisioningService:
    """
    Zero-touch device provisioning:
    discover → register → download org config/employees/templates/policies → configure.
    """

    def __init__(
        self,
        cloud: CloudClient,
        devices: DeviceRepository,
        device_manager: Any,
        employee_sync: Any,
        template_service: Any,
        inventory: InventoryService,
        audit: AuditRepository,
        sync_logs: SyncLogRepository,
    ) -> None:
        self._cloud = cloud
        self._devices = devices
        self._device_manager = device_manager
        self._employee_sync = employee_sync
        self._templates = template_service
        self._inventory = inventory
        self._audit = audit
        self._sync_logs = sync_logs

    def provision_discovered(
        self,
        *,
        ip_address: str,
        port: int,
        manufacturer: str,
        password: str = "0",
        organization: str = "",
        branch: str = "",
        name: str = "",
        model: str = "",
        serial_number: str = "",
        mac_address: str = "",
        firmware: str = "",
        sdk_version: str = "",
    ) -> dict:
        existing = self._devices.get_by_ip_port(ip_address, port)
        if existing:
            device = existing
            created = False
        else:
            device = self._device_manager.add_device(
                name=name or f"{manufacturer}-{ip_address}",
                manufacturer=manufacturer or "zkteco",
                model=model,
                ip_address=ip_address,
                port=port,
                password=password,
                organization=organization,
                branch=branch,
                serial_number=serial_number,
                mac_address=mac_address,
                firmware=firmware,
                sdk_version=sdk_version,
                enabled=True,
            )
            created = True
            try:
                self._cloud.register_device(device.to_public_dict())
            except Exception as exc:
                logger.warning("Cloud device register skipped: %s", exc)

        # Download organization configuration / policies
        policies: dict[str, Any] = {}
        try:
            policies = self._cloud.download_device_policies(device_id=device.id)
        except Exception as exc:
            logger.warning("Policy download skipped: %s", exc)

        if isinstance(policies.get("device"), dict):
            updates = policies["device"]
            self._device_manager.update_device(
                device.id,
                polling_interval=updates.get("polling_interval"),
                timezone=updates.get("timezone"),
                organization=updates.get("organization") or organization or None,
                branch=updates.get("branch") or branch or None,
                department=updates.get("department"),
                building=updates.get("building"),
                floor=updates.get("floor"),
                gate=updates.get("gate"),
            )

        # Employees + templates
        emp_result = {}
        try:
            emp_result = self._employee_sync.sync_all()
        except Exception as exc:
            emp_result = {"error": str(exc)}

        try:
            self._templates.download_from_device(device.id)
        except Exception:
            pass

        inv = self._inventory._collect_one(self._devices.get(device.id) or device)
        self._audit.add(
            action="device_provision",
            actor="system",
            success=True,
            message=f"Provisioned {device.name}",
            resource_type="device",
            resource_id=device.id,
            details={"created": created, "policies": bool(policies)},
        )
        self._sync_logs.add(
            device_id=device.id,
            action="provision",
            success=True,
            message="Device provisioned",
        )
        return {
            "device_id": device.id,
            "created": created,
            "employee_sync": emp_result,
            "inventory": inv,
            "policies_applied": bool(policies),
        }


class AccessControlService:
    """Architecture-ready access-control domain for door controllers and schedules."""

    def __init__(
        self,
        repo: AccessControlRepository,
        audit: AuditRepository,
        *,
        enabled: bool = False,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self.enabled = enabled

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise PermissionError(
                "Access control is disabled (enable_access_control=false) until "
                "door/controller enforcement is certified."
            )

    def upsert_level(self, **kwargs) -> AccessLevel:
        self._require_enabled()
        level = AccessLevel(
            id=kwargs.get("id") or new_id(),
            name=str(kwargs["name"]),
            organization=str(kwargs.get("organization") or ""),
            branch=str(kwargs.get("branch") or ""),
            schedule_json=json.dumps(kwargs.get("schedule") or {}),
            door_ids=list(kwargs.get("door_ids") or []),
            extra=dict(kwargs.get("extra") or {}),
        )
        saved = self._repo.upsert_level(level)
        self._audit.add(
            action="access_level_upsert",
            actor="admin",
            success=True,
            message=saved.name,
            resource_type="access_level",
            resource_id=saved.id,
        )
        return saved

    def upsert_door(self, **kwargs) -> DoorController:
        self._require_enabled()
        door = DoorController(
            id=kwargs.get("id") or new_id(),
            name=str(kwargs["name"]),
            device_id=str(kwargs.get("device_id") or ""),
            ip_address=str(kwargs.get("ip_address") or ""),
            port=int(kwargs.get("port") or 4370),
            organization=str(kwargs.get("organization") or ""),
            branch=str(kwargs.get("branch") or ""),
            building=str(kwargs.get("building") or ""),
            floor=str(kwargs.get("floor") or ""),
            gate=str(kwargs.get("gate") or ""),
            status=str(kwargs.get("status") or "unknown"),
            extra=dict(kwargs.get("extra") or {}),
        )
        return self._repo.upsert_door(door)

    def upsert_schedule(self, **kwargs) -> AccessSchedule:
        self._require_enabled()
        schedule = AccessSchedule(
            id=kwargs.get("id") or new_id(),
            name=str(kwargs["name"]),
            timezone=str(kwargs.get("timezone") or "Asia/Kolkata"),
            rules_json=json.dumps(kwargs.get("rules") or []),
        )
        return self._repo.upsert_schedule(schedule)

    def snapshot(self) -> dict:
        return {
            "access_levels": [a.__dict__ for a in self._repo.list_levels()],
            "doors": [d.__dict__ for d in self._repo.list_doors()],
        }


class ReportingService:
    def __init__(
        self,
        db: Database,
        reports: ReportRepository,
        report_dir: Path,
        cloud: Optional[CloudClient] = None,
    ) -> None:
        self._db = db
        self._reports = reports
        self.report_dir = report_dir
        self._cloud = cloud
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, report_type: str, parameters: dict | None = None) -> ReportRequest:
        req = self._reports.add(
            ReportRequest(
                id=new_id(),
                report_type=report_type,
                parameters=parameters or {},
                status="running",
            )
        )
        try:
            data = self._build(report_type, parameters or {})
            path = self.report_dir / f"{report_type}_{req.id[:8]}.json"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            req.status = "succeeded"
            req.result_path = str(path)
            req.completed_at = utcnow().isoformat()
            self._reports.update(req)
            if self._cloud and self._cloud.settings.connector_id:
                try:
                    self._cloud.push_report(
                        {
                            "report_id": req.id,
                            "report_type": report_type,
                            "generated_at": req.completed_at,
                            "data": data,
                        }
                    )
                except Exception:
                    pass
            return req
        except Exception as exc:
            req.status = "failed"
            req.result_path = ""
            req.completed_at = utcnow().isoformat()
            self._reports.update(req)
            raise RuntimeError(str(exc)) from exc

    def _build(self, report_type: str, params: dict) -> dict:
        limit = int(params.get("limit") or 500)
        builders = {
            "attendance_sync": self._attendance_sync,
            "employee_sync": self._employee_sync,
            "device_health": self._device_health,
            "device_uptime": self._device_uptime,
            "template_sync": self._template_sync,
            "firmware": self._firmware,
            "command_history": self._command_history,
            "connector_health": self._connector_health,
            "audit": self._audit,
            "performance": self._performance,
        }
        fn = builders.get(report_type)
        if not fn:
            raise ValueError(f"Unknown report type: {report_type}")
        return {
            "report_type": report_type,
            "generated_at": utcnow().isoformat(),
            "rows": fn(limit),
        }

    def _attendance_sync(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                """
                SELECT action, success, message, records_count, created_at, device_id
                FROM sync_logs WHERE action LIKE 'poll%' OR action LIKE 'upload%'
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        ]

    def _employee_sync(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                """
                SELECT * FROM sync_logs WHERE action LIKE 'employee_sync%'
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        ]

    def _device_health(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                "SELECT * FROM device_health ORDER BY collected_at DESC LIMIT ?",
                (limit,),
            )
        ]

    def _device_uptime(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                """
                SELECT id, name, status, uptime_seconds, last_health_at, branch, gate
                FROM devices ORDER BY name LIMIT ?
                """,
                (limit,),
            )
        ]

    def _template_sync(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                """
                SELECT id, employee_code, template_type, finger_index, size, vendor, updated_at
                FROM biometric_templates ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            )
        ]

    def _firmware(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                "SELECT * FROM firmware_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        ]

    def _command_history(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                "SELECT * FROM remote_commands ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        ]

    def _connector_health(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                "SELECT * FROM heartbeat_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        ]

    def _audit(self, limit: int) -> list[dict]:
        return [
            dict(r)
            for r in self._db.fetchall(
                "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        ]

    def _performance(self, limit: int) -> list[dict]:
        metrics = self._db.fetchone("SELECT * FROM upload_metrics WHERE id=1")
        queue = self._db.fetchone(
            "SELECT COUNT(*) AS c FROM attendance_queue WHERE status IN ('pending','failed','uploading')"
        )
        return [
            {
                "upload_metrics": dict(metrics) if metrics else {},
                "pending_queue": int(queue["c"]) if queue else 0,
                "device_count": self._db.fetchone("SELECT COUNT(*) AS c FROM devices")["c"],
            }
        ]


class NotificationEngine:
    """Fan-out notifications for Phase 3 operational events."""

    def __init__(
        self,
        notifications: NotificationRepository,
        alerts: AlertService,
        cloud: CloudClient,
        audit: AuditRepository,
    ) -> None:
        self._notifications = notifications
        self._alerts = alerts
        self._cloud = cloud
        self._audit = audit

    def notify(
        self,
        *,
        event_type: str,
        title: str,
        message: str,
        severity: str = AlertSeverity.WARNING.value,
        device_id: str = "",
        details: dict | None = None,
    ) -> NotificationEvent:
        # Map to alert service for dedupe + local persistence
        alert_type = event_type if event_type in {
            a.value for a in AlertType
        } else AlertType.SYNC_FAILURE.value
        self._alerts.raise_alert(
            alert_type=alert_type,
            title=title,
            message=message,
            severity=severity,
            device_id=device_id,
            details=details,
        )
        event = self._notifications.add(
            NotificationEvent(
                id=new_id(),
                event_type=event_type,
                title=title,
                message=message,
                severity=severity,
                device_id=device_id,
                details=details or {},
            )
        )
        delivered = False
        if self._cloud.settings.connector_id:
            try:
                self._cloud.push_notification(
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "title": event.title,
                        "message": event.message,
                        "severity": event.severity,
                        "device_id": event.device_id,
                        "created_at": event.created_at,
                        "details": event.details,
                    }
                )
                delivered = True
                self._notifications.mark_delivered(event.id)
            except Exception as exc:
                logger.debug("Notification push failed: %s", exc)
        event.delivered = delivered
        return event


class RemoteSupportService:
    """Cloud-driven remote support operations for field engineers."""

    def __init__(
        self,
        cloud: CloudClient,
        diagnostics: NetworkDiagnostics,
        cleanup: Any,
        backup: BackupRestoreService,
        audit: AuditRepository,
        paths_logs: Path,
        paths_data: Path,
        db: Database,
        engine_restart: Optional[Callable[[], None]] = None,
    ) -> None:
        self._cloud = cloud
        self._diagnostics = diagnostics
        self._cleanup = cleanup
        self._backup = backup
        self._audit = audit
        self.paths_logs = paths_logs
        self.paths_data = paths_data
        self._db = db
        self.engine_restart = engine_restart

    def download_logs(self) -> dict:
        archive = self.paths_data / f"remote_logs_{utcnow().strftime('%Y%m%d_%H%M%S')}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in self.paths_logs.glob("*.log"):
                zf.write(path, arcname=path.name)
        result = {"path": str(archive), "size": archive.stat().st_size}
        try:
            self._cloud.upload_support_artifact(
                artifact_type="logs",
                file_path=str(archive),
                meta=result,
            )
        except Exception as exc:
            result["upload_error"] = str(exc)
        self._audit.add(
            action="remote_log_download",
            actor="cloud_support",
            success=True,
            message="Logs packaged",
            resource_type="support",
            resource_id=archive.name,
        )
        return result

    def run_diagnostics(self, device: Optional[Device] = None) -> dict:
        suite = self._diagnostics.run_full_suite(device)
        self._audit.add(
            action="remote_diagnostics",
            actor="cloud_support",
            success=True,
            message="Diagnostics executed",
            resource_type="support",
            details={"keys": list(suite.keys())},
        )
        try:
            self._cloud.push_support_diagnostics(suite)
        except Exception:
            pass
        return suite

    def restart_connector(self) -> dict:
        if self.engine_restart:
            self.engine_restart()
        self._audit.add(
            action="remote_restart_connector",
            actor="cloud_support",
            success=True,
            message="Connector restart requested",
            resource_type="support",
        )
        return {"restarted": True}

    def cache_cleanup(self) -> dict:
        result = self._cleanup.run_once()
        self._audit.add(
            action="remote_cache_cleanup",
            actor="cloud_support",
            success=True,
            message="Cleanup executed",
            resource_type="support",
            details=result,
        )
        return result

    def database_backup(self) -> dict:
        dest = self.paths_data / f"connector_db_{utcnow().strftime('%Y%m%d_%H%M%S')}.db"
        src = self._db.db_path
        shutil.copy2(src, dest)
        result = {"path": str(dest), "size": dest.stat().st_size}
        try:
            self._cloud.upload_support_artifact(
                artifact_type="database_backup",
                file_path=str(dest),
                meta=result,
            )
        except Exception as exc:
            result["upload_error"] = str(exc)
        self._audit.add(
            action="remote_db_backup",
            actor="cloud_support",
            success=True,
            message="Database backup created",
            resource_type="support",
            resource_id=dest.name,
        )
        return result

    def database_restore(self, backup_path: str) -> dict:
        src = Path(backup_path)
        if not src.exists():
            raise FileNotFoundError(backup_path)
        # Safety copy current DB then replace
        safety = self.paths_data / f"pre_restore_{utcnow().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(self._db.db_path, safety)
        self._db.close()
        shutil.copy2(src, self._db.db_path)
        self._db.initialize()
        self._audit.add(
            action="remote_db_restore",
            actor="cloud_support",
            success=True,
            message=f"Restored from {src.name}",
            resource_type="support",
            details={"safety_copy": str(safety)},
        )
        return {"restored": True, "safety_copy": str(safety)}
