"""Resolve biometric user IDs to human-readable employee names.

Name sources, in priority order:

1. HRMS / cloud employee directory (`employees.employee_code`)
2. Name stored on the biometric terminal (`device_users.name` for that device)
3. Same user ID on any terminal (fallback)
4. Not reported — display "Device user {code}" without inventing a person name

Never fabricate a name. If the device only reports a numeric user ID, the UI
must show that the name was not reported.
"""

from __future__ import annotations

from typing import Any


def employee_display_name(
    name: str | None,
    code: str | None,
    *,
    invent_prefix: bool = True,
) -> str:
    person = str(name or "").strip()
    code_text = str(code or "").strip()
    if person and code_text and person.lower() != code_text.lower():
        return f"{person} ({code_text})"
    if person:
        return person
    if code_text and invent_prefix:
        return f"Device user {code_text}"
    return code_text or "Unidentified employee"


def name_source(hrms_name: str | None, device_name: str | None) -> str:
    if str(hrms_name or "").strip():
        return "hrms"
    if str(device_name or "").strip():
        return "device"
    return "not_reported"


def name_reported(name: str | None) -> bool:
    return bool(str(name or "").strip())


def enrich_employee_fields(item: dict[str, Any]) -> dict[str, Any]:
    code = str(item.get("device_employee_code") or item.get("employee_code") or "").strip()
    hrms = str(item.get("hrms_employee_name") or "").strip()
    device = str(item.get("device_user_name") or "").strip()
    resolved = hrms or device or str(item.get("employee_name") or "").strip()
    item["employee_code"] = code
    item["employee_name"] = resolved
    item["employee_display_name"] = employee_display_name(resolved, code)
    item["employee_name_source"] = name_source(hrms, device or resolved)
    item["employee_name_reported"] = name_reported(resolved)
    if not item["employee_name_reported"]:
        item["employee_name_hint"] = "Name not reported by device or HRMS"
    else:
        item["employee_name_hint"] = (
            "From HR directory" if item["employee_name_source"] == "hrms" else "From biometric device"
        )
    return item
