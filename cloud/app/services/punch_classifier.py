"""Attendance IN/OUT classification on the cloud.

Mirrors the connector classifier so historical punches that were stored as
universal OUT (when the device did not report direction) can be repaired
without inventing device-reported state.

DEVICE_REPORTED punches are never rewritten.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AttendancePunch

CLASSIFICATION_DEVICE = "device_reported"
CLASSIFICATION_INFERENCE = "system_inference"
CLASSIFICATION_MANUAL = "manual"
CLASSIFICATION_UNKNOWN = "unknown"

DUPLICATE_PUNCH_WINDOW_SECONDS = 60

_DEVICE_EVENTS = {"IN", "OUT", "BREAK_OUT", "BREAK_IN", "OT_IN", "OT_OUT"}


def _as_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def session_date_key(value: Any) -> str:
    parsed = _as_datetime(value)
    if parsed is None:
        text = str(value or "")
        return text[:10] if len(text) >= 10 else ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d")


def _method(meta: dict[str, Any] | None) -> str:
    return str((meta or {}).get("classification_method") or "").strip().lower()


def _is_device_reported(meta: dict[str, Any] | None) -> bool:
    method = _method(meta)
    return method in {"device_reported", "device"} or bool((meta or {}).get("direction_reported"))


def classify_inferred_event(
    *,
    previous_event: str | None,
    previous_time: datetime | None,
    punch_time: datetime | None,
) -> tuple[str, str, str]:
    """Return (event_type, method, reason_code) for a punch without device direction."""
    if (
        previous_event in _DEVICE_EVENTS
        and previous_time is not None
        and punch_time is not None
    ):
        try:
            delta = abs((punch_time - previous_time).total_seconds())
        except TypeError:
            naive = punch_time.replace(tzinfo=None) if punch_time.tzinfo else punch_time
            prev = previous_time.replace(tzinfo=None) if previous_time.tzinfo else previous_time
            delta = abs((naive - prev).total_seconds())
        if delta <= DUPLICATE_PUNCH_WINDOW_SECONDS:
            return previous_event, CLASSIFICATION_INFERENCE, "duplicate_window"
    if previous_event in {None, "", "OUT", "BREAK_OUT", "OT_OUT", "UNKNOWN"}:
        return "IN", CLASSIFICATION_INFERENCE, "first_or_after_out"
    return "OUT", CLASSIFICATION_INFERENCE, "alternate_after_in"


def reclassify_inferred_punches(
    db: Session,
    *,
    organisation_id: str | None,
    employee_codes: Iterable[str] | None = None,
) -> int:
    """Rewrite stored IN/OUT when the device never reported direction.

    Scoped to one organisation. Never matches device user IDs globally.
    Device-reported days are left untouched.
    """
    org_id = str(organisation_id or "").strip()
    if not org_id:
        return 0
    stmt = select(AttendancePunch).where(AttendancePunch.organisation_id == org_id)
    codes = {str(c).strip() for c in (employee_codes or []) if str(c).strip()}
    if codes:
        stmt = stmt.where(AttendancePunch.device_employee_code.in_(sorted(codes)))
    stmt = stmt.order_by(AttendancePunch.punch_time.asc(), AttendancePunch.id.asc())
    rows = list(db.scalars(stmt).all())
    groups: dict[tuple[str, str], list[AttendancePunch]] = {}
    for punch in rows:
        day = session_date_key(punch.punch_time)
        groups.setdefault((str(punch.device_employee_code), day), []).append(punch)

    changed = 0
    for group in groups.values():
        if any(_is_device_reported(p.metadata_json if isinstance(p.metadata_json, dict) else {}) for p in group):
            continue
        events = {str(p.event_type or "").upper() for p in group}
        # Historical connector uploads used OUT as a universal fallback when the
        # terminal did not report direction. Only repair those days.
        if not events.issubset({"OUT", "UNKNOWN", ""}):
            continue
        previous_event: str | None = None
        previous_time: datetime | None = None
        for punch in group:
            punch_time = _as_datetime(punch.punch_time)
            event, method, reason = classify_inferred_event(
                previous_event=previous_event,
                previous_time=previous_time,
                punch_time=punch_time,
            )
            meta = dict(punch.metadata_json or {})
            already = (
                str(punch.event_type or "").upper() == event
                and _method(meta) == method
            )
            if not already:
                punch.event_type = event
                meta["classification_method"] = method
                meta["classification_reason_code"] = reason
                meta["direction_reported"] = False
                punch.metadata_json = meta
                changed += 1
            else:
                if _method(meta) != method:
                    meta["classification_method"] = method
                    meta["classification_reason_code"] = reason
                    meta["direction_reported"] = False
                    punch.metadata_json = meta
                    changed += 1
            if reason != "duplicate_window":
                previous_event = event
                previous_time = punch_time
            elif previous_time is None:
                previous_event = event
                previous_time = punch_time
    if changed:
        db.flush()
    return changed
