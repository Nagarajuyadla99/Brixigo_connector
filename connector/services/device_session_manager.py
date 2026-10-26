"""Single owner of ZK/eSSL sockets for a device.

All heartbeat, poll, health, diagnostics, and UI tests must go through this
manager so the eSSL K90 (one SDK session) is never opened concurrently.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, TypeVar

from adapters.base import IBiometricAdapter, parse_comm_password
from adapters.protocol.zk_protocol import ZKClient
from app.constants import POST_TCP_CLOSE_DELAY_SECONDS
from adapters.registry import create_adapter
from models import Device
from services.connection_state import (
    classify_protocol_error,
    communication_error_message,
    is_tcp_connect_failure,
    tcp_probe,
)
from services.retry_policy import next_backoff_seconds

logger = logging.getLogger("brixigo.connection")

T = TypeVar("T")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


@dataclass
class ProtocolDiagnostic:
    tcp_ok: bool = False
    tcp_latency_ms: float = 0.0
    tcp_message: str = ""
    protocol_ok: bool = False
    protocol_message: str = ""
    protocol_code: str = ""
    failure_stage: str = ""
    auth_status: str = "not_completed"
    auth_completed: bool = False
    comm_key_is_zero: bool = True
    session_id: int = 0
    latency_ms: float = 0.0
    device_time: str = ""
    clock_corrected: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    last_command: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "tcp_ok": self.tcp_ok,
            "tcp_latency_ms": round(self.tcp_latency_ms, 2),
            "tcp_message": self.tcp_message,
            "protocol_ok": self.protocol_ok,
            "protocol_message": self.protocol_message,
            "protocol_code": self.protocol_code,
            "failure_stage": self.failure_stage,
            "auth_status": self.auth_status,
            "auth_completed": self.auth_completed,
            "comm_key_is_zero": self.comm_key_is_zero,
            "session_id": self.session_id,
            "latency_ms": round(self.latency_ms, 2),
            "device_time": self.device_time,
            "clock_corrected": self.clock_corrected,
            "info": self.info,
            "last_command": self.last_command,
        }


class DeviceSessionManager:
    """
    Serialized device session owner.

                    DeviceSessionManager
                            |
             +--------------+--------------+
             |              |              |
         Heartbeat        Poll          Manual Test
             |              |              |
             +--------------+--------------+
                            |
                            v
                     device IP:port
    """

    def __init__(
        self,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        protocol_retries: int = 4,
    ) -> None:
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.protocol_retries = max(1, int(protocol_retries))
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def _lock_for(self, device_id: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(device_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[device_id] = lock
            return lock

    def execute(
        self,
        device: Device,
        operation: Callable[[IBiometricAdapter], T],
        *,
        retries: int | None = None,
        owner: str = "execute",
    ) -> T:
        """Run an adapter operation with exclusive access and protocol retry/backoff."""
        attempts = self.protocol_retries if retries is None else max(1, int(retries))
        last_exc: Optional[Exception] = None
        lock = self._lock_for(device.id)
        tcp_budget = min(attempts, 5)
        for attempt in range(1, attempts + 1):
            with lock:
                logger.info(
                    "[%s] SESSION_EXECUTE owner=%s device=%s attempt=%s/%s",
                    _ts(),
                    owner,
                    device.ip_address,
                    attempt,
                    attempts,
                    extra={"device_id": device.id},
                )
                # Escalate connect timeout on TCP retries: 4 → 8 → full budget.
                if attempt == 1:
                    connect_timeout = min(self.connect_timeout, 4.0)
                elif attempt == 2:
                    connect_timeout = min(self.connect_timeout, 8.0)
                else:
                    connect_timeout = self.connect_timeout
                adapter = create_adapter(
                    device,
                    connect_timeout=connect_timeout,
                    read_timeout=self.read_timeout,
                )
                try:
                    with adapter:
                        return operation(adapter)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "[%s] SESSION_EXECUTE_FAIL owner=%s attempt=%s/%s error=%s",
                        _ts(),
                        owner,
                        attempt,
                        attempts,
                        exc,
                        extra={"device_id": device.id},
                    )
                    if is_tcp_connect_failure(exc):
                        from services.connection_state import is_hard_lan_unreachable

                        # WinError 10065/10051: no path — more SYNs only worsen ARP.
                        if is_hard_lan_unreachable(exc):
                            break
                        if attempt < tcp_budget:
                            # K90 still releasing the previous SDK session, or
                            # Windows ARP is incomplete after overnight sleep.
                            from adapters.protocol.zk_transport import (
                                refresh_neighbor_arp,
                                wake_neighbor,
                            )

                            if attempt == 1:
                                refresh_neighbor_arp(device.ip_address)
                            else:
                                wake_neighbor(device.ip_address)
                            time.sleep(max(POST_TCP_CLOSE_DELAY_SECONDS, 0.8) * attempt)
                            continue
                        break
            if attempt < attempts and last_exc is not None and not is_tcp_connect_failure(last_exc):
                delay = next_backoff_seconds(attempt, jitter=False)
                # User-specified protocol backoff: 2, 5, 10, 20
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def heartbeat(self, device: Device) -> ProtocolDiagnostic:
        """Protocol lifecycle heartbeat. Yields immediately if a poll owns the K90."""
        lock = self._lock_for(device.id)
        if not lock.acquire(blocking=False):
            from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS

            diag = ProtocolDiagnostic(comm_key_is_zero=parse_comm_password(device.password) == 0)
            live = DEVICE_PORT_LOCKS.session_established(
                device.ip_address, int(device.port or 4370)
            )
            diag.tcp_ok = live
            diag.protocol_ok = live
            diag.tcp_message = "session-in-use" if live else "connecting"
            diag.protocol_message = diag.tcp_message
            diag.protocol_code = "OK" if live else ""
            return diag
        try:
            return self._heartbeat_locked(device)
        finally:
            lock.release()

    def _heartbeat_locked(self, device: Device) -> ProtocolDiagnostic:
        diag = ProtocolDiagnostic(comm_key_is_zero=parse_comm_password(device.password) == 0)
        client = self._client_for(device)
        try:
            online, latency, message = client.health_check()
            snap = client.diagnostics_snapshot()
            diag.latency_ms = latency
            diag.auth_status = str(snap.get("auth_status") or "")
            diag.auth_completed = diag.auth_status in ("success", "not_required")
            diag.session_id = int(snap.get("session_id") or 0)
            diag.failure_stage = str(snap.get("failure_stage") or "")
            diag.last_command = str(snap.get("last_command_name") or "")
            diag.comm_key_is_zero = bool(snap.get("comm_key_is_zero", diag.comm_key_is_zero))
            if online:
                try:
                    diag.device_time = client.get_time().isoformat(sep=" ")
                except Exception:
                    pass
                diag.clock_corrected = False
                diag.protocol_ok = True
                diag.protocol_message = message
                diag.tcp_ok = True
                diag.tcp_latency_ms = latency
                diag.tcp_message = "open"
                diag.protocol_code = "OK"
                return diag
            diag.protocol_ok = False
            diag.protocol_message = message
            diag.protocol_code = classify_protocol_error(RuntimeError(message), tcp_ok=None)
        except Exception as exc:
            snap = client.diagnostics_snapshot()
            diag.protocol_ok = False
            diag.protocol_message = str(exc)
            diag.failure_stage = str(snap.get("failure_stage") or "")
            diag.last_command = str(snap.get("last_command_name") or "")
            diag.auth_status = str(snap.get("auth_status") or "not_completed")
            diag.protocol_code = classify_protocol_error(exc)
        finally:
            client.disconnect()

        if is_tcp_connect_failure(diag.protocol_message, failure_stage=diag.failure_stage):
            diag.tcp_ok = False
            diag.tcp_message = diag.protocol_message
            diag.protocol_code = classify_protocol_error(
                RuntimeError(diag.protocol_message or "unreachable"), tcp_ok=False
            )
            return diag

        tcp_ok, tcp_ms, tcp_msg = tcp_probe(
            device.ip_address, int(device.port or 4370), timeout=min(self.connect_timeout, 3.0)
        )
        diag.tcp_ok = tcp_ok
        diag.tcp_latency_ms = tcp_ms
        diag.tcp_message = tcp_msg
        if tcp_ok and not diag.protocol_ok:
            diag.protocol_code = classify_protocol_error(
                RuntimeError(diag.protocol_message or "protocol failed"), tcp_ok=True
            )
        elif not tcp_ok:
            diag.protocol_code = classify_protocol_error(
                RuntimeError(tcp_msg or "unreachable"), tcp_ok=False
            )
        return diag

    def test_protocol(self, device: Device) -> ProtocolDiagnostic:
        """
        Smallest safe session:

            TCP connect → CMD_CONNECT → AUTH if required → CMD_GET_TIME → CMD_EXIT
        """
        lock = self._lock_for(device.id)
        with lock:
            return self._test_protocol_locked(device)

    def _test_protocol_locked(self, device: Device) -> ProtocolDiagnostic:
        diag = ProtocolDiagnostic(comm_key_is_zero=parse_comm_password(device.password) == 0)
        started = time.perf_counter()
        client = self._client_for(device)
        try:
            online, latency, message = client.health_check()
            snap = client.diagnostics_snapshot()
            diag.latency_ms = latency
            diag.auth_status = str(snap.get("auth_status") or "not_completed")
            diag.auth_completed = diag.auth_status in ("success", "not_required")
            diag.session_id = int(snap.get("session_id") or 0)
            diag.failure_stage = str(snap.get("failure_stage") or "")
            diag.last_command = str(snap.get("last_command_name") or "")
            diag.comm_key_is_zero = bool(snap.get("comm_key_is_zero", diag.comm_key_is_zero))
            diag.protocol_message = message
            if online:
                diag.protocol_ok = True
                diag.tcp_ok = True
                diag.tcp_latency_ms = latency
                diag.tcp_message = "open"
                diag.protocol_code = "OK"
                try:
                    diag.device_time = client.get_time().isoformat(sep=" ")
                except Exception as exc:
                    diag.device_time = str(exc)
                try:
                    diag.info = {
                        "firmware": client.get_firmware_version(),
                        "serial_number": client.get_serial_number(),
                        "platform": client.get_platform(),
                    }
                except Exception as exc:
                    diag.info = {"info_error": str(exc)}
            else:
                diag.protocol_ok = False
                diag.protocol_code = classify_protocol_error(
                    RuntimeError(message), tcp_ok=None
                )
                if not diag.auth_completed:
                    diag.auth_status = "not_completed"
        except Exception as exc:
            snap = client.diagnostics_snapshot()
            diag.protocol_ok = False
            diag.protocol_message = str(exc)
            diag.failure_stage = str(snap.get("failure_stage") or "")
            diag.last_command = str(snap.get("last_command_name") or "")
            diag.auth_status = str(snap.get("auth_status") or "not_completed")
            diag.protocol_code = classify_protocol_error(exc)
        finally:
            client.disconnect()

        if not diag.protocol_ok:
            if is_tcp_connect_failure(diag.protocol_message, failure_stage=diag.failure_stage):
                diag.tcp_ok = False
                diag.tcp_message = diag.protocol_message
                diag.protocol_code = classify_protocol_error(
                    RuntimeError(diag.protocol_message or "unreachable"), tcp_ok=False
                )
            else:
                tcp_ok, tcp_ms, tcp_msg = tcp_probe(
                    device.ip_address,
                    int(device.port or 4370),
                    timeout=min(self.connect_timeout, 3.0),
                )
                diag.tcp_ok = tcp_ok
                diag.tcp_latency_ms = tcp_ms
                diag.tcp_message = tcp_msg
                diag.protocol_code = classify_protocol_error(
                    RuntimeError(diag.protocol_message or tcp_msg), tcp_ok=tcp_ok
                )
        if not diag.latency_ms:
            diag.latency_ms = (time.perf_counter() - started) * 1000
        return diag

    def get_device_info(self, device: Device) -> dict[str, Any]:
        def _op(adapter: IBiometricAdapter) -> dict[str, Any]:
            info = adapter.device_info()
            return info.__dict__ if hasattr(info, "__dict__") else dict(info)

        return self.execute(device, _op, retries=2, owner="get_device_info")

    def get_attendance(self, device: Device, since=None) -> list:
        def _op(adapter: IBiometricAdapter):
            return adapter.download_logs(since=since)

        return self.execute(device, _op, retries=3, owner="get_attendance")

    def operator_message(self, device: Device, diag: ProtocolDiagnostic) -> str:
        if diag.protocol_ok:
            return f"Device online. Protocol session established ({diag.latency_ms:.0f} ms)."
        if diag.tcp_ok:
            return communication_error_message(
                device.ip_address, int(device.port or 4370), error=diag.protocol_message
            )
        return (
            f"Device network unreachable at {device.ip_address}:{device.port}.\n"
            f"{diag.tcp_message or diag.protocol_message}"
        )

    def _client_for(self, device: Device) -> ZKClient:
        # Use the full configured connect timeout — short caps caused false
        # "TCP connect failed" on sleepy K90 / multi-NIC Windows setups.
        return ZKClient(
            ip=device.ip_address,
            port=int(device.port or 4370),
            password=parse_comm_password(device.password),
            timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            force_udp=device.communication_type == "udp",
            manufacturer=device.manufacturer or "",
            model=device.model or device.name or "",
            max_retries=2,
        )
