"""Phase 6 — tenant isolation hardening tests for mutate-by-ID routes."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Device, User, new_id
from app.security.auth import hash_password

PREFIX = "/api/wf/biometric-enterprise"


def _make_user(
    client: TestClient,
    settings,
    *,
    org_id: str,
    email: str,
    role: str = "organisation_admin",
    password: str = "OrgAdmin!2026",
) -> dict[str, str]:
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
                    full_name=role,
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _two_orgs(client: TestClient, auth_headers):
    a = client.post(
        "/api/v1/tenancy/organisations",
        headers=auth_headers,
        json={"name": "Tenant Org A", "code": "T-ORG-A"},
    )
    b = client.post(
        "/api/v1/tenancy/organisations",
        headers=auth_headers,
        json={"name": "Tenant Org B", "code": "T-ORG-B"},
    )
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    return a.json()["id"], b.json()["id"]


def _register_connector(client: TestClient, *, org_id: str, name: str, hostname: str):
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


def _create_emp(client, headers, org_name: str, code: str, name: str):
    res = client.post(
        "/api/v1/employees",
        headers=headers,
        json={"organisation_name": org_name, "employee_code": code, "name": name},
    )
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Mandatory employee isolation
# ---------------------------------------------------------------------------
def test_employee_cross_tenant_mutations_forbidden_same_tenant_ok(client, settings, auth_headers):
    org_a, org_b = _two_orgs(client, auth_headers)
    ha = _make_user(client, settings, org_id=org_a, email="emp-a@test.com")
    hb = _make_user(client, settings, org_id=org_b, email="emp-b@test.com")

    emp_a = _create_emp(client, ha, "Tenant Org A", "25", "Alice A")
    emp_b = _create_emp(client, hb, "Tenant Org B", "25", "Bob B")
    y = emp_b["id"]

    # Cross-tenant from A → Y
    assert client.patch(f"/api/v1/employees/{y}", headers=ha, json={"name": "Hijack"}).status_code == 403
    assert client.delete(f"/api/v1/employees/{y}", headers=ha).status_code == 403
    assert client.post(f"/api/v1/employees/{y}/sync", headers=ha).status_code == 403
    assert (
        client.post(
            f"/api/v1/employees/{y}/transfer",
            headers=ha,
            json={"branch_id": None},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/employees/bulk-assign",
            headers=ha,
            json={"employee_ids": [y], "device_ids": ["dev-x"]},
        ).status_code
        == 403
    )

    # B employee unchanged
    still = client.get(f"/api/v1/employees/{y}", headers=hb)
    assert still.status_code == 200
    assert still.json()["name"] == "Bob B"

    # Same-tenant B ops succeed
    assert (
        client.patch(f"/api/v1/employees/{y}", headers=hb, json={"name": "Bob Updated"}).status_code
        == 200
    )
    assert client.post(f"/api/v1/employees/{y}/sync", headers=hb).status_code == 200
    assert (
        client.post(
            f"/api/v1/employees/{y}/transfer",
            headers=hb,
            json={"branch_id": None},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/employees/bulk-assign",
            headers=hb,
            json={"employee_ids": [y], "device_ids": ["dev-b"]},
        ).status_code
        == 200
    )

    # Unknown id — existing behavior (404 for mutate paths that require employee)
    missing = "00000000-0000-0000-0000-000000000099"
    assert client.patch(f"/api/v1/employees/{missing}", headers=ha, json={"name": "X"}).status_code == 404
    assert client.delete(f"/api/v1/employees/{missing}", headers=ha).status_code == 404
    assert client.post(f"/api/v1/employees/{missing}/sync", headers=ha).json()["events_queued"] == 0

    # Super admin can still mutate B
    patched = client.patch(
        f"/api/v1/employees/{y}",
        headers=auth_headers,
        json={"name": "Platform Renamed"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Platform Renamed"

    # Cleanup delete by owner still works on A
    assert client.delete(f"/api/v1/employees/{emp_a['id']}", headers=ha).status_code == 200


# ---------------------------------------------------------------------------
# Mandatory connector isolation
# ---------------------------------------------------------------------------
def test_connector_cross_tenant_mutations_forbidden(client, settings, auth_headers):
    org_a, org_b = _two_orgs(client, auth_headers)
    ha = _make_user(client, settings, org_id=org_a, email="conn-a@test.com")
    hb = _make_user(client, settings, org_id=org_b, email="conn-b@test.com")

    # Register via agent API under each org
    ca, _ = _register_connector(client, org_id=org_a, name="Conn A Hard", hostname="host-a-hard")
    cb, _ = _register_connector(client, org_id=org_b, name="Conn B Hard", hostname="host-b-hard")

    before = client.get(f"/api/v1/connectors/{cb}", headers=hb).json()
    assert before["is_active"] is True

    for path, method in [
        (f"/api/v1/connectors/{cb}", "PATCH"),
        (f"/api/v1/connectors/{cb}/deactivate", "POST"),
        (f"/api/v1/connectors/{cb}/restart", "POST"),
        (f"/api/v1/connectors/{cb}/upgrade", "POST"),
        (f"/api/v1/connectors/{cb}/reconnect", "POST"),
        (f"/api/v1/connectors/{cb}", "DELETE"),
    ]:
        if method == "PATCH":
            res = client.patch(path, headers=ha, json={"name": "Stolen"})
        elif method == "DELETE":
            res = client.delete(path, headers=ha)
        else:
            res = client.post(path, headers=ha)
        assert res.status_code == 403, f"{method} {path} -> {res.status_code} {res.text}"

    after = client.get(f"/api/v1/connectors/{cb}", headers=hb).json()
    assert after["name"] == before["name"]
    assert after["is_active"] is True

    # Same-tenant B can patch
    ok = client.patch(f"/api/v1/connectors/{cb}", headers=hb, json={"name": "Conn B Renamed"})
    assert ok.status_code == 200
    assert ok.json()["name"] == "Conn B Renamed"

    # Unknown
    assert client.post("/api/v1/connectors/missing-id/restart", headers=ha).status_code == 404

    # Platform admin can act on B
    assert client.post(f"/api/v1/connectors/{cb}/restart", headers=auth_headers).status_code == 200

    # Same-tenant deactivate on A works
    assert client.post(f"/api/v1/connectors/{ca}/deactivate", headers=ha).status_code == 200


# ---------------------------------------------------------------------------
# Mandatory support isolation (IT admin has support module)
# ---------------------------------------------------------------------------
def test_support_cross_tenant_forbidden(client, settings, auth_headers):
    org_a, org_b = _two_orgs(client, auth_headers)
    ha = _make_user(client, settings, org_id=org_a, email="it-a@test.com", role="it_administrator")
    hb = _make_user(client, settings, org_id=org_b, email="it-b@test.com", role="it_administrator")
    _, _ = _register_connector(client, org_id=org_a, name="Supp A", hostname="supp-a")
    cb, _ = _register_connector(client, org_id=org_b, name="Supp B", hostname="supp-b")

    for path in [
        f"/api/v1/support/connectors/{cb}/download-logs",
        f"/api/v1/support/connectors/{cb}/diagnostics",
        f"/api/v1/support/connectors/{cb}/clear-queue",
    ]:
        res = client.post(path, headers=ha)
        assert res.status_code == 403, f"{path} -> {res.status_code}"

    assert client.get(f"/api/v1/support/artifacts?connector_id={cb}", headers=ha).status_code == 403

    # Same-tenant B allowed
    assert client.post(f"/api/v1/support/connectors/{cb}/diagnostics", headers=hb).status_code == 200
    # Platform admin allowed
    assert client.post(f"/api/v1/support/connectors/{cb}/clear-queue", headers=auth_headers).status_code == 200


# ---------------------------------------------------------------------------
# Provisioning isolation
# ---------------------------------------------------------------------------
def test_provisioning_cross_tenant_forbidden(client, settings, auth_headers):
    org_a, org_b = _two_orgs(client, auth_headers)
    ha = _make_user(client, settings, org_id=org_a, email="prov-a@test.com")
    hb = _make_user(client, settings, org_id=org_b, email="prov-b@test.com")
    cb, key_b = _register_connector(client, org_id=org_b, name="Prov B", hostname="prov-b")

    # Create device under B via push
    push = client.post(
        f"{PREFIX}/connectors/PROV-B/push",
        headers={"X-Connector-Id": cb, "X-Device-Token": key_b, "X-Organisation-Id": org_b},
        json={
            "punches": [
                {
                    "device_employee_code": "1",
                    "punch_time": "2026-09-07 09:00:00",
                    "event_type": "IN",
                    "idempotency_key": "prov:b:1",
                    "device_id": "device-prov-b",
                }
            ]
        },
    )
    assert push.status_code == 200, push.text

    # Find device id
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    db = sessionmaker(bind=engine)()
    try:
        device = db.scalar(select(Device).where(Device.organisation_id == org_b))
        assert device is not None
        device_id = device.id
    finally:
        db.close()

    assert client.post("/api/v1/provisioning/approve", headers=ha, json={"connector_id": cb}).status_code == 403
    assert (
        client.post(
            "/api/v1/provisioning/assign",
            headers=ha,
            json={"device_id": device_id, "branch_id": None},
        ).status_code
        == 403
    )

    assert client.post("/api/v1/provisioning/approve", headers=hb, json={"connector_id": cb}).status_code == 200
    assert (
        client.post(
            "/api/v1/provisioning/assign",
            headers=hb,
            json={"device_id": device_id, "branch_id": None},
        ).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# Mandatory device upsert isolation
# ---------------------------------------------------------------------------
def test_device_upsert_rejects_foreign_device_id(client, settings, auth_headers):
    org_a, org_b = _two_orgs(client, auth_headers)
    ca, key_a = _register_connector(client, org_id=org_a, name="DevA Conn", hostname="deva")
    cb, key_b = _register_connector(client, org_id=org_b, name="DevB Conn", hostname="devb")

    # Create D1 under Org A
    push_a = client.post(
        f"{PREFIX}/connectors/TERM-A/push",
        headers={"X-Connector-Id": ca, "X-Device-Token": key_a, "X-Organisation-Id": org_a},
        json={
            "punches": [
                {
                    "device_employee_code": "10",
                    "punch_time": "2026-09-07 09:00:00",
                    "event_type": "IN",
                    "idempotency_key": "deva:10",
                    "device_id": "shared-looking-id-d1",
                    "device_name": "Device A",
                }
            ]
        },
    )
    assert push_a.status_code == 200
    assert push_a.json()["accepted"] == 1

    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    db = sessionmaker(bind=engine)()
    try:
        d1 = db.get(Device, "shared-looking-id-d1")
        assert d1 is not None
        assert d1.organisation_id == org_a
        owner_before = d1.organisation_id
        connector_before = d1.connector_id
    finally:
        db.close()

    # Connector B tries to reclaim D1
    push_steal = client.post(
        f"{PREFIX}/connectors/TERM-B/push",
        headers={"X-Connector-Id": cb, "X-Device-Token": key_b, "X-Organisation-Id": org_b},
        json={
            "punches": [
                {
                    "device_employee_code": "20",
                    "punch_time": "2026-09-07 09:05:00",
                    "event_type": "IN",
                    "idempotency_key": "devb:steal",
                    "device_id": "shared-looking-id-d1",
                    "device_name": "Stolen",
                }
            ]
        },
    )
    assert push_steal.status_code == 403, push_steal.text

    db = sessionmaker(bind=engine)()
    try:
        d1 = db.get(Device, "shared-looking-id-d1")
        assert d1.organisation_id == owner_before == org_a
        assert d1.connector_id == connector_before
        assert d1.name == "Device A"
    finally:
        db.close()

    # Org B can create its own D2
    push_b = client.post(
        f"{PREFIX}/connectors/TERM-B2/push",
        headers={"X-Connector-Id": cb, "X-Device-Token": key_b, "X-Organisation-Id": org_b},
        json={
            "punches": [
                {
                    "device_employee_code": "20",
                    "punch_time": "2026-09-07 09:10:00",
                    "event_type": "IN",
                    "idempotency_key": "devb:d2",
                    "device_id": "device-d2-org-b",
                    "device_name": "Device B",
                }
            ]
        },
    )
    assert push_b.status_code == 200
    assert push_b.json()["accepted"] == 1

    # Same-org update of D1 still works
    push_a2 = client.post(
        f"{PREFIX}/connectors/TERM-A/push",
        headers={"X-Connector-Id": ca, "X-Device-Token": key_a, "X-Organisation-Id": org_a},
        json={
            "punches": [
                {
                    "device_employee_code": "11",
                    "punch_time": "2026-09-07 09:15:00",
                    "event_type": "IN",
                    "idempotency_key": "deva:11",
                    "device_id": "shared-looking-id-d1",
                    "device_name": "Device A Updated",
                }
            ]
        },
    )
    assert push_a2.status_code == 200
    assert push_a2.json()["accepted"] == 1
