"""Align connector employee codes with mission numbers.

The server and the connector keep the prefixed code (``Emp001``). A mission
that was created without that prefix is addressed as ``001``. The active
policy is published from connector settings, including the copy delivered
to an installed connector by remote configuration.
"""

from __future__ import annotations

import re
from typing import Any

_PREFIXED = re.compile(r"^([A-Za-z]+)(\d+)$")
_active = {"prefix": "", "omit": False}


def set_active_policy(*, prefix: str = "", omit_prefix: bool | str | None = False) -> None:
    """Remember the organisation rule for this running connector."""
    _active["prefix"] = "".join(ch for ch in str(prefix or "") if ch.isalpha())
    if isinstance(omit_prefix, str):
        _active["omit"] = omit_prefix.strip().lower() in {"1", "true", "yes", "on"}
    else:
        _active["omit"] = bool(omit_prefix)


def active_prefix() -> str:
    return str(_active["prefix"] or "")


def active_omit() -> bool:
    return bool(_active["omit"])


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
    """True when ``Emp001`` and ``001`` are the same person."""
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
    """Number sent to a mission that has no prefix. ``Emp001`` → ``001``."""
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
    """Prefixed code shown on the connector. ``001`` + ``Emp`` → ``Emp001``."""
    raw = str(code or "").strip()
    wanted = "".join(ch for ch in str(prefix or "") if ch.isalpha())
    if not raw or not wanted:
        return raw
    letter, _number = split_employee_code(raw)
    if letter or not raw.isdigit():
        return raw
    return f"{wanted}{raw}"


def alignment_key(code: str, *, omit_prefix: bool | None = None) -> str:
    """Group key so ``Emp001`` and ``001`` are one person when the policy allows it."""
    raw = str(code or "").strip()
    if omit_prefix is None:
        omit_prefix = active_omit()
    if not omit_prefix:
        return raw
    letter, number = split_employee_code(raw)
    if number and (letter or raw.isdigit()):
        return numeric_identity(raw) or raw
    return raw
