"""Biometric device adapter interface and shared helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from models import AttendanceLog, Device, DeviceInfo, DeviceUser, HealthResult


class IBiometricAdapter(ABC):
    """Vendor-agnostic contract. New manufacturers implement this interface."""

    manufacturer: str = "generic"

    def __init__(self, device: Device, *, connect_timeout: float = 8.0, read_timeout: float = 30.0):
        self.device = device
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def download_logs(self, since: Optional[datetime] = None) -> list[AttendanceLog]:
        ...

    @abstractmethod
    def download_users(self) -> list[DeviceUser]:
        ...

    @abstractmethod
    def device_info(self) -> DeviceInfo:
        ...

    @abstractmethod
    def health(self) -> HealthResult:
        ...

    @abstractmethod
    def sync_time(self, when: Optional[datetime] = None) -> None:
        ...

    def enable_device(self) -> None:
        """Re-enable keypad/terminal after maintenance operations."""
        raise NotImplementedError(f"{self.manufacturer} adapter does not support enable_device")

    def disable_device(self) -> None:
        """Temporarily disable terminal input during bulk operations."""
        raise NotImplementedError(f"{self.manufacturer} adapter does not support disable_device")

    def clear_attendance(self) -> None:
        """Clear attendance logs stored on the device."""
        raise NotImplementedError(f"{self.manufacturer} adapter does not support clear_attendance")

    def restart_device(self) -> None:
        """Request a device reboot."""
        raise NotImplementedError(f"{self.manufacturer} adapter does not support restart_device")

    def set_user(self, user: DeviceUser) -> None:
        """Create or update a user on the device."""
        raise NotImplementedError(f"{self.manufacturer} adapter does not support set_user")

    def delete_user(self, user_id: str, *, uid: int | None = None) -> None:
        """Delete a user from the device."""
        raise NotImplementedError(f"{self.manufacturer} adapter does not support delete_user")

    def download_templates(self) -> list[dict[str, Any]]:
        """Download biometric templates from the device."""
        raise NotImplementedError(
            f"{self.manufacturer} adapter does not support download_templates"
        )

    def upload_template(self, template: dict[str, Any]) -> None:
        """Upload a vendor-specific biometric template to the device."""
        raise NotImplementedError(
            f"{self.manufacturer} adapter does not support upload_template"
        )

    def delete_template(self, *, uid: int, finger_index: int = 0) -> None:
        """Delete a biometric template from the device."""
        raise NotImplementedError(
            f"{self.manufacturer} adapter does not support delete_template"
        )

    def get_device_config(self) -> dict[str, Any]:
        """Return device configuration parameters."""
        return {}

    def get_device_time(self) -> Optional[datetime]:
        """Return device clock when supported."""
        return None

    def attendance_log_count(self) -> int | None:
        """Device attendance buffer size, or None when the vendor cannot report it."""
        return None

    def apply_firmware(self, package_path: str) -> dict[str, Any]:
        """
        Apply a firmware package. Vendor support varies; adapters that cannot
        stream firmware still validate the package and document the requirement.
        """
        raise NotImplementedError(
            f"{self.manufacturer} adapter does not support apply_firmware"
        )

    def collect_health_metrics(self) -> dict[str, Any]:
        """
        Collect extended health metrics for enterprise monitoring.
        Default implementation uses health() + device_info().
        """
        result = self.health()
        metrics: dict[str, Any] = {
            "online": result.online,
            "ping_ms": result.latency_ms,
            "message": result.message,
        }
        if result.online:
            try:
                with self:
                    info = self.device_info()
                    metrics.update(
                        {
                            "firmware_version": info.firmware,
                            "serial_number": info.serial_number,
                            "device_name": info.device_name,
                            "mac_address": info.mac_address,
                            "sdk_version": info.sdk_version,
                            "model": info.model or info.platform,
                            "current_users": info.user_count,
                            "current_logs": info.attendance_count,
                            "device_time": str((info.raw or {}).get("device_time") or ""),
                            "user_capacity": int((info.raw or {}).get("user_capacity") or 0),
                            "log_capacity": int((info.raw or {}).get("log_capacity") or 0),
                            "battery_level": (info.raw or {}).get("battery_level"),
                            "cpu_temperature": (info.raw or {}).get("cpu_temperature"),
                            "memory_usage_percent": float(
                                (info.raw or {}).get("memory_usage_percent") or 0
                            ),
                            "storage_usage_percent": float(
                                (info.raw or {}).get("storage_usage_percent") or 0
                            ),
                            "restart_count": int((info.raw or {}).get("restart_count") or 0),
                        }
                    )
            except Exception as exc:
                metrics["message"] = f"{result.message}; metrics partial: {exc}"
        return metrics

    def __enter__(self) -> "IBiometricAdapter":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()


def parse_comm_password(password: str) -> int:
    """ZK/eSSL devices use numeric communication keys."""
    if not password:
        return 0
    try:
        return int(str(password).strip())
    except ValueError:
        # Non-numeric secrets still hash to a stable 32-bit key space
        return abs(hash(password)) % (2**31)
