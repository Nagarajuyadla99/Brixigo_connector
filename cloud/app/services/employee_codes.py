"""Align server/connector employee codes with mission codes.

Some organisations store ``Emp001`` on the server and on the connector, and
``001`` on the mission because the mission record was created without the
prefix. Those are the same person inside one organisation:

* the mission is queried with the number (``001``)
* the server and connector keep showing the prefixed code (``Emp001``)

Number alignment is used only when the organisation says the mission has no
prefix. Other organisations keep an exact code match.
"""

from __future__ import annotations

import re
from typing import Any

_PREFIXED = re.compile(r"^([A-Za-z]+)(\d+)$")


def split_employee_code(code: str) -> tuple[str, str]:
    """Return ``(prefix, number)``. ``Emp001`` → ``("Emp", "001")``."""
    raw = str(code or "").strip()
    match = _PREFIXED.match(raw)
    if match:
        return match.group(1), match.group(2)
    if raw.isdigit():
        return "", raw
    return "", ""


def numeric_identity(code: str) -> str:
    """Integer identity of the employee number. ``Emp001``, ``001``, and ``1`` → ``1``."""
    _prefix, number = split_employee_code(code)
    digits = number or "".join(ch for ch in str(code or "") if ch.isdigit())
    if not digits:
        return ""
    return str(int(digits))


def codes_align(left: str, right: str) -> bool:
    """True when two codes are the same person under number alignment.

    ``Emp001`` aligns with ``001`` and ``1``. ``Emp001`` does not align with
    ``AB001`` or ``Emp002``.
    """
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a.casefold() == b.casefold():
        return True
    left_id = numeric_identity(a)
    right_id = numeric_identity(b)
    if not left_id or left_id != right_id:
        return False
    left_prefix, _left_number = split_employee_code(a)
    right_prefix, _right_number = split_employee_code(b)
    if left_prefix and right_prefix and left_prefix.casefold() != right_prefix.casefold():
        return False
    return True


def organisation_omits_prefix(settings: Any) -> bool:
    data = settings if isinstance(settings, dict) else {}
    return bool(data.get("mission_omits_employee_prefix"))


def organisation_code_prefix(settings: Any) -> str:
    data = settings if isinstance(settings, dict) else {}
    return "".join(ch for ch in str(data.get("employee_code_prefix") or "") if ch.isalpha())


def mission_employee_code(code: str, *, omit_prefix: bool, prefix: str = "") -> str:
    """Code to send to the mission.

    When the mission has no prefix, ``Emp001`` is sent as ``001`` (leading
    zeros are kept). Otherwise the server code is sent unchanged.
    """
    raw = str(code or "").strip()
    if not omit_prefix or not raw:
        return raw
    letter, number = split_employee_code(raw)
    if not letter or not number:
        return raw
    wanted = "".join(ch for ch in str(prefix or "") if ch.isalpha())
    if wanted and letter.casefold() != wanted.casefold():
        return raw
    return number


def display_employee_code(code: str, *, prefix: str) -> str:
    """Code shown on the server and connector. A bare number gains the prefix."""
    raw = str(code or "").strip()
    wanted = "".join(ch for ch in str(prefix or "") if ch.isalpha())
    if not raw or not wanted:
        return raw
    letter, _number = split_employee_code(raw)
    if letter or not raw.isdigit():
        return raw
    return f"{wanted}{raw}"


def alignment_key(code: str, *, omit_prefix: bool) -> str:
    """Group key so ``Emp001`` and ``001`` land on one person when allowed."""
    raw = str(code or "").strip()
    if not omit_prefix:
        return raw
    letter, number = split_employee_code(raw)
    if number and (letter or raw.isdigit()):
        return numeric_identity(raw) or raw
    return raw


class EmployeeCodeIndex:
    """Exact code lookup, plus unique number alignment for opted-in organisations."""

    def __init__(self, rows: list[Any], *, omit_org_ids: set[str] | None = None):
        self._exact: dict[tuple[str, str], Any] = {}
        self._numbers: dict[tuple[str, str], list[Any]] = {}
        omit = omit_org_ids or set()
        for row in rows:
            org_id = str(getattr(row, "organisation_id", "") or "")
            code = str(getattr(row, "employee_code", "") or "")
            if not org_id or not code:
                continue
            self._exact[(org_id, code)] = row
            if org_id not in omit:
                continue
            ident = numeric_identity(code)
            if ident:
                self._numbers.setdefault((org_id, ident), []).append(row)

    def get(self, organisation_id: str, code: str) -> Any | None:
        org_id = str(organisation_id or "")
        raw = str(code or "").strip()
        if not org_id or not raw:
            return None
        found = self._exact.get((org_id, raw))
        if found is not None:
            return found
        ident = numeric_identity(raw)
        if not ident:
            return None
        hits = [
            row
            for row in self._numbers.get((org_id, ident), [])
            if codes_align(str(getattr(row, "employee_code", "")), raw)
        ]
        if len(hits) == 1:
            return hits[0]
        return None
