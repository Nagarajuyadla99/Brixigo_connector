"""Attendance event classification: device-reported vs system inference.

The generic engine never reads manufacturer protocol bytes. It consumes a
normalized punch contract:

    device_direction      IN/OUT/... or None
    direction_reported    True only when the device actually provided it
    punch_time            timezone-aware or naive local ISO
    employee_code         device user id
    organisation_id       tenant (optional; connector is typically one tenant)

When the device does not report direction, punches are classified
independently per tenant + employee + calendar day, in chronological order:

    first punch of the day → IN
    then alternate OUT, IN, OUT, ...
    punches within DUPLICATE_PUNCH_WINDOW_SECONDS keep the previous event
    (they do not create a fake IN/OUT pair)

Missing OUT is left open (last event stays IN). No OUT is invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from api.json_codec import parse_datetime

CLASSIFICATION_DEVICE = "device_reported"
CLASSIFICATION_INFERENCE = "system_inference"
CLASSIFICATION_MANUAL = "manual"
CLASSIFICATION_UNKNOWN = "unknown"

CLASSIFICATION_LABELS = {
    CLASSIFICATION_DEVICE: "Device reported",
    CLASSIFICATION_INFERENCE: "System inferred",
    CLASSIFICATION_MANUAL: "Manual",
    CLASSIFICATION_UNKNOWN: "Unknown",
}

# Repeated finger/face reads on the same terminal must not toggle IN/OUT.
DUPLICATE_PUNCH_WINDOW_SECONDS = 60


@dataclass
class ClassifiedPunch:
    event_type: str
    classification_method: str
    classification_reason: str
    classification_reason_code: str
    direction_reported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "classification_method": self.classification_method,
            "classification_reason": self.classification_reason,
            "classification_reason_code": self.classification_reason_code,
            "direction_reported": self.direction_reported,
        }


def _as_datetime(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if isinstance(parsed, datetime):
        return parsed
    return None


def session_date_key(value: Any) -> str:
    """Local calendar day of a punch. Overnight shifts stay on their own dates."""
    parsed = _as_datetime(value)
    if parsed is None:
        text = str(value or "")
        return text[:10] if len(text) >= 10 else ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d")


def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
    ts = str(item.get("punch_time") or item.get("normalized_timestamp") or "")
    ident = str(item.get("id") or item.get("idempotency_key") or "")
    return (ts, ident)


def classify_punch(
    item: dict[str, Any],
    *,
    previous_event: str | None,
    previous_time: datetime | None,
    device_reports_direction: bool,
) -> ClassifiedPunch:
    """Classify one punch given the previous *accepted* event on the same day."""
    raw = item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}
    direction = item.get("device_direction")
    if direction in (None, "", "none"):
        direction = raw.get("device_direction")
    reported = bool(item.get("direction_reported") or raw.get("direction_reported"))
    if device_reports_direction and reported and direction in {
        "IN",
        "OUT",
        "BREAK_OUT",
        "BREAK_IN",
        "OT_IN",
        "OT_OUT",
    }:
        return ClassifiedPunch(
            event_type=str(direction),
            classification_method=CLASSIFICATION_DEVICE,
            classification_reason="Terminal reported this attendance state.",
            classification_reason_code="device_reported",
            direction_reported=True,
        )

    punch_time = _as_datetime(item.get("punch_time") or item.get("normalized_timestamp"))
    if (
        previous_event in {"IN", "OUT", "BREAK_IN", "BREAK_OUT", "OT_IN", "OT_OUT"}
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
            return ClassifiedPunch(
                event_type=previous_event,
                classification_method=CLASSIFICATION_INFERENCE,
                classification_reason=(
                    f"Repeat punch within {int(DUPLICATE_PUNCH_WINDOW_SECONDS)}s "
                    "of the previous event; not a new IN/OUT transition."
                ),
                classification_reason_code="duplicate_window",
                direction_reported=False,
            )

    if previous_event in {None, "", "OUT", "BREAK_OUT", "OT_OUT", "UNKNOWN"}:
        return ClassifiedPunch(
            event_type="IN",
            classification_method=CLASSIFICATION_INFERENCE,
            classification_reason="First punch of the attendance day (or after an OUT).",
            classification_reason_code="first_or_after_out",
            direction_reported=False,
        )
    return ClassifiedPunch(
        event_type="OUT",
        classification_method=CLASSIFICATION_INFERENCE,
        classification_reason="Alternating punch after an unmatched IN on this day.",
        classification_reason_code="alternate_after_in",
        direction_reported=False,
    )


def classify_employee_day(
    punches: Iterable[dict[str, Any]],
    *,
    device_reports_direction: bool,
) -> list[tuple[dict[str, Any], ClassifiedPunch]]:
    """Classify one employee's punches for one calendar day, oldest first."""
    ordered = sorted(list(punches), key=_sort_key)
    out: list[tuple[dict[str, Any], ClassifiedPunch]] = []
    previous_event: str | None = None
    previous_time: datetime | None = None
    for item in ordered:
        classified = classify_punch(
            item,
            previous_event=previous_event,
            previous_time=previous_time,
            device_reports_direction=device_reports_direction,
        )
        out.append((item, classified))
        if classified.classification_reason_code != "duplicate_window":
            previous_event = classified.event_type
            previous_time = _as_datetime(
                item.get("punch_time") or item.get("normalized_timestamp")
            )
        elif previous_time is None:
            previous_event = classified.event_type
            previous_time = _as_datetime(
                item.get("punch_time") or item.get("normalized_timestamp")
            )
    return out


def classify_punch_groups(
    punches: Iterable[dict[str, Any]],
    *,
    device_reports_direction: bool,
    organisation_id: str = "",
) -> list[tuple[dict[str, Any], ClassifiedPunch]]:
    """Classify punches per tenant + employee + calendar day."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in punches:
        org = str(
            item.get("organisation_id")
            or item.get("organization")
            or organisation_id
            or ""
        )
        employee = str(
            item.get("device_employee_code") or item.get("employee_code") or ""
        ).strip()
        day = session_date_key(
            item.get("punch_time") or item.get("normalized_timestamp")
        )
        groups.setdefault((org, employee, day), []).append(item)
    result: list[tuple[dict[str, Any], ClassifiedPunch]] = []
    for group in groups.values():
        result.extend(
            classify_employee_day(
                group, device_reports_direction=device_reports_direction
            )
        )
    return result


def apply_classification(item: dict[str, Any], classified: ClassifiedPunch) -> dict[str, Any]:
    """Write classification onto a punch dict and its raw_payload (non-destructive)."""
    raw = dict(item.get("raw_payload") or {}) if isinstance(item.get("raw_payload"), dict) else {}
    raw.setdefault("device_status", raw.get("status"))
    raw.setdefault("device_punch", raw.get("punch"))
    raw["classification_method"] = classified.classification_method
    raw["classification_reason"] = classified.classification_reason
    raw["classification_reason_code"] = classified.classification_reason_code
    raw["direction_reported"] = classified.direction_reported
    raw["event_type"] = classified.event_type
    item["event_type"] = classified.event_type
    item["classification_method"] = classified.classification_method
    item["classification_reason"] = classified.classification_reason
    item["classification_reason_code"] = classified.classification_reason_code
    item["raw_payload"] = raw
    return item
