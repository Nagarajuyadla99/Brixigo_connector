"""Interpret ZK/eSSL attendance bytes without inventing IN/OUT.

ZK-family 40-byte logs expose two vendor bytes commonly named ``status``
and ``punch``. Firmware disagrees about which byte is check-in/out and
which is the verification type. Many eSSL K90 / iClock terminals have no
IN/OUT keys at all: ``status`` is the verify type (1=fingerprint) and
``punch`` is an unused/work-code byte (often 16–255).

This module only interprets those two bytes. Attendance IN/OUT inference
belongs in ``services.punch_classifier``.
"""

from __future__ import annotations

from typing import Any

# Official ZK punch-state vocabulary (when the field is actually a state).
ZK_PUNCH_STATES: dict[int, str] = {
    0: "IN",
    1: "OUT",
    2: "BREAK_OUT",
    3: "BREAK_IN",
    4: "OT_IN",
    5: "OT_OUT",
}

# Common ZK verify-type vocabulary (when the field is actually a method).
ZK_VERIFY_METHODS: dict[int, str] = {
    0: "password",
    1: "fingerprint",
    2: "card",
    3: "fingerprint",
    4: "fingerprint",
    5: "fingerprint",
    6: "card",
    7: "fingerprint",
    8: "face",
    9: "face",
    10: "face",
    11: "face",
    12: "face",
    13: "face",
    14: "face",
    15: "face",
}


def _as_byte(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def interpret_zk_log_fields(status: Any, punch: Any) -> dict[str, Any]:
    """Return a capability-aware reading of one ZK attendance record.

    A constant ``status=1`` with a work-code ``punch`` is fingerprint
    verification, not checkout. ``device_direction`` is only a *candidate*
    for the classifier; ``zk_logs_report_direction`` must also be true.
    """
    status_byte = _as_byte(status)
    punch_byte = _as_byte(punch)
    punch_is_workcode = punch_byte > 15

    method = "not_reported"
    direction: str | None = None
    direction_field = "none"

    if punch_is_workcode:
        if status_byte in ZK_VERIFY_METHODS:
            method = ZK_VERIFY_METHODS[status_byte]
    elif punch_byte in ZK_PUNCH_STATES and status_byte in ZK_VERIFY_METHODS:
        # Common pyzk layout: status = verify, punch = IN/OUT.
        direction = ZK_PUNCH_STATES[punch_byte]
        direction_field = "punch"
        method = ZK_VERIFY_METHODS[status_byte]
    elif status_byte in ZK_PUNCH_STATES and punch_byte in ZK_VERIFY_METHODS:
        # Alternate firmware: status = IN/OUT, punch = verify.
        direction = ZK_PUNCH_STATES[status_byte]
        direction_field = "status"
        method = ZK_VERIFY_METHODS[punch_byte]
    elif status_byte in ZK_VERIFY_METHODS:
        method = ZK_VERIFY_METHODS[status_byte]
    elif punch_byte in ZK_VERIFY_METHODS:
        method = ZK_VERIFY_METHODS[punch_byte]

    return {
        "device_status": status_byte,
        "device_punch": punch_byte,
        "verification_method": method,
        "device_direction": direction,
        "direction_reported": False if punch_is_workcode else direction is not None,
        "direction_field": "none" if punch_is_workcode else direction_field,
    }


def _row_byte(row: dict[str, Any], *keys: str) -> int:
    raw = row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {}
    for key in keys:
        if row.get(key) is not None:
            return _as_byte(row.get(key))
        if raw.get(key) is not None:
            return _as_byte(raw.get(key))
    return 0


def zk_logs_report_direction(rows: list[dict[str, Any]]) -> bool:
    """True when a batch actually varies IN/OUT rather than a constant verify byte."""
    if not rows:
        return False
    punch_bytes = [_row_byte(r, "device_punch", "punch") for r in rows]
    status_bytes = [_row_byte(r, "device_status", "status") for r in rows]
    workcode_ratio = sum(1 for value in punch_bytes if value > 15) / len(punch_bytes)
    if workcode_ratio >= 0.5:
        return False
    punch_states = {value for value in punch_bytes if value in ZK_PUNCH_STATES}
    status_states = {value for value in status_bytes if value in ZK_PUNCH_STATES}
    if {0, 1}.issubset(punch_states):
        return True
    if {0, 1}.issubset(status_states) and workcode_ratio < 0.3:
        return True
    return False
