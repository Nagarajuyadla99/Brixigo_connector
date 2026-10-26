"""ZK session state, authentication status, and protocol statistics."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from adapters.protocol.zk_const import PROTOCOL_VERSION, USHRT_MAX


class SessionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    ESTABLISHED = "established"
    RECOVERING = "recovering"
    ERROR = "error"


class AuthStatus(str, Enum):
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    SUCCESS = "success"
    FAILED = "failed"


class ProtocolStage(str, Enum):
    IDLE = "idle"
    TCP_CONNECT = "tcp_connect"
    PROTOCOL_INIT = "protocol_init"
    AUTHENTICATION = "authentication"
    FIRST_COMMAND = "first_command"
    COMMAND = "command"
    DISCONNECT = "disconnect"


@dataclass
class ProtocolStats:
    packets_sent: int = 0
    packets_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    checksum_failures: int = 0
    framing_errors: int = 0
    dropped_packets: int = 0
    reconnect_count: int = 0
    timeout_count: int = 0
    retry_count: int = 0
    last_command: int = 0
    last_reply: int = 0
    last_rtt_ms: float = 0.0
    avg_rtt_ms: float = 0.0
    _rtt_samples: int = 0
    last_error: str = ""
    last_tx_hex: str = ""
    last_rx_hex: str = ""

    def record_rtt(self, rtt_ms: float) -> None:
        self.last_rtt_ms = float(rtt_ms)
        self._rtt_samples += 1
        if self._rtt_samples == 1:
            self.avg_rtt_ms = self.last_rtt_ms
        else:
            # Exponential moving average
            self.avg_rtt_ms = (self.avg_rtt_ms * 0.8) + (self.last_rtt_ms * 0.2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "checksum_failures": self.checksum_failures,
            "framing_errors": self.framing_errors,
            "dropped_packets": self.dropped_packets,
            "reconnect_count": self.reconnect_count,
            "timeout_count": self.timeout_count,
            "retry_count": self.retry_count,
            "last_command": self.last_command,
            "last_reply": self.last_reply,
            "last_rtt_ms": round(self.last_rtt_ms, 2),
            "avg_rtt_ms": round(self.avg_rtt_ms, 2),
            "last_error": self.last_error,
            "last_tx_hex": self.last_tx_hex,
            "last_rx_hex": self.last_rx_hex,
        }


@dataclass
class ZKSession:
    state: SessionState = SessionState.DISCONNECTED
    auth_status: AuthStatus = AuthStatus.UNKNOWN
    session_id: int = 0
    reply_id: int = USHRT_MAX - 1
    authenticated: bool = False
    protocol_version: str = PROTOCOL_VERSION
    connected_at: float = 0.0
    last_activity_at: float = 0.0
    stage: ProtocolStage = ProtocolStage.IDLE
    last_command_name: str = ""
    failure_stage: str = ""
    stats: ProtocolStats = field(default_factory=ProtocolStats)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def reset(self) -> None:
        with self._lock:
            self.state = SessionState.DISCONNECTED
            self.auth_status = AuthStatus.UNKNOWN
            self.session_id = 0
            self.reply_id = USHRT_MAX - 1
            self.authenticated = False
            self.connected_at = 0.0
            self.last_activity_at = 0.0
            self.stage = ProtocolStage.IDLE
            self.last_command_name = ""
            # Preserve cumulative stats and failure_stage across reconnects

    def mark_connecting(self) -> None:
        with self._lock:
            self.state = SessionState.CONNECTING
            self.session_id = 0
            self.reply_id = USHRT_MAX - 1
            self.authenticated = False
            self.auth_status = AuthStatus.UNKNOWN
            self.stage = ProtocolStage.TCP_CONNECT
            self.failure_stage = ""

    def mark_established(self, *, auth_status: AuthStatus) -> None:
        with self._lock:
            now = time.time()
            self.state = SessionState.ESTABLISHED
            self.auth_status = auth_status
            self.authenticated = auth_status in (
                AuthStatus.NOT_REQUIRED,
                AuthStatus.SUCCESS,
            )
            self.connected_at = now
            self.last_activity_at = now

    def touch(self) -> None:
        with self._lock:
            self.last_activity_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state.value,
                "auth_status": self.auth_status.value,
                "session_id": self.session_id,
                "reply_id": self.reply_id,
                "authenticated": self.authenticated,
                "protocol_version": self.protocol_version,
                "connected_at": self.connected_at,
                "last_activity_at": self.last_activity_at,
                "stage": self.stage.value,
                "last_command_name": self.last_command_name,
                "failure_stage": self.failure_stage,
                "stats": self.stats.as_dict(),
            }
