"""Unlock the open November period and rebuild 3 Nov attendance from device punches."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone

from sqlalchemy import select

from database import AsyncSessionLocal
from models.employee_model import Employee
from models.org_models import Organisation
from models.payroll_models import PayPeriod
from models.wf_models import RawAttendanceEvent
from services.wf_day_result_service import recompute_employee_day

WORK_DATE = date(2026, 11, 3)


async def main() -> None:
    day_start = datetime.combine(WORK_DATE, time.min, tzinfo=timezone.utc)
    day_end = datetime.combine(WORK_DATE, time.max, tzinfo=timezone.utc)
    async with AsyncSessionLocal() as db:
        org = (
            await db.execute(select(Organisation).where(Organisation.name == "brixigo"))
        ).scalar_one()
        periods = (
            await db.execute(
                select(PayPeriod).where(
                    PayPeriod.organisation_id == org.organisation_id,
                    PayPeriod.start_date <= WORK_DATE,
                    PayPeriod.end_date >= WORK_DATE,
                    PayPeriod.status == "open",
                    PayPeriod.attendance_leave_locked.is_(True),
                )
            )
        ).scalars().all()
        for period in periods:
            period.attendance_leave_locked = False
        await db.commit()
        print("UNLOCKED_OPEN_PERIODS", len(periods))

        employee_ids = (
            await db.execute(
                select(RawAttendanceEvent.employee_id)
                .where(
                    RawAttendanceEvent.organisation_id == org.organisation_id,
                    RawAttendanceEvent.source == "brixigo_cloud",
                    RawAttendanceEvent.punch_time >= day_start,
                    RawAttendanceEvent.punch_time <= day_end,
                )
                .distinct()
            )
        ).scalars().all()
        print("EMPLOYEES_WITH_DEVICE_PUNCHES", len(employee_ids))
        for employee_id in employee_ids:
            row = await recompute_employee_day(
                db, org.organisation_id, employee_id, WORK_DATE
            )
            emp = await db.get(Employee, employee_id)
            code = getattr(emp, "employee_code", "")
            if row is None:
                print("DAY", code, "none")
                continue
            print(
                "DAY",
                code,
                row.status,
                str(row.time_in),
                str(row.time_out),
                row.source,
            )
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
