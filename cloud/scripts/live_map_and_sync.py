"""Live mapping + HTTP sync. Prints status only; never prints secrets."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import httpx

PAYROLL = "http://127.0.0.1:9000"
CLOUD = "http://127.0.0.1:8080"
CLOUD_DB = Path(r"D:\Brixigo-Connector\cloud\data\brixigo_cloud.db")
PAYROLL_ENV = Path(r"D:\payroll_management\etax_payroll\backend\.env")
CODE = "infoessl9999"


def _env_key(path: Path, name: str) -> str:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    return os.getenv(name, "")


def cloud_outbox():
    conn = sqlite3.connect(str(CLOUD_DB))
    conn.row_factory = sqlite3.Row
    punches = conn.execute("select count(*) n from attendance_punches").fetchone()["n"]
    sai = conn.execute(
        "select count(*) n from attendance_punches where device_employee_code='5'"
    ).fetchone()["n"]
    rows = list(conn.execute("select status, count(*) n from hrms_attendance_outbox group by status"))
    total = conn.execute("select count(*) n from hrms_attendance_outbox").fetchone()["n"]
    mapping = conn.execute("select code, settings_json from organisations").fetchone()
    conn.close()
    counts = {r["status"]: r["n"] for r in rows}
    settings = {}
    try:
        settings = json.loads(mapping["settings_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        settings = {}
    return {
        "punches": punches,
        "sai": sai,
        "outbox_total": total,
        "counts": counts,
        "hrms_external_code": settings.get("hrms_external_code") or "",
        "cloud_code": mapping["code"] if mapping else "",
    }


def main() -> None:
    print("BEFORE", json.dumps(cloud_outbox()))
    with httpx.Client(timeout=20.0) as client:
        docs = client.get(f"{PAYROLL}/docs")
        print("PAYROLL_DOCS", docs.status_code)
        login = client.post(
            f"{PAYROLL}/api/auth/login",
            data={"username": "raju", "password": "Admin@12345"},
        )
        print("PAYROLL_LOGIN", login.status_code)
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        mapped = client.put(
            f"{PAYROLL}/api/integrations/brixigo-cloud/organisation/external-code",
            headers=headers,
            json={"external_code": CODE},
        )
        print("SET_EXTERNAL_CODE", mapped.status_code, mapped.text[:500])
        mapped.raise_for_status()
        body = mapped.json()
        print("MAPPED_ORG_NAME", body.get("name"))
        print("MAPPED_EXTERNAL_CODE", body.get("external_code"))

        cloud_login = client.post(
            f"{CLOUD}/api/v1/auth/login",
            json={"email": "admin@brixigo.com", "password": "BrixigoAdmin!2026"},
        )
        print("CLOUD_LOGIN", cloud_login.status_code)
        cloud_login.raise_for_status()
        cloud_headers = {"Authorization": f"Bearer {cloud_login.json()['access_token']}"}
        cmap = client.put(
            f"{CLOUD}/api/v1/payroll/hrms-mapping",
            headers=cloud_headers,
            json={"hrms_external_code": CODE},
        )
        print("SET_CLOUD_MAPPING", cmap.status_code, cmap.text[:500])
        cmap.raise_for_status()
        got = client.get(f"{CLOUD}/api/v1/payroll/hrms-mapping", headers=cloud_headers)
        print("GET_CLOUD_MAPPING", got.json())

        key = _env_key(PAYROLL_ENV, "BRIXIGO_CLOUD_INTEGRATION_KEY")
        health = client.get(
            f"{PAYROLL}/api/integrations/brixigo-cloud/health",
            headers={"X-Brixigo-Cloud-Key": key},
        )
        print("INTEGRATION_HEALTH", health.status_code, health.text[:200])
        health.raise_for_status()

        sync = client.post(
            f"{CLOUD}/api/v1/payroll/hrms-sync/run",
            headers=cloud_headers,
            timeout=120.0,
        )
        print("SYNC_RUN", sync.status_code, sync.text[:1000])
        status = client.get(f"{CLOUD}/api/v1/payroll/hrms-sync", headers=cloud_headers)
        print("SYNC_STATUS", status.status_code, status.text[:1500])

    print("AFTER", json.dumps(cloud_outbox()))


if __name__ == "__main__":
    main()
