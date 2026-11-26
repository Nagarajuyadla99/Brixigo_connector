"""Confirm Shivaa's 3 Nov 2026 attendance after the cloud handoff."""

from pathlib import Path

import psycopg2

env = Path("/data/payroll_app/backend/.env")
url = ""
for line in env.read_text(encoding="utf-8").splitlines():
    if line.startswith("DATABASE_URL="):
        url = line.split("=", 1)[1].strip().strip('"').strip("'")
url = url.replace("postgresql+asyncpg://", "postgresql://")
conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute(
    """
    SELECT a.work_date::text, a.status, a.time_in::text, a.time_out::text, a.source
    FROM attendances a
    JOIN employees e ON e.employee_id = a.employee_id
    JOIN organisations o ON o.organisation_id = e.organisation_id
    WHERE e.employee_code = 'EMP1119' AND o.name = 'brixigo' AND a.work_date = DATE '2026-11-03'
    """
)
print("ATTENDANCE", cur.fetchall())
cur.execute(
    """
    SELECT a.work_date::text, a.status, a.time_in::text, a.time_out::text, a.source
    FROM attendances a
    JOIN employees e ON e.employee_id = a.employee_id
    JOIN organisations o ON o.organisation_id = e.organisation_id
    WHERE e.employee_code = 'EMP1119' AND o.name = 'brixigo'
    ORDER BY a.work_date
    """
)
print("ATTENDANCE_ALL", cur.fetchall())
cur.execute(
    """
    SELECT p.start_date::text, p.end_date::text, p.attendance_leave_locked, COALESCE(p.status, '')
    FROM pay_periods p
    JOIN organisations o ON o.organisation_id = p.organisation_id
    WHERE o.name = 'brixigo'
      AND p.start_date <= DATE '2026-11-03'
      AND p.end_date >= DATE '2026-11-03'
    """
)
print("PAY_PERIODS", cur.fetchall())
cur.execute(
    """
    SELECT r.event_type, r.punch_time::text, r.source
    FROM raw_attendance_events r
    JOIN employees e ON e.employee_id = r.employee_id
    JOIN organisations o ON o.organisation_id = e.organisation_id
    WHERE e.employee_code = 'EMP1119' AND o.name = 'brixigo'
      AND r.punch_time::date = DATE '2026-11-03'
    ORDER BY r.punch_time
    """
)
print("EVENTS", cur.fetchall())
conn.close()
