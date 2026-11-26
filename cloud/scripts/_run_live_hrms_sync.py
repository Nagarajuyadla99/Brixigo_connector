"""Send pending connector punches into brixigo.com. Prints statuses only."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/data/brixigo_connector/cloud")
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import HrmsAttendanceOutbox
from app.services.hrms_outbound import backfill_pending, sync_pending


def main() -> None:
    settings = get_settings()
    print("enabled", settings.hrms_enabled)
    print("url", settings.hrms_base_url)
    print("key_present", bool(str(settings.hrms_api_key or "").strip()))
    db = SessionLocal()
    try:
        created = backfill_pending(db, limit=2000)
        now = datetime.now(timezone.utc)
        for row in db.scalars(select(HrmsAttendanceOutbox)).all():
            if row.status in {"failed", "pending", "organisation_unmatched", "employee_unmatched"}:
                row.next_attempt_at = now
        db.commit()
        print("backfill", created)
        for index in range(25):
            stats = sync_pending(db, settings)
            db.commit()
            print("SYNC", index, {k: stats.get(k) for k in ("queued", "synced", "duplicate", "organisation_unmatched", "employee_unmatched", "failed", "invalid", "skipped")})
            if stats.get("skipped"):
                break
            if not stats.get("queued"):
                break
            if stats.get("failed") and not any(stats.get(k) for k in ("synced", "duplicate", "organisation_unmatched", "employee_unmatched", "invalid")):
                break
        rows = db.scalars(
            select(HrmsAttendanceOutbox).where(HrmsAttendanceOutbox.employee_code == "1119")
        ).all()
        counts: dict[str, int] = {}
        sample = ""
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
            if row.status != "synced" and row.last_error and not sample:
                sample = str(row.last_error)[:240]
        print("SHIVAA_1119", len(rows), counts)
        if sample:
            print("SHIVAA_ERROR", sample)
    finally:
        db.close()


if __name__ == "__main__":
    main()
