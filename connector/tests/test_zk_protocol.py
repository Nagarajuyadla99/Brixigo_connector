"""Protocol engine unit tests — framing, checksum, auth, session, parser."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.protocol.zk_auth import make_commkey
from adapters.protocol.zk_checksum import create_checksum, verify_checksum
from adapters.protocol.zk_const import (
    CMD_ACK_OK,
    CMD_AUTH,
    CMD_CONNECT,
    MACHINE_PREPARE_DATA_1,
    MACHINE_PREPARE_DATA_2,
    USHRT_MAX,
)
from adapters.protocol.zk_packet import (
    ZKProtocolError,
    decode_payload,
    encode_payload,
    peek_tcp_length,
    try_extract_tcp_frame,
    wrap_tcp,
)
from adapters.protocol.zk_profiles import resolve_profile
from adapters.protocol.zk_protocol import ZKClient, _make_packet
from adapters.protocol.zk_session import SessionState, ZKSession


def test_tcp_framing_magic_and_length():
    payload = encode_payload(CMD_CONNECT, 0, USHRT_MAX - 1, b"")
    framed = wrap_tcp(payload)
    assert framed[:4] == bytes.fromhex("5050827d")
    magic1, magic2, length = struct.unpack_from("<HHI", framed, 0)
    assert magic1 == MACHINE_PREPARE_DATA_1
    assert magic2 == MACHINE_PREPARE_DATA_2
    assert length == len(payload)
    assert len(framed) == 8 + len(payload)


def test_cmd_connect_matches_official_first_packet_shape():
    """First CONNECT uses reply_id base USHRT_MAX-1 → on-wire reply_id 0."""
    payload = encode_payload(CMD_CONNECT, 0, USHRT_MAX - 1, b"")
    command, checksum, session_id, reply_id = struct.unpack_from("<HHHH", payload, 0)
    assert command == CMD_CONNECT
    assert session_id == 0
    assert reply_id == 0
    assert verify_checksum(payload)
    framed = wrap_tcp(payload)
    # Must NOT start with raw CMD bytes
    assert framed[0:2] != bytes.fromhex("e803")
    assert framed.startswith(bytes.fromhex("5050827d"))


def test_cmd_connect_wire_bytes_match_pyzk_zklib_sdk():
    """
    Byte-for-byte lock of the first TCP CMD_CONNECT packet.

    Official / pyzk / zklib first CONNECT (empty payload, session=0,
    reply_base=USHRT_MAX-1 → on-wire reply_id=0, checksum=0xFC17):
      50 50 82 7D 08 00 00 00 E8 03 17 FC 00 00 00 00
    """
    official = bytes.fromhex("5050827D08000000E80317FC00000000")
    ours = wrap_tcp(encode_payload(CMD_CONNECT, 0, USHRT_MAX - 1, b""))
    assert ours == official
    assert ours.hex().upper() == "5050827D08000000E80317FC00000000"
    # Field checks against SDK framing
    magic1, magic2, length, cmd, chk, sid, rid = struct.unpack_from("<HHIHHHH", ours, 0)
    assert magic1 == MACHINE_PREPARE_DATA_1
    assert magic2 == MACHINE_PREPARE_DATA_2
    assert length == 8  # payload length only (not including TCP top)
    assert cmd == CMD_CONNECT
    assert chk == 0xFC17
    assert sid == 0
    assert rid == 0
    # Checksum computed on pre-increment reply_id (SDK createHeader)
    pre = struct.pack("<HHHH", CMD_CONNECT, 0, 0, USHRT_MAX - 1)
    assert create_checksum(pre) == 0xFC17
    assert verify_checksum(ours[8:])


def test_checksum_zkemsdk_compatible_known_vector():
    # Pre-checksum buffer: CMD_CONNECT, chk=0, session=0, reply=USHRT_MAX-1
    buf = struct.pack("<HHHH", CMD_CONNECT, 0, 0, USHRT_MAX - 1)
    chk = create_checksum(buf)
    assert 0 <= chk <= USHRT_MAX
    final = struct.pack("<HHHH", CMD_CONNECT, chk, 0, 0)
    assert verify_checksum(final)


def test_checksum_rejects_corruption():
    payload = encode_payload(CMD_CONNECT, 0, USHRT_MAX - 1, b"")
    corrupt = bytearray(payload)
    corrupt[2] ^= 0xFF
    assert verify_checksum(bytes(corrupt)) is False
    with pytest.raises(ZKProtocolError, match="Checksum"):
        decode_payload(bytes(corrupt), require_checksum=True)


def test_fragmented_tcp_reassembly():
    payload = encode_payload(CMD_ACK_OK, 1234, 5, b"")
    framed = wrap_tcp(payload)
    # Split mid-header and mid-payload
    part1 = framed[:3]
    part2 = framed[3:11]
    part3 = framed[11:]
    buf = part1
    got, buf = try_extract_tcp_frame(buf)
    assert got is None
    buf = buf + part2
    got, buf = try_extract_tcp_frame(buf)
    assert got is None
    buf = buf + part3
    got, remaining = try_extract_tcp_frame(buf)
    assert got == payload
    assert remaining == b""
    packet = decode_payload(got)
    assert packet.command == CMD_ACK_OK
    assert packet.session_id == 1234


def test_malformed_tcp_magic_rejected():
    bad = b"\xe8\x03\xe7\xfb\x00\x00\x01\x00" + b"\x00" * 8
    with pytest.raises(ZKProtocolError, match="framing"):
        try_extract_tcp_frame(bad)


def test_make_commkey_stable_for_zero_and_nonzero():
    k0 = make_commkey(0, 1000)
    assert isinstance(k0, (bytes, bytearray))
    assert len(k0) == 4
    k1 = make_commkey(12345, 1000)
    assert len(k1) == 4
    assert k0 != k1
    # Deterministic
    assert make_commkey(12345, 1000) == k1


def test_auth_command_constant():
    assert CMD_AUTH == 1102
    assert CMD_CONNECT == 1000


def test_session_reply_id_initializes_like_sdk():
    session = ZKSession()
    assert session.reply_id == USHRT_MAX - 1
    session.mark_connecting()
    assert session.state == SessionState.CONNECTING
    assert session.reply_id == USHRT_MAX - 1


def test_profile_resolves_k90_without_hardcoding_protocol():
    profile = resolve_profile(manufacturer="essl", model="K90")
    assert "k90" in profile.code or profile.code.startswith("essl")
    assert profile.prefer_tcp is True
    # Protocol constants remain shared
    assert MACHINE_PREPARE_DATA_1 == 20560


def test_backward_compatible_make_packet_exports():
    packet = _make_packet(CMD_CONNECT, 0, USHRT_MAX - 1, b"")
    assert len(packet) == 8
    assert struct.unpack("<H", packet[0:2])[0] == CMD_CONNECT


def test_peek_tcp_length():
    payload = encode_payload(CMD_CONNECT, 0, USHRT_MAX - 1)
    framed = wrap_tcp(payload)
    assert peek_tcp_length(framed) == 8
    assert peek_tcp_length(framed[:4]) == 0


def test_zkclient_constructs_with_profile():
    client = ZKClient(ip="127.0.0.1", port=4370, password=0, manufacturer="essl", model="K90")
    assert client.protocol_version.startswith("ZK-TCP")
    snap = client.diagnostics_snapshot()
    assert "session_id" in snap
    assert snap["comm_key_is_zero"] is True


def test_recv_prepare_data_body_reads_full_framed_cmd_data():
    """
    Regression: old _recv_raw_size did recv(payload_size) which truncates the
    on-wire frame (payload_size + 16). K90 sends framed CMD_DATA after PREPARE_DATA.
    """
    from adapters.protocol.zk_checksum import create_checksum
    from adapters.protocol.zk_const import CMD_DATA
    from adapters.protocol.zk_packet import wrap_tcp
    from adapters.protocol.zk_transport import ZKTransport

    size = 76
    body = bytes(range(size))
    hdr = bytearray(8 + size)
    struct.pack_into("<HHHH", hdr, 0, CMD_DATA, 0, 44242, 8)
    hdr[8:] = body
    struct.pack_into("<H", hdr, 2, create_checksum(bytes(hdr)))
    framed = wrap_tcp(bytes(hdr))
    assert len(framed) == size + 16  # TCP top 8 + ZK header 8 + body

    # Deliver in the exact truncated chunks the old bug produced: first `size`
    # bytes, then the remaining 16-byte tail.
    chunks = [framed[:size], framed[size:]]
    assert len(chunks[0]) == size
    assert len(chunks[1]) == 16

    class _FakeSock:
        def __init__(self) -> None:
            self._chunks = list(chunks)

        def settimeout(self, _t: float) -> None:
            return None

        def recv(self, _n: int) -> bytes:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    transport = ZKTransport(ip="127.0.0.1", port=4370, connect_timeout=2.0, read_timeout=2.0)
    transport._sock = _FakeSock()  # type: ignore[assignment]
    got = transport._recv_prepare_data_body(size)
    assert got == body


def test_recv_prepare_data_body_raw_unframed_fallback():
    from adapters.protocol.zk_transport import ZKTransport

    body = b"abcdefghijklmnop"
    chunks = [body[:7], body[7:]]

    class _FakeSock:
        def __init__(self) -> None:
            self._chunks = list(chunks)

        def settimeout(self, _t: float) -> None:
            return None

        def recv(self, _n: int) -> bytes:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    transport = ZKTransport(ip="127.0.0.1", port=4370)
    transport._sock = _FakeSock()  # type: ignore[assignment]
    assert transport._recv_prepare_data_body(len(body)) == body


def test_strip_buffer_size_prefix():
    raw = struct.pack("<I", 72) + (b"\x01" * 72)
    assert ZKClient._strip_buffer_size_prefix(raw) == b"\x01" * 72
    assert ZKClient._strip_buffer_size_prefix(b"\x01" * 40) == b"\x01" * 40