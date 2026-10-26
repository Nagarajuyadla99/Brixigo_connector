"""Read-only snapshot of Cloud attendance identity and HRMS mapping readiness."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "brixigo_cloud.db"


def main() -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print("CLOUD_DB", DB.exists())
    orgs = list(conn.execute("select id, name, code, timezone, settings_json from organisations"))
    print("CLOUD_ORGS", len(orgs))
    for org in orgs:
        settings = {}
        try:
            settings = json.loads(org["settings_json"] or "{}")
        except json.JSONDecodeError:
            settings = {}
        print(
            "ORG",
            org["code"],
            "name=",
            org["name"],
            "tz=",
            org["timezone"],
            "hrms_external_code=",
            settings.get("hrms_external_code") or "",
        )
        emps = list(
            conn.execute(
                "select employee_code, name from employees where organisation_id=? order by employee_code",
                (org["id"],),
            )
        )
        for emp in emps:
            print("  EMP", emp["employee_code"], emp["name"])
        devices = list(
            conn.execute(
                "select name, terminal_code, id from devices where organisation_id=?",
                (org["id"],),
            )
        )
        for dev in devices:
            print("  DEVICE", "name=", dev["name"], "terminal=", dev["terminal_code"], "id=", dev["id"])
        punches = conn.execute(
            "select count(*) as n from attendance_punches where organisation_id=?",
            (org["id"],),
        ).fetchone()["n"]
        print("  PUNCHES", punches)
        sai = list(
            conn.execute(
                """
                select device_employee_code, event_type, punch_time
                from attendance_punches
                where organisation_id=? and device_employee_code=?
                order by punch_time
                limit 5
                """,
                (org["id"], "5"),
            )
        )
        print("  SAI_CODE5_SAMPLE", len(sai), [dict(r) for r in sai])
    try:
        outbox = conn.execute(
            "select status, count(*) as n from hrms_attendance_outbox group by status"
        ).fetchall()
        print("OUTBOX", [dict(r) for r in outbox])
    except sqlite3.OperationalError as exc:
        print("OUTBOX_TABLE", str(exc))
    conn.close()


if __name__ == "__main__":
    main()
