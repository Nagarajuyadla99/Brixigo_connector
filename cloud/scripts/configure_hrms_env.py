"""Configure local Cloud → HRMS server-to-server credentials. Does not print secrets."""

from __future__ import annotations

import secrets
from pathlib import Path

CLOUD_ENV = Path(__file__).resolve().parents[1] / ".env"
PAYROLL_ENV = Path(r"D:\payroll_management\etax_payroll\backend\.env")
HRMS_URL = "http://127.0.0.1:9000"


def _has_key(path: Path, name: str) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == name and value.strip():
            return True
    return False


def _read_key(path: Path, name: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def _upsert(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    existing = _read_key(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY") or _read_key(
        CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_API_KEY"
    )
    key = existing or secrets.token_urlsafe(32)
    _upsert(PAYROLL_ENV, {"BRIXIGO_CLOUD_INTEGRATION_KEY": key})
    _upsert(
        CLOUD_ENV,
        {
            "BRIXIGO_CLOUD_HRMS_ENABLED": "true",
            "BRIXIGO_CLOUD_HRMS_BASE_URL": HRMS_URL,
            "BRIXIGO_CLOUD_HRMS_API_KEY": key,
        },
    )
    print("payroll_key_present", _has_key(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY"))
    print("cloud_hrms_enabled", _has_key(CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_ENABLED"))
    print("cloud_hrms_url", _read_key(CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_BASE_URL"))
    print("keys_match", _read_key(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY") == _read_key(CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_API_KEY"))


if __name__ == "__main__":
    main()
