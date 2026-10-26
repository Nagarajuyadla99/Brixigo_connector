"""Core cloud platform tests — bootstrap, auth, connectors, employees, commands."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_bootstrap_org_exists(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_production_rejects_default_secrets():
    from app.config import Settings

    insecure = Settings(
        environment="production",
        jwt_secret="change-me-brixigo-cloud-jwt-secret-production",
        command_hmac_secret="change-me-brixigo-command-hmac-secret",
        bootstrap_admin_password="BrixigoAdmin!2026",
    )
    try:
        insecure.assert_secure_for_environment()
        raised = False
    except RuntimeError:
        raised = True
    assert raised is True

    secure = Settings(
        environment="production",
        jwt_secret="production-jwt-secret-key-32-characters-min",
        command_hmac_secret="production-hmac-secret-16+",
        bootstrap_admin_password="NotTheDefaultPassword!99",
        cors_origins="https://admin.brixigo.com",
    )
    secure.assert_secure_for_environment()


def test_cors_not_wildcard_in_dev(settings):
    origins = settings.cors_origin_list()
    assert "*" not in origins
    assert any("localhost" in o or "127.0.0.1" in o for o in origins)


def test_payroll_punch_feed(client: TestClient, auth_headers):
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_id = orgs[0]["id"]
    emp = client.post(
        "/api/v1/employees",
        headers=auth_headers,
        json={
            "organisation_name": "Test Organisation",
            "employee_code": "E900",
            "name": "Payroll Match User",
            "designation": "Staff",
        },
    )
    assert emp.status_code == 200, emp.text

    reg = client.post(
        "/api/wf/biometric-enterprise/connectors/register",
        json={
            "hostname": "pay-host",
            "version": "3.0.2",
            "organisation_id": org_id,
            "name": "Payroll Connector",
        },
    )
    assert reg.status_code == 200, reg.text
    data = reg.json()
    api_key = data["api_key"]
    connector_id = data["connector_id"]

    push = client.post(
        "/api/wf/biometric-enterprise/connectors/PAYGATE1/push",
        headers={
            "X-Connector-Id": connector_id,
            "X-Organisation-Id": org_id,
            "X-Device-Token": api_key,
        },
        json={
            "source": "brixigo-connector",
            "punches": [
                {
                    "contract_version": "1.0",
                    "device_employee_code": "E900",
                    "punch_time": "2026-07-28 09:15:00",
                    "event_type": "IN",
                    "verification_method": "fingerprint",
                    "idempotency_key": "zkteco:d:E900:2026-07-28 09:15:00:IN",
                    "payroll_ready": True,
                    "manufacturer": "zkteco",
                    "device_id": "local-device-1",
                    "metadata": {"vendor": "zkteco"},
                }
            ],
        },
    )
    assert push.status_code == 200, push.text
    assert push.json()["accepted"] == 1
    assert push.json()["payroll_ready"] is True

    feed = client.get("/api/v1/payroll/punches", headers=auth_headers)
    assert feed.status_code == 200, feed.text
    body = feed.json()
    assert body["contract_version"] == "1.0"
    assert body["count"] >= 1
    matched = next(p for p in body["punches"] if p["device_employee_code"] == "E900")
    assert matched["employee_name"] == "Payroll Match User"
    assert matched["employee_matched"] is True
    assert matched["employee_id"]
    assert body["matched_employees"] >= 1


def test_login_and_me(client: TestClient, settings, auth_headers):
    res = client.get("/api/v1/auth/me", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == settings.bootstrap_admin_email
    assert data["role"] == "super_admin"


def test_register_connector(client: TestClient, auth_headers):
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    assert len(orgs) >= 1
    org_id = orgs[0]["id"]

    res = client.post(
        "/api/v1/connectors/register",
        headers=auth_headers,
        json={"organisation_name": "Test Organisation", "name": "Test Connector", "hostname": "test-host"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["connector"]["name"] == "Test Connector"
    assert data["api_key"].startswith("bxk_")

    listed = client.get("/api/v1/connectors", headers=auth_headers).json()
    assert any(c["name"] == "Test Connector" for c in listed)


def test_create_employee(client: TestClient, auth_headers):
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_id = orgs[0]["id"]

    res = client.post(
        "/api/v1/employees",
        headers=auth_headers,
        json={
            "organisation_name": "Test Organisation",
            "employee_code": "EMP001",
            "name": "Jane Doe",
            "designation": "Engineer",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["employee_code"] == "EMP001"
    assert data["name"] == "Jane Doe"


def test_create_command(client: TestClient, auth_headers):
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_id = orgs[0]["id"]

    conn_res = client.post(
        "/api/v1/connectors/register",
        headers=auth_headers,
        json={"organisation_name": "Test Organisation", "name": "Cmd Connector"},
    )
    connector_name = conn_res.json()["connector"]["name"]

    res = client.post(
        "/api/v1/commands",
        headers=auth_headers,
        json={"connector_name": connector_name, "command_type": "ping", "payload": {"test": True}},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["command_type"] == "ping"
    assert data["status"] == "queued"
    assert data["signature"]

    listed = client.get("/api/v1/commands?status=queued", headers=auth_headers).json()
    assert any(c["id"] == data["id"] for c in listed)
