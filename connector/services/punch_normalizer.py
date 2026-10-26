"""Normalize vendor punch fields into a stable internal representation.

Biometric terminals (eSSL / ZKTeco / others) expose inconsistent event and
verification codes. This module is the only place those vendor values become
application vocabulary.

Timestamp concepts (never collapse silently):
    device_timestamp      original wall-clock from the terminal
    server_received_at    when the connector stored the record
    normalized_timestamp  application timestamp after documented rules

Clock mismatch is detected and reported. The original device stamp is always
retained in raw_payload.device_punch_time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

from api.json_codec import (
    UNSET_DEVICE_CLOCK_YEAR,
    correct_unset_device_clock,
    format_api_datetime,
    parse_datetime,
)
from contracts.payroll_punch import (
    EVENT_TYPES,
    normalize_event_type,
    normalize_verification_method,
)
from services.punch_classifier import CLASSIFICATION_LABELS

METHOD_LABELS = {
    "fingerprint": "Fingerprint",
    "face": "Face",
    "palm": "Palm",
    "card": "Card",
    "pin": "PIN",
    "password": "Password",
    "qr": "QR",
    "iris": "Iris",
    "vein": "Vein",
    "multi": "Multi-factor",
    "manual": "Manual",
    "other": "Other",
    "not_reported": "Not reported by device",
    "unknown": "Not reported by device",
}

EVENT_LABELS = {
    "IN": "IN",
    "OUT": "OUT",
    "BREAK_OUT": "Break out",
    "BREAK_IN": "Break in",
    "OT_IN": "OT in",
    "OT_OUT": "OT out",
    "UNKNOWN": "Unclassified",
}

UPLOAD_STATUS_LABELS = {
    "pending": "Pending",
    "uploading": "Uploading",
    "uploaded": "Uploaded",
    "failed": "Failed",
    "duplicate": "Duplicate",
    "dead": "Permanently failed",
}

# Drift large enough to treat as an unset / wrong-era device clock.
CRITICAL_CLOCK_DRIFT_SECONDS = 86400 * 30  # 30 days
WARNING_CLOCK_DRIFT_SECONDS = 120


@dataclass(frozen=True)
class ClockAnalysis:
    device_timestamp: str
    server_received_at: str
    normalized_timestamp: str
    mismatch: bool
    critical: bool
    difference_seconds: float
    difference_label: str
    reason: str
    device_year: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_timestamp": self.device_timestamp,
            "server_received_at": self.server_received_at,
            "normalized_timestamp": self.normalized_timestamp,
            "mismatch": self.mismatch,
            "critical": self.critical,
            "difference_seconds": self.difference_seconds,
            "difference_label": self.difference_label,
            "reason": self.reason,
            "device_year": self.device_year,
        }


def method_label(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return METHOD_LABELS["not_reported"]
    return METHOD_LABELS.get(raw, METHOD_LABELS["not_reported"])


def event_label(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return EVENT_LABELS["UNKNOWN"]
    return EVENT_LABELS.get(raw, raw)


def classification_label(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return CLASSIFICATION_LABELS["unknown"]
    return CLASSIFICATION_LABELS.get(raw, raw.replace("_", " ").title())


def event_display(event_type: Any, classification_method: Any = "") -> str:
    """IN/OUT plus whether the device or the system produced it."""
    base = event_label(event_type)
    source = str(classification_method or "").strip().lower()
    if source == "device_reported":
        return f"{base} · device"
    if source == "system_inference":
        return f"{base} · inferred"
    if source == "manual":
        return f"{base} · manual"
    return base


def upload_status_label(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return UPLOAD_STATUS_LABELS.get(raw, raw or "Unknown")


def normalize_method_for_storage(value: Any, *, punch: int | None = None) -> str:
    method = normalize_verification_method(value, punch=punch)
    if method in {"unknown", ""}:
        return "not_reported"
    return method


def normalize_event_for_storage(value: Any, *, status: int | None = None) -> str:
    event = normalize_event_type(value, status=status)
    if event not in EVENT_TYPES:
        return "UNKNOWN"
    return event


def _as_datetime(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if isinstance(parsed, datetime):
        return parsed
    return None


def connector_wall_clock() -> datetime:
    """Naive local wall clock. ZK/eSSL terminals store local time, not UTC."""
    return datetime.now().replace(microsecond=0)


def as_naive_local(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def device_clock_is_modern(device_time: datetime | None) -> bool:
    """True when the terminal RTC is past the ZK unset-clock epoch (year 2000/2002)."""
    if device_time is None:
        return False
    try:
        return as_naive_local(device_time).year >= UNSET_DEVICE_CLOCK_YEAR
    except Exception:
        return False


def device_clock_needs_correction(
    device_time: datetime | None,
    now: datetime | None = None,
    *,
    threshold_seconds: float = 86400,
) -> bool:
    """True when the RTC is unset (year 2000) or drifted beyond threshold."""
    if device_time is None:
        return False
    device = as_naive_local(device_time)
    current = as_naive_local(now or connector_wall_clock())
    if device.year < UNSET_DEVICE_CLOCK_YEAR:
        return True
    try:
        return abs((device - current).total_seconds()) > float(threshold_seconds)
    except TypeError:
        return False


def write_device_clock(
    *,
    get_time: Callable[[], datetime | None],
    set_time: Callable[[datetime], None],
    when: datetime | None = None,
    threshold_seconds: float = 86400,
    write_if_unreadable: bool = False,
) -> dict[str, Any]:
    """Write the PC local clock to a connected terminal when it is wrong.

    Must run on an already-open device session. Year-2000 / pre-2010 clocks
    always qualify; smaller drift uses ``threshold_seconds``.
    """
    now = as_naive_local(when or connector_wall_clock())
    before: datetime | None = None
    try:
        before = get_time()
    except Exception:
        before = None
    if before is None:
        if not write_if_unreadable:
            return {
                "corrected": False,
                "success": True,
                "device_time": None,
                "previous_time": None,
                "drift_seconds": 0.0,
                "message": "Device clock read unsupported",
            }
        set_time(now)
        return {
            "corrected": True,
            "success": True,
            "device_time": now,
            "previous_time": None,
            "drift_seconds": 0.0,
            "message": "Time synced (device clock read unsupported)",
        }
    before_naive = as_naive_local(before)
    if not device_clock_needs_correction(
        before_naive, now, threshold_seconds=threshold_seconds
    ):
        drift = abs((before_naive - now).total_seconds())
        return {
            "corrected": False,
            "success": True,
            "device_time": before_naive,
            "previous_time": before_naive,
            "drift_seconds": drift,
            "message": f"Within threshold ({drift:.1f}s)",
        }
    set_time(now)
    after: datetime | None = None
    try:
        after = get_time()
    except Exception:
        after = None
    after_naive = as_naive_local(after) if after is not None else None
    still_wrong = after_naive is not None and device_clock_needs_correction(
        after_naive, now, threshold_seconds=threshold_seconds
    )
    drift_before = abs((before_naive - now).total_seconds())
    return {
        "corrected": True,
        "success": not still_wrong,
        "device_time": after_naive or now,
        "previous_time": before_naive,
        "drift_seconds": drift_before,
        "message": (
            f"Corrected drift of {drift_before:.1f}s"
            if not still_wrong
            else "Device rejected time write"
        ),
    }


def format_drift(seconds: float) -> str:
    magnitude = abs(float(seconds))
    sign = "ahead" if seconds > 0 else "behind"
    if magnitude < 1:
        return "in sync"
    if magnitude < 60:
        return f"{magnitude:.0f}s {sign} of server"
    if magnitude < 3600:
        return f"{magnitude / 60:.0f} min {sign} of server"
    if magnitude < 86400:
        return f"{magnitude / 3600:.1f} hours {sign} of server"
    years = magnitude / (86400 * 365.25)
    if years >= 1:
        return f"~{years:.0f} years {sign} of server"
    return f"{magnitude / 86400:.0f} days {sign} of server"


def analyze_clock(
    device_timestamp: Any,
    server_received_at: Any = None,
    *,
    default_timezone: str | None = None,
    now: datetime | None = None,
) -> ClockAnalysis:
    """Classify a punch timestamp without discarding the original value."""
    server = _as_datetime(server_received_at) or now or datetime.now(timezone.utc)
    device = _as_datetime(device_timestamp)
    device_iso = format_api_datetime(device_timestamp, default_timezone=default_timezone)
    server_iso = format_api_datetime(server, default_timezone=default_timezone)

    if device is None:
        return ClockAnalysis(
            device_timestamp=device_iso,
            server_received_at=server_iso,
            normalized_timestamp=server_iso,
            mismatch=True,
            critical=False,
            difference_seconds=0.0,
            difference_label="timestamp not reported",
            reason="missing_device_timestamp",
            device_year=None,
        )

    device_year = device.year
    corrected = correct_unset_device_clock(
        device, server, default_timezone=default_timezone
    )
    normalized = corrected or device
    normalized_iso = format_api_datetime(normalized, default_timezone=default_timezone)

    comparable_server = server
    if device.tzinfo is None and comparable_server.tzinfo is not None:
        comparable_server = comparable_server.replace(tzinfo=None)
    elif device.tzinfo is not None and comparable_server.tzinfo is None:
        comparable_server = comparable_server.replace(tzinfo=device.tzinfo)
    try:
        delta = (device - comparable_server).total_seconds()
    except TypeError:
        delta = 0.0

    unset_clock = device_year < UNSET_DEVICE_CLOCK_YEAR
    future = delta > 86400 * 2
    ancient = (not unset_clock) and delta < -CRITICAL_CLOCK_DRIFT_SECONDS
    warning = abs(delta) >= WARNING_CLOCK_DRIFT_SECONDS

    if unset_clock:
        reason = "unset_device_clock"
        mismatch = True
        critical = True
    elif future:
        reason = "impossible_future_timestamp"
        mismatch = True
        critical = True
    elif ancient:
        reason = "extremely_old_timestamp"
        mismatch = True
        critical = True
    elif warning:
        reason = "clock_drift"
        mismatch = True
        critical = False
    else:
        reason = ""
        mismatch = False
        critical = False

    return ClockAnalysis(
        device_timestamp=device_iso,
        server_received_at=server_iso,
        normalized_timestamp=normalized_iso,
        mismatch=mismatch,
        critical=critical,
        difference_seconds=delta,
        difference_label=format_drift(delta),
        reason=reason,
        device_year=device_year,
    )


def enrich_punch_record(item: dict[str, Any]) -> dict[str, Any]:
    """Attach display/normalized fields to a punch dict from the repository."""
    raw = item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}
    device_ts = item.get("device_punch_time") or raw.get("device_punch_time") or item.get("punch_time")
    collected = item.get("created_at") or item.get("uploaded_at")
    clock = analyze_clock(device_ts, collected)
    method = normalize_method_for_storage(item.get("verification_method"))
    event = normalize_event_for_storage(item.get("event_type"))
    item["device_timestamp"] = clock.device_timestamp
    item["server_received_at"] = clock.server_received_at
    # Prefer the stored modern punch_time. Re-deriving from a year-2000/2002
    # device stamp would show fake times like 16:01 on Live/Daily.
    stored = item.get("punch_time") or raw.get("normalized_timestamp")
    stored_dt = _as_datetime(stored)
    if stored_dt is not None and stored_dt.year >= UNSET_DEVICE_CLOCK_YEAR:
        item["normalized_timestamp"] = format_api_datetime(
            stored_dt, default_timezone=None
        )
    else:
        item["normalized_timestamp"] = clock.normalized_timestamp
    item["clock"] = clock.to_dict()
    item["verification_method"] = method
    item["verification_method_label"] = method_label(method)
    item["event_type"] = event
    item["event_label"] = event_label(event)
    item["classification_method"] = str(
        item.get("classification_method") or raw.get("classification_method") or ""
    )
    item["classification_reason"] = str(
        item.get("classification_reason") or raw.get("classification_reason") or ""
    )
    item["classification_reason_code"] = str(
        item.get("classification_reason_code") or raw.get("classification_reason_code") or ""
    )
    item["classification_label"] = classification_label(item["classification_method"])
    item["event_display"] = event_display(event, item["classification_method"])
    item["upload_status_label"] = upload_status_label(item.get("status"))
    item["device_status"] = raw.get("device_status", raw.get("status"))
    item["device_punch"] = raw.get("device_punch", raw.get("punch"))
    item["device_direction"] = raw.get("device_direction")
    return item


def is_retryable_upload_error(error: str) -> bool:
    text = (error or "").lower()
    permanent = (
        "invalid employee",
        "validation",
        "schema",
        "malformed",
        "not json",
        "device missing locally",
    )
    if any(token in text for token in permanent):
        return False
    transient = (
        "timeout",
        "timed out",
        "handshake",
        "ssl",
        "tls",
        "network",
        "temporarily",
        "unavailable",
        "connection reset",
        "503",
        "502",
        "504",
        "500",
        "connection refused",
    )
    return any(token in text for token in transient) or not text
