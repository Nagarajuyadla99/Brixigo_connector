"""Daily and monthly attendance roll-ups with employee names.

These queries run in SQLite against the durable local queue. They do not
invent punches or names. A day is "present" when at least one punch exists
for that employee on that calendar day (real punch_time date). Unset-clock
remaps (year-2000 device stamps forced onto the download day) are excluded
upstream so Daily does not treat a full history dump as "everyone present today".
"""

from __future__ import annotations

import calendar
from typing import Any, Iterable

from services.employee_directory import enrich_employee_fields
from services.punch_normalizer import event_label, method_label


def resolve_daily_day(day: str | None, *, today: str) -> str:
    """Use today when the picker is blank or accidentally set in the future."""
    requested = str(day or "").strip()[:10]
    today_key = str(today or "")[:10]
    if len(today_key) != 10:
        return requested
    if len(requested) != 10 or requested > today_key:
        return today_key
    return requested


def _date_key(value: str | None) -> str:
    text = str(value or "")[:10]
    return text if len(text) == 10 and text[4] == "-" else ""


def build_daily_rows(punches: Iterable[dict[str, Any]], *, day: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for punch in punches:
        code = str(punch.get("device_employee_code") or punch.get("employee_code") or "").strip()
        if not code:
            continue
        row = grouped.setdefault(
            code,
            {
                "employee_code": code,
                "hrms_employee_name": punch.get("hrms_employee_name") or "",
                "device_user_name": punch.get("device_user_name") or "",
                "employee_name": punch.get("employee_name") or "",
                "branch": punch.get("branch") or "",
                "devices": [],
                "device_ids": [],
                "punches": [],
                "first_in": None,
                "last_out": None,
                "first_punch": None,
                "last_punch": None,
                "in_count": 0,
                "out_count": 0,
                "present": True,
                "day": day,
            },
        )
        device_name = str(punch.get("device_name") or "").strip()
        if device_name and device_name not in row["devices"]:
            row["devices"].append(device_name)
        device_id = str(punch.get("device_id") or "")
        if device_id and device_id not in row["device_ids"]:
            row["device_ids"].append(device_id)
        if punch.get("branch") and not row["branch"]:
            row["branch"] = punch.get("branch")
        if punch.get("hrms_employee_name") and not row["hrms_employee_name"]:
            row["hrms_employee_name"] = punch.get("hrms_employee_name")
        if punch.get("device_user_name") and not row["device_user_name"]:
            row["device_user_name"] = punch.get("device_user_name")
        if punch.get("employee_name") and not row["employee_name"]:
            row["employee_name"] = punch.get("employee_name")
        ts = punch.get("normalized_timestamp") or punch.get("punch_time") or ""
        event = str(punch.get("event_type") or "").upper()
        row["punches"].append(
            {
                "id": punch.get("id"),
                "time": ts,
                "event": event,
                "event_label": event_label(event),
                "method": method_label(punch.get("verification_method")),
                "device": device_name,
                "status": punch.get("status"),
            }
        )
        if not row["first_punch"] or str(ts) < str(row["first_punch"]):
            row["first_punch"] = ts
        if not row["last_punch"] or str(ts) > str(row["last_punch"]):
            row["last_punch"] = ts
        if event == "IN":
            row["in_count"] += 1
            if not row["first_in"] or str(ts) < str(row["first_in"]):
                row["first_in"] = ts
        elif event == "OUT":
            row["out_count"] += 1
            if not row["last_out"] or str(ts) > str(row["last_out"]):
                row["last_out"] = ts
    out = []
    for row in grouped.values():
        row["punch_count"] = len(row["punches"])
        enrich_employee_fields(row)
        row["punches"].sort(key=lambda p: str(p.get("time") or ""))
        out.append(row)
    out.sort(
        key=lambda r: (
            str(r.get("last_punch") or r.get("last_out") or r.get("first_in") or ""),
            str(r.get("employee_display_name") or "").lower(),
        ),
        reverse=True,
    )
    return out


def build_monthly_rows(
    punches: Iterable[dict[str, Any]],
    *,
    year: int,
    month: int,
) -> dict[str, Any]:
    days_in_month = calendar.monthrange(int(year), int(month))[1]
    day_keys = [f"{year:04d}-{month:02d}-{day:02d}" for day in range(1, days_in_month + 1)]
    employees: dict[str, dict[str, Any]] = {}
    for punch in punches:
        code = str(punch.get("device_employee_code") or punch.get("employee_code") or "").strip()
        day = _date_key(punch.get("normalized_timestamp") or punch.get("punch_time"))
        if not code or day not in set(day_keys):
            continue
        emp = employees.setdefault(
            code,
            {
                "employee_code": code,
                "hrms_employee_name": punch.get("hrms_employee_name") or "",
                "device_user_name": punch.get("device_user_name") or "",
                "employee_name": punch.get("employee_name") or "",
                "branch": punch.get("branch") or "",
                "days": {key: {"present": False, "punches": 0, "first": None, "last": None} for key in day_keys},
            },
        )
        if punch.get("hrms_employee_name") and not emp["hrms_employee_name"]:
            emp["hrms_employee_name"] = punch.get("hrms_employee_name")
        if punch.get("device_user_name") and not emp["device_user_name"]:
            emp["device_user_name"] = punch.get("device_user_name")
        if punch.get("employee_name") and not emp["employee_name"]:
            emp["employee_name"] = punch.get("employee_name")
        if punch.get("branch") and not emp["branch"]:
            emp["branch"] = punch.get("branch")
        cell = emp["days"][day]
        cell["present"] = True
        cell["punches"] += 1
        ts = punch.get("normalized_timestamp") or punch.get("punch_time")
        if ts and (not cell["first"] or str(ts) < str(cell["first"])):
            cell["first"] = ts
        if ts and (not cell["last"] or str(ts) > str(cell["last"])):
            cell["last"] = ts
    rows = []
    for emp in employees.values():
        enrich_employee_fields(emp)
        present_days = sum(1 for cell in emp["days"].values() if cell["present"])
        emp["present_days"] = present_days
        emp["absent_days"] = days_in_month - present_days
        emp["attendance_percent"] = round(100.0 * present_days / days_in_month, 1) if days_in_month else 0
        rows.append(emp)
    rows.sort(key=lambda r: (str(r.get("employee_display_name") or "").lower(), r["employee_code"]))
    return {
        "year": int(year),
        "month": int(month),
        "days": day_keys,
        "employee_count": len(rows),
        "rows": rows,
    }


def trend_from_counts(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "day": str(item.get("day") or ""),
            "collected": int(item.get("collected") or 0),
            "uploaded": int(item.get("uploaded") or 0),
            "failed": int(item.get("failed") or 0),
            "pending": int(item.get("pending") or 0),
        }
        for item in days
    ]
