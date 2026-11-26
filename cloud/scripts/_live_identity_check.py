"""Read-only identity check. Prints org codes and Shivaa rows. Never prints secrets."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

CLOUD_DB = Path("/data/brixigo_connector/cloud/data/brixigo_cloud.db")
PAYROLL_ENV = Path("/data/payroll_app/backend/.env")


def cloud() -> None:
    conn = sqlite3.connect(str(CLOUD_DB))
    conn.row_factory = sqlite3.Row
    print("CLOUD_ORGS")
    for org in conn.execute("select id, name, code, settings_json from organisations"):
        settings = {}
        try:
            settings = json.loads(org["settings_json"] or "{}")
        except json.JSONDecodeError:
            settings = {}
        print(
            "ORG",
            "name=", org["name"],
            "code=", org["code"],
            "hrms_external_code=", settings.get("hrms_external_code") or "",
        )
        emps = conn.execute(
            "select employee_code, name from employees where organisation_id=? and (employee_code like '%1119%' or lower(name) like '%shiva%')",
            (org["id"],),
        )
        for emp in emps:
            print("  EMP", emp["employee_code"], emp["name"])
        punches = conn.execute(
            """
            select device_employee_code, event_type, punch_time
            from attendance_punches
            where organisation_id=? and device_employee_code in ('1119','1')
            order by punch_time
            """,
            (org["id"],),
        )
        for row in punches:
            print("  PUNCH", row["device_employee_code"], row["event_type"], row["punch_time"])
    try:
        rows = conn.execute("select status, count(*) n from hrms_attendance_outbox group by status")
        print("OUTBOX", [(r["status"], r["n"]) for r in rows])
    except sqlite3.OperationalError as exc:
        print("OUTBOX_ERR", exc)
    conn.close()


def payroll() -> None:
    url = ""
    for line in PAYROLL_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    import psycopg2

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    print("PAYROLL_ORGS")
    cur.execute(
        "select name, external_code, is_active from organisations order by name"
    )
    for name, code, active in cur.fetchall():
        print("ORG", "name=", name, "external_code=", code or "", "active=", active)
    print("PAYROLL_SHIVAA")
    cur.execute(
        """
        select employee_code, display_name, first_name, is_active
        from employees
        where lower(coalesce(display_name,'')) like '%shiva%'
           or lower(coalesce(first_name,'')) like '%shiva%'
           or employee_code ilike '%1119%'
        """
    )
    for row in cur.fetchall():
        print("EMP", row)
    conn.close()


if __name__ == "__main__":
    cloud()
    payroll()
