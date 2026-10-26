"""Align Connector → Cloud → Server and refresh HRMS attendance display.

Safe to rerun. Does not print secrets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.live_map_and_sync import cloud_outbox, main as map_and_sync  # noqa: E402

PAYROLL = "http://127.0.0.1:9000"
ADMIN_USER = "raju"
ADMIN_PASS = "Admin@12345"


def recompute_attendance(*, from_date: str, to_date: str) -> dict:
    with httpx.Client(timeout=120.0) as client:
        login = client.post(
            f"{PAYROLL}/api/auth/login",
            data={"username": ADMIN_USER, "password": ADMIN_PASS},
        )
        login.raise_for_status()
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "Content-Type": "application/json",
        }
        resp = client.post(
            f"{PAYROLL}/api/wf/attendance/recompute",
            headers=headers,
            json={"from_date": from_date, "to_date": to_date, "persist": True},
        )
        resp.raise_for_status()
        return resp.json()


def main() -> None:
    print("BEFORE", json.dumps(cloud_outbox()))
    map_and_sync()
    recompute = recompute_attendance(from_date="2026-09-01", to_date="2026-09-10")
    print(
        "RECOMPUTE",
        json.dumps(
            {
                "processed": recompute.get("processed"),
                "skipped": recompute.get("skipped"),
                "errors": recompute.get("errors"),
            }
        ),
    )
    print("AFTER", json.dumps(cloud_outbox()))


if __name__ == "__main__":
    main()
