"""Official ZKTeco checksum (from zkemsdk.c / Standalone SDK)."""

from __future__ import annotations

import struct

from adapters.protocol.zk_const import USHRT_MAX


def create_checksum(payload: bytes) -> int:
    """
    Calculate the 16-bit ZK packet checksum.

    Algorithm matches zkemsdk.c / pyzk / zklib:
    sum little-endian 16-bit words with USHRT_MAX wrap, then ones-complement
    via bitwise NOT normalized into [0, USHRT_MAX).
    """
    data = payload
    length = len(data)
    checksum = 0
    offset = 0
    while length > 1:
        checksum += data[offset] | (data[offset + 1] << 8)
        offset += 2
        if checksum > USHRT_MAX:
            checksum -= USHRT_MAX
        length -= 2
    if length:
        checksum += data[-1]
    while checksum > USHRT_MAX:
        checksum -= USHRT_MAX
    checksum = ~checksum
    while checksum < 0:
        checksum += USHRT_MAX
    return checksum & USHRT_MAX


def verify_checksum(payload: bytes) -> bool:
    """
    Verify a complete payload (header with checksum field already filled).

    Official createHeader computes the checksum using the *pre-increment*
    reply_id, then writes the *post-increment* reply_id into the packet.
    Receivers may also checksum the final on-wire reply_id. We accept either.
    """
    if len(payload) < 8:
        return False
    embedded = struct.unpack_from("<H", payload, 2)[0]

    scratch = bytearray(payload)
    struct.pack_into("<H", scratch, 2, 0)
    if create_checksum(bytes(scratch)) == embedded:
        return True

    # SDK TX quirk: restore pre-increment reply_id then re-check
    reply_id = struct.unpack_from("<H", payload, 6)[0]
    prev = reply_id - 1
    if prev < 0:
        prev += USHRT_MAX
    struct.pack_into("<H", scratch, 6, prev & USHRT_MAX)
    return create_checksum(bytes(scratch)) == embedded
