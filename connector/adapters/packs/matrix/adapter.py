"""Matrix COSEC / SATATYA adapter scaffold — SDK/REST wiring pending."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from adapters.base import IBiometricAdapter
from models import AttendanceLog, Device, DeviceInfo, DeviceUser, HealthResult


class MatrixAdapter(IBiometricAdapter):
    """
    Scaffold for Matrix Cosec devices.
    Status: scaffold — typically HTTPS/REST or vendor SDK.
    """

    manufacturer = "matrix"
    status = "scaffold"
    default_port = 80

    def __init__(self, device: Device, *, connect_timeout: float = 8.0, read_timeout: float = 30.0):
        super().__init__(device, connect_timeout=connect_timeout, read_timeout=read_timeout)

    def connect(self) -> None:
        import socket

        port = int(self.device.port or self.default_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)
        try:
            sock.connect((self.device.ip_address, port))
        finally:
            sock.close()

    def disconnect(self) -> None:
        return None

    def _nyi(self, op: str) -> None:
        raise NotImplementedError(
            f"Matrix {op} is not implemented yet (scaffold). "
            "Wire Matrix COSEC/SATATYA SDK or REST API next."
        )

    def download_logs(self, since: Optional[datetime] = None) -> list[AttendanceLog]:
        self._nyi("download_logs")
        return []

    def download_users(self) -> list[DeviceUser]:
        self._nyi("download_users")
        return []

    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            device_name=self.device.name,
            model=self.device.model or "matrix-scaffold",
            raw={"status": self.status, "message": "Matrix API not wired"},
        )

    def health(self) -> HealthResult:
        try:
            self.connect()
            return HealthResult(
                online=True,
                message="TCP reachable — Matrix protocol scaffold (no attendance yet)",
                latency_ms=0,
                details={"status": self.status},
            )
        except Exception as exc:
            return HealthResult(online=False, message=str(exc), details={"status": self.status})

    def sync_time(self, when: Optional[datetime] = None) -> None:
        self._nyi("sync_time")
