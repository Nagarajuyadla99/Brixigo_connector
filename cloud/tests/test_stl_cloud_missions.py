"""Cloud Missions — Silicon Wireless STL040 Port 80 URL-push ingest."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _bind_sid(client: TestClient, auth_headers: dict, sid: str = "99999") -> dict:
    res = client.put(
        "/api/v1/cloud-missions/settings",
        headers=auth_headers,
        json={"stl_sid": sid},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["stl_sid"] == sid
    assert "/attendance?" in data["get_url"]
    assert "/attservice?" in data["post_url"]
    return data


def test_stl_get_attendance_ack_and_store(client: TestClient, auth_headers):
    _bind_sid(client, auth_headers, "99999")

    # Manual §4.1 single record
    query = "$99999&99&0008478100&27122013113700*"
    res = client.get(f"/attendance?{query}")
    assert res.status_code == 200
    assert res.text == "$RFID=0#"

    feed = client.get(
        "/api/v1/cloud-missions/punches?today=false&limit=50",
        headers=auth_headers,
    )
    assert feed.status_code == 200, feed.text
    punches = feed.json()["punches"]
    assert len(punches) >= 1
    row = punches[0]
    assert row["device_employee_code"] == "8478100"
    assert row["event_type"] == "IN"
    assert row["terminal_code"] == "9999999"
    assert row["source"] == "cloud-mission"

    devices = client.get("/api/v1/cloud-missions/devices", headers=auth_headers).json()["devices"]
    assert any(d["terminal_code"] == "9999999" for d in devices)


def test_stl_get_aspx_alias_and_duplicate(client: TestClient, auth_headers):
    _bind_sid(client, auth_headers, "99999")
    query = "$99999&88&0008478100&27122013113700*"
    first = client.get(f"/rfid/attendance.aspx?{query}")
    second = client.get(f"/api/stl/attendance?{query}")
    assert first.text == "$RFID=0#"
    assert second.text == "$RFID=0#"

    feed = client.get(
        "/api/v1/cloud-missions/punches?today=false&limit=50",
        headers=auth_headers,
    ).json()["punches"]
    matching = [p for p in feed if p["terminal_code"] == "9999988" and p["device_employee_code"] == "8478100"]
    assert len(matching) == 1


def test_stl_post_json_ack_and_inout(client: TestClient, auth_headers):
    _bind_sid(client, auth_headers, "12345")

    body = {
        "MachineID": "1234501",
        "Attendance": [
            {"PunchID": "1001", "PunchTime": "26032026100100", "PunchStatus": "0"},
            {"PunchID": "1001", "PunchTime": "26032026180100", "PunchStatus": "1"},
        ],
    }
    res = client.post("/attservice", json=body)
    assert res.status_code == 200
    assert res.json() == {"DeviceResponse": 0}

    feed = client.get(
        "/api/v1/cloud-missions/punches?today=false&limit=50",
        headers=auth_headers,
    ).json()["punches"]
    rows = [p for p in feed if p["terminal_code"] == "1234501"]
    assert len(rows) == 2
    events = {r["event_type"] for r in rows}
    assert events == {"IN", "OUT"}


def test_stl_post_unknown_org_still_acks(client: TestClient):
    # Manual: respond irrespective of validity
    res = client.post(
        "/attservice",
        json={
            "MachineID": "0000099",
            "Attendance": [
                {"PunchID": "1", "PunchTime": "26032026100100", "PunchStatus": "0"},
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["DeviceResponse"] in (0, 1)


def test_stl_get_multi_records(client: TestClient, auth_headers):
    _bind_sid(client, auth_headers, "55555")
    query = (
        "$55555&01&0008478100&27122013113700,"
        "0008478200&27122013113800*"
    )
    res = client.get(f"/attendance?{query}")
    assert res.text == "$RFID=0#"
    feed = client.get(
        "/api/v1/cloud-missions/punches?today=false&limit=50",
        headers=auth_headers,
    ).json()["punches"]
    codes = {p["device_employee_code"] for p in feed if p["terminal_code"] == "5555501"}
    assert "8478100" in codes
    assert "8478200" in codes


def test_cloud_missions_settings_sms_example(client: TestClient, auth_headers):
    data = _bind_sid(client, auth_headers, "42424")
    assert "*555#001#" in data["sms_http_example"]
    assert data["sms_http_example"].endswith("#80#")
    assert "42424" == data["stl_sid"]
