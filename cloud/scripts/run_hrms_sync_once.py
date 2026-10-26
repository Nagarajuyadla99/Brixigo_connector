"""One-shot Cloud → HRMS outbox drain. Prints counts only; never prints secrets."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)
os.chdir(ROOT)

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
settings = get_settings()

from app.database import SessionLocal  # noqa: E402
from app.services.hrms_outbound import summarize_outbox, sync_pending  # noqa: E402


def main() -> None:
    print("hrms_enabled", settings.hrms_enabled)
    print("hrms_url", settings.hrms_base_url)
    print("hrms_key_configured", bool(settings.hrms_api_key))
    db = SessionLocal()
    try:
        combined = {
            "queued": 0,
            "synced": 0,
            "duplicate": 0,
            "organisation_unmatched": 0,
            "employee_unmatched": 0,
            "failed": 0,
            "invalid": 0,
            "skipped": 0,
        }
        for _ in range(8):
            stats = sync_pending(db, settings)
            db.commit()
            for key, value in stats.items():
                combined[key] = combined.get(key, 0) + int(value or 0)
            if int(stats.get("queued") or 0) == 0 and int(stats.get("skipped") or 0):
                break
            if int(stats.get("queued") or 0) == 0:
                break
        print("SYNC_STATS", combined)
        print("OUTBOX", summarize_outbox(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
