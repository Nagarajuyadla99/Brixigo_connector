"""Central JSON serializer for API / persistence payloads.

Internal application code keeps Python ``datetime`` / ``date`` objects.
This module is the only place those values become JSON strings.

Convention
----------
* Timezone-aware UTC  → ``2026-08-26T10:36:32Z``
* Timezone-aware other → ISO-8601 with offset, e.g. ``2026-08-26T16:06:32+05:30``
* Naive datetime      → ISO-8601 without invented offset, unless a named
  ``default_timezone`` is supplied (device timezone). Wall-clock time is
  preserved; the zone is attached, never converted.
* ``datetime.date``   → ``YYYY-MM-DD``

K90 / ZK attendance timestamps are naive device-local values produced by
``_decode_time``. They are not UTC. Do not treat them as UTC.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

try:
    import orjson
except ImportError:  # pragma: no cover - orjson is a production dependency
    orjson = None  # type: ignore[assignment]


class JSONSerializationError(TypeError):
    """Raised when a payload contains a value that cannot be serialized."""


# Named zones with a fixed offset used only when zoneinfo is unavailable.
# Asia/Kolkata is the connector's default device timezone (no DST).
_FIXED_OFFSETS: dict[str, timezone] = {
    "Asia/Kolkata": timezone(timedelta(hours=5, minutes=30)),
    "Asia/Calcutta": timezone(timedelta(hours=5, minutes=30)),
    "UTC": timezone.utc,
    "Etc/UTC": timezone.utc,
    "Z": timezone.utc,
}


def resolve_timezone(name: str | None) -> Optional[timezone | Any]:
    """Resolve a timezone name. Returns None rather than inventing an offset."""
    label = (name or "").strip()
    if not label:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(label)
    except Exception:
        return _FIXED_OFFSETS.get(label)


def localize_naive(dt: datetime, tz_name: str | None) -> datetime:
    """Attach ``tz_name`` to a naive datetime without converting wall-clock time."""
    if dt.tzinfo is not None:
        return dt
    tzinfo = resolve_timezone(tz_name)
    if tzinfo is None:
        return dt
    return dt.replace(tzinfo=tzinfo)


def parse_datetime(value: Any) -> datetime | date | None:
    """Parse a datetime/date/ISO string. Returns None if the value is not temporal."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    normalized = text.replace("Z", "+00:00")
    if "T" not in normalized[:19] and " " in normalized:
        normalized = normalized.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def datetime_to_iso(value: datetime | date, *, default_timezone: str | None = None) -> str:
    """Serialize a datetime/date to the project API timestamp convention."""
    if isinstance(value, datetime):
        dt = localize_naive(value, default_timezone)
        if dt.tzinfo is not None:
            iso = dt.isoformat(timespec="seconds")
            if iso.endswith("+00:00"):
                return iso[:-6] + "Z"
            return iso
        return dt.isoformat(timespec="seconds")
    return value.isoformat()


UNSET_DEVICE_CLOCK_YEAR = 2010
# Align time-of-day when the terminal RTC is more than two minutes off.
CLOCK_TIME_ALIGN_SECONDS = 120


def _as_datetime(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if isinstance(parsed, datetime):
        return parsed
    if isinstance(parsed, date):
        return datetime(parsed.year, parsed.month, parsed.day)
    return None


def _wall_in_zone(dt: datetime, zone) -> datetime:
    """Naive wall clock in ``zone``. Naive values are already wall-clock."""
    if zone is not None and dt.tzinfo is not None:
        return dt.astimezone(zone).replace(tzinfo=None)
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def correct_unset_device_clock(
    value: Any,
    collected_at: Any = None,
    *,
    default_timezone: str | None = None,
    device_now: Any = None,
    drift_threshold_seconds: float = CLOCK_TIME_ALIGN_SECONDS,
) -> datetime | None:
    """Correct ZK/eSSL punches when the terminal clock is unset or drifted.

    Unset terminals often stamp punches as year 2000/2002. When ``device_now``
    (or a batch anchor) is supplied, every punch is shifted by
    ``collector_clock − device_now`` so **relative dates and times are kept**
    — history does not collapse onto a single day.

    Without an anchor, only the calendar day is taken from collection time and
    the device time-of-day is preserved (not snapped to the collection second).
    """
    parsed = _as_datetime(value)
    if parsed is None:
        return None
    parsed = localize_naive(parsed, default_timezone)
    collected = _as_datetime(collected_at)
    if collected is None:
        collected = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    elif collected.tzinfo is None:
        collected = localize_naive(collected, default_timezone)
    zone = parsed.tzinfo
    punch_local = _wall_in_zone(parsed, zone)
    collected_local = _wall_in_zone(collected, zone).replace(microsecond=0)
    ref = _as_datetime(device_now)
    if ref is not None:
        ref = localize_naive(ref, default_timezone)
        device_local = _wall_in_zone(ref, zone)
    else:
        device_local = punch_local

    punch_unset = punch_local.year < UNSET_DEVICE_CLOCK_YEAR
    ref_unset = device_local.year < UNSET_DEVICE_CLOCK_YEAR
    if parsed.year >= UNSET_DEVICE_CLOCK_YEAR and (ref is None or ref_unset):
        # Modern punch: never shift it using an unset-era RTC/log anchor.
        return parsed

    if punch_unset and ref is not None and not ref_unset:
        # Terminal RTC is already modern but the attendance buffer still has
        # year-2000/2002 stamps. Offset from modern RTC is ~0 and would leave
        # punches on 2000 — use the real collection clock instead.
        if parsed.tzinfo is not None:
            return collected_local.replace(tzinfo=parsed.tzinfo)
        return localize_naive(collected_local, default_timezone)

    unset = punch_unset or ref_unset
    if unset and ref is not None and ref_unset:
        # Uniform offset preserves multi-day history (do not force same day first).
        try:
            offset = collected_local - device_local.replace(microsecond=0)
            punch_local = punch_local.replace(microsecond=0) + offset
        except TypeError:
            punch_local = punch_local.replace(
                year=collected_local.year,
                month=collected_local.month,
                day=collected_local.day,
            )
        punch_local = punch_local.replace(microsecond=0)
        if parsed.tzinfo is not None:
            return punch_local.replace(tzinfo=parsed.tzinfo)
        return localize_naive(punch_local, default_timezone)

    if unset:
        # No usable unset-era anchor (common after RTC sync): use collection clock.
        if parsed.tzinfo is not None:
            return collected_local.replace(tzinfo=parsed.tzinfo)
        return localize_naive(collected_local, default_timezone)

    # Modern stamp with optional small drift vs device_now.
    try:
        drift_seconds = (device_local - collected_local).total_seconds()
    except TypeError:
        drift_seconds = 0.0
    if ref is not None and abs(drift_seconds) > float(drift_threshold_seconds):
        punch_local = punch_local - timedelta(seconds=drift_seconds)
    punch_local = punch_local.replace(microsecond=0)
    if parsed.tzinfo is not None:
        return punch_local.replace(tzinfo=parsed.tzinfo)
    return localize_naive(punch_local, default_timezone)


def format_api_datetime(value: Any, *, default_timezone: str | None = None) -> str:
    """Normalize a datetime, date, or timestamp string to API ISO-8601."""
    parsed = parse_datetime(value)
    if parsed is None:
        return str(value) if value is not None else ""
    return datetime_to_iso(parsed, default_timezone=default_timezone)


def to_json_value(obj: Any, *, default_timezone: str | None = None) -> Any:
    """Recursively convert datetimes/dates inside dict/list/tuple graphs."""
    if isinstance(obj, datetime):
        return datetime_to_iso(obj, default_timezone=default_timezone)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): to_json_value(v, default_timezone=default_timezone) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_value(v, default_timezone=default_timezone) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    raise JSONSerializationError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )


class APIJSONEncoder(json.JSONEncoder):
    """stdlib encoder used when orjson is unavailable. Datetime/date only."""

    def __init__(self, *args: Any, default_timezone: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.default_timezone = default_timezone

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return datetime_to_iso(o, default_timezone=self.default_timezone)
        if isinstance(o, date):
            return o.isoformat()
        raise JSONSerializationError(
            f"Object of type {type(o).__name__} is not JSON serializable"
        )


def dumps(obj: Any, *, default_timezone: str | None = None) -> str:
    """Serialize ``obj`` to a compact JSON string with explicit datetime handling."""
    return dumps_bytes(obj, default_timezone=default_timezone).decode("utf-8")


def dumps_bytes(obj: Any, *, default_timezone: str | None = None) -> bytes:
    """Serialize ``obj`` to compact UTF-8 JSON bytes."""
    try:
        prepared = to_json_value(obj, default_timezone=default_timezone)
    except JSONSerializationError:
        raise
    except TypeError as exc:
        raise JSONSerializationError(str(exc)) from exc
    if orjson is not None:
        try:
            return orjson.dumps(prepared)
        except TypeError as exc:
            raise JSONSerializationError(str(exc)) from exc
    text = json.dumps(
        prepared,
        separators=(",", ":"),
        ensure_ascii=False,
        cls=APIJSONEncoder,
    )
    return text.encode("utf-8")
