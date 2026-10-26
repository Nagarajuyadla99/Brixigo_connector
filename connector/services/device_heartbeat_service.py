"""Lightweight biometric device heartbeat — independent of Brixigo API health."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from app.constants import (
    DEFAULT_DEVICE_HEARTBEAT_INTERVAL_SECONDS,
    DEVICE_FAILURE_COALESCE_SECONDS,
    DEVICE_HARD_LAN_BACKOFF_SECONDS,
    DEVICE_HEARTBEAT_DISCONNECT_AFTER,
    DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER,
    DEVICE_OFFLINE_RECONNECT_POLL_SECONDS,
    DEVICE_TCP_PROBE_TIMEOUT,
    DEVICE_TCP_RECONNECT_BURST_ATTEMPTS,
    DEVICE_TCP_RECONNECT_BURST_GAP_SECONDS,
)
from models import Device
from services.connection_state import (
    ConnectionStateRegistry,
    DeviceNetworkStatus,
    is_hard_lan_unreachable,
    is_inconclusive_tcp_probe,
    is_soft_syn_timeout,
    persist_device_status,
    persist_health_status,
    tcp_probe,
)
from services.device_session_manager import DeviceSessionManager, ProtocolDiagnostic
from adapters.protocol.zk_transport import wake_neighbor
from app.constants import POST_TCP_CLOSE_DELAY_SECONDS
from storage.repositories.device_repo import DeviceRepository

logger = logging.getLogger("brixigo.heartbeat")


def _heartbeat_clock_is_modern(device_time: object) -> bool:
    """True when GET_TIME already reports a post-2010 calendar."""
    text = str(device_time or "").strip()
    if len(text) < 4 or not text[:4].isdigit():
        return False
    return int(text[:4]) >= 2010


class DeviceHeartbeatService:
    """
    Every 10–15 seconds, run a protocol heartbeat (or TCP-only during backoff).

    Real TCP unreachable (not lock contention) → DEGRADED then DISCONNECTED.
    Protocol failure with TCP open → DEGRADED (never OFFLINE).

    All device sockets go through DeviceSessionManager.
    """

    def __init__(
        self,
        devices: DeviceRepository,
        states: ConnectionStateRegistry,
        *,
        list_devices: Callable[[], list[Device]],
        interval_seconds: int = DEFAULT_DEVICE_HEARTBEAT_INTERVAL_SECONDS,
        probe_timeout: float = DEVICE_TCP_PROBE_TIMEOUT,
        on_disconnected: Optional[Callable[[Device], None]] = None,
        on_connected: Optional[Callable[[Device], None]] = None,
        sessions: Optional[DeviceSessionManager] = None,
        relocator: Optional[Callable] = None,
    ) -> None:
        self._devices = devices
        self._states = states
        self._list_devices = list_devices
        self.interval_seconds = max(10, min(15, int(interval_seconds)))
        # Use the recovery probe budget (default 6s), never the 2s fast pre-check.
        self.probe_timeout = max(3.0, float(probe_timeout or DEVICE_TCP_PROBE_TIMEOUT))
        self.on_disconnected = on_disconnected
        self.on_connected = on_connected
        self._sessions = sessions
        self._relocator = relocator
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._prev_status: dict[str, str] = {}
        self._next_probe_at: dict[str, float] = {}
        self._burst_left: dict[str, int] = {}

    def set_relocator(self, relocator: Optional[Callable]) -> None:
        self._relocator = relocator

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-device-heartbeat", daemon=True
        )
        self._thread.start()
        logger.info("Device heartbeat started interval=%ss", self.interval_seconds)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def probe_device(self, device: Device) -> dict:
        skip_protocol = False
        existing = self._states.device(device.id)
        now_mono = time.monotonic()
        due = self._next_probe_at.get(device.id, 0.0)
        already_down = bool(
            existing
            and existing.consecutive_failures >= DEVICE_HEARTBEAT_DISCONNECT_AFTER
            and existing.network_status == DeviceNetworkStatus.OFFLINE
        )
        soft_down = bool(
            existing
            and existing.consecutive_failures >= DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER
            and existing.status.value == "disconnected"
        )
        burst_left = int(self._burst_left.get(device.id, 0) or 0)
        if existing is not None and (already_down or soft_down) and due > now_mono and burst_left <= 0:
            # Device is offline after burst failed; do not SYN-flood forever.
            return {
                "device_id": device.id,
                "ok": False,
                "network_ok": False,
                "protocol_ok": False,
                "status": existing.status.value,
                "network_status": existing.network_status.value,
                "protocol_status": existing.protocol_status.value,
                "latency_ms": existing.last_latency_ms,
                "consecutive_failures": existing.consecutive_failures,
                "deferred": True,
            }
        tcp_precheck: tuple[bool, float, str] | None = None
        in_backoff = bool(
            existing
            and existing.next_protocol_retry_at
            and time.time() < existing.next_protocol_retry_at
        )
        tcp_was_down = bool(
            existing and existing.network_status == DeviceNetworkStatus.OFFLINE
        )
        never_seen = existing is None or existing.network_status == DeviceNetworkStatus.UNKNOWN
        # Unreachable host: cheap TCP only. Full protocol (8s connect) just
        # stacks timeouts on poll/health and delays recovery.
        if in_backoff or tcp_was_down or never_seen or burst_left > 0:
            tcp_precheck = tcp_probe(
                device.ip_address, int(device.port or 4370), timeout=self.probe_timeout
            )
            tcp_ok = tcp_precheck[0]
            if is_inconclusive_tcp_probe(tcp_precheck[2]):
                # Another thread owns the port — do not count as offline.
                return {
                    "device_id": device.id,
                    "ok": False,
                    "network_ok": bool(
                        existing and existing.network_status == DeviceNetworkStatus.CONNECTED
                    ),
                    "protocol_ok": False,
                    "status": existing.status.value if existing else "unknown",
                    "network_status": (
                        existing.network_status.value if existing else "unknown"
                    ),
                    "protocol_status": (
                        existing.protocol_status.value if existing else "unknown"
                    ),
                    "latency_ms": existing.last_latency_ms if existing else 0.0,
                    "consecutive_failures": existing.consecutive_failures if existing else 0,
                    "inconclusive": True,
                }
            if not tcp_ok:
                skip_protocol = True
            elif in_backoff and not tcp_was_down and burst_left <= 0:
                skip_protocol = True
            else:
                skip_protocol = False
                # Probe just opened+closed TCP 4370. Wait before the protocol
                # session or the K90 times out the second SYN.
                time.sleep(max(POST_TCP_CLOSE_DELAY_SECONDS, 0.25))

        diag = ProtocolDiagnostic()
        if self._sessions and not skip_protocol:
            diag = self._sessions.heartbeat(device)
        else:
            if tcp_precheck is not None:
                ok, latency_ms, message = tcp_precheck
            else:
                ok, latency_ms, message = tcp_probe(
                    device.ip_address, int(device.port or 4370), timeout=self.probe_timeout
                )
            if is_inconclusive_tcp_probe(message):
                return {
                    "device_id": device.id,
                    "ok": False,
                    "network_ok": bool(
                        existing and existing.network_status == DeviceNetworkStatus.CONNECTED
                    ),
                    "protocol_ok": False,
                    "status": existing.status.value if existing else "unknown",
                    "network_status": (
                        existing.network_status.value if existing else "unknown"
                    ),
                    "protocol_status": (
                        existing.protocol_status.value if existing else "unknown"
                    ),
                    "latency_ms": existing.last_latency_ms if existing else 0.0,
                    "consecutive_failures": existing.consecutive_failures if existing else 0,
                    "inconclusive": True,
                }
            diag.tcp_ok = ok
            diag.tcp_latency_ms = latency_ms
            diag.tcp_message = message
            if skip_protocol:
                diag.protocol_ok = False
                diag.protocol_message = (
                    (existing.last_protocol_error if existing else "") or message
                )
                diag.protocol_code = existing.protocol_code if existing else ""
                diag.failure_stage = existing.failure_stage if existing else "tcp_connect"
                diag.auth_status = existing.auth_status if existing else ""
            else:
                diag.protocol_ok = ok
                diag.protocol_message = "" if ok else message

        # Poll/time-sync owns the port — never count lock wait as LAN down.
        if is_inconclusive_tcp_probe(diag.tcp_message) or is_inconclusive_tcp_probe(
            diag.protocol_message
        ):
            return {
                "device_id": device.id,
                "ok": False,
                "network_ok": bool(
                    existing and existing.network_status == DeviceNetworkStatus.CONNECTED
                ),
                "protocol_ok": False,
                "status": existing.status.value if existing else "unknown",
                "network_status": (
                    existing.network_status.value if existing else "unknown"
                ),
                "protocol_status": (
                    existing.protocol_status.value if existing else "unknown"
                ),
                "latency_ms": existing.last_latency_ms if existing else 0.0,
                "consecutive_failures": existing.consecutive_failures if existing else 0,
                "inconclusive": True,
            }

        if (not diag.tcp_ok) and self._relocator is not None:
            try:
                attempt = self._relocator(device, False)
            except Exception:
                logger.debug("LAN relocate during heartbeat failed device=%s", device.name, exc_info=True)
                attempt = None
            moved = getattr(attempt, "relocated", None) if attempt is not None else None
            if moved is not None:
                logger.info(
                    "Heartbeat retry after relocate device=%s ip=%s",
                    moved.name,
                    moved.ip_address,
                )
                self._next_probe_at.pop(device.id, None)
                self._next_probe_at.pop(moved.id, None)
                self._burst_left[moved.id] = DEVICE_TCP_RECONNECT_BURST_ATTEMPTS
                return self.probe_device(moved)

        timeout = (not diag.tcp_ok) and (
            "timed out" in (diag.tcp_message or "").lower()
            or "timeout" in (diag.tcp_message or "").lower()
        )
        hard_lan = (not diag.tcp_ok) and is_hard_lan_unreachable(
            diag.tcp_message or diag.protocol_message
        )
        soft_timeout = (not diag.tcp_ok) and is_soft_syn_timeout(
            diag.tcp_message or diag.protocol_message, timeout=timeout
        )
        auth_error = (diag.protocol_code or "").endswith("AUTH_ERROR")
        link = self._states.record_device_heartbeat(
            device.id,
            network_ok=diag.tcp_ok,
            protocol_ok=diag.protocol_ok,
            latency_ms=diag.latency_ms or diag.tcp_latency_ms,
            error="" if diag.protocol_ok else (diag.protocol_message or diag.tcp_message),
            ip_address=device.ip_address,
            port=int(device.port or 4370),
            name=device.name,
            timeout=timeout,
            auth_error=auth_error,
            protocol_code=diag.protocol_code,
            failure_stage=diag.failure_stage,
            auth_status=diag.auth_status,
            comm_key_is_zero=diag.comm_key_is_zero,
            protocol_attempted=not skip_protocol,
        )
        persisted = persist_device_status(link)
        health = persist_health_status(link)
        self._devices.update_status(
            device.id,
            status=persisted,
            last_error="" if diag.protocol_ok else (link.last_error or ""),
        )
        self._devices.update_telemetry(
            device.id,
            health_status=health,
            last_health_at=link.last_heartbeat_at,
        )
        if diag.clock_corrected or _heartbeat_clock_is_modern(diag.device_time):
            try:
                self._devices.touch_field(device.id, "last_time_sync_at")
            except Exception:
                logger.debug(
                    "last_time_sync_at update skipped device=%s",
                    device.id,
                    extra={"device_id": device.id},
                )
        if diag.tcp_ok:
            self._next_probe_at.pop(device.id, None)
            self._burst_left.pop(device.id, None)
        else:
            # Hard 10065: wake once, then long backoff — never burst-fire ARP.
            # Soft timeout: short reconnect burst only right after a drop.
            was_up = self._prev_status.get(device.id) in {"connected", "degraded", "timeout"}
            if hard_lan:
                self._burst_left.pop(device.id, None)
                try:
                    wake_neighbor(device.ip_address, wait_ms=800)
                except Exception:
                    logger.debug("wake_neighbor skipped device=%s", device.name)
                self._next_probe_at[device.id] = (
                    time.monotonic() + float(DEVICE_HARD_LAN_BACKOFF_SECONDS)
                )
            elif soft_timeout and (was_up or burst_left > 0) and not (already_down or soft_down):
                if burst_left <= 0:
                    self._burst_left[device.id] = DEVICE_TCP_RECONNECT_BURST_ATTEMPTS
                    burst_left = DEVICE_TCP_RECONNECT_BURST_ATTEMPTS
                    try:
                        wake_neighbor(device.ip_address, wait_ms=600)
                    except Exception:
                        logger.debug("wake_neighbor skipped device=%s", device.name)
                remaining = max(0, burst_left - 1)
                self._burst_left[device.id] = remaining
                self._next_probe_at[device.id] = (
                    time.monotonic() + DEVICE_TCP_RECONNECT_BURST_GAP_SECONDS
                )
            else:
                fails = max(1, int(link.consecutive_failures or 1))
                base = float(DEVICE_OFFLINE_RECONNECT_POLL_SECONDS)
                delay = min(
                    90.0,
                    max(base, float(self.interval_seconds) * min(fails, 4)),
                )
                # Coalesce with any in-flight poll failure window.
                delay = max(delay, float(DEVICE_FAILURE_COALESCE_SECONDS))
                self._next_probe_at[device.id] = time.monotonic() + delay
        prev = self._prev_status.get(device.id)
        self._prev_status[device.id] = link.status.value
        if (
            link.status.value == "disconnected"
            and prev != "disconnected"
            and self.on_disconnected
        ):
            self.on_disconnected(device)
            # Soft drop only: one gentle reconnect burst. Hard LAN already backed off.
            if soft_timeout and not hard_lan:
                self._burst_left[device.id] = DEVICE_TCP_RECONNECT_BURST_ATTEMPTS
                self._next_probe_at[device.id] = time.monotonic() + 1.0
        if (
            diag.protocol_ok
            and prev != "connected"
            and link.status.value == "connected"
            and self.on_connected
        ):
            self.on_connected(device)
        return {
            "device_id": device.id,
            "ok": bool(diag.protocol_ok),
            "network_ok": bool(diag.tcp_ok),
            "protocol_ok": bool(diag.protocol_ok),
            "status": link.status.value,
            "network_status": link.network_status.value,
            "protocol_status": link.protocol_status.value,
            "latency_ms": link.last_latency_ms,
            "consecutive_failures": link.consecutive_failures,
        }

    def reset_reconnect_state(self) -> None:
        """Clear offline backoff so the next cycle re-probes immediately."""
        self._next_probe_at.clear()
        self._burst_left.clear()
        self._prev_status.clear()

    def probe_all(self) -> list[dict]:
        results = []
        for device in self._list_devices():
            if not device.enabled:
                continue
            try:
                results.append(self.probe_device(device))
            except Exception as exc:
                logger.warning(
                    "Device heartbeat error device=%s: %s",
                    device.name,
                    exc,
                    extra={"device_id": device.id},
                )
        return results

    def _loop(self) -> None:
        self.probe_all()
        while not self._stop.is_set():
            # Wake sooner when a reconnect burst is scheduled.
            wait_for = float(self.interval_seconds)
            if self._next_probe_at:
                soonest = min(self._next_probe_at.values()) - time.monotonic()
                if soonest < wait_for:
                    wait_for = max(0.4, soonest)
            self._stop.wait(wait_for)
            if self._stop.is_set():
                break
            try:
                self.probe_all()
            except Exception as exc:
                logger.exception("Device heartbeat cycle failed: %s", exc)
