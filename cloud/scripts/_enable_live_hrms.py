"""Enable Cloud → brixigo.com attendance on the live server. Never prints secrets."""

from __future__ import annotations

import secrets
from pathlib import Path

PAYROLL_ENV = Path("/data/payroll_app/backend/.env")
CLOUD_ENV = Path("/data/brixigo_connector/cloud/.env")
HRMS_URL = "http://127.0.0.1:9000"


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _read(path: Path, name: str) -> str:
    for line in _lines(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def _upsert(path: Path, updates: dict[str, str]) -> None:
    lines = _lines(path)
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
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
    import psycopg2

    url = _read(PAYROLL_ENV, "DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT o.name, COALESCE(o.external_code, ''), e.employee_code,
               COALESCE(e.display_name, ''), e.is_active, o.organisation_id::text
        FROM employees e
        JOIN organisations o ON o.organisation_id = e.organisation_id
        WHERE e.employee_code = 'EMP1119'
        """
    )
    rows = cur.fetchall()
    print("EMP1119_COUNT", len(rows))
    for name, ext, code, display, active, _org_id in rows:
        print("EMP1119", "org=", name, "external=", ext, "code=", code, "name=", display, "active=", active)
    active_rows = [row for row in rows if row[4]]
    if len(active_rows) != 1:
        raise SystemExit("expected exactly one active EMP1119")
    org_name, external_code, _code, display, _active, org_id = active_rows[0]
    if org_name.strip().casefold() != "brixigo":
        raise SystemExit(f"active EMP1119 is in organisation {org_name!r}, not brixigo")
    cur.execute(
        """
        SELECT name FROM organisations
        WHERE lower(COALESCE(external_code, '')) = 'brixigo'
          AND organisation_id::text <> %s
        """,
        (org_id,),
    )
    conflicts = [row[0] for row in cur.fetchall()]
    if conflicts:
        raise SystemExit(f"external code brixigo already used by {conflicts}")
    if external_code.strip().lower() not in {"", "brixigo"}:
        raise SystemExit("organisation already has a different external code")
    cur.execute(
        "UPDATE organisations SET external_code = %s WHERE organisation_id::text = %s",
        ("brixigo", org_id),
    )
    cur.execute(
        """
        SELECT employee_code, COALESCE(display_name, ''), is_active
        FROM employees
        WHERE organisation_id::text = %s
          AND regexp_replace(COALESCE(employee_code, ''), '\\D', '', 'g') IN ('1119')
        """,
        (org_id,),
    )
    twins = cur.fetchall()
    print("CODE_1119_IN_ORG", [(row[0], row[1], row[2]) for row in twins])
    if len([row for row in twins if row[2]]) != 1:
        conn.rollback()
        raise SystemExit("device user 1119 does not map to exactly one active employee")
    conn.commit()
    conn.close()
    print("EXTERNAL_CODE_SET", org_name, display)

    key = _read(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY") or _read(
        CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_API_KEY"
    )
    if not key:
        key = secrets.token_urlsafe(32)
    _upsert(PAYROLL_ENV, {"BRIXIGO_CLOUD_INTEGRATION_KEY": key})
    _upsert(
        CLOUD_ENV,
        {
            "BRIXIGO_CLOUD_HRMS_ENABLED": "true",
            "BRIXIGO_CLOUD_HRMS_BASE_URL": HRMS_URL,
            "BRIXIGO_CLOUD_HRMS_API_KEY": key,
        },
    )
    print("payroll_key_present", bool(_read(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY")))
    print("cloud_hrms_enabled", _read(CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_ENABLED"))
    print("cloud_hrms_url", _read(CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_BASE_URL"))
    print(
        "keys_match",
        _read(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY")
        == _read(CLOUD_ENV, "BRIXIGO_CLOUD_HRMS_API_KEY")
        and bool(_read(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY")),
    )


if __name__ == "__main__":
    main()
