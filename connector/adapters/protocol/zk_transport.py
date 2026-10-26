"""ZK transport — TCP framed / UDP raw sockets with reassembly and retry."""

from __future__ import annotations

import logging
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime
from typing import Callable, Optional

from adapters.protocol.zk_const import (
    CMD_ACK_OK,
    CMD_DATA,
    CMD_PREPARE_DATA,
    MACHINE_PREPARE_DATA_1,
    MACHINE_PREPARE_DATA_2,
    PRE_SESSION_COMMANDS,
    command_name,
)
from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS
from adapters.protocol.zk_packet import (
    ZKPacket,
    ZKProtocolError,
    decode_payload,
    encode_payload,
    raw_hex,
    try_extract_tcp_frame,
    wrap_tcp,
)
from adapters.protocol.zk_session import ProtocolStage, ProtocolStats, SessionState, ZKSession
from app.constants import POST_TCP_CLOSE_DELAY_SECONDS

logger = logging.getLogger("brixigo.connection")

# eSSL K90 / ZK pull firmware can drop a session if CMD_CONNECT rides the
# TCP handshake ACK. A short settle matches pyzk + official SDK practice.
POST_TCP_CONNECT_DELAY_SECONDS = 0.08
# K90 accepts one SDK session. A graceful FIN leaves the terminal in TIME_WAIT
# so the next SYN times out. Abortive close + a brief pause fixes Sync Now.
# First SYN is often lost while Windows ARPs a quiet K90. Three attempts with
# ping-wake between them recover sleepy terminals without the old 3-minute hang
# (poll still uses a short first-attempt timeout in DeviceSessionManager).
TCP_CONNECT_ATTEMPTS = 3
# After CMD_SET_TIME the K90 drops TCP 4370 while it applies the clock.
# Wait long enough, then the client must reconnect before more commands.
POST_SET_TIME_DELAY_SECONDS = 4.0


def abortive_close(sock: socket.socket) -> None:
    """RST-close so the K90 releases TCP 4370 immediately instead of TIME_WAIT."""
    try:
        linger = struct.pack("ii", 1, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def wake_neighbor(host: str, *, wait_ms: int = 800) -> None:
    """ICMP ping to populate / refresh ARP without deleting the neighbor entry.

    Prefer this over `arp -d`. Continuous ARP deletes (from failed probes) leave
    Windows with an Incomplete neighbor so the next TCP SYN times out.
    """
    ip = str(host or "").strip()
    if not ip:
        return
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        if sys.platform == "win32":
            cmd = ["ping", "-n", "1", "-w", str(max(200, int(wait_ms))), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(max(1, int(wait_ms) // 1000) or 1), ip]
        subprocess.run(
            cmd,
            check=False,
            timeout=max(2.0, wait_ms / 1000.0 + 1.0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception:
        logger.debug("Neighbor wake skipped host=%s", ip)


def refresh_neighbor_arp(host: str) -> None:
    """Drop a stale/incomplete ARP entry, then ping so the next SYN can resolve.

    Only call after a failed SYN — never from the periodic tcp_probe path.
    Overnight Wi-Fi sleep leaves Windows with an Incomplete neighbor for the K90.
    """
    ip = str(host or "").strip()
    if not ip:
        return
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        subprocess.run(
            ["arp", "-d", ip],
            check=False,
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception:
        logger.debug("ARP refresh skipped host=%s", ip)
    wake_neighbor(ip)


def _iter_local_ipv4() -> list[tuple[str, str]]:
    """Return (ip, netmask) for non-loopback, non-link-local IPv4 NICs."""
    out: list[tuple[str, str]] = []
    try:
        import psutil  # type: ignore
    except Exception:
        return out
    try:
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                family = getattr(addr, "family", None)
                # Windows may report AF_INET as 2
                if family not in (socket.AF_INET, 2):
                    continue
                ip = str(getattr(addr, "address", "") or "").strip()
                mask = str(getattr(addr, "netmask", "") or "").strip()
                if not ip or not mask:
                    continue
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                out.append((ip, mask))
    except Exception:
        return out
    return out


def _udp_route_local(peer: str) -> str:
    """OS default-route source IP for `peer` (may be a VPN NIC — validate before bind)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((str(peer), 1))
        ip = str(sock.getsockname()[0] or "")
        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
            return ip
    except OSError:
        return ""
    finally:
        sock.close()
    return ""


def select_local_ipv4(peer: str) -> str:
    """Pick the LAN NIC that shares a subnet with the biometric terminal.

    CRITICAL: never bind to a VPN / other-subnet NIC (e.g. 10.x) when the
    terminal is on 192.168.100.x — that forced wrong-interface SYNs and made
    TCP connect time out even when the device was online.
    """
    peer_s = str(peer or "").strip()
    if not peer_s:
        return ""
    try:
        import ipaddress

        peer_ip = ipaddress.ip_address(peer_s)
    except ValueError:
        return ""

    # 1) Prefer any local address whose subnet contains the peer.
    for ip, mask in _iter_local_ipv4():
        try:
            import ipaddress

            net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
            if peer_ip in net:
                return ip
        except ValueError:
            continue

    # 2) UDP route hint — accept only when it is same-subnet.
    routed = _udp_route_local(peer_s)
    if routed:
        for ip, mask in _iter_local_ipv4():
            if ip != routed:
                continue
            try:
                import ipaddress

                net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
                if peer_ip in net:
                    return routed
            except ValueError:
                break
        # Wrong-subnet default route (VPN): do not bind — let the OS choose.
        logger.info(
            "TCP_BIND_SKIP peer=%s routed_local=%s reason=wrong_subnet",
            peer_s,
            routed,
        )
        return ""
    return ""


def open_tcp_socket(host: str, port: int, timeout: float) -> socket.socket:
    """TCP connect bound only to a same-subnet LAN NIC (never a VPN NIC)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(float(timeout))
    local = select_local_ipv4(host)
    if local:
        try:
            sock.bind((local, 0))
        except OSError:
            pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass
    # Detect dead peers in tens of seconds (default OS keepalive is ~2 hours).
    try:
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        if hasattr(socket, "TCP_KEEPINTVL"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        if hasattr(socket, "TCP_KEEPCNT"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        elif sys.platform == "win32":
            # Windows: SIO_KEEPALIVE_VALS — onoff, keepalivetime(ms), keepaliveinterval(ms)
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30_000, 10_000))
    except (OSError, AttributeError, ValueError):
        pass
    try:
        sock.connect((str(host), int(port)))
    except OSError:
        abortive_close(sock)
        raise
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    return sock


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class ZKTransport:
    """
    Low-level send/recv engine.

    TCP: official 5050827D framing with stream reassembly.
    UDP: raw payload datagrams (no TCP top).
    """

    def __init__(
        self,
        ip: str,
        port: int,
        *,
        force_udp: bool = False,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff: float = 0.4,
        connect_attempts: int = TCP_CONNECT_ATTEMPTS,
        session: Optional[ZKSession] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.ip = ip
        self.port = int(port)
        self.force_udp = bool(force_udp)
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff = float(retry_backoff)
        self.connect_attempts = max(1, int(connect_attempts))
        self.session = session or ZKSession()
        self.cancel_check = cancel_check
        self._sock: Optional[socket.socket] = None
        self._rx_buffer = bytearray()
        self._owns_port_lock = False

    @property
    def stats(self) -> ProtocolStats:
        return self.session.stats

    @property
    def connected(self) -> bool:
        return self._sock is not None and self.session.state in (
            SessionState.CONNECTING,
            SessionState.AUTHENTICATING,
            SessionState.ESTABLISHED,
            SessionState.RECOVERING,
        )

    def open(self) -> None:
        # Wait long enough for a peer that is mid-SYN (attempts × timeout).
        lock_timeout = max(8.0, float(self.connect_timeout) * float(self.connect_attempts) + 2.0)
        if not self._owns_port_lock:
            DEVICE_PORT_LOCKS.begin_priority_wait(self.ip, self.port)
            try:
                DEVICE_PORT_LOCKS.acquire(
                    self.ip,
                    self.port,
                    owner=f"zk-transport:{self.ip}:{self.port}",
                    timeout=lock_timeout,
                )
            except TimeoutError as exc:
                DEVICE_PORT_LOCKS.end_priority_wait(self.ip, self.port)
                raise ZKProtocolError(str(exc)) from exc
            except Exception:
                DEVICE_PORT_LOCKS.end_priority_wait(self.ip, self.port)
                raise
            else:
                DEVICE_PORT_LOCKS.end_priority_wait(self.ip, self.port)
            self._owns_port_lock = True
        self._close_socket(silent=True)
        family = socket.AF_INET
        sock_type = socket.SOCK_DGRAM if self.force_udp else socket.SOCK_STREAM
        mode = "UDP" if self.force_udp else "TCP"
        self.session.stage = ProtocolStage.TCP_CONNECT
        last_exc: Optional[BaseException] = None
        local = "" if self.force_udp else select_local_ipv4(self.ip)
        logger.info(
            "[%s] TCP_CONNECT_START host=%s port=%s mode=%s timeout=%s attempts=%s local=%s",
            _ts(),
            self.ip,
            self.port,
            mode.lower(),
            self.connect_timeout,
            self.connect_attempts,
            local or "-",
        )
        # Populate ARP before the first SYN — do not arp -d here.
        if not self.force_udp:
            wake_neighbor(self.ip)
        for attempt in range(1, self.connect_attempts + 1):
            if self._cancelled():
                raise ZKProtocolError("Operation cancelled")
            sock = socket.socket(family, sock_type)
            sock.settimeout(self.connect_timeout)
            if local:
                try:
                    sock.bind((local, 0))
                except OSError:
                    pass
            if not self.force_udp:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except OSError:
                    pass
            try:
                sock.connect((self.ip, self.port))
                if not self.force_udp:
                    try:
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except OSError:
                        pass
                    DEVICE_PORT_LOCKS.mark_established(self.ip, self.port)
                self._sock = sock
                self._rx_buffer.clear()
                logger.info(
                    "[%s] TCP_CONNECT_SUCCESS host=%s port=%s result=SUCCESS mode=%s attempt=%s local=%s",
                    _ts(),
                    self.ip,
                    self.port,
                    mode.lower(),
                    attempt,
                    local or "-",
                )
                if POST_TCP_CONNECT_DELAY_SECONDS > 0:
                    time.sleep(POST_TCP_CONNECT_DELAY_SECONDS)
                return
            except OSError as exc:
                last_exc = exc
                abortive_close(sock)
                self.stats.last_error = str(exc)
                self.session.failure_stage = ProtocolStage.TCP_CONNECT.value
                logger.warning(
                    "[%s] TCP_CONNECT_FAIL host=%s port=%s attempt=%s/%s local=%s error=%s",
                    _ts(),
                    self.ip,
                    self.port,
                    attempt,
                    self.connect_attempts,
                    local or "-",
                    exc,
                )
                if attempt < self.connect_attempts:
                    # First retry: drop Incomplete ARP then ping. Later: ping only.
                    if attempt == 1 and not self.force_udp:
                        refresh_neighbor_arp(self.ip)
                    else:
                        wake_neighbor(self.ip)
                    time.sleep(max(self.retry_backoff * attempt, 0.5))
        self.stats.last_error = str(last_exc or "timed out")
        self.session.failure_stage = ProtocolStage.TCP_CONNECT.value
        logger.error(
            "[%s] TCP_CONNECT_FAIL host=%s port=%s result=FAIL error=%s",
            _ts(),
            self.ip,
            self.port,
            last_exc,
        )
        raise ZKProtocolError(f"TCP connect failed: {last_exc}") from last_exc

    def _close_socket(self, *, silent: bool = False) -> None:
        if self._sock is None:
            self._rx_buffer.clear()
            return
        sock = self._sock
        self._sock = None
        abortive_close(sock)
        if not silent:
            logger.info("[%s] DISCONNECT host=%s port=%s", _ts(), self.ip, self.port)
        self._rx_buffer.clear()
        if POST_TCP_CLOSE_DELAY_SECONDS > 0:
            time.sleep(POST_TCP_CLOSE_DELAY_SECONDS)

    def close(self, *, silent: bool = False) -> None:
        self._close_socket(silent=silent)
        if self._owns_port_lock:
            DEVICE_PORT_LOCKS.release(self.ip, self.port)
            self._owns_port_lock = False

    def set_timeout(self, timeout: float) -> None:
        if self._sock is not None:
            self._sock.settimeout(float(timeout))

    def _ensure_sock(self) -> socket.socket:
        if self._sock is None:
            raise ZKProtocolError("Socket not connected")
        return self._sock

    def _cancelled(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())

    def send_command(
        self,
        command: int,
        data: bytes = b"",
        *,
        response_size: int = 1032,
        expect_reply: bool = True,
    ) -> ZKPacket:
        if self._cancelled():
            raise ZKProtocolError("Operation cancelled")
        if self._sock is None:
            raise ZKProtocolError("Socket not connected")
        if command not in PRE_SESSION_COMMANDS and self.session.state not in (
            SessionState.CONNECTING,
            SessionState.AUTHENTICATING,
            SessionState.ESTABLISHED,
            SessionState.RECOVERING,
        ):
            raise ZKProtocolError("Session not connected")

        last_exc: Optional[Exception] = None
        attempts = self.max_retries
        for attempt in range(1, attempts + 1):
            try:
                return self._send_command_once(
                    command,
                    data,
                    response_size=response_size,
                    expect_reply=expect_reply,
                )
            except (ZKProtocolError, TimeoutError, OSError, socket.timeout) as exc:
                last_exc = exc
                self.stats.retry_count += 1
                self.stats.last_error = str(exc)
                if isinstance(exc, (TimeoutError, socket.timeout)):
                    self.stats.timeout_count += 1
                logger.warning(
                    "Command %s attempt %s/%s failed: %s",
                    command_name(command),
                    attempt,
                    attempts,
                    exc,
                    exc_info=True,
                )
                if attempt >= attempts or self._cancelled():
                    break
                # Peer close leaves a half-dead socket; reopen before retrying
                # pre-session commands (CONNECT/AUTH). Post-session commands must
                # re-handshake via ZKClient.reconnect — do not silently reopen.
                closed = "Connection closed" in str(exc)
                if closed and command in PRE_SESSION_COMMANDS:
                    logger.info("Reopening socket after peer close before retry")
                    try:
                        self.open()
                    except Exception as reopen_exc:
                        logger.exception("Socket reopen failed: %s", reopen_exc)
                        break
                time.sleep(self.retry_backoff * attempt)
        assert last_exc is not None
        raise ZKProtocolError(str(last_exc)) from last_exc

    def _send_command_once(
        self,
        command: int,
        data: bytes,
        *,
        response_size: int,
        expect_reply: bool,
    ) -> ZKPacket:
        sock = self._ensure_sock()
        payload = encode_payload(
            command,
            self.session.session_id,
            self.session.reply_id,
            data,
            increment_reply=True,
        )
        wire = payload if self.force_udp else wrap_tcp(payload)

        started = time.perf_counter()
        sock.settimeout(self.read_timeout if self.session.authenticated or self.session.state == SessionState.ESTABLISHED else self.connect_timeout)

        # Protocol proof: every TX byte before send (continuous uppercase hex).
        tx_hex = raw_hex(wire)
        self.stats.last_tx_hex = tx_hex
        self.session.last_command_name = command_name(command)
        logger.info("TX RAW HEX:\n%s", tx_hex)
        logger.info(
            "[%s] PROTOCOL_COMMAND command=%s session=%s reply_base=%s bytes=%s",
            _ts(),
            command_name(command),
            self.session.session_id,
            self.session.reply_id,
            len(wire),
        )
        # sendall guarantees the full framed packet leaves the socket buffer
        sock.sendall(wire)

        self.stats.packets_sent += 1
        self.stats.bytes_sent += len(wire)
        self.stats.last_command = command

        if not expect_reply:
            return ZKPacket(command=command, checksum=0, session_id=self.session.session_id, reply_id=self.session.reply_id)

        try:
            packet = self._recv_packet(min_payload=8, hint_size=response_size)
        except ZKProtocolError as exc:
            if "Connection closed by device" in str(exc):
                logger.error(
                    "[%s] DEVICE_CONNECTION_CLOSED command=%s stage=%s",
                    _ts(),
                    command_name(command),
                    self.session.stage.value,
                )
                logger.error("Socket closed immediately after TX packet.")
                logger.error("TX that preceded close:\nTX RAW HEX:\n%s", tx_hex)
            raise
        rtt = (time.perf_counter() - started) * 1000.0
        self.stats.record_rtt(rtt)
        self.session.reply_id = packet.reply_id
        if packet.session_id:
            self.session.session_id = packet.session_id
        self.session.touch()
        self.stats.last_reply = packet.command
        logger.info(
            "[%s] DEVICE_RESPONSE cmd=%s session=%s reply=%s rtt_ms=%.1f",
            _ts(),
            packet.command_label,
            packet.session_id,
            packet.reply_id,
            rtt,
        )

        # Large buffered responses — device follows with framed CMD_DATA (+ ACK)
        if packet.command == CMD_PREPARE_DATA and len(packet.data) >= 4:
            size = struct.unpack("<I", packet.data[:4])[0]
            logger.info("Prepare Data size=%s (expecting framed CMD_DATA next)", size)
            body = self._recv_prepare_data_body(size)
            logger.info("Prepare Data body received bytes=%s", len(body))
            # Trailing ACK often follows as another framed packet
            try:
                sock.settimeout(0.5)
                ack = self._recv_packet(min_payload=8, hint_size=16)
                self.session.reply_id = ack.reply_id
                logger.info(
                    "Prepare Data trailing ACK cmd=%s reply=%s",
                    ack.command_label,
                    ack.reply_id,
                )
            except Exception as ack_exc:
                logger.debug("No trailing ACK after prepare data: %s", ack_exc)
            finally:
                sock.settimeout(self.read_timeout)
            return ZKPacket(
                command=CMD_DATA,
                checksum=packet.checksum,
                session_id=packet.session_id or self.session.session_id,
                reply_id=packet.reply_id,
                data=body,
            )
        return packet

    def _recv_packet(self, *, min_payload: int, hint_size: int) -> ZKPacket:
        if self.force_udp:
            return self._recv_udp(hint_size)
        return self._recv_tcp_frame()

    def _recv_udp(self, hint_size: int) -> ZKPacket:
        sock = self._ensure_sock()
        sock.settimeout(self.read_timeout)
        chunk = sock.recv(max(hint_size, 1024))
        if not chunk:
            self.stats.dropped_packets += 1
            logger.error("Socket closed immediately after TX packet.")
            logger.error(
                "[%s] DEVICE_CONNECTION_CLOSED command=%s stage=%s",
                _ts(),
                self.session.last_command_name or "unknown",
                self.session.stage.value,
            )
            raise ZKProtocolError("Connection closed by device")
        # Protocol proof: every RX byte before parsing.
        rx_hex = raw_hex(chunk)
        self.stats.last_rx_hex = rx_hex
        logger.info("RX RAW HEX:\n%s", rx_hex)
        self.stats.packets_received += 1
        self.stats.bytes_received += len(chunk)
        try:
            return decode_payload(chunk, require_checksum=True)
        except ZKProtocolError:
            self.stats.checksum_failures += 1
            raise

    def _recv_tcp_frame(self) -> ZKPacket:
        sock = self._ensure_sock()
        sock.settimeout(self.read_timeout)
        deadline = time.time() + self.read_timeout
        while True:
            if self._cancelled():
                raise ZKProtocolError("Operation cancelled")
            if time.time() > deadline:
                self.stats.timeout_count += 1
                raise ZKProtocolError("Timed out waiting for framed reply")

            try:
                payload, remaining = try_extract_tcp_frame(bytes(self._rx_buffer))
            except ZKProtocolError as exc:
                self.stats.framing_errors += 1
                self._rx_buffer.clear()
                raise

            if payload is not None:
                self._rx_buffer = bytearray(remaining)
                framed = wrap_tcp(payload)
                rx_hex = raw_hex(framed)
                self.stats.last_rx_hex = rx_hex
                logger.info("RX RAW HEX:\n%s", rx_hex)
                self.stats.packets_received += 1
                self.stats.bytes_received += 8 + len(payload)
                try:
                    return decode_payload(payload, require_checksum=True)
                except ZKProtocolError:
                    self.stats.checksum_failures += 1
                    raise

            # Need more bytes
            try:
                chunk = sock.recv(4096)
            except socket.timeout as exc:
                self.stats.timeout_count += 1
                raise ZKProtocolError("Timed out waiting for device reply") from exc
            if not chunk:
                self.stats.dropped_packets += 1
                if not self._rx_buffer:
                    logger.error("Socket closed immediately after TX packet.")
                else:
                    logger.error(
                        "Socket closed mid-frame after partial RX (%s bytes): RX RAW HEX:\n%s",
                        len(self._rx_buffer),
                        raw_hex(bytes(self._rx_buffer)),
                    )
                logger.error(
                    "[%s] DEVICE_CONNECTION_CLOSED command=%s stage=%s partial_rx=%s",
                    _ts(),
                    self.session.last_command_name or "unknown",
                    self.session.stage.value,
                    len(self._rx_buffer),
                )
                raise ZKProtocolError("Connection closed by device")
            # Log raw chunk as it arrives (before frame assembly / parse)
            logger.info("RX CHUNK RAW HEX:\n%s", raw_hex(chunk))
            self._rx_buffer.extend(chunk)

    def _recv_prepare_data_body(self, size: int) -> bytes:
        """
        Receive `size` bytes of application data after CMD_PREPARE_DATA.

        Matches pyzk `__recieve_chunk` / `__recieve_tcp_data` for TCP:
        devices send one or more frames shaped as:
          [50 50 82 7D][len=8+N][CMD_DATA header 8][N body bytes]
        where N is typically the prepare-data size (single frame) or a chunk.

        CRITICAL: never recv(exact payload size). The on-wire frame is
        payload_size + 16 (TCP top 8 + ZK header 8). Requesting only `size`
        bytes truncates the frame, desynchronizes the stream, and leads to
        timeouts / "Connection closed by device" on the next command.
        """
        if size <= 0:
            return b""
        sock = self._ensure_sock()
        sock.settimeout(self.read_timeout)
        deadline = time.time() + self.read_timeout
        buf = bytearray(self._rx_buffer)
        self._rx_buffer.clear()

        def _pull_more() -> None:
            if self._cancelled():
                raise ZKProtocolError("Operation cancelled")
            if time.time() > deadline:
                self.stats.timeout_count += 1
                raise ZKProtocolError(
                    f"Timed out waiting for prepare-data body ({len(buf)} buffered, need {size})"
                )
            try:
                chunk = sock.recv(4096)
            except socket.timeout as exc:
                self.stats.timeout_count += 1
                raise ZKProtocolError(
                    f"Timed out waiting for prepare-data body ({len(buf)} buffered, need {size})"
                ) from exc
            if not chunk:
                logger.error(
                    "Socket closed during prepare-data body. Buffered RX RAW HEX:\n%s",
                    raw_hex(bytes(buf)),
                )
                raise ZKProtocolError("Connection closed by device during data transfer")
            logger.info("RX CHUNK RAW HEX:\n%s", raw_hex(chunk))
            buf.extend(chunk)

        # Need at least magic+length to decide framed vs raw
        while len(buf) < 8:
            _pull_more()

        magic1, magic2, _length = struct.unpack_from("<HHI", buf, 0)
        framed = (
            magic1 == MACHINE_PREPARE_DATA_1 and magic2 == MACHINE_PREPARE_DATA_2
        )

        if not framed:
            # Legacy / UDP-style: raw stream of exactly `size` bytes
            logger.info("Prepare-data body appears unframed; reading %s raw bytes", size)
            while len(buf) < size:
                _pull_more()
            body = bytes(buf[:size])
            self._rx_buffer = bytearray(buf[size:])
            logger.info("RX RAW HEX (prepare body):\n%s", raw_hex(body))
            return body

        collected = bytearray()
        while len(collected) < size:
            while len(buf) < 8:
                _pull_more()
            magic1, magic2, length = struct.unpack_from("<HHI", buf, 0)
            if magic1 != MACHINE_PREPARE_DATA_1 or magic2 != MACHINE_PREPARE_DATA_2:
                raise ZKProtocolError(
                    f"Expected framed CMD_DATA after PREPARE_DATA, got magic "
                    f"{buf[:8].hex(' ')} (collected={len(collected)}/{size})"
                )
            if length < 8 or length > 10_000_000:
                raise ZKProtocolError(f"Invalid CMD_DATA frame length: {length}")
            total = 8 + length
            while len(buf) < total:
                _pull_more()

            frame = bytes(buf[:total])
            del buf[:total]
            logger.info("RX RAW HEX:\n%s", raw_hex(frame))
            self.stats.packets_received += 1
            self.stats.bytes_received += len(frame)
            self.stats.last_rx_hex = raw_hex(frame)

            payload = frame[8:]  # ZK header + body
            cmd = struct.unpack_from("<H", payload, 0)[0]
            if cmd == CMD_ACK_OK and not collected:
                # Spurious ACK before data — ignore and keep reading
                logger.info("Ignoring ACK_OK before CMD_DATA body")
                continue
            if cmd != CMD_DATA:
                raise ZKProtocolError(
                    f"Expected CMD_DATA after PREPARE_DATA, got {command_name(cmd)}"
                )
            chunk_body = payload[8:]
            take = min(len(chunk_body), size - len(collected))
            collected.extend(chunk_body[:take])
            leftover_in_payload = chunk_body[take:]
            if leftover_in_payload:
                # Should not happen for well-sized frames; keep for safety
                buf[0:0] = leftover_in_payload

        self._rx_buffer = bytearray(buf)
        logger.info(
            "Prepare-data body complete bytes=%s remaining_buf=%s",
            len(collected),
            len(self._rx_buffer),
        )
        return bytes(collected[:size])

    # Backward-compatible alias used by older call sites / tests
    def _recv_raw_size(self, size: int) -> bytes:
        return self._recv_prepare_data_body(size)
