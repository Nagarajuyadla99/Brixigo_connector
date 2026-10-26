"""Phase 3 remote-command dispatch handlers — plugged into CommandService."""

from __future__ import annotations

import logging
from typing import Any, Optional

from models import CommandType, Device, RemoteCommand

logger = logging.getLogger("brixigo.service")


class Phase3CommandDispatcher:
    """Routes Phase 3 remote commands to enterprise services."""

    def __init__(
        self,
        *,
        employee_sync: Any,
        templates: Any,
        users: Any,
        backup: Any,
        provisioning: Any,
        firmware: Any,
        inventory: Any,
        reporting: Any,
        remote_support: Any,
        devices: Any,
        enable_firmware_flash: bool = False,
        enable_access_control: bool = False,
    ) -> None:
        self.employee_sync = employee_sync
        self.templates = templates
        self.users = users
        self.backup = backup
        self.provisioning = provisioning
        self.firmware = firmware
        self.inventory = inventory
        self.reporting = reporting
        self.remote_support = remote_support
        self.devices = devices
        self.enable_firmware_flash = enable_firmware_flash
        self.enable_access_control = enable_access_control

    def dispatch(self, command: RemoteCommand, device: Optional[Device]) -> Optional[dict]:
        ctype = command.command_type
        payload = command.payload or {}

        if ctype in (
            CommandType.FIRMWARE_UPGRADE.value,
            CommandType.FIRMWARE_ROLLBACK.value,
        ) and not self.enable_firmware_flash:
            raise PermissionError(
                "Firmware flash is disabled (enable_firmware_flash=false). "
                "Package validation-only mode is active until vendor flash is certified."
            )

        if ctype == CommandType.SYNC_EMPLOYEES.value:
            if command.device_id and device:
                result = self.employee_sync.sync_employee_to_devices(
                    payload.get("employee_id") or ""
                ) if payload.get("employee_id") else self.employee_sync.sync_all()
            else:
                result = self.employee_sync.sync_all()
            return {"message": "Employee sync complete", "result": result}

        if ctype == CommandType.SYNC_TEMPLATES.value:
            assert device is not None
            saved = self.templates.download_from_device(device.id)
            return {"message": f"Downloaded {len(saved)} templates", "count": len(saved)}

        if ctype == CommandType.DOWNLOAD_USERS.value:
            assert device is not None
            users = self.users.download_users(device.id)
            return {"message": f"Downloaded {len(users)} users", "count": len(users)}

        if ctype == CommandType.UPLOAD_USERS.value:
            assert device is not None
            from models import DeviceUser

            user = DeviceUser(
                user_id=str(payload.get("user_id") or ""),
                name=str(payload.get("name") or ""),
                card=str(payload.get("card") or ""),
                privilege=int(payload.get("privilege") or 0),
                password=str(payload.get("password") or ""),
                enabled=bool(payload.get("enabled", True)),
            )
            self.users.upload_user(device.id, user)
            return {"message": f"Uploaded user {user.user_id}"}

        if ctype == CommandType.DELETE_USERS.value:
            assert device is not None
            user_ids = payload.get("user_ids") or (
                [payload["user_id"]] if payload.get("user_id") else []
            )
            for uid in user_ids:
                self.users.delete_user(device.id, str(uid))
            return {"message": f"Deleted {len(user_ids)} users"}

        if ctype == CommandType.BULK_SYNC_USERS.value:
            device_ids = payload.get("device_ids")
            result = self.users.bulk_sync(device_ids)
            return {"message": "Bulk user sync complete", "result": result}

        if ctype == CommandType.BACKUP_DEVICE.value:
            assert device is not None
            backup = self.backup.backup_device(device.id)
            return {"message": "Backup created", "backup_id": backup.id, "path": backup.backup_path}

        if ctype == CommandType.RESTORE_DEVICE.value:
            assert device is not None
            backup_id = str(payload.get("backup_id") or "")
            result = self.backup.restore_device(
                device.id,
                backup_id,
                include_users=bool(payload.get("include_users", True)),
                include_templates=bool(payload.get("include_templates", True)),
            )
            return {"message": "Restore complete", "result": result}

        if ctype == CommandType.PROVISION_DEVICE.value:
            result = self.provisioning.provision_discovered(
                ip_address=str(payload.get("ip_address") or (device.ip_address if device else "")),
                port=int(payload.get("port") or (device.port if device else 4370)),
                manufacturer=str(payload.get("manufacturer") or (device.manufacturer if device else "zkteco")),
                password=str(payload.get("password") or "0"),
                organization=str(payload.get("organization") or ""),
                branch=str(payload.get("branch") or ""),
                name=str(payload.get("name") or ""),
                model=str(payload.get("model") or ""),
                serial_number=str(payload.get("serial_number") or ""),
                mac_address=str(payload.get("mac_address") or ""),
                firmware=str(payload.get("firmware") or ""),
                sdk_version=str(payload.get("sdk_version") or ""),
            )
            return {"message": "Provisioning complete", "result": result}

        if ctype == CommandType.FIRMWARE_UPGRADE.value:
            assert device is not None
            job = self.firmware.upgrade(
                device.id,
                str(payload.get("package_path") or ""),
                to_version=str(payload.get("to_version") or ""),
            )
            return {"message": job.message, "job_id": job.id, "status": job.status}

        if ctype == CommandType.FIRMWARE_ROLLBACK.value:
            assert device is not None
            job = self.firmware.rollback(
                device.id,
                str(payload.get("package_path") or ""),
                to_version=str(payload.get("to_version") or ""),
            )
            return {"message": job.message, "job_id": job.id, "status": job.status}

        if ctype == CommandType.PUSH_INVENTORY.value:
            result = self.inventory.push()
            return {"message": "Inventory pushed", "result": result}

        if ctype == CommandType.REMOTE_LOG_DOWNLOAD.value:
            return {"message": "Logs packaged", "result": self.remote_support.download_logs()}

        if ctype == CommandType.REMOTE_DIAGNOSTICS.value:
            return {
                "message": "Diagnostics complete",
                "result": self.remote_support.run_diagnostics(device),
            }

        if ctype == CommandType.REMOTE_RESTART_CONNECTOR.value:
            return self.remote_support.restart_connector()

        if ctype == CommandType.REMOTE_CACHE_CLEANUP.value:
            return {"message": "Cleanup complete", "result": self.remote_support.cache_cleanup()}

        if ctype == CommandType.REMOTE_DB_BACKUP.value:
            return {"message": "DB backup complete", "result": self.remote_support.database_backup()}

        if ctype == CommandType.REMOTE_DB_RESTORE.value:
            path = str(payload.get("backup_path") or "")
            return {
                "message": "DB restore complete",
                "result": self.remote_support.database_restore(path),
            }

        if ctype == CommandType.GENERATE_REPORT.value:
            report_type = str(payload.get("report_type") or "connector_health")
            req = self.reporting.generate(report_type, payload.get("parameters") or {})
            return {
                "message": "Report generated",
                "report_id": req.id,
                "path": req.result_path,
                "status": req.status,
            }

        return None
