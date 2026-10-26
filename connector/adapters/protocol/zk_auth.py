"""ZKTeco Comm Key authentication helpers (commpro.c MakeKey)."""

from __future__ import annotations

from struct import pack, unpack


def make_commkey(key: int, session_id: int, ticks: int = 50) -> bytes:
    """
    Build the 4-byte CMD_AUTH payload from communication password + session.

    Copied from official Standalone SDK / commpro.c MakeKey (via pyzk).
    Supports key=0 and non-zero keys.
    """
    key = int(key or 0)
    session_id = int(session_id or 0)
    k = 0
    for i in range(32):
        if key & (1 << i):
            k = (k << 1) | 1
        else:
            k = k << 1
    k += session_id

    parts = unpack("BBBB", pack("I", k & 0xFFFFFFFF))
    parts = pack(
        "BBBB",
        parts[0] ^ ord("Z"),
        parts[1] ^ ord("K"),
        parts[2] ^ ord("S"),
        parts[3] ^ ord("O"),
    )
    words = unpack("HH", parts)
    parts = pack("HH", words[1], words[0])

    tick = 0xFF & int(ticks)
    bytes4 = unpack("BBBB", parts)
    return pack(
        "BBBB",
        bytes4[0] ^ tick,
        bytes4[1] ^ tick,
        tick,
        bytes4[3] ^ tick,
    )
