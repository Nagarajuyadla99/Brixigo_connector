"""Remote cloud command polling and execution."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from adapters.registry import create_adapter
from api.cloud_client import CloudAPIError, CloudClient
from models import (
    CommandStatus,
    CommandType,
    Device,
    RemoteCommand,
    new_id,
    utcnow,
)
from security.command_security import CommandSecurityGate
from services.poll_service import PollService
from storage.repositories.command_repo import CommandRepository
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import AuditRepository
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.service")


class CommandService:
    """Poll cloud for signed commands, execute locally, acknowledge results."""

    def __init__(
        self,
        cloud: CloudClient,
        commands: CommandRepository,
        devices: DeviceRepository,
        sync_logs: SyncLogRepository,
        audit: AuditRepository,
        security_gate: CommandSecurityGate,
        poll_service: PollService,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        interval_seconds: int = 30,
        on_device_disabled: Optional[Callable[[str], None]] = None,
        on_device_enabled: Optional[Callable[[str], None]] = None,
        sessions=None,
    ) -> None:
        self._cloud = cloud
        self._commands = commands
        self._devices = devices
        self._sync_logs = sync_logs
        self._audit = audit
        self._gate = security_gate
        self._poll_service = poll_service
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.interval_seconds = max(10, interval_seconds)
        self.on_device_disabled = on_device_disabled
        self.on_device_enabled = on_device_enabled
        self._sessions = sessions
        self.phase3_dispatcher = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-command-poller", daemon=True
        )
        self._thread.start()
        logger.info("Command service started interval=%ss", self.interval_seconds)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("Command service stopped")

    def poll_and_execute(self) -> list[dict]:
        results: list[dict] = []
        if self._cloud.settings.connector_id:
            try:
                remote = self._cloud.fetch_commands()
                for item in remote:
                    cmd = self._normalize(item)
                    self._commands.upsert(cmd)
            except CloudAPIError as exc:
                logger.warning("Command poll failed: %s", exc)
            except Exception as exc:
                logger.warning("Command poll error: %s", exc)

        for command in self._commands.list_pending():
            results.append(self.execute(command))
        return results

    def execute(self, command: RemoteCommand) -> dict:
        ok, reason = self._gate.validate(command)
        if not ok:
            self._commands.update_status(
                command.id,
                status=CommandStatus.REJECTED.value,
                result_message=reason,
            )
            self._ack(command, CommandStatus.REJECTED.value, reason, {})
            return {"id": command.id, "success": False, "message": reason}

        self._commands.update_status(command.id, status=CommandStatus.RUNNING.value)
        try:
            payload = self._dispatch(command)
            message = str(payload.get("message") or "ok")
            self._commands.update_status(
                command.id,
                status=CommandStatus.SUCCEEDED.value,
                result_message=message,
                result_payload=payload,
            )
            self._sync_logs.add(
                device_id=command.device_id,
                action=f"command:{command.command_type}",
                success=True,
                message=message,
                details=payload,
            )
            self._ack(command, CommandStatus.SUCCEEDED.value, message, payload)
            return {"id": command.id, "success": True, "message": message, "result": payload}
        except Exception as exc:
            message = str(exc)
            self._commands.update_status(
                command.id,
                status=CommandStatus.FAILED.value,
                result_message=message,
            )
            self._sync_logs.add(
                device_id=command.device_id,
                action=f"command:{command.command_type}",
                success=False,
                message=message,
            )
            self._ack(command, CommandStatus.FAILED.value, message, {})
            return {"id": command.id, "success": False, "message": message}

    def _dispatch(self, command: RemoteCommand) -> dict:
        ctype = command.command_type
        device = None
        if command.device_id:
            device = self._devices.get(command.device_id)
            if not device:
                raise KeyError(f"Device {command.device_id} not found")

        if ctype == CommandType.ENABLE_DEVICE.value:
            assert device is not None
            self._devices.set_enabled(device.id, True)
            if self.on_device_enabled:
                self.on_device_enabled(device.id)
            return {"message": f"Device {device.name} enabled"}

        if ctype == CommandType.DISABLE_DEVICE.value:
            assert device is not None
            self._devices.set_enabled(device.id, False)
            if self.on_device_disabled:
                self.on_device_disabled(device.id)
            return {"message": f"Device {device.name} disabled"}

        if ctype == CommandType.DOWNLOAD_ATTENDANCE.value:
            assert device is not None
            result = self._poll_service.poll_device(device)
            return {"message": "Attendance download complete", "poll": result}

        if ctype == CommandType.TEST_CONNECTION.value:
            assert device is not None
            return self._with_adapter(device, lambda a: {
                "message": "Connection tested",
                **a.health().__dict__,
            })

        if ctype == CommandType.REFRESH_DEVICE_INFO.value:
            assert device is not None
            return self._refresh_info(device)

        if ctype == CommandType.SYNC_TIME.value:
            assert device is not None
            return self._with_adapter(device, lambda a: (a.sync_time(), {"message": "Time synced"})[1])

        if ctype == CommandType.CLEAR_ATTENDANCE.value:
            assert device is not None
            return self._with_adapter(
                device, lambda a: (a.clear_attendance(), {"message": "Attendance cleared"})[1]
            )

        if ctype == CommandType.RESTART_DEVICE.value:
            assert device is not None
            return self._with_adapter(
                device, lambda a: (a.restart_device(), {"message": "Restart requested"})[1]
            )

        if self.phase3_dispatcher is not None:
            result = self.phase3_dispatcher.dispatch(command, device)
            if result is not None:
                return result

        raise ValueError(f"Unsupported command type: {ctype}")

    def _refresh_info(self, device: Device) -> dict:
        def _op(adapter):
            info = adapter.device_info()
            self._devices.update_telemetry(
                device.id,
                firmware=info.firmware or None,
                mac_address=info.mac_address or None,
                sdk_version=info.sdk_version or None,
                model=info.model or None,
                serial_number=info.serial_number or None,
                current_users=info.user_count,
                current_logs=info.attendance_count,
            )
            return {
                "message": "Device info refreshed",
                "info": info.__dict__,
            }

        return self._with_adapter(device, _op)

    def _with_adapter(self, device: Device, fn: Callable) -> dict:
        if self._sessions is not None:
            return self._sessions.execute(device, fn, retries=2, owner="command")
        adapter = create_adapter(
            device,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )
        with adapter:
            return fn(adapter)

    def _normalize(self, item: dict) -> RemoteCommand:
        return RemoteCommand(
            id=new_id(),
            cloud_command_id=str(item.get("id") or item.get("command_id") or ""),
            command_type=str(item.get("command_type") or item.get("type") or ""),
            device_id=str(item.get("device_id") or ""),
            payload=dict(item.get("payload") or {}),
            status=CommandStatus.PENDING.value,
            signature=str(item.get("signature") or ""),
            nonce=str(item.get("nonce") or ""),
            issued_at=str(item.get("issued_at") or ""),
            expires_at=str(item.get("expires_at") or ""),
            created_at=utcnow().isoformat(),
        )

    def _ack(self, command: RemoteCommand, status: str, message: str, payload: dict) -> None:
        if not command.cloud_command_id:
            return
        try:
            self._cloud.acknowledge_command(
                command_id=command.cloud_command_id,
                status=status,
                message=message,
                result=payload,
            )
        except Exception as exc:
            logger.warning("Command ack failed id=%s: %s", command.cloud_command_id, exc)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_and_execute()
            except Exception as exc:
                logger.exception("Command cycle failed: %s", exc)
            self._stop.wait(self.interval_seconds)
