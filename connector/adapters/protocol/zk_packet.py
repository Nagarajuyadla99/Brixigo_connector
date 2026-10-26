"""Reusable ZK packet encoder / decoder with TCP framing."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from adapters.protocol.zk_checksum import create_checksum, verify_checksum
from adapters.protocol.zk_const import (
    MACHINE_PREPARE_DATA_1,
    MACHINE_PREPARE_DATA_2,
    USHRT_MAX,
    command_name,
)


class ZKProtocolError(Exception):
    """Protocol / framing / checksum failures."""


@dataclass(frozen=True)
class ZKPacket:
    command: int
    checksum: int
    session_id: int
    reply_id: int
    data: bytes = b""

    @property
    def command_label(self) -> str:
        return command_name(self.command)

    @property
    def payload(self) -> bytes:
        return encode_payload(
            self.command,
            self.session_id,
            self.reply_id,
            self.data,
            checksum=self.checksum,
            increment_reply=False,
        )


def encode_payload(
    command: int,
    session_id: int,
    reply_id: int,
    data: bytes = b"",
    *,
    checksum: Optional[int] = None,
    increment_reply: bool = True,
) -> bytes:
    """
    Build the 8-byte command header + optional data.

    When increment_reply is True (normal TX path), checksum is computed using
    the pre-increment reply_id, then reply_id is incremented for the on-wire
    header — matching zkemsdk / pyzk createHeader.
    """
    base_reply = int(reply_id) & USHRT_MAX
    buf = bytearray(8 + len(data))
    struct.pack_into("<HHHH", buf, 0, int(command) & USHRT_MAX, 0, int(session_id) & USHRT_MAX, base_reply)
    if data:
        buf[8:] = data
    if checksum is None:
        checksum = create_checksum(bytes(buf))
    struct.pack_into("<H", buf, 2, int(checksum) & USHRT_MAX)
    if increment_reply:
        next_reply = base_reply + 1
        if next_reply >= USHRT_MAX:
            next_reply -= USHRT_MAX
        struct.pack_into("<H", buf, 6, next_reply)
    return bytes(buf)


def decode_payload(payload: bytes, *, require_checksum: bool = True) -> ZKPacket:
    if len(payload) < 8:
        raise ZKProtocolError(f"Payload too short: {len(payload)} bytes")
    if require_checksum and not verify_checksum(payload):
        raise ZKProtocolError("Checksum validation failed")
    command, checksum, session_id, reply_id = struct.unpack_from("<HHHH", payload, 0)
    return ZKPacket(
        command=command,
        checksum=checksum,
        session_id=session_id,
        reply_id=reply_id,
        data=payload[8:],
    )


def wrap_tcp(payload: bytes) -> bytes:
    """Prefix payload with official ZK TCP envelope."""
    top = struct.pack(
        "<HHI",
        MACHINE_PREPARE_DATA_1,
        MACHINE_PREPARE_DATA_2,
        len(payload),
    )
    return top + payload


def peek_tcp_length(buffer: bytes) -> int:
    """
    Return payload length if buffer starts with a valid TCP top, else 0.
    Does not require the full payload to be present.
    """
    if len(buffer) < 8:
        return 0
    magic1, magic2, length = struct.unpack_from("<HHI", buffer, 0)
    if magic1 != MACHINE_PREPARE_DATA_1 or magic2 != MACHINE_PREPARE_DATA_2:
        return 0
    if length < 8 or length > 10_000_000:
        return 0
    return int(length)


def try_extract_tcp_frame(buffer: bytes) -> tuple[Optional[bytes], bytes]:
    """
    Extract one complete framed payload from a TCP stream buffer.

    Returns (payload_or_None, remaining_buffer).
    Raises ZKProtocolError on clear framing corruption.
    """
    if len(buffer) < 8:
        return None, buffer

    magic1, magic2, length = struct.unpack_from("<HHI", buffer, 0)
    if magic1 != MACHINE_PREPARE_DATA_1 or magic2 != MACHINE_PREPARE_DATA_2:
        raise ZKProtocolError(
            f"Invalid TCP framing magic: {buffer[:8].hex(' ')} "
            f"(expected 50 50 82 7D …)"
        )
    if length < 8 or length > 10_000_000:
        raise ZKProtocolError(f"Invalid TCP payload length: {length}")

    total = 8 + length
    if len(buffer) < total:
        return None, buffer

    payload = buffer[8:total]
    remaining = buffer[total:]
    return payload, remaining


def hex_dump(data: bytes, *, limit: int = 64) -> str:
    """Spaced lowercase hex for compact diagnostics (may truncate)."""
    if not data:
        return ""
    clipped = data[:limit]
    suffix = " …" if len(data) > limit else ""
    return clipped.hex(" ") + suffix


def raw_hex(data: bytes) -> str:
    """Full continuous uppercase hex of every byte (protocol proof dumps)."""
    if not data:
        return ""
    return data.hex().upper()
