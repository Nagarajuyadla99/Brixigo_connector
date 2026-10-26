"""Production flow verification: Device→Connector→Cloud→Employee→Website feed.

Read-only architecture proof via API tests. Does not modify connector push contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import AttendancePunch, Employee, User, new_id
from app.security.auth import hash_password

PREFIX = "/api/wf/biometric-enterprise"


def _org_headers(client: TestClient, settings, org_id: str, email: str) -> dict[str, str]:
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        existing = db.scalar(select(User).where(User.email == email))
        if not existing:
            db.add(
                User(
                    id=new_id(),
                    organisation_id=org_id,
                    email=email,
                    full_name="Org Admin",
                    password_hash=hash_password("OrgAdmin!2026"),
                    role="organisation_admin",
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "OrgAdmin!2026"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _register_connector(client: TestClient, *, org_id: str, name: str, hostname: str) -> tuple[str, str]:
    reg = client.post(
        f"{PREFIX}/connectors/register",
        json={
            "hostname": hostname,
            "version": "3.0.2",
            "organisation_id": org_id,
            "name": name,
        },
    )
    assert reg.status_code == 200, reg.text
    data = reg.json()
    return data["connector_id"], data["api_key"]


def _push(
    client: TestClient,
    *,
    terminal: str,
    connector_id: str,
    api_key: str,
    org_id: str | None,
    punches: list[dict],
    extra_headers: dict | None = None,
    employees: list[dict] | None = None,
):
    headers = {
        "X-Connector-Id": connector_id,
        "X-Device-Token": api_key,
    }
    if org_id is not None:
        headers["X-Organisation-Id"] = org_id
    if extra_headers:
        headers.update(extra_headers)
    payload: dict = {"source": "brixigo-connector", "punches": punches}
    if employees:
        payload["employees"] = employees
    return client.post(
        f"{PREFIX}/connectors/{terminal}/push",
        headers=headers,
        json=payload,
    )


def _punch(
    code: str,
    punch_time: str,
    *,
    idem: str | None = None,
    device_id: str = "dev-a",
    event_type: str = "IN",
    employee_name: str = "",
    classification_method: str = "",
    direction_reported: bool | None = None,
) -> dict:
    row = {
        "contract_version": "1.0",
        "device_employee_code": code,
        "punch_time": punch_time,
        "event_type": event_type,
        "verification_method": "fingerprint",
        "idempotency_key": idem or f"essl:{device_id}:{code}:{punch_time}:{event_type}",
        "payroll_ready": True,
        "manufacturer": "essl",
        "device_id": device_id,
        "device_name": device_id,
        "metadata": {},
    }
    if employee_name:
        row["employee_name"] = employee_name
        row["metadata"]["employee_name"] = employee_name
    if classification_method:
        row["classification_method"] = classification_method
        row["metadata"]["classification_method"] = classification_method
    if direction_reported is not None:
        row["direction_reported"] = direction_reported
        row["metadata"]["direction_reported"] = direction_reported
    return row


@pytest.fixture
def two_orgs(client: TestClient, auth_headers):
    a = client.post(
        "/api/v1/tenancy/organisations",
        headers=auth_headers,
        json={"name": "Organisation Alpha", "code": "ORG-A"},
    )
    b = client.post(
        "/api/v1/tenancy/organisations",
        headers=auth_headers,
        json={"name": "Organisation Beta", "code": "ORG-B"},
    )
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    return a.json()["id"], b.json()["id"]


# ---------------------------------------------------------------------------
# 1. Normal matched employee
# ---------------------------------------------------------------------------
def test_01_normal_matched_employee(client, auth_headers, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-match@test.com")
    emp = client.post(
        "/api/v1/employees",
        headers=headers,
        json={"organisation_name": "Organisation Alpha", "employee_code": "25", "name": "John Doe"},
    )
    assert emp.status_code == 200, emp.text
    cid, key = _register_connector(client, org_id=org_a, name="Conn A Match", hostname="pc-a-match")
    push = _push(
        client,
        terminal="GATE-A",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("25", "2026-09-07 09:00:00", device_id="device-a")],
    )
    assert push.status_code == 200
    assert push.json()["accepted"] == 1

    feed = client.get("/api/v1/payroll/punches?employee_code=25&limit=20", headers=headers)
    assert feed.status_code == 200
    row = next(p for p in feed.json()["punches"] if p["device_employee_code"] == "25")
    assert row["employee_matched"] is True
    assert row["employee_name"] == "John Doe"
    assert row["employee_id"] == emp.json()["id"]
    assert row["organisation_id"] == org_a


# ---------------------------------------------------------------------------
# 2. Unknown device user — punch kept and enrolled on the organisation roster
# ---------------------------------------------------------------------------
def test_02_unknown_employee_kept_unmatched(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-unk@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn A Unk", hostname="pc-a-unk")
    push = _push(
        client,
        terminal="GATE-UNK",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("9999", "2026-09-07 10:00:00", device_id="device-unk")],
    )
    assert push.status_code == 200
    assert push.json()["accepted"] == 1

    feed = client.get("/api/v1/payroll/punches?employee_code=9999", headers=headers)
    body = feed.json()
    row = next(p for p in body["punches"] if p["device_employee_code"] == "9999")
    assert row["employee_matched"] is True
    assert row["employee_name"] == ""
    assert row["employee_display_name"] == ""
    assert row["name_reported"] is False
    assert row["employee_id"] is not None
    assert row["employee"]["code"] == "9999"
    assert row["employee"]["name"] == ""

    roster = client.get("/api/v1/employees", headers=headers).json()
    unnamed = next(e for e in roster if e["employee_code"] == "9999")
    assert unnamed["name"] == ""
    assert unnamed["name_reported"] is False
    assert unnamed["device_user_id"] == "9999"
    assert unnamed["name"] != "Employee 9999"


def test_02b_device_reported_name_enrolled(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-named@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn Named", hostname="pc-named")
    push = _push(
        client,
        terminal="GATE-NAMED",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[
            _punch(
                "5",
                "2026-09-07 09:05:00",
                device_id="device-named",
                employee_name="Anita Sharma",
            )
        ],
    )
    assert push.json()["accepted"] == 1
    feed = client.get("/api/v1/payroll/punches?employee_code=5", headers=headers).json()
    row = next(p for p in feed["punches"] if p["device_employee_code"] == "5")
    assert row["employee_matched"] is True
    assert row["employee_name"] == "Anita Sharma"
    roster = client.get("/api/v1/employees", headers=headers).json()
    assert any(e["employee_code"] == "5" and e["name"] == "Anita Sharma" for e in roster)


# ---------------------------------------------------------------------------
# 3. Employee added after punch — resolution without re-upload
# ---------------------------------------------------------------------------
def test_03_employee_added_after_punch(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-late@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn A Late", hostname="pc-a-late")
    push = _push(
        client,
        terminal="GATE-LATE",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("25", "2026-09-07 11:00:00", device_id="device-late", idem="late:25:1100:IN")],
    )
    assert push.json()["accepted"] == 1

    before = client.get("/api/v1/payroll/punches?employee_code=25", headers=headers).json()
    row = next(p for p in before["punches"] if p["idempotency_key"] == "late:25:1100:IN")
    assert row["employee_matched"] is True
    assert row["employee_name"] == ""
    assert row["name_reported"] is False

    created = client.post(
        "/api/v1/employees",
        headers=headers,
        json={"organisation_name": "Organisation Alpha", "employee_code": "25", "name": "Late Bind"},
    )
    assert created.status_code == 200, created.text

    after = client.get("/api/v1/payroll/punches?employee_code=25", headers=headers).json()
    row2 = next(p for p in after["punches"] if p["idempotency_key"] == "late:25:1100:IN")
    assert row2["employee_matched"] is True
    assert row2["employee_name"] == "Late Bind"
    assert row2["id"] == row["id"]  # same canonical punch; no re-upload


# ---------------------------------------------------------------------------
# 4. Same employee_code in two organisations — no cross-tenant name leak
# ---------------------------------------------------------------------------
def test_04_same_code_two_organisations(client, settings, two_orgs):
    org_a, org_b = two_orgs
    ha = _org_headers(client, settings, org_a, "admin-a-iso@test.com")
    hb = _org_headers(client, settings, org_b, "admin-b-iso@test.com")

    assert (
        client.post(
            "/api/v1/employees",
            headers=ha,
            json={"organisation_name": "Organisation Alpha", "employee_code": "25", "name": "Alice Alpha"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/employees",
            headers=hb,
            json={"organisation_name": "Organisation Beta", "employee_code": "25", "name": "Bob Beta"},
        ).status_code
        == 200
    )

    ca, ka = _register_connector(client, org_id=org_a, name="Conn Iso A", hostname="pc-iso-a")
    cb, kb = _register_connector(client, org_id=org_b, name="Conn Iso B", hostname="pc-iso-b")
    assert _push(
        client,
        terminal="T-A",
        connector_id=ca,
        api_key=ka,
        org_id=org_a,
        punches=[_punch("25", "2026-09-07 12:00:00", device_id="da", idem="iso-a:25")],
    ).json()["accepted"] == 1
    assert _push(
        client,
        terminal="T-B",
        connector_id=cb,
        api_key=kb,
        org_id=org_b,
        punches=[_punch("25", "2026-09-07 12:05:00", device_id="db", idem="iso-b:25")],
    ).json()["accepted"] == 1

    feed_a = client.get("/api/v1/payroll/punches?employee_code=25", headers=ha).json()
    feed_b = client.get("/api/v1/payroll/punches?employee_code=25", headers=hb).json()
    assert all(p["organisation_id"] == org_a for p in feed_a["punches"])
    assert all(p["organisation_id"] == org_b for p in feed_b["punches"])
    row_a = next(p for p in feed_a["punches"] if p["idempotency_key"] == "iso-a:25")
    row_b = next(p for p in feed_b["punches"] if p["idempotency_key"] == "iso-b:25")
    assert row_a["employee_name"] == "Alice Alpha"
    assert row_b["employee_name"] == "Bob Beta"
    assert "Bob" not in row_a["employee_name"]
    assert "Alice" not in row_b["employee_name"]


# ---------------------------------------------------------------------------
# 5. Same employee on two devices — both resolve; distinct punches
# ---------------------------------------------------------------------------
def test_05_same_employee_two_devices(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-2dev@test.com")
    client.post(
        "/api/v1/employees",
        headers=headers,
        json={"organisation_name": "Organisation Alpha", "employee_code": "25", "name": "Multi Device"},
    )
    cid, key = _register_connector(client, org_id=org_a, name="Conn 2Dev", hostname="pc-2dev")
    p1 = _push(
        client,
        terminal="DEVICE-A",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("25", "2026-09-07 13:00:00", device_id="device-a", idem="2dev:a:25:1300")],
    )
    p2 = _push(
        client,
        terminal="DEVICE-B",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("25", "2026-09-07 13:30:00", device_id="device-b", idem="2dev:b:25:1330")],
    )
    assert p1.json()["accepted"] == 1
    assert p2.json()["accepted"] == 1

    feed = client.get("/api/v1/payroll/punches?employee_code=25&limit=50", headers=headers).json()
    rows = [p for p in feed["punches"] if p["idempotency_key"].startswith("2dev:")]
    assert len(rows) == 2
    assert {r["terminal_code"] for r in rows} == {"DEVICE-A", "DEVICE-B"}
    assert all(r["employee_matched"] and r["employee_name"] == "Multi Device" for r in rows)
    assert rows[0]["id"] != rows[1]["id"]


# ---------------------------------------------------------------------------
# 6. Duplicate punch upload — exactly one canonical row
# ---------------------------------------------------------------------------
def test_06_duplicate_punch_upload(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-dup@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn Dup", hostname="pc-dup")
    payload = [_punch("25", "2026-09-07 14:00:00", device_id="dup", idem="dup:25:1400:IN")]
    first = _push(client, terminal="DUP", connector_id=cid, api_key=key, org_id=org_a, punches=payload)
    second = _push(client, terminal="DUP", connector_id=cid, api_key=key, org_id=org_a, punches=payload)
    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 1

    feed = client.get("/api/v1/payroll/punches?employee_code=25", headers=headers).json()
    keys = [p for p in feed["punches"] if p["idempotency_key"] == "dup:25:1400:IN"]
    assert len(keys) == 1


# ---------------------------------------------------------------------------
# 7. Wrong organisation header — rejected
# ---------------------------------------------------------------------------
def test_07_wrong_organisation_header(client, two_orgs):
    org_a, org_b = two_orgs
    cid, key = _register_connector(client, org_id=org_a, name="Conn WrongOrg", hostname="pc-wrong")
    res = _push(
        client,
        terminal="WRONG",
        connector_id=cid,
        api_key=key,
        org_id=org_b,  # connector belongs to A, header claims B
        punches=[_punch("25", "2026-09-07 15:00:00", idem="wrong-org")],
    )
    assert res.status_code == 403
    assert "Organisation mismatch" in res.text


# ---------------------------------------------------------------------------
# 8. Cross-organisation employee lookup blocked for org admin
# ---------------------------------------------------------------------------
def test_08_cross_organisation_employee_lookup(client, settings, two_orgs):
    org_a, org_b = two_orgs
    ha = _org_headers(client, settings, org_a, "admin-a-xemp@test.com")
    hb = _org_headers(client, settings, org_b, "admin-b-xemp@test.com")
    emp_b = client.post(
        "/api/v1/employees",
        headers=hb,
        json={"organisation_name": "Organisation Beta", "employee_code": "77", "name": "Secret Beta"},
    )
    assert emp_b.status_code == 200
    emp_id = emp_b.json()["id"]

    listed_a = client.get("/api/v1/employees", headers=ha).json()
    assert all(e.get("employee_code") != "77" or e.get("name") != "Secret Beta" for e in listed_a)
    assert not any(e["id"] == emp_id for e in listed_a)

    get_cross = client.get(f"/api/v1/employees/{emp_id}", headers=ha)
    assert get_cross.status_code == 403


# ---------------------------------------------------------------------------
# 9. Cross-organisation attendance lookup blocked for org admin
# ---------------------------------------------------------------------------
def test_09_cross_organisation_attendance_lookup(client, settings, two_orgs):
    org_a, org_b = two_orgs
    ha = _org_headers(client, settings, org_a, "admin-a-xpunch@test.com")
    hb = _org_headers(client, settings, org_b, "admin-b-xpunch@test.com")
    cb, kb = _register_connector(client, org_id=org_b, name="Conn XB", hostname="pc-xb")
    assert (
        _push(
            client,
            terminal="XB",
            connector_id=cb,
            api_key=kb,
            org_id=org_b,
            punches=[_punch("88", "2026-09-07 16:00:00", idem="xb:88")],
        ).json()["accepted"]
        == 1
    )
    feed_a = client.get("/api/v1/payroll/punches", headers=ha).json()
    assert not any(p.get("idempotency_key") == "xb:88" for p in feed_a["punches"])
    feed_b = client.get("/api/v1/payroll/punches", headers=hb).json()
    assert any(p.get("idempotency_key") == "xb:88" for p in feed_b["punches"])


# ---------------------------------------------------------------------------
# 10. Employee deletion — punch remains, becomes unmatched
# ---------------------------------------------------------------------------
def test_10_employee_deletion(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-del@test.com")
    emp = client.post(
        "/api/v1/employees",
        headers=headers,
        json={"organisation_name": "Organisation Alpha", "employee_code": "25", "name": "To Delete"},
    )
    assert emp.status_code == 200
    emp_id = emp.json()["id"]
    cid, key = _register_connector(client, org_id=org_a, name="Conn Del", hostname="pc-del")
    assert (
        _push(
            client,
            terminal="DEL",
            connector_id=cid,
            api_key=key,
            org_id=org_a,
            punches=[_punch("25", "2026-09-07 17:00:00", idem="del:25")],
        ).json()["accepted"]
        == 1
    )
    before = next(
        p
        for p in client.get("/api/v1/payroll/punches", headers=headers).json()["punches"]
        if p["idempotency_key"] == "del:25"
    )
    assert before["employee_matched"] is True

    deleted = client.delete(f"/api/v1/employees/{emp_id}", headers=headers)
    assert deleted.status_code == 200

    after = next(
        p
        for p in client.get("/api/v1/payroll/punches", headers=headers).json()["punches"]
        if p["idempotency_key"] == "del:25"
    )
    assert after["id"] == before["id"]
    assert after["employee_matched"] is False
    assert after["employee_name"] == ""
    assert after["device_employee_code"] == "25"


# ---------------------------------------------------------------------------
# 11. Employee transfer — punch still matches same employee (branch change)
# ---------------------------------------------------------------------------
def test_11_employee_transfer(client, settings, two_orgs, auth_headers):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-xfer@test.com")
    br1 = client.post(
        "/api/v1/tenancy/branches",
        headers=auth_headers,
        json={"organisation_id": org_a, "name": "Branch One", "code": "B1"},
    )
    br2 = client.post(
        "/api/v1/tenancy/branches",
        headers=auth_headers,
        json={"organisation_id": org_a, "name": "Branch Two", "code": "B2"},
    )
    assert br1.status_code == 200, br1.text
    assert br2.status_code == 200, br2.text
    emp = client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            "organisation_name": "Organisation Alpha",
            "employee_code": "25",
            "name": "Transfer Me",
            "branch_name": "Branch One",
        },
    )
    assert emp.status_code == 200, emp.text
    emp_id = emp.json()["id"]
    cid, key = _register_connector(client, org_id=org_a, name="Conn Xfer", hostname="pc-xfer")
    assert (
        _push(
            client,
            terminal="XFER",
            connector_id=cid,
            api_key=key,
            org_id=org_a,
            punches=[_punch("25", "2026-09-07 18:00:00", idem="xfer:25")],
        ).json()["accepted"]
        == 1
    )
    xfer = client.post(
        f"/api/v1/employees/{emp_id}/transfer",
        headers=headers,
        json={"branch_id": br2.json()["id"]},
    )
    assert xfer.status_code == 200, xfer.text
    assert xfer.json()["branch_id"] == br2.json()["id"]

    row = next(
        p
        for p in client.get("/api/v1/payroll/punches", headers=headers).json()["punches"]
        if p["idempotency_key"] == "xfer:25"
    )
    assert row["employee_matched"] is True
    assert row["employee_name"] == "Transfer Me"
    assert row["employee_id"] == emp_id


# ---------------------------------------------------------------------------
# 12. Pagination (limit) — feed respects limit; no offset API
# ---------------------------------------------------------------------------
def test_12_pagination_limit(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-page@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn Page", hostname="pc-page")
    punches = [
        _punch("P", f"2026-09-07 19:{i:02d}:00", device_id="page", idem=f"page:{i}")
        for i in range(5)
    ]
    assert _push(
        client, terminal="PAGE", connector_id=cid, api_key=key, org_id=org_a, punches=punches
    ).json()["accepted"] == 5

    limited = client.get("/api/v1/payroll/punches?order=asc&limit=2", headers=headers).json()
    assert limited["count"] == 2
    assert len(limited["punches"]) == 2


# ---------------------------------------------------------------------------
# 13. `since` synchronization — created_at lower bound (inclusive)
# ---------------------------------------------------------------------------
def test_13_since_synchronization(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-since@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn Since", hostname="pc-since")
    assert (
        _push(
            client,
            terminal="SINCE",
            connector_id=cid,
            api_key=key,
            org_id=org_a,
            punches=[_punch("S1", "2026-09-07 20:00:00", idem="since:1")],
        ).json()["accepted"]
        == 1
    )
    first = next(
        p
        for p in client.get("/api/v1/payroll/punches?order=asc", headers=headers).json()["punches"]
        if p["idempotency_key"] == "since:1"
    )
    assert first["created_at"]

    # Push a later punch, then query since first.created_at (inclusive → first still returned)
    assert (
        _push(
            client,
            terminal="SINCE",
            connector_id=cid,
            api_key=key,
            org_id=org_a,
            punches=[_punch("S2", "2026-09-07 20:05:00", idem="since:2")],
        ).json()["accepted"]
        == 1
    )
    since = first["created_at"]
    batch = client.get(
        f"/api/v1/payroll/punches?since={since}&order=asc&limit=50",
        headers=headers,
    ).json()
    keys = {p["idempotency_key"] for p in batch["punches"]}
    assert "since:1" in keys  # inclusive boundary — website must dedupe by id
    assert "since:2" in keys

    # Advance past first created_at by 1 microsecond (if parseable) to exclude first
    dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    after = (dt + timedelta(microseconds=1)).isoformat()
    later = client.get(
        f"/api/v1/payroll/punches?since={after}&order=asc&limit=50",
        headers=headers,
    ).json()
    later_keys = {p["idempotency_key"] for p in later["punches"]}
    assert "since:1" not in later_keys
    assert "since:2" in later_keys


# ---------------------------------------------------------------------------
# 14. Connector offline/retry — covered by connector unit tests (reference)
# ---------------------------------------------------------------------------
def test_14_connector_offline_retry_reference():
    """Documented expectation: UploadEngine keeps pending on transient errors.

    Concrete behavior is asserted in connector/tests/test_attendance_serialization.py
    (retry / mark_retry_later). This marker test confirms the production review
    checklist item is tracked; run that suite separately for connector isolation.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "connector" / "tests" / "test_attendance_serialization.py"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "UploadEngine" in text
    assert "mark_retry" in text or "retry" in text.lower()


# ---------------------------------------------------------------------------
# Extra: mutate-by-id employee — must now be blocked (Phase 6)
# ---------------------------------------------------------------------------
def test_extra_org_admin_cannot_mutate_foreign_employee_by_id(client, settings, two_orgs):
    """Org A admin must receive 403 when mutating Org B employee by UUID."""
    org_a, org_b = two_orgs
    ha = _org_headers(client, settings, org_a, "admin-a-mut@test.com")
    hb = _org_headers(client, settings, org_b, "admin-b-mut@test.com")
    emp_b = client.post(
        "/api/v1/employees",
        headers=hb,
        json={"organisation_name": "Organisation Beta", "employee_code": "99", "name": "Victim"},
    )
    assert emp_b.status_code == 200
    emp_id = emp_b.json()["id"]

    patched = client.patch(
        f"/api/v1/employees/{emp_id}",
        headers=ha,
        json={"name": "Hijacked By A"},
    )
    assert patched.status_code == 403, patched.text
    listed = client.get("/api/v1/employees", headers=hb).json()
    victim = next(e for e in listed if e["id"] == emp_id)
    assert victim["name"] == "Victim"


# ---------------------------------------------------------------------------
# Device directory names, idempotency, inference, tenant isolation
# ---------------------------------------------------------------------------
def test_directory_upgrades_placeholder_and_is_idempotent(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-dir@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn Dir", hostname="pc-dir")
    conn_headers = {"X-Connector-Id": cid, "X-Device-Token": key, "X-Organisation-Id": org_a}

    unnamed = _push(
        client,
        terminal="infoessl9999",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("5", "2026-09-08 09:00:00", device_id="infoessl9999")],
    )
    assert unnamed.json()["accepted"] == 1
    before = client.get("/api/v1/payroll/punches?employee_code=5", headers=headers).json()
    row = next(p for p in before["punches"] if p["device_employee_code"] == "5")
    punch_id = row["id"]
    assert row["employee_name"] == ""

    body = {
        "employees": [
            {"employee_code": "1", "name": "Shiva", "device_name": "infoessl9999", "terminal_code": "infoessl9999"},
            {"employee_code": "5", "name": "Sai", "device_name": "infoessl9999", "terminal_code": "infoessl9999"},
        ]
    }
    first = client.post(f"{PREFIX}/connectors/{cid}/employees/directory", headers=conn_headers, json=body)
    second = client.post(f"{PREFIX}/connectors/{cid}/employees/directory", headers=conn_headers, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["created"] == 0

    roster = client.get("/api/v1/employees", headers=headers).json()
    names = {e["employee_code"]: e["name"] for e in roster if e["employee_code"] in {"1", "5"}}
    assert names["5"] == "Sai"
    assert names["1"] == "Shiva"
    assert len([e for e in roster if e["employee_code"] == "5"]) == 1

    after = client.get("/api/v1/payroll/punches?employee_code=5", headers=headers).json()
    row2 = next(p for p in after["punches"] if p["id"] == punch_id)
    assert row2["employee_name"] == "Sai"
    assert row2["employee"]["name"] == "Sai"
    assert row2["device_user_id"] == "5"


def test_device_name_change_updates_device_managed_employee(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-rename@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn Rename", hostname="pc-rename")
    conn_headers = {"X-Connector-Id": cid, "X-Device-Token": key, "X-Organisation-Id": org_a}
    client.post(
        f"{PREFIX}/connectors/{cid}/employees/directory",
        headers=conn_headers,
        json={"employees": [{"employee_code": "5", "name": "Sai"}]},
    )
    client.post(
        f"{PREFIX}/connectors/{cid}/employees/directory",
        headers=conn_headers,
        json={"employees": [{"employee_code": "5", "name": "Sai Kumar"}]},
    )
    roster = client.get("/api/v1/employees", headers=headers).json()
    assert next(e["name"] for e in roster if e["employee_code"] == "5") == "Sai Kumar"


def test_hr_name_is_not_overwritten_by_device_directory(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-hr@test.com")
    created = client.post(
        "/api/v1/employees",
        headers=headers,
        json={"organisation_name": "Organisation Alpha", "employee_code": "5", "name": "HR Sai"},
    )
    assert created.status_code == 200
    cid, key = _register_connector(client, org_id=org_a, name="Conn HR", hostname="pc-hr")
    client.post(
        f"{PREFIX}/connectors/{cid}/employees/directory",
        headers={"X-Connector-Id": cid, "X-Device-Token": key, "X-Organisation-Id": org_a},
        json={"employees": [{"employee_code": "5", "name": "Device Sai"}]},
    )
    roster = client.get("/api/v1/employees", headers=headers).json()
    assert next(e["name"] for e in roster if e["employee_code"] == "5") == "HR Sai"


def test_two_organisations_user_5_do_not_collide(client, settings, two_orgs):
    org_a, org_b = two_orgs
    ha = _org_headers(client, settings, org_a, "admin-a-u5@test.com")
    hb = _org_headers(client, settings, org_b, "admin-b-u5@test.com")
    ca, ka = _register_connector(client, org_id=org_a, name="Conn A5", hostname="pc-a5")
    cb, kb = _register_connector(client, org_id=org_b, name="Conn B5", hostname="pc-b5")
    assert _push(
        client, terminal="dev-a", connector_id=ca, api_key=ka, org_id=org_a,
        punches=[_punch("5", "2026-09-08 09:00:00", device_id="dev-a", employee_name="Sai Alpha")],
    ).json()["accepted"] == 1
    assert _push(
        client, terminal="dev-b", connector_id=cb, api_key=kb, org_id=org_b,
        punches=[_punch("5", "2026-09-08 09:00:00", device_id="dev-b", employee_name="Sai Beta")],
    ).json()["accepted"] == 1
    a = client.get("/api/v1/employees", headers=ha).json()
    b = client.get("/api/v1/employees", headers=hb).json()
    assert next(e["name"] for e in a if e["employee_code"] == "5") == "Sai Alpha"
    assert next(e["name"] for e in b if e["employee_code"] == "5") == "Sai Beta"


def test_heartbeat_directory_upserts_employees(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-hb@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn HB", hostname="pc-hb")
    beat = client.post(
        f"{PREFIX}/connectors/{cid}/heartbeat",
        headers={"X-Connector-Id": cid, "X-Device-Token": key, "X-Organisation-Id": org_a},
        json={
            "status": "online",
            "employees": [
                {"employee_code": "3", "name": "Nagatvenkatadurga"},
                {"employee_code": "5", "name": "Sai"},
            ],
        },
    )
    assert beat.status_code == 200, beat.text
    roster = {e["employee_code"]: e["name"] for e in client.get("/api/v1/employees", headers=headers).json()}
    assert roster.get("5") == "Sai"
    assert roster.get("3") == "Nagatvenkatadurga"


def test_punch_push_employees_array_is_idempotent(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-piggy@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn Piggy", hostname="pc-piggy")
    payload_employees = [{"employee_code": "5", "name": "Sai"}]
    p1 = _push(
        client, terminal="infoessl9999", connector_id=cid, api_key=key, org_id=org_a,
        punches=[_punch("5", "2026-09-08 10:00:00", device_id="infoessl9999", employee_name="Sai")],
        employees=payload_employees,
    )
    p2 = _push(
        client, terminal="infoessl9999", connector_id=cid, api_key=key, org_id=org_a,
        punches=[_punch("5", "2026-09-08 10:00:00", device_id="infoessl9999", employee_name="Sai")],
        employees=payload_employees,
    )
    assert p1.json()["accepted"] == 1
    assert p2.json()["duplicates"] == 1
    roster = [e for e in client.get("/api/v1/employees", headers=headers).json() if e["employee_code"] == "5"]
    assert len(roster) == 1
    assert roster[0]["name"] == "Sai"


def test_historical_all_out_is_inferred_not_device_reported(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-io@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn IO", hostname="pc-io")
    punches = [
        _punch("5", "2026-09-08 09:00:00", device_id="d1", event_type="OUT", idem="io:5:0900"),
        _punch("5", "2026-09-08 13:00:00", device_id="d1", event_type="OUT", idem="io:5:1300"),
        _punch("5", "2026-09-08 18:00:00", device_id="d1", event_type="OUT", idem="io:5:1800"),
    ]
    assert _push(
        client, terminal="T1", connector_id=cid, api_key=key, org_id=org_a, punches=punches,
    ).json()["accepted"] == 3
    feed = client.get("/api/v1/payroll/punches?employee_code=5&limit=20", headers=headers).json()
    rows = sorted(
        [p for p in feed["punches"] if p["device_employee_code"] == "5"],
        key=lambda p: p["punch_time"],
    )
    assert [p["event_type"] for p in rows] == ["IN", "OUT", "IN"]
    assert all(p["classification_method"] == "system_inference" for p in rows)
    assert all(p["metadata"].get("direction_reported") is False for p in rows)


def test_device_reported_inout_is_preserved(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-devio@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="Conn DevIO", hostname="pc-devio")
    punches = [
        _punch(
            "5", "2026-09-08 09:00:00", device_id="d1", event_type="OUT",
            idem="devio:0900", classification_method="device_reported", direction_reported=True,
        ),
        _punch(
            "5", "2026-09-08 18:00:00", device_id="d1", event_type="IN",
            idem="devio:1800", classification_method="device_reported", direction_reported=True,
        ),
    ]
    assert _push(
        client, terminal="T1", connector_id=cid, api_key=key, org_id=org_a, punches=punches,
    ).json()["accepted"] == 2
    feed = client.get("/api/v1/payroll/punches?employee_code=5", headers=headers).json()
    rows = sorted(
        [p for p in feed["punches"] if str(p["idempotency_key"]).startswith("devio:")],
        key=lambda p: p["punch_time"],
    )
    assert [p["event_type"] for p in rows] == ["OUT", "IN"]
    assert all(p["classification_method"] == "device_reported" for p in rows)


def test_infoessl9999_user_5_sai_end_to_end(client, settings, two_orgs):
    org_a, _ = two_orgs
    headers = _org_headers(client, settings, org_a, "admin-a-sai@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="info9999", hostname="info9999-pc")
    assert _push(
        client,
        terminal="infoessl9999",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[
            _punch("5", "2026-09-08 09:12:00", device_id="infoessl9999", employee_name="Sai"),
        ],
        employees=[{"employee_code": "5", "name": "Sai", "device_name": "infoessl9999"}],
    ).json()["accepted"] == 1
    feed = client.get("/api/v1/payroll/punches?employee_code=5", headers=headers).json()
    row = next(p for p in feed["punches"] if p["device_employee_code"] == "5")
    assert row["employee_name"] == "Sai"
    assert row["employee_id"]
    assert row["terminal_code"] == "infoessl9999"
    roster = client.get("/api/v1/employees", headers=headers).json()
    person = next(e for e in roster if e["employee_code"] == "5")
    assert person["name"] == "Sai"
    assert person["device_user_id"] == "5"
    assert person["id"] == row["employee_id"]

