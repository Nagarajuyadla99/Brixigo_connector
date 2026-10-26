"""Correct unset ZK/eSSL device clocks (year 2000) onto the collection clock."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AttendancePunch

UNSET_DEVICE_CLOCK_YEAR = 2010
CLOCK_TIME_ALIGN_SECONDS = 120


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if "T" not in text[:19] and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _wall_in_zone(dt: datetime, zone) -> datetime:
    if zone is not None and dt.tzinfo is not None:
        return dt.astimezone(zone).replace(tzinfo=None)
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def correct_unset_device_clock(value: Any, collected_at: Any = None) -> datetime | None:
    parsed = _as_datetime(value)
    if parsed is None:
        return None
    if parsed.year >= UNSET_DEVICE_CLOCK_YEAR:
        return parsed
    collected = _as_datetime(collected_at)
    if collected is None:
        collected = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    if collected.tzinfo is None and parsed.tzinfo is not None:
        collected = collected.replace(tzinfo=parsed.tzinfo)
    zone = parsed.tzinfo
    collected_local = _wall_in_zone(collected, zone).replace(microsecond=0)
    # Always use the real collection clock for unset-era stamps — never leave
    # year-2000/2002 values in stored or displayed punch times.
    if parsed.tzinfo is not None:
        return collected_local.replace(tzinfo=parsed.tzinfo)
    return collected_local


def format_punch_iso(value: datetime) -> str:
    if value.tzinfo is not None:
        iso = value.isoformat(timespec="seconds")
        if iso.endswith("+00:00"):
            return iso[:-6] + "Z"
        return iso
    return value.isoformat(timespec="seconds")


def rewrite_unset_clock_punches(db: Session) -> int:
    updated = 0
    for row in db.scalars(select(AttendancePunch)).all():
        meta = dict(row.metadata_json or {})
        source = meta.get("device_punch_time") or row.punch_time
        if not _year_before(source, UNSET_DEVICE_CLOCK_YEAR) and not _year_before(
            row.punch_time, UNSET_DEVICE_CLOCK_YEAR
        ):
            continue
        fixed = correct_unset_device_clock(source, row.created_at)
        if fixed is None:
            continue
        new_iso = format_punch_iso(fixed)
        if new_iso == row.punch_time:
            continue
        meta.setdefault("device_punch_time", row.punch_time)
        meta["clock_corrected"] = True
        row.metadata_json = meta
        row.punch_time = new_iso
        updated += 1
    if updated:
        db.flush()
    return updated


def _year_before(punch_time: str, year: int) -> bool:
    prefix = str(punch_time or "")[:4]
    return prefix.isdigit() and int(prefix) < year
