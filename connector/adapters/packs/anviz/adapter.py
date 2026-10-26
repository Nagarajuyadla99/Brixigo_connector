"""Anviz biometric adapter scaffold — protocol/SDK wiring pending."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from adapters.base import IBiometricAdapter
from models import AttendanceLog, Device, DeviceInfo, DeviceUser, HealthResult


class AnvizAdapter(IBiometricAdapter):
    """
    Scaffold for Anviz TCP/SDK devices.
    Status: scaffold — register devices but do not expect live punches yet.
    Typical ports: 5010 (CrossChex) / vendor SDK.
    """

    manufacturer = "anviz"
    status = "scaffold"
    default_port = 5010

    def __init__(self, device: Device, *, connect_timeout: float = 8.0, read_timeout: float = 30.0):
        super().__init__(device, connect_timeout=connect_timeout, read_timeout=read_timeout)
        self._connected = False

    def connect(self) -> None:
        # Scaffold: TCP reachability probe only — no Anviz frame protocol yet.
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)
        try:
            sock.connect((self.device.ip_address, int(self.device.port or self.default_port)))
            self._connected = True
        finally:
            sock.close()

    def disconnect(self) -> None:
        self._connected = False

    def _nyi(self, op: str) -> None:
        raise NotImplementedError(
            f"Anviz {op} is not implemented yet (scaffold). "
            "Wire Anviz SDK / CrossChex protocol next."
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
            model=self.device.model or "anviz-scaffold",
            raw={"status": self.status, "message": "Anviz protocol not wired"},
        )

    def health(self) -> HealthResult:
        try:
            self.connect()
            return HealthResult(
                online=True,
                message="TCP reachable — Anviz protocol scaffold (no attendance yet)",
                latency_ms=0,
                details={"status": self.status},
            )
        except Exception as exc:
            return HealthResult(online=False, message=str(exc), details={"status": self.status})

    def sync_time(self, when: Optional[datetime] = None) -> None:
        self._nyi("sync_time")
