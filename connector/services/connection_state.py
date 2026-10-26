"""Independent connection states — device, server, internet, sync, auth, connector.

A Brixigo API/TLS failure must never flip the biometric device to disconnected.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from app.constants import (
    API_ACCEPTABLE_CONSECUTIVE_THRESHOLD,
    API_FAILURE_CONSECUTIVE_THRESHOLD,
    API_LATENCY_ACCEPTABLE_MS,
    API_LATENCY_HEALTHY_MS,
    API_LATENCY_SLOW_MS,
    API_RECOVERY_CONSECUTIVE_THRESHOLD,
    API_SLOW_CONSECUTIVE_THRESHOLD,
    DEVICE_FAILURE_COALESCE_SECONDS,
    DEVICE_HEARTBEAT_DEGRADED_AFTER,
    DEVICE_HEARTBEAT_DISCONNECT_AFTER,
    DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER,
    DEVICE_TCP_PROBE_TIMEOUT,
    RETRY_BACKOFF_SCHEDULE,
)
from models import DeviceStatus, utcnow

logger = logging.getLogger("brixigo.connection")


class DeviceLinkStatus(str, Enum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    TIMEOUT = "timeout"
    AUTH_ERROR = "auth_error"
    ERROR = "error"
    UNKNOWN = "unknown"


class DeviceNetworkStatus(str, Enum):
    CONNECTED = "connected"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class DeviceProtocolStatus(str, Enum):
    CONNECTED = "connected"
    ERROR = "error"
    FAILED = "failed"
    NOT_COMPLETED = "not_completed"
    UNKNOWN = "unknown"


class DeviceProtocolCode(str, Enum):
    OK = "OK"
    DEVICE_NETWORK_UNREACHABLE = "DEVICE_NETWORK_UNREACHABLE"
    DEVICE_PORT_CLOSED = "DEVICE_PORT_CLOSED"
    DEVICE_PROTOCOL_ERROR = "DEVICE_PROTOCOL_ERROR"
    DEVICE_CONNECTION_CLOSED = "DEVICE_CONNECTION_CLOSED"
    DEVICE_AUTH_ERROR = "DEVICE_AUTH_ERROR"
    DEVICE_TIMEOUT = "DEVICE_TIMEOUT"
    DEVICE_BUSY = "DEVICE_BUSY"
    DEVICE_UNSUPPORTED_PROTOCOL = "DEVICE_UNSUPPORTED_PROTOCOL"


class ServerStatus(str, Enum):
    CONNECTED = "connected"
    ACCEPTABLE = "acceptable"
    SLOW = "slow"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    TLS_ERROR = "tls_error"
    AUTH_ERROR = "auth_error"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


class ApiAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class InternetStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class TlsStatus(str, Enum):
    HEALTHY = "healthy"
    TIMEOUT = "timeout"
    ERROR = "error"
    UNKNOWN = "unknown"


class AuthStatus(str, Enum):
    VALID = "valid"
    EXPIRED = "expired"
    REQUIRED = "required"
    UNKNOWN = "unknown"


class SyncStatus(str, Enum):
    SYNCED = "synced"
    SYNCING = "syncing"
    PENDING = "pending"
    DELAYED = "delayed"
    FAILED = "failed"
    PAUSED = "paused"
    WAITING_FOR_DEVICE = "waiting_for_device"


class ConnectorRuntimeStatus(str, Enum):
    RUNNING = "running"
    STARTING = "starting"
    STOPPED = "stopped"
    STOPPING = "stopping"


def is_tcp_connect_failure(error: object = "", *, failure_stage: str = "") -> bool:
    """True when the device never completed TCP; extra retries/probes only freeze the UI."""
    stage = str(failure_stage or "").strip().lower()
    if stage in {"tcp_connect", "connect"}:
        return True
    msg = str(error or "").lower()
    if "session-in-use" in msg:
        return False
    return "tcp connect failed" in msg


def is_hard_lan_unreachable(error: object = "") -> bool:
    """True when Windows has no path to the host (not a slow/sleepy SYN).

    A SYN timeout is recoverable: stale ARP, Wi-Fi sleep, or the K90 still
    releasing the previous SDK session. WinError 10051/10064/10065 means the
    NIC has no route or the neighbor never answered ARP.
    """
    msg = str(error or "").lower()
    if not msg:
        return False
    if any(
        token in msg
        for token in (
            "session-in-use",
            "connecting",
            "probe-in-flight",
            "timed out waiting for",
        )
    ):
        return False
    hard = (
        "10051",
        "10064",
        "10065",
        "unreachable host",
        "host is down",
        "network is unreachable",
        "network is down",
        "no route",
        "network unreachable",
        "host unreachable",
    )
    return any(token in msg for token in hard)


def tcp_probe(
    host: str,
    port: int,
    *,
    timeout: float = DEVICE_TCP_PROBE_TIMEOUT,
) -> tuple[bool, float, str]:
    """Lightweight LAN reachability check for host:port. Independent of HTTPS/API.

    Never opens a competing socket while a protocol session already owns this
    host:port — reports reachable (session-in-use) instead.

    A concurrent tcp-probe is NOT proof the device is online; that lock is
    held for the whole SYN timeout on a dead host. Callers must treat
    ``connecting`` / ``probe-in-flight`` as inconclusive via
    ``is_inconclusive_tcp_probe`` — never as a network failure.
    """
    from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS

    holder = DEVICE_PORT_LOCKS.holder(host, int(port))
    if holder and str(holder).startswith("tcp-probe:"):
        return False, 0.0, "probe-in-flight"
    if DEVICE_PORT_LOCKS.session_established(host, int(port)):
        return True, 0.0, "session-in-use"
    if holder or DEVICE_PORT_LOCKS.owned_by_current_thread(host, int(port)):
        # SYN still in flight — not proof the terminal answered.
        return False, 0.0, "connecting"
    acquired = DEVICE_PORT_LOCKS.try_acquire(
        host, int(port), owner=f"tcp-probe:{host}:{port}"
    )
    if not acquired:
        if DEVICE_PORT_LOCKS.session_established(host, int(port)):
            return True, 0.0, "session-in-use"
        return False, 0.0, "connecting"
    started = datetime.now(timezone.utc)
    from adapters.protocol.zk_transport import abortive_close, open_tcp_socket

    sock = None
    try:
        sock = open_tcp_socket(host, int(port), timeout)
        latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        return True, latency_ms, "open"
    except OSError as exc:
        latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        # Do NOT arp -d here. Probe failures are frequent; deleting the neighbor
        # every few seconds leaves Incomplete ARP and makes real poll SYNs time out.
        return False, latency_ms, str(exc)
    finally:
        if sock is not None:
            abortive_close(sock)
        DEVICE_PORT_LOCKS.release(host, int(port))


INCONCLUSIVE_TCP_PROBE_MESSAGES = frozenset({"connecting", "probe-in-flight"})


def is_inconclusive_tcp_probe(message: str | None) -> bool:
    """True when another thread owns the port — not a real LAN failure."""
    msg = str(message or "").strip().lower()
    if msg in INCONCLUSIVE_TCP_PROBE_MESSAGES:
        return True
    # ZKTransport lock wait — peer did not refuse; we never got a SYN slot.
    return "timed out waiting for" in msg


def is_soft_syn_timeout(error: object = "", *, timeout: bool = False) -> bool:
    """True for recoverable SYN timeouts — not hard WinError 10065/10051."""
    if is_hard_lan_unreachable(error):
        return False
    if is_inconclusive_tcp_probe(str(error or "")):
        return False
    if timeout:
        return True
    msg = str(error or "").lower()
    return "timed out" in msg or "timeout" in msg


def classify_protocol_error(exc: BaseException, *, tcp_ok: bool | None = None) -> str:
    """Map a device exception to a protocol code. Never treats TLS/API errors as device faults."""
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    if "authentication" in msg or "unauth" in msg or "comm key" in msg or "comm_key" in msg:
        return DeviceProtocolCode.DEVICE_AUTH_ERROR.value
    if "busy" in msg or "session-in-use" in msg or "already connected" in msg:
        return DeviceProtocolCode.DEVICE_BUSY.value
    if "unsupported" in msg or "unknown protocol" in msg:
        return DeviceProtocolCode.DEVICE_UNSUPPORTED_PROTOCOL.value
    if "connection closed" in msg or "closed by device" in msg:
        return DeviceProtocolCode.DEVICE_CONNECTION_CLOSED.value
    if "timed out" in msg or "timeout" in msg or "timeout" in name:
        if tcp_ok is False:
            return DeviceProtocolCode.DEVICE_NETWORK_UNREACHABLE.value
        return DeviceProtocolCode.DEVICE_TIMEOUT.value
    if tcp_ok is False:
        if "refused" in msg or "actively refused" in msg:
            return DeviceProtocolCode.DEVICE_PORT_CLOSED.value
        return DeviceProtocolCode.DEVICE_NETWORK_UNREACHABLE.value
    return DeviceProtocolCode.DEVICE_PROTOCOL_ERROR.value


def communication_error_message(ip: str, port: int, *, error: str = "") -> str:
    detail = (error or "Connection closed by device").strip()
    return (
        "🟠 Device Communication Error\n\n"
        "The biometric device is reachable at:\n\n"
        f"{ip}:{port}\n\n"
        "but the device closed the communication session.\n\n"
        f"Protocol error: {detail}\n\n"
        "Possible causes:\n"
        "• Incorrect device protocol\n"
        "• Incorrect device password\n"
        "• Unsupported command\n"
        "• Device communication mode\n"
        "• Another application already connected\n"
        "• Device connection limit reached\n"
        "• Device firmware/protocol incompatibility\n\n"
        "The connector will retry automatically."
    )


def classify_transport_error(exc: BaseException) -> str:
    """Map an exception to tls | timeout | network | auth | server | client."""
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    if "handshake" in msg or "ssl" in msg or "tls" in msg or "certificate" in msg:
        return "tls"
    if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
        return "auth"
    if "timeout" in msg or "timed out" in msg or "timeout" in name:
        return "timeout"
    if "connect" in name or "network" in msg or "name or service" in msg:
        return "network"
    if "500" in msg or "502" in msg or "503" in msg or "504" in msg:
        return "server"
    return "network"


def format_latency_ms(ms: float) -> str:
    value = float(ms or 0.0)
    if value <= 0:
        return "—"
    if value < 1000:
        return f"{value:.0f} ms"
    return f"{value / 1000.0:.2f} sec"


def _clock(iso_ts: Optional[str]) -> str:
    if not iso_ts:
        return "never"
    try:
        when = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone().strftime("%H:%M:%S")
    except ValueError:
        return iso_ts


def classify_api_sample(
    kind: str,
    latency_ms: float = 0.0,
    status_code: int | None = None,
) -> str:
    """Classify one probe into healthy|acceptable|slow|timeout|tls|auth|server|unavailable.

    Latency is classified independently of availability. HTTP 200 at 3.5s is
    still an available API.
    """
    token = (kind or "").strip().lower()
    if status_code in (401, 403) or token == "auth":
        return "auth"
    if token == "tls":
        return "tls"
    if token in {"network", "unavailable"}:
        return "unavailable"
    if token == "server" or (status_code is not None and status_code >= 500):
        return "server"
    if token == "timeout":
        return "timeout"
    ms = float(latency_ms or 0.0)
    if ms >= API_LATENCY_SLOW_MS:
        return "slow"
    if ms >= API_LATENCY_ACCEPTABLE_MS:
        return "slow"
    if ms >= API_LATENCY_HEALTHY_MS:
        return "acceptable"
    return "healthy"


def public_cloud_status(server_status: str) -> str:
    """Dashboard Cloud badge. Never maps latency to a generic DEGRADED label."""
    mapping = {
        ServerStatus.CONNECTED.value: "connected",
        ServerStatus.ACCEPTABLE.value: "slightly slow",
        ServerStatus.SLOW.value: "slow",
        ServerStatus.TIMEOUT.value: "timeout",
        ServerStatus.UNAVAILABLE.value: "unavailable",
        ServerStatus.TLS_ERROR.value: "tls_error",
        ServerStatus.AUTH_ERROR.value: "auth_error",
        ServerStatus.SERVER_ERROR.value: "unavailable",
        ServerStatus.UNKNOWN.value: "unknown",
        # Leftover internal labels from older builds / health monitors.
        "degraded": "slow",
        "healthy": "connected",
        "ok": "connected",
    }
    return mapping.get((server_status or "").lower(), server_status or "unknown")


def _ago(iso_ts: Optional[str]) -> str:
    if not iso_ts:
        return "never"
    try:
        when = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = max(0, int((utcnow() - when).total_seconds()))
    except ValueError:
        return iso_ts
    if seconds < 60:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} hr ago"


def _heartbeat_age_seconds(iso_ts: Optional[str]) -> float | None:
    if not iso_ts:
        return None
    try:
        when = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (utcnow() - when).total_seconds())
    except ValueError:
        return None


LIVE_RETRY_WINDOW_SECONDS = 90.0


def is_live_retry(link: "DeviceLinkState | dict | None") -> bool:
    """True while the engine is still probing LAN and the last heartbeat is fresh."""
    if link is None:
        return False
    if isinstance(link, dict):
        network = str(link.get("network_status") or "").lower()
        status = str(link.get("status") or "").lower()
        hb = link.get("last_heartbeat_at")
    else:
        network = (
            link.network_status.value
            if hasattr(link.network_status, "value")
            else str(link.network_status or "")
        )
        status = (
            link.status.value if hasattr(link.status, "value") else str(link.status or "")
        )
        hb = link.last_heartbeat_at
    if network == DeviceNetworkStatus.CONNECTED.value:
        return False
    if status == DeviceLinkStatus.CONNECTED.value:
        return False
    age = _heartbeat_age_seconds(hb)
    return age is not None and age <= LIVE_RETRY_WINDOW_SECONDS


def live_display_fields(link: "DeviceLinkState | dict | None") -> dict[str, Any]:
    """Operator-facing labels. A live TCP retry is connecting, not frozen offline."""
    if link is None:
        return {
            "display_status": "unknown",
            "display_network": "unknown",
            "display_protocol": "unknown",
            "live_retry": False,
        }
    if isinstance(link, dict):
        status = str(link.get("status") or "unknown")
        network = str(link.get("network_status") or "unknown")
        protocol = str(link.get("protocol_status") or "unknown")
    else:
        status = link.status.value
        network = link.network_status.value
        protocol = link.protocol_status.value
    if is_live_retry(link):
        return {
            "display_status": "connecting",
            "display_network": "probing",
            "display_protocol": "waiting",
            "live_retry": True,
        }
    return {
        "display_status": status,
        "display_network": network,
        "display_protocol": protocol,
        "live_retry": False,
    }


def live_heartbeat_connected(link: "DeviceLinkState | dict | None") -> bool:
    """True when the independent heartbeat already proved TCP+protocol this cycle."""
    if link is None:
        return False
    if isinstance(link, dict):
        status = str(link.get("status") or "").lower()
        network = str(link.get("network_status") or "").lower()
        protocol = str(link.get("protocol_status") or "").lower()
    else:
        status = (
            link.status.value if hasattr(link.status, "value") else str(link.status or "")
        ).lower()
        network = (
            link.network_status.value
            if hasattr(link.network_status, "value")
            else str(link.network_status or "")
        ).lower()
        protocol = (
            link.protocol_status.value
            if hasattr(link.protocol_status, "value")
            else str(link.protocol_status or "")
        ).lower()
    return (
        status == DeviceLinkStatus.CONNECTED.value
        and network == DeviceNetworkStatus.CONNECTED.value
        and protocol == DeviceProtocolStatus.CONNECTED.value
    )


def operator_device_badge(link: "DeviceLinkState | dict | None", *, fallback: str = "unknown") -> str:
    """Devices-tab / dashboard badge. Live heartbeat wins over a stale DB status."""
    if link is None:
        return (fallback or "unknown").lower()
    display = live_display_fields(link).get("display_status") or ""
    token = str(display).lower()
    mapping = {
        "connected": DeviceStatus.ONLINE.value,
        "connecting": "connecting",
        "probing": "connecting",
        "degraded": DeviceStatus.DEGRADED.value,
        "timeout": DeviceStatus.DEGRADED.value,
        "disconnected": DeviceStatus.OFFLINE.value,
        "auth_error": DeviceStatus.AUTH_ERROR.value,
        "error": DeviceStatus.ERROR.value,
        "online": DeviceStatus.ONLINE.value,
        "offline": DeviceStatus.OFFLINE.value,
        "unknown": (fallback or "unknown").lower(),
    }
    return mapping.get(token, (fallback or "unknown").lower())


def persist_device_status(link: "DeviceLinkState") -> str:
    """Map independent device-link status to the persisted Device.status column."""
    if link.network_status == DeviceNetworkStatus.CONNECTED and link.protocol_status in (
        DeviceProtocolStatus.ERROR,
        DeviceProtocolStatus.FAILED,
    ):
        return DeviceStatus.DEGRADED.value
    if link.status == DeviceLinkStatus.CONNECTED:
        return DeviceStatus.ONLINE.value
    if link.status == DeviceLinkStatus.CONNECTING:
        return DeviceStatus.UNKNOWN.value
    if link.status in (DeviceLinkStatus.DEGRADED, DeviceLinkStatus.TIMEOUT):
        if link.network_status == DeviceNetworkStatus.CONNECTED:
            return DeviceStatus.DEGRADED.value
        if link.consecutive_failures < DEVICE_HEARTBEAT_DISCONNECT_AFTER:
            return DeviceStatus.DEGRADED.value
    if link.status == DeviceLinkStatus.TIMEOUT and link.consecutive_failures < DEVICE_HEARTBEAT_DISCONNECT_AFTER:
        return DeviceStatus.DEGRADED.value
    if link.status == DeviceLinkStatus.AUTH_ERROR:
        return DeviceStatus.AUTH_ERROR.value
    if link.status == DeviceLinkStatus.ERROR:
        return DeviceStatus.ERROR.value
    if link.status == DeviceLinkStatus.UNKNOWN:
        return DeviceStatus.UNKNOWN.value
    # Live heartbeat is still probing — do not freeze the UI on "offline".
    age = _heartbeat_age_seconds(link.last_heartbeat_at)
    if age is not None and age <= LIVE_RETRY_WINDOW_SECONDS:
        return DeviceStatus.TIMEOUT.value
    return DeviceStatus.OFFLINE.value


def persist_health_status(link: "DeviceLinkState") -> str:
    if link.status == DeviceLinkStatus.CONNECTED and link.protocol_status == DeviceProtocolStatus.CONNECTED:
        return "healthy"
    if link.network_status == DeviceNetworkStatus.CONNECTED and link.protocol_status in (
        DeviceProtocolStatus.ERROR,
        DeviceProtocolStatus.FAILED,
    ):
        return "warning"
    if link.status in (DeviceLinkStatus.DEGRADED, DeviceLinkStatus.TIMEOUT, DeviceLinkStatus.CONNECTING):
        return "warning"
    if link.status == DeviceLinkStatus.AUTH_ERROR:
        return "critical"
    if link.status == DeviceLinkStatus.DISCONNECTED:
        age = _heartbeat_age_seconds(link.last_heartbeat_at)
        if age is not None and age <= LIVE_RETRY_WINDOW_SECONDS:
            return "warning"
        return "unhealthy"
    if link.status == DeviceLinkStatus.ERROR:
        return "unhealthy"
    return "unknown"


@dataclass
class DeviceLinkState:
    device_id: str
    ip_address: str = ""
    port: int = 4370
    name: str = ""
    status: DeviceLinkStatus = DeviceLinkStatus.UNKNOWN
    consecutive_failures: int = 0
    consecutive_protocol_failures: int = 0
    last_heartbeat_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_latency_ms: float = 0.0
    last_error: str = ""
    network_status: DeviceNetworkStatus = DeviceNetworkStatus.UNKNOWN
    protocol_status: DeviceProtocolStatus = DeviceProtocolStatus.UNKNOWN
    protocol_code: str = ""
    failure_stage: str = ""
    auth_status: str = ""
    last_tcp_heartbeat_at: Optional[str] = None
    last_protocol_success_at: Optional[str] = None
    last_protocol_error: str = ""
    next_protocol_retry_at: float = 0.0
    comm_key_is_zero: bool = True
    last_failure_mono: float = 0.0

    def to_public_dict(self) -> dict[str, Any]:
        display = live_display_fields(self)
        return {
            "device_id": self.device_id,
            "ip_address": self.ip_address,
            "port": self.port,
            "name": self.name,
            "status": self.status.value,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_protocol_failures": self.consecutive_protocol_failures,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_heartbeat_ago": _ago(self.last_heartbeat_at),
            "last_success_at": self.last_success_at,
            "last_latency_ms": round(self.last_latency_ms, 2),
            "last_error": self.last_error[:500],
            "network_status": self.network_status.value,
            "protocol_status": self.protocol_status.value,
            "protocol_code": self.protocol_code,
            "failure_stage": self.failure_stage,
            "auth_status": self.auth_status,
            "last_tcp_heartbeat_at": self.last_tcp_heartbeat_at,
            "last_tcp_heartbeat_ago": _ago(self.last_tcp_heartbeat_at),
            "last_protocol_success_at": self.last_protocol_success_at,
            "last_protocol_success_ago": _ago(self.last_protocol_success_at),
            "last_protocol_error": self.last_protocol_error[:500],
            "comm_key_is_zero": self.comm_key_is_zero,
            "display_status": display["display_status"],
            "display_network": display["display_network"],
            "display_protocol": display["display_protocol"],
            "live_retry": bool(display["live_retry"]),
        }


@dataclass
class ResolvedStatus:
    code: str
    title: str
    body: str
    tone: str  # ok | warn | error | info


@dataclass
class ConnectionSnapshot:
    connector_status: str
    internet_status: str
    dns_status: str
    server_status: str
    tls_status: str
    authentication_status: str
    sync_status: str
    api_latency_ms: float
    last_api_error: str
    last_api_check_at: Optional[str]
    last_successful_sync: Optional[str]
    pending_records: int
    failed_records: int
    devices: list[dict[str, Any]] = field(default_factory=list)
    resolved: Optional[ResolvedStatus] = None
    api_availability: str = ApiAvailability.UNKNOWN.value
    api_latency_class: str = "unknown"
    api_latency_avg_ms: float = 0.0
    api_latency_min_ms: float = 0.0
    api_latency_max_ms: float = 0.0
    consecutive_slow: int = 0
    consecutive_failures: int = 0
    consecutive_healthy: int = 0
    dns_ms: float = 0.0
    tcp_ms: float = 0.0
    tls_handshake_ms: float = 0.0
    ttfb_ms: float = 0.0

    def to_public_dict(self) -> dict[str, Any]:
        data = {
            "connector_status": self.connector_status,
            "internet_status": self.internet_status,
            "dns_status": self.dns_status,
            "server_status": self.server_status,
            "cloud_status": public_cloud_status(self.server_status),
            "tls_status": self.tls_status,
            "authentication_status": self.authentication_status,
            "sync_status": self.sync_status,
            "api_availability": self.api_availability,
            "api_latency_class": self.api_latency_class,
            "api_latency_ms": round(self.api_latency_ms, 1),
            "api_latency_avg_ms": round(self.api_latency_avg_ms, 1),
            "api_latency_min_ms": round(self.api_latency_min_ms, 1),
            "api_latency_max_ms": round(self.api_latency_max_ms, 1),
            "consecutive_slow": self.consecutive_slow,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_healthy": self.consecutive_healthy,
            "dns_ms": round(self.dns_ms, 1),
            "tcp_ms": round(self.tcp_ms, 1),
            "tls_handshake_ms": round(self.tls_handshake_ms, 1),
            "ttfb_ms": round(self.ttfb_ms, 1),
            "last_api_error": self.last_api_error[:500] if self.last_api_error else "",
            "last_api_check_at": self.last_api_check_at,
            "last_successful_sync": self.last_successful_sync,
            "last_successful_sync_ago": _ago(self.last_successful_sync),
            "pending_records": self.pending_records,
            "failed_records": self.failed_records,
            "devices": self.devices,
        }
        if self.resolved:
            data["headline"] = self.resolved.title
            data["detail"] = self.resolved.body
            data["tone"] = self.resolved.tone
            data["code"] = self.resolved.code
        return data

    def diagnostics_lines(self) -> list[str]:
        primary = self.devices[0] if self.devices else {}
        cloud_label = public_cloud_status(self.server_status).replace("_", " ").upper()
        return [
            f"Internet             {self.internet_status.title()}",
            f"DNS                  {self.dns_status.title()}",
            f"Cloud Status         {cloud_label}",
            f"API Availability     {self.api_availability.replace('_', ' ').title()}",
            f"Last API Check       {_clock(self.last_api_check_at)}",
            f"Response Time        {format_latency_ms(self.api_latency_ms)}",
            f"Average              {format_latency_ms(self.api_latency_avg_ms)}",
            f"Min                  {format_latency_ms(self.api_latency_min_ms)}",
            f"Max                  {format_latency_ms(self.api_latency_max_ms)}",
            f"Consecutive Slow     {self.consecutive_slow}",
            f"Consecutive Failures {self.consecutive_failures}",
            f"DNS Lookup           {format_latency_ms(self.dns_ms)}",
            f"TCP Connect          {format_latency_ms(self.tcp_ms)}",
            f"TLS Handshake        {format_latency_ms(self.tls_handshake_ms)}",
            f"TTFB                 {format_latency_ms(self.ttfb_ms)}",
            f"TLS/SSL              {self.tls_status.title()}",
            f"Device Network       {(primary.get('network_status') or 'unknown').replace('_', ' ').title()}",
            f"Device Protocol      {(primary.get('protocol_status') or 'unknown').replace('_', ' ').title()}",
            f"Device Status        {(primary.get('status') or 'unknown').replace('_', ' ').title()}",
            f"Protocol Code        {primary.get('protocol_code') or '—'}",
            f"Failure Stage        {primary.get('failure_stage') or '—'}",
            f"Device IP            {primary.get('ip_address') or '—'}",
            f"Device Port          {primary.get('port') or '—'}",
            f"Last TCP Heartbeat   {primary.get('last_tcp_heartbeat_ago') or primary.get('last_heartbeat_ago') or 'never'}",
            f"Last Protocol Session {primary.get('last_protocol_success_ago') or 'never'}",
            f"Last Successful Sync  {_ago(self.last_successful_sync)}",
            f"Pending Records       {self.pending_records}",
            f"Failed Records        {self.failed_records}",
            f"Authentication        {self.authentication_status.title()}",
            f"Connector             {self.connector_status.title()}",
        ]


class ConnectionStateRegistry:
    """Thread-safe store of independent connection states."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.connector_status = ConnectorRuntimeStatus.STOPPED
        self.internet = InternetStatus.UNKNOWN
        self.dns_ok: Optional[bool] = None
        self.dns_message = ""
        self.server = ServerStatus.UNKNOWN
        self.tls = TlsStatus.UNKNOWN
        self.auth = AuthStatus.UNKNOWN
        self.sync = SyncStatus.PENDING
        self.api_latency_ms = 0.0
        self.api_availability = ApiAvailability.UNKNOWN
        self.api_latency_class = "unknown"
        self.last_api_error = ""
        self.last_api_check_at: Optional[str] = None
        self.last_successful_sync: Optional[str] = None
        self.pending_records = 0
        self.failed_records = 0
        self.consecutive_slow = 0
        self.consecutive_failures = 0
        self.consecutive_healthy = 0
        self.consecutive_acceptable = 0
        self.dns_ms = 0.0
        self.tcp_ms = 0.0
        self.tls_handshake_ms = 0.0
        self.ttfb_ms = 0.0
        self._latency_samples: deque[float] = deque(maxlen=20)
        self._devices: dict[str, DeviceLinkState] = {}
        self._time_sync_grace_until: dict[str, float] = {}

    def mark_post_time_sync(
        self, device_id: str, *, seconds: float | None = None
    ) -> None:
        """Suppress TCP-offline flaps while the terminal restarts after SET_TIME."""
        from app.constants import DEVICE_TIME_SYNC_GRACE_SECONDS
        import time as _time

        grace = float(
            seconds if seconds is not None else DEVICE_TIME_SYNC_GRACE_SECONDS
        )
        with self._lock:
            self._time_sync_grace_until[str(device_id)] = _time.monotonic() + max(5.0, grace)

    def in_time_sync_grace(self, device_id: str) -> bool:
        import time as _time

        with self._lock:
            until = float(self._time_sync_grace_until.get(str(device_id), 0.0) or 0.0)
        return _time.monotonic() < until

    def set_connector(self, status: ConnectorRuntimeStatus) -> None:
        with self._lock:
            self.connector_status = status

    def set_internet(self, status: InternetStatus) -> None:
        with self._lock:
            self.internet = status

    def set_dns(self, ok: bool, message: str = "") -> None:
        with self._lock:
            self.dns_ok = ok
            self.dns_message = message

    def set_sync(self, status: SyncStatus, *, pending: int | None = None, failed: int | None = None) -> None:
        with self._lock:
            self.sync = status
            if pending is not None:
                self.pending_records = pending
            if failed is not None:
                self.failed_records = failed

    def set_last_sync(self, when: Optional[str]) -> None:
        with self._lock:
            if when:
                self.last_successful_sync = when

    def record_api_result(
        self,
        *,
        kind: str,
        latency_ms: float = 0.0,
        error: str = "",
        status_code: int | None = None,
        dns_ms: float = 0.0,
        tcp_ms: float = 0.0,
        tls_ms: float = 0.0,
        ttfb_ms: float = 0.0,
        clears_auth: bool = True,
    ) -> ServerStatus:
        """Update server/TLS/auth from an API probe. Never touches device state."""
        with self._lock:
            sample = classify_api_sample(kind, latency_ms, status_code)
            self.api_latency_ms = float(latency_ms or 0.0)
            self.last_api_check_at = utcnow().isoformat()
            self.last_api_error = (error or "")[:500]
            self.dns_ms = float(dns_ms or 0.0)
            self.tcp_ms = float(tcp_ms or 0.0)
            self.tls_handshake_ms = float(tls_ms or 0.0)
            self.ttfb_ms = float(ttfb_ms or 0.0)
            if self.api_latency_ms > 0:
                self._latency_samples.append(self.api_latency_ms)
            self.api_latency_class = (
                "healthy"
                if sample == "healthy"
                else "acceptable"
                if sample == "acceptable"
                else "slow"
                if sample == "slow"
                else "timeout"
                if sample == "timeout"
                else self.api_latency_class
            )
            available = sample in {"healthy", "acceptable", "slow"}
            if available:
                self.api_availability = ApiAvailability.AVAILABLE
            elif sample == "auth":
                self.api_availability = ApiAvailability.AVAILABLE
            else:
                self.api_availability = ApiAvailability.UNAVAILABLE

            previous = self.server
            error_status = {
                "tls": ServerStatus.TLS_ERROR,
                "timeout": ServerStatus.TIMEOUT,
                "auth": ServerStatus.AUTH_ERROR,
                "server": ServerStatus.SERVER_ERROR,
                "unavailable": ServerStatus.UNAVAILABLE,
            }.get(sample)

            if available:
                self.consecutive_failures = 0
                if sample == "slow":
                    self.consecutive_slow += 1
                    self.consecutive_healthy = 0
                    self.consecutive_acceptable = 0
                else:
                    self.consecutive_slow = 0
                    self.consecutive_healthy += 1
                    if sample == "acceptable":
                        self.consecutive_acceptable += 1
                    else:
                        self.consecutive_acceptable = 0
            else:
                self.consecutive_healthy = 0
                self.consecutive_acceptable = 0
                self.consecutive_slow = 0
                self.consecutive_failures += 1

            holding = (
                ServerStatus.CONNECTED,
                ServerStatus.ACCEPTABLE,
                ServerStatus.SLOW,
            )
            recovered = self.consecutive_healthy >= API_RECOVERY_CONSECUTIVE_THRESHOLD
            already_ok = previous in (ServerStatus.CONNECTED, ServerStatus.ACCEPTABLE, ServerStatus.UNKNOWN)

            if sample == "auth":
                server = ServerStatus.AUTH_ERROR
            elif available and sample == "slow":
                if self.consecutive_slow >= API_SLOW_CONSECUTIVE_THRESHOLD:
                    server = ServerStatus.SLOW
                elif previous in holding or previous == ServerStatus.UNKNOWN:
                    server = previous if previous != ServerStatus.UNKNOWN else ServerStatus.CONNECTED
                else:
                    server = previous
            elif available:
                if recovered or already_ok:
                    if (
                        sample == "acceptable"
                        and self.consecutive_acceptable >= API_ACCEPTABLE_CONSECUTIVE_THRESHOLD
                    ):
                        server = ServerStatus.ACCEPTABLE
                    elif previous == ServerStatus.ACCEPTABLE and sample == "acceptable":
                        server = ServerStatus.ACCEPTABLE
                    else:
                        server = ServerStatus.CONNECTED
                else:
                    server = previous
            else:
                mapped = error_status or ServerStatus.UNAVAILABLE
                if self.consecutive_failures >= API_FAILURE_CONSECUTIVE_THRESHOLD:
                    server = mapped
                elif previous in holding:
                    server = previous
                else:
                    server = previous

            tls = self.tls
            auth = self.auth
            if server == ServerStatus.TLS_ERROR:
                tls = TlsStatus.TIMEOUT if "timeout" in (error or "").lower() else TlsStatus.ERROR
            elif available:
                tls = TlsStatus.HEALTHY
            if sample == "auth" or status_code in (401, 403):
                auth = AuthStatus.EXPIRED if status_code == 401 else AuthStatus.REQUIRED
            elif available and clears_auth:
                auth = AuthStatus.VALID

            self.server = server
            self.tls = tls
            self.auth = auth
            if server == ServerStatus.AUTH_ERROR and self.sync != SyncStatus.PAUSED:
                self.sync = SyncStatus.PAUSED
            return server

    def device(self, device_id: str) -> Optional[DeviceLinkState]:
        with self._lock:
            link = self._devices.get(device_id)
            return DeviceLinkState(**link.__dict__) if link else None

    def ensure_device(
        self,
        device_id: str,
        *,
        ip_address: str = "",
        port: int = 4370,
        name: str = "",
    ) -> DeviceLinkState:
        with self._lock:
            link = self._devices.get(device_id)
            if link is None:
                link = DeviceLinkState(
                    device_id=device_id, ip_address=ip_address, port=port, name=name
                )
                self._devices[device_id] = link
            else:
                if ip_address:
                    link.ip_address = ip_address
                if port:
                    link.port = port
                if name:
                    link.name = name
            return link

    def reset_device_probe_state(self) -> None:
        """Clear stale disconnect flags so a restart can re-probe the LAN."""
        with self._lock:
            for link in self._devices.values():
                link.consecutive_failures = 0
                link.consecutive_protocol_failures = 0
                link.next_protocol_retry_at = 0.0
                if link.status == DeviceLinkStatus.DISCONNECTED:
                    link.status = DeviceLinkStatus.CONNECTING
                    link.network_status = DeviceNetworkStatus.UNKNOWN
                    link.protocol_status = DeviceProtocolStatus.NOT_COMPLETED
                    link.last_error = ""

    def record_device_probe(
        self,
        device_id: str,
        *,
        ok: bool,
        latency_ms: float = 0.0,
        error: str = "",
        ip_address: str = "",
        port: int = 4370,
        name: str = "",
        auth_error: bool = False,
        timeout: bool = False,
    ) -> DeviceLinkState:
        """Legacy TCP-only probe. Prefer record_device_heartbeat when protocol is known."""
        return self.record_device_heartbeat(
            device_id,
            network_ok=ok,
            protocol_ok=ok,
            latency_ms=latency_ms,
            error=error,
            ip_address=ip_address,
            port=port,
            name=name,
            auth_error=auth_error,
            timeout=timeout,
        )

    def record_device_heartbeat(
        self,
        device_id: str,
        *,
        network_ok: bool,
        protocol_ok: bool = False,
        latency_ms: float = 0.0,
        error: str = "",
        ip_address: str = "",
        port: int = 4370,
        name: str = "",
        auth_error: bool = False,
        timeout: bool = False,
        protocol_code: str = "",
        failure_stage: str = "",
        auth_status: str = "",
        comm_key_is_zero: bool | None = None,
        protocol_attempted: bool = True,
    ) -> DeviceLinkState:
        """Apply network vs protocol heartbeat rules. Independent of API health."""
        with self._lock:
            link = self.ensure_device(
                device_id, ip_address=ip_address, port=port, name=name
            )
            now = utcnow().isoformat()
            link.last_latency_ms = float(latency_ms or 0.0)
            link.last_heartbeat_at = now
            if comm_key_is_zero is not None:
                link.comm_key_is_zero = bool(comm_key_is_zero)
            if failure_stage:
                link.failure_stage = failure_stage
            if auth_status:
                link.auth_status = auth_status
            if protocol_code:
                link.protocol_code = protocol_code

            soft_timeout = is_soft_syn_timeout(error, timeout=timeout)
            hard_lan = is_hard_lan_unreachable(error)
            disconnect_after = (
                DEVICE_HEARTBEAT_DISCONNECT_AFTER
                if hard_lan or not soft_timeout
                else DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER
            )

            if network_ok:
                link.network_status = DeviceNetworkStatus.CONNECTED
                link.consecutive_failures = 0
                link.last_failure_mono = 0.0
                link.last_tcp_heartbeat_at = now
            else:
                # After CMD_SET_TIME the terminal briefly refuses TCP — do not
                # treat that as a real disconnect flap.
                if self.in_time_sync_grace(device_id):
                    link.last_error = error or "post-time-sync reconnect"
                    if link.network_status != DeviceNetworkStatus.CONNECTED:
                        link.network_status = DeviceNetworkStatus.UNKNOWN
                    # Keep prior consecutive_failures; do not escalate to OFFLINE.
                else:
                    now_mono = time.monotonic()
                    coalesced = (
                        link.consecutive_failures > 0
                        and link.last_failure_mono > 0
                        and (now_mono - link.last_failure_mono)
                        < float(DEVICE_FAILURE_COALESCE_SECONDS)
                    )
                    if not coalesced:
                        link.consecutive_failures += 1
                        link.last_failure_mono = now_mono
                    link.last_error = error
                    fails = link.consecutive_failures
                    # Soft SYN timeout: UNKNOWN until disconnect threshold so the
                    # UI shows reconnecting — never freeze on CONNECTED or OFFLINE.
                    if hard_lan and fails >= DEVICE_HEARTBEAT_DEGRADED_AFTER:
                        link.network_status = DeviceNetworkStatus.OFFLINE
                    elif soft_timeout and fails < disconnect_after:
                        link.network_status = DeviceNetworkStatus.UNKNOWN
                    else:
                        link.network_status = DeviceNetworkStatus.OFFLINE

            if protocol_ok:
                link.protocol_status = DeviceProtocolStatus.CONNECTED
                link.protocol_code = DeviceProtocolCode.OK.value
                link.consecutive_protocol_failures = 0
                link.last_protocol_error = ""
                link.last_protocol_success_at = now
                link.next_protocol_retry_at = 0.0
                link.last_error = ""
                link.last_success_at = now
                link.status = DeviceLinkStatus.CONNECTED
            elif not protocol_attempted:
                if not network_ok:
                    if self.in_time_sync_grace(device_id):
                        # Hold prior status while the terminal settles after SET_TIME.
                        pass
                    else:
                        fails = link.consecutive_failures
                        if soft_timeout and fails < disconnect_after:
                            link.status = DeviceLinkStatus.TIMEOUT
                        elif fails < disconnect_after:
                            link.status = DeviceLinkStatus.DEGRADED
                        else:
                            link.status = DeviceLinkStatus.DISCONNECTED
                elif link.protocol_status in (
                    DeviceProtocolStatus.ERROR,
                    DeviceProtocolStatus.FAILED,
                ):
                    link.status = DeviceLinkStatus.DEGRADED
            elif auth_error:
                link.protocol_status = DeviceProtocolStatus.ERROR
                link.protocol_code = protocol_code or DeviceProtocolCode.DEVICE_AUTH_ERROR.value
                link.consecutive_protocol_failures += 1
                link.last_protocol_error = error
                link.last_error = error
                link.status = DeviceLinkStatus.AUTH_ERROR
                self._schedule_protocol_retry(link)
            elif network_ok:
                link.protocol_status = DeviceProtocolStatus.ERROR
                link.protocol_code = protocol_code or (
                    DeviceProtocolCode.DEVICE_CONNECTION_CLOSED.value
                    if "closed" in (error or "").lower()
                    else DeviceProtocolCode.DEVICE_PROTOCOL_ERROR.value
                )
                link.consecutive_protocol_failures += 1
                link.last_protocol_error = error
                link.last_error = error
                link.status = DeviceLinkStatus.DEGRADED
                self._schedule_protocol_retry(link)
            else:
                link.protocol_status = DeviceProtocolStatus.NOT_COMPLETED
                fails = link.consecutive_failures
                if soft_timeout and fails < disconnect_after:
                    link.status = DeviceLinkStatus.TIMEOUT
                elif fails < DEVICE_HEARTBEAT_DEGRADED_AFTER:
                    link.status = DeviceLinkStatus.DEGRADED
                elif fails < disconnect_after:
                    link.status = DeviceLinkStatus.DEGRADED
                else:
                    link.status = DeviceLinkStatus.DISCONNECTED
                    link.protocol_code = protocol_code or DeviceProtocolCode.DEVICE_NETWORK_UNREACHABLE.value

            logger.info(
                "DEVICE_HEARTBEAT device=%s network=%s protocol=%s status=%s "
                "code=%s stage=%s latency_ms=%.1f net_fail=%s proto_fail=%s error=%s",
                link.ip_address or device_id,
                link.network_status.value,
                link.protocol_status.value,
                link.status.value,
                link.protocol_code,
                link.failure_stage,
                link.last_latency_ms,
                link.consecutive_failures,
                link.consecutive_protocol_failures,
                (link.last_error or "")[:160],
                extra={"device_id": device_id},
            )
            return DeviceLinkState(**link.__dict__)

    @staticmethod
    def _schedule_protocol_retry(link: DeviceLinkState) -> None:
        attempt = max(1, int(link.consecutive_protocol_failures))
        idx = min(attempt - 1, len(RETRY_BACKOFF_SCHEDULE) - 1)
        delay = float(RETRY_BACKOFF_SCHEDULE[idx])
        link.next_protocol_retry_at = time.time() + delay

    def connected_device_count(self) -> int:
        with self._lock:
            return sum(
                1
                for d in self._devices.values()
                if d.network_status == DeviceNetworkStatus.CONNECTED
                or d.status
                in (
                    DeviceLinkStatus.CONNECTED,
                    DeviceLinkStatus.CONNECTING,
                    DeviceLinkStatus.DEGRADED,
                    DeviceLinkStatus.TIMEOUT,
                )
            )

    def connecting_device_count(self) -> int:
        with self._lock:
            return sum(1 for d in self._devices.values() if is_live_retry(d))

    def disconnected_device_count(self) -> int:
        with self._lock:
            return sum(
                1
                for d in self._devices.values()
                if d.status == DeviceLinkStatus.DISCONNECTED and not is_live_retry(d)
            )

    def any_device_connected(self) -> bool:
        return self.connected_device_count() > 0

    def any_device_disconnected(self) -> bool:
        with self._lock:
            if not self._devices:
                return False
            return any(d.status == DeviceLinkStatus.DISCONNECTED for d in self._devices.values())

    def snapshot(self) -> ConnectionSnapshot:
        with self._lock:
            dns = "unknown"
            if self.dns_ok is True:
                dns = "connected"
            elif self.dns_ok is False:
                dns = "failed"
            samples = list(self._latency_samples)
            avg_ms = sum(samples) / len(samples) if samples else 0.0
            min_ms = min(samples) if samples else 0.0
            max_ms = max(samples) if samples else 0.0
            devices = [d.to_public_dict() for d in self._devices.values()]
            snap = ConnectionSnapshot(
                connector_status=self.connector_status.value,
                internet_status=self.internet.value,
                dns_status=dns,
                server_status=self.server.value,
                tls_status=self.tls.value,
                authentication_status=self.auth.value,
                sync_status=self.sync.value,
                api_latency_ms=self.api_latency_ms,
                last_api_error=self.last_api_error,
                last_api_check_at=self.last_api_check_at,
                last_successful_sync=self.last_successful_sync,
                pending_records=self.pending_records,
                failed_records=self.failed_records,
                devices=devices,
                api_availability=self.api_availability.value,
                api_latency_class=self.api_latency_class,
                api_latency_avg_ms=avg_ms,
                api_latency_min_ms=min_ms,
                api_latency_max_ms=max_ms,
                consecutive_slow=self.consecutive_slow,
                consecutive_failures=self.consecutive_failures,
                consecutive_healthy=self.consecutive_healthy,
                dns_ms=self.dns_ms,
                tcp_ms=self.tcp_ms,
                tls_handshake_ms=self.tls_handshake_ms,
                ttfb_ms=self.ttfb_ms,
            )
            snap.resolved = resolve_headline(snap)
            return snap


def resolve_headline(snap: ConnectionSnapshot) -> ResolvedStatus:
    """Priority-ordered operator-facing status. Device connectivity is its own status."""
    devices = snap.devices
    primary = devices[0] if devices else {}
    ip = primary.get("ip_address") or "—"
    port = primary.get("port") or 4370
    pending = snap.pending_records
    heartbeat_ago = primary.get("last_heartbeat_ago") or "never"
    last_sync = _ago(snap.last_successful_sync)

    if snap.connector_status in (
        ConnectorRuntimeStatus.STOPPED.value,
        ConnectorRuntimeStatus.STOPPING.value,
    ):
        return ResolvedStatus(
            "CONNECTOR_OFFLINE",
            "Connector Offline",
            "The connector process is not running. Device status is unknown until the connector starts.",
            "error",
        )

    device_status = (primary.get("status") or "").lower()
    network_status = (primary.get("network_status") or "").lower()
    protocol_status = (primary.get("protocol_status") or "").lower()
    consecutive_fails = int(primary.get("consecutive_failures") or 0)
    last_err = str(primary.get("last_error") or "")
    soft_outage = is_soft_syn_timeout(last_err, timeout="timed out" in last_err.lower())
    disconnect_after = (
        DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER
        if soft_outage
        else DEVICE_HEARTBEAT_DISCONNECT_AFTER
    )
    connecting = device_status in (
        DeviceLinkStatus.UNKNOWN.value,
        DeviceLinkStatus.CONNECTING.value,
    ) or (
        protocol_status == DeviceProtocolStatus.NOT_COMPLETED.value
        and consecutive_fails < disconnect_after
        and network_status != DeviceNetworkStatus.CONNECTED.value
    )
    if connecting and consecutive_fails < disconnect_after:
        return ResolvedStatus(
            "DEVICE_CONNECTING",
            "Connecting to biometric device",
            (
                f"Checking LAN reachability to {ip}:{port}.\n\n"
                "This is not a confirmed disconnect. The connector will retry automatically."
            ),
            "info",
        )

    all_disconnected = bool(devices) and all(
        d.get("status") == DeviceLinkStatus.DISCONNECTED.value
        and d.get("network_status") != DeviceNetworkStatus.CONNECTED.value
        for d in devices
    )
    if all_disconnected or (
        device_status == DeviceLinkStatus.DISCONNECTED.value
        and network_status != DeviceNetworkStatus.CONNECTED.value
        and len(devices) == 1
    ):
        age = _heartbeat_age_seconds(primary.get("last_heartbeat_at"))
        live_retry = (
            snap.connector_status == ConnectorRuntimeStatus.RUNNING.value
            and age is not None
            and age <= LIVE_RETRY_WINDOW_SECONDS
        )
        last_err = str(primary.get("last_error") or "").strip()
        if live_retry:
            return ResolvedStatus(
                "DEVICE_CONNECTING",
                "Connecting to biometric device",
                (
                    f"Live retry in progress for {ip}:{port}.\n\n"
                    f"Last attempt: {heartbeat_ago}"
                    + (f"\nLast result: {last_err}" if last_err else "")
                    + "\n\nThe connector keeps trying automatically. "
                    "This is not a confirmed permanent disconnect."
                ),
                "warn",
            )
        return ResolvedStatus(
            "DEVICE_DISCONNECTED",
            "🔴 Biometric Device Unreachable",
            (
                "Brixigo cannot reach the biometric device on the LAN.\n\n"
                f"Device: {ip}\nPort: {port}\n\n"
                "Check:\n• Device power\n• LAN/network connection\n"
                "• Device IP address\n• Port configuration\n\n"
                "The connector will retry automatically."
            ),
            "error",
        )

    protocol_failed = protocol_status in (
        DeviceProtocolStatus.ERROR.value,
        DeviceProtocolStatus.FAILED.value,
    ) or device_status in (DeviceLinkStatus.DEGRADED.value, DeviceLinkStatus.AUTH_ERROR.value)
    if network_status == DeviceNetworkStatus.CONNECTED.value and protocol_failed:
        return ResolvedStatus(
            "DEVICE_PROTOCOL_ERROR",
            "🟠 Device Communication Error",
            communication_error_message(str(ip), int(port), error=str(primary.get("last_error") or "")),
            "warn",
        )

    if device_status in (
        DeviceLinkStatus.TIMEOUT.value,
        DeviceLinkStatus.DEGRADED.value,
    ) and network_status != DeviceNetworkStatus.CONNECTED.value:
        return ResolvedStatus(
            "DEVICE_NETWORK_DEGRADED",
            "🟡 Device Network Degraded",
            (
                f"LAN reachability to {ip}:{port} is unstable.\n\n"
                "The connector will retry automatically and only mark the device "
                "disconnected after consecutive heartbeat failures."
            ),
            "warn",
        )

    if snap.authentication_status in (AuthStatus.EXPIRED.value, AuthStatus.REQUIRED.value) or (
        snap.server_status == ServerStatus.AUTH_ERROR.value
    ):
        return ResolvedStatus(
            "AUTHENTICATION_REQUIRED",
            "🔐 Authentication Required",
            (
                "The connector cannot authenticate with Brixigo Cloud.\n\n"
                "This is not the device comm key. Open Settings and enter "
                "Organisation ID + API Key from the Brixigo admin portal. "
                "The biometric device can stay connected; cloud upload is paused "
                "until those cloud credentials are saved."
            ),
            "warn",
        )

    if snap.server_status == ServerStatus.TLS_ERROR.value:
        return ResolvedStatus(
            "SERVER_SECURE_CONNECTION_ERROR",
            "🟠 Secure Server Connection Problem",
            (
                "The Brixigo server secure connection could not be established.\n\n"
                "The biometric device remains connected."
            ),
            "warn",
        )

    if snap.internet_status == InternetStatus.OFFLINE.value or snap.server_status in (
        ServerStatus.UNAVAILABLE.value,
        ServerStatus.TIMEOUT.value,
        ServerStatus.SERVER_ERROR.value,
    ):
        return ResolvedStatus(
            "SERVER_UNAVAILABLE",
            "🟠 Brixigo Server Unavailable",
            (
                "The Brixigo server could not be reached.\n\n"
                "The biometric device remains connected and attendance will be "
                "synchronized automatically when the server recovers.\n\n"
                f"Last successful sync: {last_sync}\nPending records: {pending}"
            ),
            "warn",
        )

    if snap.server_status in (ServerStatus.SLOW.value, ServerStatus.ACCEPTABLE.value):
        code = "SERVER_SLOW" if snap.server_status == ServerStatus.SLOW.value else "SERVER_ACCEPTABLE"
        return ResolvedStatus(
            code,
            "🟡 Server Response Slow",
            (
                "Brixigo server is available but responding slower than normal.\n"
                f"Response time: {format_latency_ms(snap.api_latency_ms)}\n\n"
                "Biometric device remains connected."
            ),
            "warn",
        )

    if pending > 0 or snap.sync_status in (
        SyncStatus.PENDING.value,
        SyncStatus.DELAYED.value,
        SyncStatus.SYNCING.value,
    ):
        return ResolvedStatus(
            "SYNC_PENDING",
            "🟡 Sync Pending",
            (
                f"The biometric device is connected. {pending} attendance record(s) "
                "are waiting to synchronize."
            ),
            "info",
        )

    return ResolvedStatus(
        "CONNECTED",
        "🟢 Cloud Connected",
        (
            "Brixigo server is responding normally.\n"
            f"Response time: {format_latency_ms(snap.api_latency_ms)}\n"
            f"Last heartbeat: {heartbeat_ago}"
        ),
        "ok",
    )
