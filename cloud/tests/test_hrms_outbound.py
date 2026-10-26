"""Cloud → Brixigo.com outbound attendance sync tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import AttendancePunch, HrmsAttendanceOutbox, HrmsSyncStatus, Organisation, User
from app.security.auth import hash_password
from app.services.hrms_outbound import enqueue_punch, sync_pending, backfill_pending
from app.services.hrms_payload import (
    CLASSIFICATION_DEVICE,
    CLASSIFICATION_INFERRED,
    build_hrms_punch,
    organisation_external_code,
    normalize_classification,
)
from tests.test_production_attendance_flow import (
    PREFIX,
    _org_headers,
    _punch,
    _push,
    _register_connector,
)


def _session(settings):
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _enable_hrms(settings, monkeypatch, *, url="http://hrms.test"):
    monkeypatch.setattr(settings, "hrms_enabled", True)
    monkeypatch.setattr(settings, "hrms_base_url", url)
    monkeypatch.setattr(settings, "hrms_api_key", "test-cloud-key-value")
    monkeypatch.setattr(settings, "hrms_timeout_seconds", 5)
    monkeypatch.setattr(settings, "hrms_batch_size", 50)
    monkeypatch.setattr(settings, "hrms_max_attempts", 8)


class FakeHrms:
    def __init__(self):
        self.calls = 0
        self.bodies: list[dict] = []
        self.headers: list[dict] = []
        self.status_code = 200
        self.raise_timeout = False
        self.known_orgs = {"org-a", "infoessl9999"}
        self.known_employees = {("org-a", "25"), ("infoessl9999", "5"), ("org-a", "1")}
        self.seen: dict[str, dict] = {}
        self.fail_once = 0
        self.drop_last = False
        self.force_status: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.headers.append(dict(request.headers))
        if self.raise_timeout:
            raise httpx.TimeoutException("hrms timeout")
        if self.fail_once:
            self.fail_once -= 1
            return httpx.Response(503, json={"detail": "down"})
        if request.headers.get("x-brixigo-cloud-key") != "test-cloud-key-value":
            return httpx.Response(401, json={"detail": "Invalid Cloud integration key"})
        payload = json.loads(request.content.decode("utf-8"))
        self.bodies.append(payload)
        results = []
        punches = payload.get("punches") or []
        for idx, punch in enumerate(punches):
            if self.drop_last and idx == len(punches) - 1:
                continue
            pid = str(punch.get("source_punch_id") or "")
            org = str(punch.get("organisation_external_code") or "")
            code = str(punch.get("employee_code") or punch.get("device_user_id") or "")
            if self.force_status:
                results.append({"status": self.force_status, "source_punch_id": pid, "reason": "forced"})
                continue
            if org not in self.known_orgs:
                results.append(
                    {
                        "status": "organisation_unmatched",
                        "source_punch_id": pid,
                        "organisation_external_code": org,
                        "reason": "organisation_external_code_not_found",
                    }
                )
                continue
            if (org, code) not in self.known_employees:
                results.append(
                    {
                        "status": "employee_unmatched",
                        "source_punch_id": pid,
                        "organisation_external_code": org,
                        "employee_code": code,
                        "reason": "employee_not_found",
                    }
                )
                continue
            if pid in self.seen:
                prev = self.seen[pid]
                results.append({**prev, "status": "duplicate", "reason": "already_synced"})
                continue
            event = {
                "status": "synced",
                "source_punch_id": pid,
                "organisation_id": f"hrms-org-{org}",
                "employee_id": f"hrms-emp-{org}-{code}",
                "event_id": f"evt-{pid[:8]}",
                "event_type": punch.get("event_type"),
                "classification_method": punch.get("classification_method"),
                "punch_time": punch.get("punch_time"),
            }
            self.seen[pid] = event
            results.append(event)
        return httpx.Response(self.status_code, json={"ok": True, "results": results, "total": len(results)})


def _client_for(fake: FakeHrms) -> httpx.Client:
    transport = httpx.MockTransport(fake.handler)
    return httpx.Client(transport=transport)


def _org_code(db, org_id: str) -> str:
    return db.get(Organisation, org_id).code


@pytest.fixture
def org_a(client, auth_headers):
    res = client.post(
        "/api/v1/tenancy/organisations",
        headers=auth_headers,
        json={"name": "Alpha Display Name", "code": "org-a"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


@pytest.fixture
def org_b(client, auth_headers):
    res = client.post(
        "/api/v1/tenancy/organisations",
        headers=auth_headers,
        json={"name": "Brixigo Demo Organisation", "code": "org-b"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_classification_not_converted():
    assert normalize_classification("device_reported") == CLASSIFICATION_DEVICE
    assert normalize_classification("system_inference") == CLASSIFICATION_INFERRED
    assert normalize_classification("SYSTEM_INFERRED") == CLASSIFICATION_INFERRED


def test_org_routing_uses_code_not_display_name(settings):
    org = Organisation(id="1", name="Brixigo Demo Organisation", code="infoessl9999")
    assert organisation_external_code(org) == "infoessl9999"
    org.settings_json = {"hrms_external_code": "mapped-code"}
    assert organisation_external_code(org) == "mapped-code"


def _push_one(client, settings, org_id, email, code, punch_time, *, terminal="GATE-1", **punch_kw):
    headers = _org_headers(client, settings, org_id, email)
    cid, key = _register_connector(client, org_id=org_id, name=f"c-{email}", hostname=email)
    push = _push(
        client,
        terminal=terminal,
        connector_id=cid,
        api_key=key,
        org_id=org_id,
        punches=[_punch(code, punch_time, **punch_kw)],
        employees=[{"employee_code": code, "name": punch_kw.get("employee_name") or ""}],
    )
    assert push.status_code == 200, push.text
    return headers, cid, key


def test_01_org_mapping_uses_stable_code(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "map-org@test.com", "25", "2026-09-08T09:00:00+05:30", employee_name="John")
    fake = FakeHrms()
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["synced"] == 1
        assert fake.bodies[0]["punches"][0]["organisation_external_code"] == "org-a"
        assert fake.bodies[0]["punches"][0]["organisation_external_code"] != "Alpha Display Name"
    finally:
        db.close()


def test_02_employee_mapping_uses_code_not_name(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "map-emp@test.com", "25", "2026-09-08T09:01:00+05:30", employee_name="John Doe")
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        punch = fake.bodies[0]["punches"][0]
        assert punch["employee_code"] == "25"
        assert punch["device_user_id"] == "25"
        assert punch["employee_name"] == "John Doe"
    finally:
        db.close()


def test_03_cloud_punch_to_hrms_attendance(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "punch-hrms@test.com", "25", "2026-09-08T09:02:00+05:30", employee_name="John")
    fake = FakeHrms()
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["synced"] == 1
        row = db.scalar(select(HrmsAttendanceOutbox))
        assert row.status == HrmsSyncStatus.SYNCED.value
        assert row.hrms_event_id.startswith("evt-")
        assert row.hrms_employee_id.endswith("-25")
    finally:
        db.close()


def test_04_sai_device_user_5_end_to_end(client, settings, monkeypatch, auth_headers):
    _enable_hrms(settings, monkeypatch)
    org = client.post(
        "/api/v1/tenancy/organisations",
        headers=auth_headers,
        json={"name": "Cloud Tenant", "code": "infoessl9999"},
    ).json()
    headers, _, _ = _push_one(
        client,
        settings,
        org["id"],
        "sai@test.com",
        "5",
        "2026-09-08T10:19:57+05:30",
        employee_name="Sai",
        device_id="infoessl9999",
        classification_method="system_inference",
        direction_reported=False,
        terminal="infoessl9999",
    )
    fake = FakeHrms()
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["synced"] == 1
        body = fake.bodies[0]["punches"][0]
        assert body["organisation_external_code"] == "infoessl9999"
        assert body["employee_code"] == "5"
        assert body["employee_name"] == "Sai"
        assert body["punch_time"] == "2026-09-08T10:19:57+05:30"
        assert body["event_type"] in {"IN", "OUT"}
        assert body["classification_method"] == CLASSIFICATION_INFERRED
        feed = client.get("/api/v1/payroll/punches?employee_code=5&today=false", headers=headers).json()
        assert feed["punches"][0]["hrms_sync_status"] == "synced"
    finally:
        db.close()


def test_05_multiple_employees(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    headers = _org_headers(client, settings, org_a, "multi-emp@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="multi", hostname="multi")
    fake = FakeHrms()
    fake.known_employees.add(("org-a", "5"))
    _push(
        client,
        terminal="GATE-M",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[
            _punch("25", "2026-09-08T09:10:00+05:30", employee_name="John", device_id="d1"),
            _punch("5", "2026-09-08T09:11:00+05:30", employee_name="Sai", device_id="d1"),
        ],
        employees=[{"employee_code": "25", "name": "John"}, {"employee_code": "5", "name": "Sai"}],
    )
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["synced"] == 2
        codes = {p["employee_code"] for p in fake.bodies[0]["punches"]}
        assert codes == {"25", "5"}
    finally:
        db.close()
    _ = headers


def test_06_multiple_organisations_isolated(client, settings, monkeypatch, org_a, org_b):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "iso-a@test.com", "25", "2026-09-08T09:20:00+05:30", terminal="GATE-A", device_id="dev-iso-a")
    _push_one(client, settings, org_b, "iso-b@test.com", "25", "2026-09-08T09:21:00+05:30", terminal="GATE-B", device_id="dev-iso-b")
    fake = FakeHrms()
    fake.known_orgs.add("org-b")
    fake.known_employees.add(("org-b", "25"))
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        orgs = {p["organisation_external_code"] for b in fake.bodies for p in b["punches"]}
        assert "org-a" in orgs and "org-b" in orgs
        assert stats["synced"] >= 2
    finally:
        db.close()


def test_07_unknown_organisation(client, settings, monkeypatch, org_b):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_b, "unk-org@test.com", "25", "2026-09-08T09:30:00+05:30")
    fake = FakeHrms()
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["organisation_unmatched"] == 1
        row = db.scalar(select(HrmsAttendanceOutbox))
        assert row.status == HrmsSyncStatus.ORGANISATION_UNMATCHED.value
        assert row.hrms_organisation_id == ""
    finally:
        db.close()


def test_08_unknown_employee(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "unk-emp@test.com", "99", "2026-09-08T09:31:00+05:30", employee_name="Ghost")
    fake = FakeHrms()
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["employee_unmatched"] == 1
        row = db.scalar(select(HrmsAttendanceOutbox))
        assert row.status == HrmsSyncStatus.EMPLOYEE_UNMATCHED.value
        assert row.employee_code == "99"
        assert "Ghost" in row.employee_name or row.employee_name == "Ghost"
    finally:
        db.close()


def test_09_duplicate_cloud_punch_one_outbox(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    headers = _org_headers(client, settings, org_a, "dup@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="dup", hostname="dup")
    punch = _punch("25", "2026-09-08T09:40:00+05:30", idem="stable-dup-key")
    for _ in range(3):
        _push(client, terminal="GATE-D", connector_id=cid, api_key=key, org_id=org_a, punches=[punch])
    db = _session(settings)
    try:
        punches = db.scalars(select(AttendancePunch)).all()
        assert len(punches) == 1
        outbox = db.scalars(select(HrmsAttendanceOutbox)).all()
        assert len(outbox) == 1
    finally:
        db.close()
    _ = headers


def test_10_retry_after_hrms_failure(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "retry@test.com", "25", "2026-09-08T09:41:00+05:30")
    fake = FakeHrms()
    fake.fail_once = 1
    db = _session(settings)
    try:
        first = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert first["failed"] == 1
        row = db.scalar(select(HrmsAttendanceOutbox))
        row.next_attempt_at = datetime.now(timezone.utc)
        db.commit()
        second = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert second["synced"] == 1
        assert db.scalar(select(HrmsAttendanceOutbox)).status == "synced"
    finally:
        db.close()


def test_11_server_timeout(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "timeout@test.com", "25", "2026-09-08T09:42:00+05:30")
    fake = FakeHrms()
    fake.raise_timeout = True
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["failed"] == 1
        assert db.scalar(select(HrmsAttendanceOutbox)).status == "failed"
        assert "timeout" in db.scalar(select(HrmsAttendanceOutbox)).last_error.lower()
    finally:
        db.close()


def test_12_partial_batch_failure(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    headers = _org_headers(client, settings, org_a, "partial@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="partial", hostname="partial")
    _push(
        client,
        terminal="GATE-P",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[
            _punch("25", "2026-09-08T09:50:00+05:30", device_id="p1"),
            _punch("25", "2026-09-08T10:50:00+05:30", device_id="p2"),
        ],
    )
    fake = FakeHrms()
    fake.drop_last = True
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        statuses = {r.status for r in db.scalars(select(HrmsAttendanceOutbox)).all()}
        assert "synced" in statuses
        assert "failed" in statuses
        assert stats["synced"] == 1
        assert stats["failed"] == 1
    finally:
        db.close()
    _ = headers


def test_13_14_timestamp_and_timezone_preserved(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    stamp = "2026-09-08T10:19:57+05:30"
    _push_one(client, settings, org_a, "tz@test.com", "25", stamp)
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        sent = fake.bodies[0]["punches"][0]
        assert sent["punch_time"] == stamp
        assert sent["normalized_timestamp"] == stamp
        assert "05:30" in sent["punch_time"]
        assert sent["timezone"]
    finally:
        db.close()


def test_15_in_event(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "in@test.com", "25", "2026-09-08T09:00:00+05:30", event_type="IN", classification_method="device_reported", direction_reported=True)
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        assert fake.bodies[0]["punches"][0]["event_type"] == "IN"
    finally:
        db.close()


def test_16_out_event(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "out@test.com", "25", "2026-09-08T18:00:00+05:30", event_type="OUT", classification_method="device_reported", direction_reported=True)
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        assert fake.bodies[0]["punches"][0]["event_type"] == "OUT"
    finally:
        db.close()


def test_17_system_inferred_not_converted(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(
        client,
        settings,
        org_a,
        "inf@test.com",
        "25",
        "2026-09-08T09:05:00+05:30",
        classification_method="system_inference",
        direction_reported=False,
    )
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        assert fake.bodies[0]["punches"][0]["classification_method"] == CLASSIFICATION_INFERRED
    finally:
        db.close()


def test_18_device_reported_not_converted(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(
        client,
        settings,
        org_a,
        "devrep@test.com",
        "25",
        "2026-09-08T09:06:00+05:30",
        event_type="IN",
        classification_method="device_reported",
        direction_reported=True,
    )
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        assert fake.bodies[0]["punches"][0]["classification_method"] == CLASSIFICATION_DEVICE
    finally:
        db.close()


def test_19_employee_name_change_keeps_code(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    headers = _org_headers(client, settings, org_a, "rename@test.com")
    emp = client.post(
        "/api/v1/employees",
        headers=headers,
        json={"organisation_name": "Alpha Display Name", "employee_code": "25", "name": "Sai"},
    )
    assert emp.status_code == 200, emp.text
    client.patch(f"/api/v1/employees/{emp.json()['id']}", headers=headers, json={"name": "Sai Kumar"})
    cid, key = _register_connector(client, org_id=org_a, name="rename", hostname="rename")
    _push(
        client,
        terminal="GATE-R",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("25", "2026-09-08T11:00:00+05:30")],
    )
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        sent = fake.bodies[0]["punches"][0]
        assert sent["employee_code"] == "25"
        assert sent["employee_name"] != "25"
    finally:
        db.close()


def test_20_organisation_name_change_keeps_code(client, settings, monkeypatch, org_a, auth_headers):
    _enable_hrms(settings, monkeypatch)
    client.patch(
        f"/api/v1/tenancy/organisations/{org_a}",
        headers=auth_headers,
        json={"name": "Completely New Display Name"},
    )
    _push_one(client, settings, org_a, "orgname@test.com", "25", "2026-09-08T11:10:00+05:30")
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        assert fake.bodies[0]["punches"][0]["organisation_external_code"] == "org-a"
    finally:
        db.close()


def test_21_22_23_restarts_reuse_outbox(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "restart@test.com", "25", "2026-09-08T12:00:00+05:30")
    db = _session(settings)
    try:
        first = db.scalars(select(HrmsAttendanceOutbox)).all()
        assert len(first) == 1
        punch = db.scalar(select(AttendancePunch))
        org = db.get(Organisation, org_a)
        enqueue_punch(db, punch, org)
        enqueue_punch(db, punch, org)
        db.commit()
        assert len(list(db.scalars(select(HrmsAttendanceOutbox)).all())) == 1
    finally:
        db.close()


def test_24_historical_backfill(client, settings, monkeypatch, org_a):
    headers = _org_headers(client, settings, org_a, "hist@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="hist", hostname="hist")
    _push(
        client,
        terminal="GATE-H",
        connector_id=cid,
        api_key=key,
        org_id=org_a,
        punches=[_punch("25", "2026-09-01T09:00:00+05:30", device_id="hist")],
    )
    db = _session(settings)
    try:
        for row in db.scalars(select(HrmsAttendanceOutbox)).all():
            db.delete(row)
        db.commit()
        created = backfill_pending(db)
        db.commit()
        assert created >= 1
    finally:
        db.close()
    _ = headers
    _ = monkeypatch


def test_25_live_sync_after_new_punch(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    headers = _org_headers(client, settings, org_a, "live@test.com")
    cid, key = _register_connector(client, org_id=org_a, name="live", hostname="live")
    fake = FakeHrms()
    _push(client, terminal="GATE-L", connector_id=cid, api_key=key, org_id=org_a, punches=[_punch("25", "2026-09-08T13:00:00+05:30", device_id="l1")])
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        first = fake.calls
        _push(client, terminal="GATE-L", connector_id=cid, api_key=key, org_id=org_a, punches=[_punch("25", "2026-09-08T18:00:00+05:30", device_id="l2", event_type="OUT")])
        sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert fake.calls > first
        assert db.scalars(select(HrmsAttendanceOutbox).where(HrmsAttendanceOutbox.status == "synced")).all()
    finally:
        db.close()
    _ = headers


def test_26_tenant_isolation(client, settings, monkeypatch, org_a, org_b):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "ten-a@test.com", "25", "2026-09-08T14:00:00+05:30", terminal="TEN-A", device_id="dev-ten-a")
    _push_one(client, settings, org_b, "ten-b@test.com", "25", "2026-09-08T14:01:00+05:30", terminal="TEN-B", device_id="dev-ten-b")
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, organisation_id=org_a, client=_client_for(fake))
        db.commit()
        codes = {p["organisation_external_code"] for b in fake.bodies for p in b["punches"]}
        assert codes == {"org-a"}
    finally:
        db.close()


def test_27_authentication_failure(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    monkeypatch.setattr(settings, "hrms_api_key", "wrong-key")
    _push_one(client, settings, org_a, "authfail@test.com", "25", "2026-09-08T14:10:00+05:30")
    fake = FakeHrms()
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["failed"] == 1
        assert "authentication" in db.scalar(select(HrmsAttendanceOutbox)).last_error.lower()
    finally:
        db.close()


def test_28_invalid_payload_from_hrms(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "invalid@test.com", "25", "2026-09-08T14:20:00+05:30")
    fake = FakeHrms()
    fake.force_status = "invalid"
    db = _session(settings)
    try:
        stats = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert stats["invalid"] == 1
        assert db.scalar(select(HrmsAttendanceOutbox)).status == "invalid"
    finally:
        db.close()


def test_29_idempotent_redelivery(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "idem@test.com", "25", "2026-09-08T14:30:00+05:30")
    fake = FakeHrms()
    db = _session(settings)
    try:
        sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        row = db.scalar(select(HrmsAttendanceOutbox))
        row.status = HrmsSyncStatus.PENDING.value
        row.next_attempt_at = datetime.now(timezone.utc)
        db.commit()
        sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert db.scalar(select(HrmsAttendanceOutbox)).status == "synced"
        assert len(fake.seen) == 1
    finally:
        db.close()


def test_30_unmatched_recovery_after_mapping(client, settings, monkeypatch, org_a):
    _enable_hrms(settings, monkeypatch)
    _push_one(client, settings, org_a, "recover@test.com", "77", "2026-09-08T14:40:00+05:30", employee_name="New Hire")
    fake = FakeHrms()
    db = _session(settings)
    try:
        first = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert first["employee_unmatched"] == 1
        fake.known_employees.add(("org-a", "77"))
        row = db.scalar(select(HrmsAttendanceOutbox))
        row.next_attempt_at = datetime.now(timezone.utc)
        db.commit()
        second = sync_pending(db, settings, client=_client_for(fake))
        db.commit()
        assert second["synced"] == 1
        assert db.scalar(select(HrmsAttendanceOutbox)).status == "synced"
    finally:
        db.close()
