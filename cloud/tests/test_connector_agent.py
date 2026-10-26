"""Connector agent API contract tests — matches connector/api/cloud_client.py."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.security.hmac_signing import sign_command

PREFIX = "/api/wf/biometric-enterprise"


def test_api_health(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/liveness").json()["check"] == "liveness"
    assert client.get("/readiness").json()["check"] == "readiness"
    assert client.get(f"{PREFIX}/time").json()["timezone"] == "UTC"


def test_agent_register_heartbeat_push_commands(client: TestClient, auth_headers, settings):
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_id = orgs[0]["id"]

    reg = client.post(
        f"{PREFIX}/connectors/register",
        json={
            "hostname": "branch-pc-1",
            "version": "3.0.0",
            "organisation_name": "Test Organisation",
            "name": "Branch PC",
        },
    )
    assert reg.status_code == 200, reg.text
    data = reg.json()
    connector_id = data["connector_id"]
    api_key = data["api_key"]
    assert data["command_signing_secret"]

    headers = {
        "X-Organisation-Id": org_id,
        "X-Connector-Id": connector_id,
        "X-Device-Token": api_key,
    }

    hb = client.post(
        f"{PREFIX}/connectors/{connector_id}/heartbeat",
        headers=headers,
        json={
            "connector_version": "3.0.0",
            "status": "online",
            "cpu_percent": 12.5,
            "ram_percent": 40.0,
            "disk_percent": 55.0,
            "connected_devices": 2,
            "offline_devices": 0,
            "pending_queue": 3,
        },
    )
    assert hb.status_code == 200
    assert hb.json()["ok"] is True

    dev = client.post(
        f"{PREFIX}/devices",
        headers=headers,
        json={
            "name": "Gate-1",
            "terminal_code": "GATE1",
            "manufacturer": "essl",
            "ip_address": "192.168.1.10",
            "port": 4370,
        },
    )
    assert dev.status_code == 200

    push = client.post(
        f"{PREFIX}/connectors/GATE1/push",
        headers=headers,
        json={
            "punches": [
                {
                    "device_employee_code": "E100",
                    "punch_time": "2026-07-16T09:00:00+05:30",
                    "event_type": "IN",
                    "verification_method": "fingerprint",
                    "idempotency_key": "idem-1",
                }
            ],
            "source": "brixigo-connector",
            "version": "3.0.0",
        },
    )
    assert push.status_code == 200
    assert push.json()["accepted"] == 1

    push2 = client.post(
        f"{PREFIX}/connectors/GATE1/push",
        headers=headers,
        json={
            "punches": [
                {
                    "device_employee_code": "E100",
                    "punch_time": "2026-07-16T09:00:00+05:30",
                    "idempotency_key": "idem-1",
                }
            ]
        },
    )
    assert push2.json()["duplicates"] == 1

    cmd = client.post(
        "/api/v1/commands",
        headers=auth_headers,
        json={
            "connector_name": "Branch PC",
            "command_type": "restart_device",
            "device_name": "",
            "payload": {"reason": "test"},
        },
    )
    assert cmd.status_code == 200
    cmd_data = cmd.json()

    polled = client.get(f"{PREFIX}/connectors/{connector_id}/commands", headers=headers)
    assert polled.status_code == 200
    commands = polled.json()["commands"]
    match = next(c for c in commands if c["id"] == cmd_data["id"])

    expected = sign_command(
        cloud_command_id=match["id"],
        command_type=match["command_type"],
        device_id=match.get("device_id") or "",
        payload=match.get("payload") or {},
        nonce=match["nonce"],
        issued_at=match["issued_at"],
        expires_at=match["expires_at"],
        secret=settings.command_hmac_secret,
    )
    assert match["signature"] == expected

    ack = client.post(
        f"{PREFIX}/connectors/{connector_id}/commands/{cmd_data['id']}/ack",
        headers=headers,
        json={"status": "succeeded", "message": "ok", "result": {}},
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == "completed"

    dash = client.get(f"{PREFIX}/connectors/{connector_id}/dashboard", headers=headers)
    assert dash.status_code == 200
    assert dash.json()["devices_total"] >= 1


def test_heartbeat_accepts_separated_connection_states(client: TestClient, auth_headers):
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_id = orgs[0]["id"]
    reg = client.post(
        f"{PREFIX}/connectors/register",
        json={"hostname": "sep-states", "version": "3.0.2", "organisation_name": "Test Organisation"},
    )
    assert reg.status_code == 200
    connector_id = reg.json()["connector_id"]
    api_key = reg.json()["api_key"]
    headers = {
        "X-Organisation-Id": org_id,
        "X-Connector-Id": connector_id,
        "X-Device-Token": api_key,
    }
    hb = client.post(
        f"{PREFIX}/connectors/{connector_id}/heartbeat",
        headers=headers,
        json={
            "connector_version": "3.0.2",
            "status": "running",
            "connected_devices": 1,
            "offline_devices": 0,
            "internet_status": "online",
            "server_status": "tls_error",
            "tls_status": "timeout",
            "authentication_status": "valid",
            "sync_status": "pending",
            "connection_states": {
                "devices": [{"device_id": "d1", "status": "connected", "ip_address": "192.168.100.2"}],
            },
        },
    )
    assert hb.status_code == 200
    status = client.get(f"{PREFIX}/connectors/{connector_id}/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["connected_devices"] == 1
    assert status.json()["offline_devices"] == 0


def test_agent_accepts_collapsed_connector_and_org_names(client: TestClient, auth_headers):
    """Settings often drop spaces from the Cloud copy-box display names."""
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_name = orgs[0]["name"]
    reg = client.post(
        f"{PREFIX}/connectors/register",
        json={
            "hostname": "branch-pc-spaces",
            "version": "3.0.2",
            "organisation_name": org_name,
            "name": "Branch Connector",
        },
    )
    assert reg.status_code == 200, reg.text
    api_key = reg.json()["api_key"]
    headers = {
        "X-Organisation-Id": org_name.replace(" ", ""),
        "X-Connector-Id": "BranchConnector",
        "X-Device-Token": api_key,
    }
    hb = client.post(
        f"{PREFIX}/connectors/BranchConnector/heartbeat",
        headers=headers,
        json={"connector_version": "3.0.2", "status": "running"},
    )
    assert hb.status_code == 200, hb.text
    assert hb.json()["ok"] is True
    assert hb.json()["status"] == "online"


def test_push_rewrites_year_2000_punch_to_today(client: TestClient, auth_headers):
    from datetime import datetime

    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_id = orgs[0]["id"]
    reg = client.post(
        f"{PREFIX}/connectors/register",
        json={"hostname": "k90-pc", "version": "3.0.2", "organisation_name": "Test Organisation", "name": "K90 PC"},
    )
    assert reg.status_code == 200, reg.text
    connector_id = reg.json()["connector_id"]
    headers = {
        "X-Organisation-Id": org_id,
        "X-Connector-Id": connector_id,
        "X-Device-Token": reg.json()["api_key"],
    }
    client.post(
        f"{PREFIX}/devices",
        headers=headers,
        json={"name": "eSSL K90", "terminal_code": "K90-1", "manufacturer": "essl"},
    )
    push = client.post(
        f"{PREFIX}/connectors/K90-1/push",
        headers=headers,
        json={
            "punches": [
                {
                    "device_employee_code": "1",
                    "punch_time": "2000-02-08T17:46:55+05:30",
                    "event_type": "IN",
                    "verification_method": "fingerprint",
                    "idempotency_key": "k90-2000-1",
                }
            ]
        },
    )
    assert push.status_code == 200, push.text
    assert push.json()["accepted"] == 1
    feed = client.get("/api/v1/payroll/punches?limit=20", headers=auth_headers)
    assert feed.status_code == 200, feed.text
    punches = feed.json()["punches"]
    today = datetime.now().strftime("%Y-%m-%d")
    assert any(p["punch_time"].startswith(today) and p["device_employee_code"] == "1" for p in punches)


def test_connector_directory_pushes_device_user_names(client: TestClient, auth_headers):
    orgs = client.get("/api/v1/tenancy/organisations", headers=auth_headers).json()
    org_id = orgs[0]["id"]
    reg = client.post(
        f"{PREFIX}/connectors/register",
        json={
            "hostname": "name-pc",
            "version": "3.0.2",
            "organisation_name": "Test Organisation",
            "name": "Name PC",
        },
    )
    assert reg.status_code == 200, reg.text
    connector_id = reg.json()["connector_id"]
    headers = {
        "X-Organisation-Id": org_id,
        "X-Connector-Id": connector_id,
        "X-Device-Token": reg.json()["api_key"],
    }
    pushed = client.post(
        f"{PREFIX}/connectors/{connector_id}/employees/directory",
        headers=headers,
        json={
            "employees": [
                {"employee_code": "1", "name": "Shiva", "device_name": "infoessl9999"},
                {"employee_code": "5", "name": "Sai", "device_name": "infoessl9999"},
            ]
        },
    )
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["named"] == 2
    roster = client.get("/api/v1/employees", headers=auth_headers).json()
    names = {e["employee_code"]: e["name"] for e in roster}
    assert names.get("1") == "Shiva"
    assert names.get("5") == "Sai"
