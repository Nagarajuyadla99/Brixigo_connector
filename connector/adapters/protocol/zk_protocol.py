"""Enterprise ZKTeco / eSSL protocol client (pull devices).

Architecture:
  Packet engine  →  Transport (TCP framed / UDP)  →  ZKClient commands

This module preserves the historical ZKClient API used by adapters while
implementing official Standalone SDK TCP framing, CMD_AUTH, session state,
checksum validation, retries, and protocol diagnostics.
"""

from __future__ import annotations

import logging
import struct
import time
from datetime import datetime
from typing import Any, Callable, Optional

from adapters.protocol.zk_auth import make_commkey
from adapters.protocol.zk_const import (
    CMD_ACK_OK,
    CMD_ACK_UNAUTH,
    CMD_ATTLOG_RRQ,
    CMD_AUTH,
    CMD_CLEAR_ATTLOG,
    CMD_CONNECT,
    CMD_DATA,
    CMD_DELETE_USER,
    CMD_DEVICE,
    CMD_DISABLEDEVICE,
    CMD_ENABLEDEVICE,
    CMD_EXIT,
    CMD_GET_FREE_SIZES,
    CMD_GET_TIME,
    CMD_GET_VERSION,
    CMD_PREPARE_DATA,
    CMD_REFRESHDATA,
    CMD_SET_TIME,
    CMD_USER_WRQ,
    CMD_USERTEMP_RRQ,
    CMD_USERTEMP_WRQ,
    LEVEL_ADMIN,
    LEVEL_USER,
    PROTOCOL_VERSION,
    SUCCESS_REPLY_CODES,
)
from adapters.protocol.zk_packet import ZKProtocolError
from adapters.protocol.zk_profiles import DeviceProfile, resolve_profile
from adapters.protocol.zk_session import AuthStatus, ProtocolStage, SessionState, ZKSession
from adapters.protocol.zk_transport import (
    POST_SET_TIME_DELAY_SECONDS,
    POST_TCP_CLOSE_DELAY_SECONDS,
    TCP_CONNECT_ATTEMPTS,
    ZKTransport,
)

logger = logging.getLogger("brixigo.connection")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


# Re-export constants historically imported from this module
USHRT_MAX = 65535
CMD_ACK_ERROR = 2001
CMD_ACK_DATA = 2002
CMD_FREE_DATA = 1502
CMD_GET_DEVICE_NAME = 1101
CMD_VERIFY_WRQ = 87
CMD_CLEAR_DATA = 14


def _decode_time(value: int) -> datetime:
    second = value % 60
    value //= 60
    minute = value % 60
    value //= 60
    hour = value % 24
    value //= 24
    day = value % 31 + 1
    value //= 31
    month = value % 12 + 1
    value //= 12
    year = value + 2000
    return datetime(year, month, day, hour, minute, second)


def _encode_time(dt: datetime) -> int:
    return (
        ((dt.year % 100) * 12 * 31 + ((dt.month - 1) * 31) + dt.day - 1) * (24 * 60 * 60)
        + (dt.hour * 60 + dt.minute) * 60
        + dt.second
    )


class ZKClient:
    """High-level ZK-compatible biometric device client."""

    def __init__(
        self,
        ip: str,
        port: int = 4370,
        password: int = 0,
        timeout: float = 8.0,
        read_timeout: float | None = None,
        force_udp: bool = False,
        *,
        manufacturer: str = "",
        model: str = "",
        profile: DeviceProfile | None = None,
        max_retries: int | None = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.ip = ip
        self.port = int(port)
        self.password = int(password or 0)
        self.force_udp = bool(force_udp)
        self.profile = profile or resolve_profile(manufacturer=manufacturer, model=model)
        self.timeout = float(timeout or self.profile.connect_timeout)
        self.read_timeout = (
            float(read_timeout)
            if read_timeout is not None
            else float(self.profile.read_timeout)
        )
        self.session = ZKSession()
        self._transport = ZKTransport(
            ip=self.ip,
            port=self.port,
            force_udp=self.force_udp,
            connect_timeout=self.timeout,
            read_timeout=self.read_timeout,
            max_retries=max_retries if max_retries is not None else self.profile.max_retries,
            retry_backoff=self.profile.retry_backoff_seconds,
            connect_attempts=TCP_CONNECT_ATTEMPTS,
            session=self.session,
            cancel_check=cancel_check,
        )
        self._connected = False

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected and self.session.state == SessionState.ESTABLISHED

    @property
    def protocol_version(self) -> str:
        return PROTOCOL_VERSION

    def diagnostics_snapshot(self) -> dict[str, Any]:
        snap = self.session.snapshot()
        snap.update(
            {
                "ip": self.ip,
                "port": self.port,
                "force_udp": self.force_udp,
                "profile": self.profile.code,
                "profile_label": self.profile.label,
                "comm_key_is_zero": self.password == 0,
            }
        )
        return snap

    def connect(self) -> None:
        if self.connected:
            return
        last_exc: Optional[BaseException] = None
        original_udp = self.force_udp
        try_udp_after_tcp = not original_udp
        modes = [original_udp]
        if try_udp_after_tcp:
            modes.append(True)
        for index, use_udp in enumerate(modes):
            self.force_udp = bool(use_udp)
            self._transport.force_udp = self.force_udp
            self.session.mark_connecting()
            try:
                self._handshake()
                return
            except Exception as exc:
                last_exc = exc
                if not self.session.failure_stage:
                    self.session.failure_stage = self.session.stage.value
                self.session.stats.last_error = str(exc)
                self.session.state = SessionState.ERROR
                logger.exception(
                    "[%s] PROTOCOL_STATUS result=FAILED stage=%s ip=%s port=%s error=%s | last_tx=%s last_rx=%s",
                    _ts(),
                    self.session.failure_stage,
                    self.ip,
                    self.port,
                    exc,
                    self.session.stats.last_tx_hex,
                    self.session.stats.last_rx_hex,
                )
                self.disconnect()
                tcp_fail = "TCP connect failed" in str(exc)
                refused = "refused" in str(exc).lower()
                # UDP only helps when the host is up but TCP 4370 is closed.
                # A SYN timeout means the terminal is not on this LAN.
                if use_udp or not tcp_fail or not refused or index >= len(modes) - 1:
                    self.force_udp = original_udp
                    self._transport.force_udp = original_udp
                    raise
                logger.warning(
                    "[%s] TCP_CONNECT_FAIL falling back to UDP ip=%s port=%s",
                    _ts(),
                    self.ip,
                    self.port,
                )
        if last_exc is not None:
            raise last_exc

    def _handshake(self) -> None:
        logger.info(
            "[%s] PROTOCOL_INIT result=STARTED ip=%s port=%s force_udp=%s comm_key=%s profile=%s",
            _ts(),
            self.ip,
            self.port,
            self.force_udp,
            "0" if self.password == 0 else "set",
            self.profile.code,
        )
        self._transport.open()
        self.session.stage = ProtocolStage.PROTOCOL_INIT
        logger.info("[%s] PROTOCOL_INIT_START command=CMD_CONNECT", _ts())
        reply = self._transport.send_command(CMD_CONNECT, b"", response_size=16)
        self.session.session_id = reply.session_id or self.session.session_id
        self.session.reply_id = reply.reply_id
        logger.info(
            "[%s] PROTOCOL_INIT_SUCCESS cmd=%s session_id=%s reply_id=%s",
            _ts(),
            reply.command_label,
            self.session.session_id,
            self.session.reply_id,
        )

        if reply.command == CMD_ACK_UNAUTH:
            self.session.state = SessionState.AUTHENTICATING
            self.session.auth_status = AuthStatus.REQUIRED
            self.session.stage = ProtocolStage.AUTHENTICATION
            logger.info(
                "[%s] AUTH_START session=%s comm_key=%s",
                _ts(),
                self.session.session_id,
                "set" if self.password else "0",
            )
            auth_payload = make_commkey(self.password, self.session.session_id)
            logger.info(
                "Authentication sending CMD_AUTH payload_hex=%s",
                auth_payload.hex().upper(),
            )
            auth = self._transport.send_command(CMD_AUTH, auth_payload, response_size=16)
            if auth.command not in SUCCESS_REPLY_CODES and auth.command != CMD_ACK_OK:
                self.session.auth_status = AuthStatus.FAILED
                self.session.failure_stage = ProtocolStage.AUTHENTICATION.value
                raise ZKProtocolError(
                    "Authentication failed — check device password/comm key"
                )
            self.session.session_id = auth.session_id or self.session.session_id
            self.session.reply_id = auth.reply_id
            self.session.mark_established(auth_status=AuthStatus.SUCCESS)
            logger.info(
                "[%s] AUTH_SUCCESS session=%s", _ts(), self.session.session_id
            )
        elif reply.command == CMD_ACK_OK:
            self.session.mark_established(auth_status=AuthStatus.NOT_REQUIRED)
            logger.info(
                "[%s] PROTOCOL_INIT result=SUCCESS auth=not_required session=%s",
                _ts(),
                self.session.session_id,
            )
        else:
            self.session.state = SessionState.ERROR
            self.session.failure_stage = ProtocolStage.PROTOCOL_INIT.value
            raise ZKProtocolError(f"Connect rejected: cmd={reply.command}")

        self._transport.set_timeout(self.read_timeout)
        self._connected = True
        logger.info(
            "[%s] PROTOCOL_STATUS result=ESTABLISHED ip=%s port=%s session=%s auth=%s",
            _ts(),
            self.ip,
            self.port,
            self.session.session_id,
            self.session.auth_status.value,
        )

    def disconnect(self) -> None:
        self.session.stage = ProtocolStage.DISCONNECT
        try:
            if self._connected:
                try:
                    logger.info("[%s] PROTOCOL_COMMAND command=CMD_EXIT", _ts())
                    self._transport.send_command(CMD_EXIT, b"", response_size=16)
                except Exception:
                    pass
        finally:
            self._transport.close()
            self._connected = False
            self.session.reset()

    def reconnect(self) -> None:
        self.session.stats.reconnect_count += 1
        self.session.state = SessionState.RECOVERING
        logger.info("Reconnect attempt #%s %s:%s", self.session.stats.reconnect_count, self.ip, self.port)
        self.disconnect()
        self.connect()

    def __enter__(self) -> "ZKClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _send(self, command: int, data: bytes = b"", *, response_size: int = 1032):
        if not self._connected and command not in (CMD_CONNECT, CMD_AUTH):
            self.connect()
        return self._transport.send_command(command, data, response_size=response_size)

    def ping(self) -> float:
        """Lightweight heartbeat — round-trip get_time."""
        started = time.perf_counter()
        _ = self.get_time()
        return (time.perf_counter() - started) * 1000.0

    def disable_device(self) -> None:
        self._send(CMD_DISABLEDEVICE)

    def enable_device(self) -> None:
        self._send(CMD_ENABLEDEVICE)

    def get_device_name(self) -> str:
        reply = self._send(CMD_DEVICE, b"~DeviceName\x00")
        return self._decode_string(reply.data)

    def get_serial_number(self) -> str:
        reply = self._send(CMD_DEVICE, b"~SerialNumber\x00")
        return self._decode_string(reply.data)

    def get_firmware_version(self) -> str:
        reply = self._send(CMD_GET_VERSION)
        return self._decode_string(reply.data)

    def get_platform(self) -> str:
        reply = self._send(CMD_DEVICE, b"~Platform\x00")
        return self._decode_string(reply.data)

    def get_time(self) -> datetime:
        reply = self._send(CMD_GET_TIME)
        data = reply.data or b""
        if len(data) < 4:
            raise ZKProtocolError("Invalid time payload")
        value = struct.unpack("<I", data[:4])[0]
        return _decode_time(value)

    def set_time(self, dt: datetime | None = None) -> None:
        dt = dt or datetime.now()
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        payload = struct.pack("<I", _encode_time(dt))
        disabled = False
        try:
            try:
                self.disable_device()
                disabled = True
            except Exception:
                logger.debug("disable_device before set_time skipped")
            reply = self._send(CMD_SET_TIME, payload)
            if reply.command not in SUCCESS_REPLY_CODES and reply.command != CMD_ACK_OK:
                raise ZKProtocolError("Failed to set device time")
            if POST_SET_TIME_DELAY_SECONDS > 0:
                time.sleep(POST_SET_TIME_DELAY_SECONDS)
            # K90 closes TCP 4370 while applying the new clock. Rebuild the
            # session immediately so the next command / heartbeat is not a
            # false "TCP DOWN".
            try:
                self.disconnect()
            except Exception:
                logger.debug("disconnect after set_time skipped")
            time.sleep(max(0.5, POST_TCP_CLOSE_DELAY_SECONDS))
            self.connect()
            disabled = True
        finally:
            if disabled:
                try:
                    self.enable_device()
                except Exception:
                    logger.debug("enable_device after set_time skipped")

    def get_option(self, key: str) -> str:
        reply = self._send(CMD_DEVICE, f"{key}\x00".encode("ascii", errors="ignore"))
        return self._decode_string(reply.data)

    def set_option(self, key: str, value: str) -> None:
        from adapters.protocol.zk_const import CMD_OPTIONS_WRQ

        payload = f"{key}={value}\x00".encode("ascii", errors="ignore")
        reply = self._send(CMD_OPTIONS_WRQ, payload)
        if reply.command not in SUCCESS_REPLY_CODES and reply.command != CMD_ACK_OK:
            raise ZKProtocolError(f"set_option rejected for {key}")

    def get_attendance(self) -> list[dict[str, Any]]:
        self.disable_device()
        try:
            records: list[dict[str, Any]] = []
            raw = self._read_with_buffer(CMD_ATTLOG_RRQ)
            sizes = self.profile.attendance_record_sizes
            size = sizes[0]
            if raw:
                for candidate in sizes:
                    if len(raw) % candidate == 0:
                        size = candidate
                        break
                else:
                    size = 40 if len(raw) >= 40 else 16

            offset = 0
            while offset + size <= len(raw):
                chunk = raw[offset : offset + size]
                offset += size
                try:
                    if size >= 40:
                        uid = struct.unpack("<H", chunk[0:2])[0]
                        user_id = (
                            chunk[2:26]
                            .split(b"\x00")[0]
                            .decode("utf-8", errors="ignore")
                            .strip()
                        )
                        status = chunk[26]
                        punch = chunk[27]
                        timestamp = struct.unpack("<I", chunk[28:32])[0]
                    elif size >= 16:
                        uid = struct.unpack("<H", chunk[0:2])[0]
                        user_id = str(struct.unpack("<I", chunk[2:6])[0])
                        status = chunk[6]
                        timestamp = (
                            struct.unpack("<I", chunk[7:11])[0] if len(chunk) >= 11 else 0
                        )
                        punch = chunk[12] if len(chunk) > 12 else 0
                    else:
                        continue
                    if not user_id or timestamp <= 0:
                        continue
                    punch_time = _decode_time(timestamp)
                    # Naive device-local datetime. Do not convert to UTC here.
                    # JSON serialization happens at the API / SQLite JSON boundary.
                    records.append(
                        {
                            "uid": uid,
                            "user_id": user_id,
                            "status": int(status),
                            "punch": int(punch),
                            "timestamp": punch_time.isoformat(sep=" "),
                            "punch_time": punch_time,
                        }
                    )
                except Exception as exc:
                    logger.debug("Skipping malformed attendance record: %s", exc)
            return records
        finally:
            try:
                self.enable_device()
            except Exception:
                pass

    def get_users(self) -> list[dict[str, Any]]:
        self.disable_device()
        try:
            users: list[dict[str, Any]] = []
            raw = self._read_with_buffer(CMD_USERTEMP_RRQ)
            size = self.profile.user_record_size
            if raw and len(raw) % 28 == 0 and len(raw) % 72 != 0:
                size = 28
            offset = 0
            while offset + size <= len(raw):
                chunk = raw[offset : offset + size]
                offset += size
                try:
                    if size >= 72:
                        uid = struct.unpack("<H", chunk[0:2])[0]
                        privilege = chunk[2]
                        password = (
                            chunk[3:11]
                            .split(b"\x00")[0]
                            .decode("utf-8", errors="ignore")
                        )
                        name = (
                            chunk[11:35]
                            .split(b"\x00")[0]
                            .decode("utf-8", errors="ignore")
                        )
                        card = struct.unpack("<I", chunk[35:39])[0]
                        user_id = (
                            chunk[48:57]
                            .split(b"\x00")[0]
                            .decode("utf-8", errors="ignore")
                        )
                    else:
                        uid = struct.unpack("<H", chunk[0:2])[0]
                        privilege = chunk[2]
                        name = (
                            chunk[8:16]
                            .split(b"\x00")[0]
                            .decode("utf-8", errors="ignore")
                        )
                        user_id = str(uid)
                        password = ""
                        card = 0
                    users.append(
                        {
                            "uid": uid,
                            "user_id": user_id or str(uid),
                            "name": name,
                            "privilege": privilege,
                            "password": password,
                            "card": str(card) if card else "",
                        }
                    )
                except Exception as exc:
                    logger.debug("Skipping malformed user record: %s", exc)
            return users
        finally:
            try:
                self.enable_device()
            except Exception:
                pass

    def get_device_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {}
        for key, fn in (
            ("serial_number", self.get_serial_number),
            ("firmware", self.get_firmware_version),
            ("platform", self.get_platform),
            ("device_name", self.get_device_name),
        ):
            try:
                info[key] = fn()
            except Exception as exc:
                info[key] = ""
                info[f"{key}_error"] = str(exc)
        try:
            info["device_time"] = self.get_time().isoformat(sep=" ")
        except Exception as exc:
            info["device_time_error"] = str(exc)
        return info

    def health_check(self) -> tuple[bool, float, str]:
        """Minimal read-only session: connect + CMD_GET_TIME. Does not download logs."""
        started = time.perf_counter()
        try:
            if not self.connected:
                self.connect()
            self.session.stage = ProtocolStage.FIRST_COMMAND
            logger.info("[%s] COMMAND_START: DEVICE_TIME (CMD_GET_TIME)", _ts())
            _ = self.get_time()
            logger.info("[%s] COMMAND_OK: DEVICE_TIME", _ts())
            latency = (time.perf_counter() - started) * 1000
            return True, latency, "ok"
        except Exception as exc:
            if not self.session.failure_stage:
                self.session.failure_stage = self.session.stage.value
            latency = (time.perf_counter() - started) * 1000
            return False, latency, str(exc)

    def clear_attendance(self) -> None:
        reply = self._send(CMD_CLEAR_ATTLOG)
        if reply.command != CMD_ACK_OK:
            raise ZKProtocolError("Failed to clear attendance logs")

    def reboot(self) -> None:
        reply = self._send(CMD_DEVICE, b"Reboot\x00")
        if reply.command not in (CMD_ACK_OK, CMD_ACK_DATA, CMD_DATA):
            reply = self._send(CMD_DEVICE, b"~Reboot\x00")
            if reply.command not in (CMD_ACK_OK, CMD_ACK_DATA, CMD_DATA):
                raise ZKProtocolError("Device rejected reboot command")

    def get_mac_address(self) -> str:
        reply = self._send(CMD_DEVICE, b"MAC\x00")
        value = self._decode_string(reply.data)
        if value:
            return value
        reply = self._send(CMD_DEVICE, b"~MAC\x00")
        return self._decode_string(reply.data)

    def get_product_code(self) -> str:
        reply = self._send(CMD_DEVICE, b"~ProductCode\x00")
        return self._decode_string(reply.data)

    def get_sdk_version(self) -> str:
        reply = self._send(CMD_DEVICE, b"~SDKVersion\x00")
        value = self._decode_string(reply.data)
        if value:
            return value
        reply = self._send(CMD_GET_VERSION)
        return self._decode_string(reply.data)

    def get_free_sizes(self) -> dict[str, Any]:
        reply = self._send(CMD_GET_FREE_SIZES)
        data = reply.data or b""
        result: dict[str, Any] = {"raw_len": len(data)}
        if len(data) >= 28:
            values = struct.unpack(
                "<" + "I" * (len(data) // 4), data[: len(data) - (len(data) % 4)]
            )
            result["values"] = list(values)
            if len(values) >= 7:
                result.update(
                    {
                        "user_capacity": int(values[0]),
                        "user_count": int(values[1]),
                        "fp_capacity": int(values[2]),
                        "fp_count": int(values[3]),
                        "log_capacity": int(values[4]),
                        "log_count": int(values[5]),
                        "pwd_count": int(values[6]),
                    }
                )
            if len(values) >= 9:
                result["face_capacity"] = int(values[7])
                result["face_count"] = int(values[8])
        return result

    def get_extended_device_info(self) -> dict[str, Any]:
        info = self.get_device_info()
        for key, fn in (
            ("mac_address", self.get_mac_address),
            ("model", self.get_product_code),
            ("sdk_version", self.get_sdk_version),
        ):
            try:
                info[key] = fn()
            except Exception as exc:
                info[key] = ""
                info[f"{key}_error"] = str(exc)
        try:
            sizes = self.get_free_sizes()
            info["capacity"] = sizes
            info["user_count"] = int(sizes.get("user_count") or 0)
            info["attendance_count"] = int(sizes.get("log_count") or 0)
            info["user_capacity"] = int(sizes.get("user_capacity") or 0)
            info["log_capacity"] = int(sizes.get("log_capacity") or 0)
            info["fp_capacity"] = int(sizes.get("fp_capacity") or 0)
            info["fp_count"] = int(sizes.get("fp_count") or 0)
            info["face_capacity"] = int(sizes.get("face_capacity") or 0)
            info["face_count"] = int(sizes.get("face_count") or 0)
        except Exception as exc:
            info["capacity_error"] = str(exc)
        try:
            info["timezone"] = self.get_option("~ZKTimeZone") or self.get_option("TimeZone")
        except Exception:
            info["timezone"] = ""
        info["protocol_version"] = PROTOCOL_VERSION
        info["profile"] = self.profile.code
        info["session"] = self.diagnostics_snapshot()
        return info

    def set_user(
        self,
        *,
        uid: int,
        user_id: str,
        name: str = "",
        privilege: int = 0,
        password: str = "",
        card: int | str = 0,
    ) -> None:
        self.disable_device()
        try:
            card_int = int(str(card or "0").strip() or 0)
            record = bytearray(72)
            struct.pack_into("<H", record, 0, int(uid) & 0xFFFF)
            record[2] = int(privilege) & 0xFF
            pwd = str(password or "").encode("utf-8")[:8]
            record[3 : 3 + len(pwd)] = pwd
            n = str(name or "").encode("utf-8")[:24]
            record[11 : 11 + len(n)] = n
            struct.pack_into("<I", record, 35, card_int & 0xFFFFFFFF)
            uid_str = str(user_id or "").encode("utf-8")[:9]
            record[48 : 48 + len(uid_str)] = uid_str
            reply = self._send(CMD_USER_WRQ, bytes(record))
            if reply.command not in (CMD_ACK_OK, CMD_ACK_DATA, CMD_DATA):
                raise ZKProtocolError(f"set_user rejected for {user_id}")
            try:
                self._send(CMD_REFRESHDATA)
            except Exception:
                pass
        finally:
            try:
                self.enable_device()
            except Exception:
                pass

    def delete_user(self, uid: int) -> None:
        self.disable_device()
        try:
            payload = struct.pack("<H", int(uid) & 0xFFFF)
            reply = self._send(CMD_DELETE_USER, payload)
            if reply.command not in (CMD_ACK_OK, CMD_ACK_DATA, CMD_DATA):
                raise ZKProtocolError(f"delete_user rejected uid={uid}")
            try:
                self._send(CMD_REFRESHDATA)
            except Exception:
                pass
        finally:
            try:
                self.enable_device()
            except Exception:
                pass

    def get_templates(self) -> list[dict[str, Any]]:
        self.disable_device()
        try:
            raw = self._read_with_buffer(CMD_USERTEMP_RRQ)
            templates: list[dict[str, Any]] = []
            offset = 0
            while offset + 6 <= len(raw):
                uid = struct.unpack("<H", raw[offset : offset + 2])[0]
                finger = raw[offset + 2]
                valid = raw[offset + 3]
                size = struct.unpack("<H", raw[offset + 4 : offset + 6])[0]
                if size <= 0 or offset + 6 + size > len(raw):
                    offset += 6
                    continue
                body = raw[offset + 6 : offset + 6 + size]
                offset += 6 + size
                if valid == 0:
                    continue
                templates.append(
                    {
                        "uid": uid,
                        "finger_index": int(finger),
                        "valid": int(valid),
                        "size": size,
                        "template": body,
                        "template_type": "fingerprint",
                    }
                )
            return templates
        finally:
            try:
                self.enable_device()
            except Exception:
                pass

    def set_template(
        self,
        *,
        uid: int,
        finger_index: int,
        template: bytes,
        valid: int = 1,
    ) -> None:
        if not template:
            raise ZKProtocolError("Empty template")
        self.disable_device()
        try:
            header = struct.pack(
                "<HBBH",
                int(uid) & 0xFFFF,
                int(finger_index) & 0xFF,
                int(valid) & 0xFF,
                len(template) & 0xFFFF,
            )
            payload = header + template
            reply = self._send(CMD_USERTEMP_WRQ, payload)
            if reply.command not in (CMD_ACK_OK, CMD_ACK_DATA, CMD_DATA):
                raise ZKProtocolError(
                    f"set_template rejected uid={uid} finger={finger_index}"
                )
            try:
                self._send(CMD_REFRESHDATA)
            except Exception:
                pass
        finally:
            try:
                self.enable_device()
            except Exception:
                pass

    def delete_template(self, *, uid: int, finger_index: int = 0) -> None:
        self.set_template(uid=uid, finger_index=finger_index, template=b"\x00" * 4, valid=0)

    def get_device_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {}
        for key in (
            "~DeviceName",
            "~SerialNumber",
            "~Platform",
            "~OEMVendor",
            "LockOn",
            "Delay",
            "~ZKFPVersion",
        ):
            try:
                reply = self._send(CMD_DEVICE, f"{key}\x00".encode("ascii"))
                config[key.lstrip("~")] = self._decode_string(reply.data)
            except Exception as exc:
                config[f"{key}_error"] = str(exc)
        return config

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_with_buffer(self, command: int) -> bytes:
        reply = self._send(command)
        if reply.command == CMD_ACK_OK and not reply.data:
            return b""
        if reply.command in (CMD_ACK_DATA, CMD_DATA, CMD_PREPARE_DATA):
            raw = reply.data or b""
        elif reply.command == CMD_ACK_OK:
            raw = reply.data or b""
        elif reply.command == CMD_ACK_ERROR:
            raise ZKProtocolError("Device returned ACK_ERROR for data request")
        else:
            raw = reply.data or b""
        return self._strip_buffer_size_prefix(raw)

    @staticmethod
    def _strip_buffer_size_prefix(raw: bytes) -> bytes:
        """
        ZK buffered payloads often start with a little-endian uint32 size.

        pyzk get_attendance: total_size = unpack('I', data[:4]); data = data[4:]
        Live K90: PREPARE size=76 with body 72+4, and size=444 with body 440+4.
        """
        if len(raw) < 4:
            return raw
        declared = struct.unpack("<I", raw[:4])[0]
        if declared > 0 and declared + 4 == len(raw):
            return raw[4:]
        return raw
    @staticmethod
    def _decode_string(data: bytes) -> str:
        if not data:
            return ""
        text = data.split(b"\x00")[0].decode("utf-8", errors="ignore")
        if "=" in text:
            return text.split("=", 1)[1].strip()
        return text.strip()


# Backward-compatible helper names used by older imports / tests
def _create_checksum(payload: bytes) -> int:
    from adapters.protocol.zk_checksum import create_checksum

    return create_checksum(payload)


def _make_packet(command: int, session_id: int, reply_id: int, data: bytes = b"") -> bytes:
    from adapters.protocol.zk_packet import encode_payload

    return encode_payload(command, session_id, reply_id, data, increment_reply=True)
