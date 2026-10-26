"""ZKTeco biometric adapter with enterprise command/health extensions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from adapters.base import IBiometricAdapter, parse_comm_password
from adapters.protocol.zk_attendance_fields import interpret_zk_log_fields
from adapters.protocol.zk_protocol import ZKClient, ZKProtocolError
from adapters.protocol.zk_user_ops import ZKUserTemplateMixin
from models import AttendanceLog, Device, DeviceInfo, DeviceUser, HealthResult


class ZKTecoAdapter(ZKUserTemplateMixin, IBiometricAdapter):
    manufacturer = "zkteco"

    def __init__(self, device: Device, *, connect_timeout: float = 8.0, read_timeout: float = 30.0):
        super().__init__(device, connect_timeout=connect_timeout, read_timeout=read_timeout)
        self._client: Optional[ZKClient] = None

    def connect(self) -> None:
        if self._client and self._client.connected:
            return
        self._client = ZKClient(
            ip=self.device.ip_address,
            port=int(self.device.port or 4370),
            password=parse_comm_password(self.device.password),
            timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            force_udp=self.device.communication_type == "udp",
            manufacturer=self.device.manufacturer or "zkteco",
            model=self.device.model or "",
        )
        self._client.connect()

    def disconnect(self) -> None:
        if self._client:
            self._client.disconnect()
            self._client = None

    def _require(self) -> ZKClient:
        if not self._client or not self._client.connected:
            self.connect()
        assert self._client is not None
        return self._client

    def download_logs(self, since: Optional[datetime] = None) -> list[AttendanceLog]:
        client = self._require()
        logs: list[AttendanceLog] = []
        for item in client.get_attendance():
            punch_time: datetime = item["punch_time"]
            # Keep punches at the cursor second (re-fetch is idempotent) so a
            # cursor advanced without a successful insert cannot drop them.
            if since and punch_time < since:
                continue
            status = int(item.get("status") or 0)
            punch = int(item.get("punch") or 0)
            fields = interpret_zk_log_fields(status, punch)
            logs.append(
                AttendanceLog(
                    user_id=str(item["user_id"]),
                    punch_time=punch_time,
                    status=status,
                    punch=punch,
                    uid=int(item.get("uid") or 0),
                    raw={**item, "vendor": "zkteco", **fields},
                )
            )
        return logs

    def download_users(self) -> list[DeviceUser]:
        return [
            DeviceUser(
                user_id=str(u.get("user_id") or ""),
                name=str(u.get("name") or ""),
                card=str(u.get("card") or ""),
                privilege=int(u.get("privilege") or 0),
                password=str(u.get("password") or ""),
                user_uid=int(u.get("uid") or 0),
                raw=u,
            )
            for u in self._require().get_users()
        ]

    def device_info(self) -> DeviceInfo:
        info = self._require().get_extended_device_info()
        return DeviceInfo(
            serial_number=str(info.get("serial_number") or ""),
            firmware=str(info.get("firmware") or ""),
            platform=str(info.get("platform") or ""),
            user_count=int(info.get("user_count") or 0),
            attendance_count=int(info.get("attendance_count") or 0),
            device_name=str(info.get("device_name") or self.device.name),
            mac_address=str(info.get("mac_address") or ""),
            sdk_version=str(info.get("sdk_version") or ""),
            model=str(info.get("model") or ""),
            raw=info,
        )

    def health(self) -> HealthResult:
        owned = self._client is None or not self._client.connected
        try:
            client = self._require()
            online, latency, message = client.health_check()
            details: dict[str, Any] = {}
            if online:
                try:
                    details = client.get_extended_device_info()
                except Exception:
                    details = {}
            return HealthResult(
                online=online, message=message, latency_ms=latency, details=details
            )
        except Exception as exc:
            return HealthResult(online=False, message=str(exc))
        finally:
            if owned:
                self.disconnect()

    def sync_time(self, when: Optional[datetime] = None) -> None:
        try:
            self._require().set_time(when)
        except ZKProtocolError:
            raise
        except Exception as exc:
            raise ZKProtocolError(str(exc)) from exc

    def enable_device(self) -> None:
        self._require().enable_device()

    def disable_device(self) -> None:
        self._require().disable_device()

    def clear_attendance(self) -> None:
        self._require().clear_attendance()

    def restart_device(self) -> None:
        self._require().reboot()

    def collect_health_metrics(self) -> dict[str, Any]:
        result = self.health()
        details = result.details or {}
        user_cap = int(details.get("user_capacity") or 0)
        log_cap = int(details.get("log_capacity") or 0)
        users = int(details.get("user_count") or 0)
        logs = int(details.get("attendance_count") or 0)
        storage = 0.0
        if log_cap > 0:
            storage = min(100.0, (logs / log_cap) * 100.0)
        memory = 0.0
        if user_cap > 0:
            memory = min(100.0, (users / user_cap) * 100.0)
        return {
            "online": result.online,
            "ping_ms": result.latency_ms,
            "message": result.message,
            "firmware_version": str(details.get("firmware") or ""),
            "serial_number": str(details.get("serial_number") or ""),
            "device_name": str(details.get("device_name") or ""),
            "mac_address": str(details.get("mac_address") or ""),
            "sdk_version": str(details.get("sdk_version") or ""),
            "model": str(details.get("model") or details.get("platform") or ""),
            "current_users": users,
            "current_logs": logs,
            "user_capacity": user_cap,
            "log_capacity": log_cap,
            "device_time": str(details.get("device_time") or ""),
            "battery_level": details.get("battery_level"),
            "cpu_temperature": details.get("cpu_temperature"),
            "memory_usage_percent": memory,
            "storage_usage_percent": storage,
            "restart_count": int(details.get("restart_count") or 0),
            "capacity": details.get("capacity") or {},
        }
