"""Reliability tests: device heartbeat, API/TLS isolation, queue, retry, auth."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models import AttendanceRecord, ConnectorSettings, Device, QueueStatus, new_id, utcnow
from services.connection_state import (
    AuthStatus,
    ConnectionStateRegistry,
    ConnectorRuntimeStatus,
    DeviceLinkStatus,
    DeviceNetworkStatus,
    DeviceProtocolStatus,
    InternetStatus,
    ServerStatus,
    SyncStatus,
    classify_api_sample,
    classify_transport_error,
    is_hard_lan_unreachable,
    is_live_retry,
    is_tcp_connect_failure,
    live_display_fields,
    persist_device_status,
    public_cloud_status,
    resolve_headline,
    tcp_probe,
)
from services.retry_policy import is_transient_error_kind, next_backoff_seconds
from storage.database import Database
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.device_repo import DeviceRepository


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "rel.db")
    database.initialize()
    yield database
    database.close()


@pytest.fixture()
def secrets():
    from security.encryption import SecretBox, derive_machine_key

    return SecretBox(derive_machine_key(app_id="test-rel"))


def _device(**kwargs) -> Device:
    defaults = dict(
        id=new_id(),
        name="Gate-1",
        manufacturer="zkteco",
        model="K30",
        ip_address="192.168.100.2",
        port=4370,
        password="0",
        branch="HQ",
        terminal_code="GATE1",
    )
    defaults.update(kwargs)
    return Device(**defaults)


def _fail_probes(states: ConnectionStateRegistry, device_id: str = "d1", *, n: int = 1, **kwargs):
    """Record n counted network failures (bypass coalesce window for unit tests)."""
    defaults = dict(
        ok=False,
        error="timed out",
        timeout=True,
        ip_address="192.168.100.2",
        port=4370,
    )
    defaults.update(kwargs)
    link = None
    for _ in range(n):
        link = states.record_device_probe(device_id, **defaults)
        with states._lock:
            if device_id in states._devices:
                states._devices[device_id].last_failure_mono = 0.0
    return link


def test_backoff_schedule_and_cap():
    delays = [next_backoff_seconds(i, jitter=False) for i in range(1, 7)]
    assert delays[:5] == [2.0, 5.0, 10.0, 20.0, 30.0]
    assert delays[5] == 30.0
    jittered = next_backoff_seconds(1, jitter=True)
    assert 2.0 <= jittered <= 3.0


def test_classify_tls_timeout_and_auth():
    assert classify_transport_error(TimeoutError("The handshake operation timed out")) == "tls"
    assert classify_transport_error(TimeoutError("_ssl.c:1063: handshake timed out")) == "tls"
    assert classify_transport_error(TimeoutError("Read timed out")) == "timeout"
    assert classify_transport_error(RuntimeError("Cloud API 401")) == "auth"
    assert classify_transport_error(ConnectionError("Name or service not known")) == "network"


def test_api_failure_does_not_mark_device_disconnected():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_device_probe(
        "d1",
        ok=True,
        latency_ms=2.0,
        ip_address="192.168.100.2",
        port=4370,
        name="Gate-1",
    )
    states.record_api_result(kind="ok", latency_ms=200)
    states.record_api_result(kind="tls", latency_ms=30000, error="handshake timed out")
    snap = states.snapshot()
    assert snap.devices[0]["status"] == DeviceLinkStatus.CONNECTED.value
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert persist_device_status(states.device("d1")) == "online"
    states.record_api_result(kind="tls", latency_ms=30000, error="handshake timed out")
    states.record_api_result(kind="tls", latency_ms=30000, error="handshake timed out")
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.TLS_ERROR.value
    assert snap.devices[0]["status"] == DeviceLinkStatus.CONNECTED.value
    resolved = resolve_headline(snap)
    assert resolved.code == "SERVER_SECURE_CONNECTION_ERROR"
    assert "remains connected" in resolved.body.lower()


def test_slow_api_keeps_device_connected():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_device_probe("d1", ok=True, latency_ms=2.0, ip_address="192.168.100.2", port=4370)
    states.record_api_result(kind="ok", latency_ms=2954)
    snap = states.snapshot()
    assert snap.devices[0]["status"] == "connected"
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert snap.api_availability == "available"


def test_api_unavailable_and_internet_outage_keep_device_connected():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_device_probe("d1", ok=True, latency_ms=2.0, ip_address="192.168.100.2", port=4370)
    states.set_internet(InternetStatus.OFFLINE)
    states.record_api_result(kind="unavailable", error="offline")
    snap = states.snapshot()
    assert snap.devices[0]["status"] == "connected"
    assert resolve_headline(snap).code == "SERVER_UNAVAILABLE"


def test_device_outage_independent_of_api():
    from app.constants import DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER

    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_api_result(kind="ok", latency_ms=200)
    _fail_probes(states, n=DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER)
    snap = states.snapshot()
    assert snap.devices[0]["status"] == DeviceLinkStatus.DISCONNECTED.value
    assert snap.server_status == ServerStatus.CONNECTED.value
    resolved = resolve_headline(snap)
    assert resolved.code == "DEVICE_CONNECTING"
    assert "Unreachable" not in resolved.title
    assert persist_device_status(states.device("d1")) == "timeout"


def test_stale_heartbeat_shows_unreachable():
    from datetime import timedelta

    from app.constants import DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER

    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    _fail_probes(states, n=DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER)
    with states._lock:
        states._devices["d1"].last_heartbeat_at = (utcnow() - timedelta(minutes=5)).isoformat()
    snap = states.snapshot()
    assert resolve_headline(snap).code == "DEVICE_DISCONNECTED"
    assert "Unreachable" in resolve_headline(snap).title


def test_live_retry_is_connecting_not_offline_in_ui():
    from app.constants import DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER

    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    _fail_probes(states, n=DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER)
    link = states.device("d1")
    assert link is not None
    assert link.status == DeviceLinkStatus.DISCONNECTED
    assert is_live_retry(link)
    display = live_display_fields(link)
    assert display["display_status"] == "connecting"
    assert display["display_network"] == "probing"
    assert display["live_retry"] is True
    assert states.connecting_device_count() == 1
    assert states.disconnected_device_count() == 0
    public = states.snapshot().devices[0]
    assert public["live_retry"] is True
    assert public["display_status"] == "connecting"


def test_stale_disconnect_counts_as_offline():
    from datetime import timedelta

    from app.constants import DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER

    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    _fail_probes(states, n=DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER)
    with states._lock:
        states._devices["d1"].last_heartbeat_at = (utcnow() - timedelta(minutes=5)).isoformat()
    assert is_live_retry(states.device("d1")) is False
    assert states.connecting_device_count() == 0
    assert states.disconnected_device_count() == 1


def test_heartbeat_consecutive_failures_degraded_then_disconnected():
    from app.constants import DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER

    states = ConnectionStateRegistry()
    first = _fail_probes(states, n=1)
    assert first.status in (DeviceLinkStatus.DEGRADED, DeviceLinkStatus.TIMEOUT)
    assert persist_device_status(first) == "degraded"
    mid = None
    for _ in range(DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER - 2):
        mid = _fail_probes(states, n=1)
        assert mid.status in (DeviceLinkStatus.DEGRADED, DeviceLinkStatus.TIMEOUT)
    last = _fail_probes(states, n=1)
    assert last.status == DeviceLinkStatus.DISCONNECTED
    assert persist_device_status(last) == "timeout"
    recovered = states.record_device_probe("d1", ok=True, latency_ms=2.0)
    assert recovered.status == DeviceLinkStatus.CONNECTED
    assert recovered.consecutive_failures == 0


def test_soft_timeout_coalesce_and_keeps_network_unknown():
    """Poll+heartbeat same second must not double-count; soft fail ≠ TCP DOWN yet."""
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_device_probe("d1", ok=True, latency_ms=2.0, ip_address="192.168.100.2", port=4370)
    a = states.record_device_probe(
        "d1", ok=False, error="timed out", timeout=True, ip_address="192.168.100.2", port=4370
    )
    b = states.record_device_probe(
        "d1", ok=False, error="timed out", timeout=True, ip_address="192.168.100.2", port=4370
    )
    assert a.consecutive_failures == 1
    assert b.consecutive_failures == 1  # coalesced
    assert b.network_status == DeviceNetworkStatus.UNKNOWN
    assert b.status == DeviceLinkStatus.TIMEOUT


def test_hard_lan_marks_offline_faster_than_soft():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    link = _fail_probes(
        states,
        n=2,
        error="[WinError 10065] A socket operation was attempted to an unreachable host",
        timeout=False,
    )
    assert link.network_status == DeviceNetworkStatus.OFFLINE
    assert link.status in (DeviceLinkStatus.DEGRADED, DeviceLinkStatus.TIMEOUT, DeviceLinkStatus.DISCONNECTED)


def test_protocol_error_with_tcp_open_is_degraded_not_offline():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    link = states.record_device_heartbeat(
        "d1",
        network_ok=True,
        protocol_ok=False,
        latency_ms=2.0,
        error="Connection closed by device",
        ip_address="192.168.100.2",
        port=4370,
        name="eSSL K90",
        protocol_code="DEVICE_CONNECTION_CLOSED",
        failure_stage="protocol_init",
    )
    assert link.network_status.value == "connected"
    assert link.protocol_status.value == "error"
    assert link.status == DeviceLinkStatus.DEGRADED
    assert persist_device_status(link) == "degraded"
    snap = states.snapshot()
    resolved = resolve_headline(snap)
    assert resolved.code == "DEVICE_PROTOCOL_ERROR"
    assert "Communication Error" in resolved.title
    assert "Biometric Machine Disconnected" not in resolved.title
    assert "Biometric Device Unreachable" not in resolved.title
    assert "192.168.100.2:4370" in resolved.body


def test_classify_connection_closed_and_auth():
    from services.connection_state import classify_protocol_error

    assert classify_protocol_error(RuntimeError("Connection closed by device"), tcp_ok=True) == (
        "DEVICE_CONNECTION_CLOSED"
    )
    assert classify_protocol_error(RuntimeError("Authentication failed — check device password/comm key")) == (
        "DEVICE_AUTH_ERROR"
    )


def test_session_manager_serializes_device_access():
    import threading

    from models import Device
    from services.device_session_manager import DeviceSessionManager

    device = _device(manufacturer="essl", model="K90")
    mgr = DeviceSessionManager(connect_timeout=1.0, read_timeout=1.0)
    order: list[str] = []
    barrier = threading.Barrier(2)

    def _op(label):
        def inner(_adapter):
            order.append(f"{label}-start")
            barrier.wait(timeout=2)
            time.sleep(0.05)
            order.append(f"{label}-end")
            return label

        return inner

    # execute() creates a real adapter and connects. Stub execute's adapter path
    # by calling the lock directly through two overlapping execute mocks.
    results: list[str] = []

    def worker(label: str) -> None:
        with mgr._lock_for(device.id):
            order.append(f"{label}-start")
            time.sleep(0.08)
            order.append(f"{label}-end")
            results.append(label)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)
    # One owner at a time: start/end of first fully before second start, or nested
    # only as start-start-end-end would mean overlap. RLock is per-thread so two
    # threads cannot overlap.
    assert order in (["a-start", "a-end", "b-start", "b-end"], ["b-start", "b-end", "a-start", "a-end"])


def test_auth_401_pauses_sync_not_device():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_device_probe("d1", ok=True, latency_ms=2.0, ip_address="192.168.100.2", port=4370)
    states.record_api_result(kind="auth", status_code=401, error="Cloud API 401")
    snap = states.snapshot()
    assert snap.devices[0]["status"] == "connected"
    assert snap.authentication_status in (AuthStatus.EXPIRED.value, AuthStatus.REQUIRED.value)
    assert snap.sync_status == SyncStatus.PAUSED.value
    assert resolve_headline(snap).code == "AUTHENTICATION_REQUIRED"


def test_connector_stopped_headline():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.STOPPED)
    snap = states.snapshot()
    assert resolve_headline(snap).code == "CONNECTOR_OFFLINE"


def test_tcp_probe_yields_when_protocol_priority_waiting():
    from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS

    ip, port = "192.168.100.11", 4370
    DEVICE_PORT_LOCKS.begin_priority_wait(ip, port)
    try:
        assert DEVICE_PORT_LOCKS.try_acquire(ip, port, owner=f"tcp-probe:{ip}:{port}") is False
        assert DEVICE_PORT_LOCKS.try_acquire(ip, port, owner=f"zk-transport:{ip}:{port}") is True
        DEVICE_PORT_LOCKS.release(ip, port)
    finally:
        DEVICE_PORT_LOCKS.end_priority_wait(ip, port)


def test_select_local_ipv4_prefers_same_subnet(monkeypatch):
    from adapters.protocol import zk_transport as zt

    monkeypatch.setattr(
        zt,
        "_iter_local_ipv4",
        lambda: [("10.36.82.101", "255.255.255.0"), ("192.168.100.254", "255.255.255.0")],
    )
    monkeypatch.setattr(zt, "_udp_route_local", lambda _peer: "10.36.82.101")
    assert zt.select_local_ipv4("192.168.100.201") == "192.168.100.254"
    assert zt.select_local_ipv4("10.36.82.50") == "10.36.82.101"


def test_tcp_probe_localhost_closed_port():
    ok, latency, message = tcp_probe("127.0.0.1", 1, timeout=0.3)
    assert ok is False
    assert latency >= 0
    assert message


def test_tcp_probe_reports_reachable_when_session_lock_held():
    from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS

    ip, port = "192.168.100.2", 4370
    DEVICE_PORT_LOCKS.acquire(ip, port, owner="unit-test-session")
    DEVICE_PORT_LOCKS.mark_established(ip, port)
    try:
        ok, latency, message = tcp_probe(ip, port, timeout=0.3)
        assert ok is True
        assert message == "session-in-use"
        assert latency == 0.0
    finally:
        DEVICE_PORT_LOCKS.release(ip, port)


def test_tcp_probe_connecting_lock_is_not_online():
    from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS

    ip, port = "192.168.100.8", 4370
    DEVICE_PORT_LOCKS.acquire(ip, port, owner="zk-transport:192.168.100.8:4370")
    try:
        ok, _latency, message = tcp_probe(ip, port, timeout=0.3)
        assert ok is False
        assert message == "connecting"
    finally:
        DEVICE_PORT_LOCKS.release(ip, port)


def test_tcp_probe_does_not_treat_inflight_probe_as_online():
    from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS

    ip, port = "192.168.100.9", 4370
    DEVICE_PORT_LOCKS.acquire(ip, port, owner="tcp-probe:192.168.100.9:4370")
    try:
        ok, _latency, message = tcp_probe(ip, port, timeout=0.3)
        assert ok is False
        assert message == "probe-in-flight"
    finally:
        DEVICE_PORT_LOCKS.release(ip, port)


def test_time_sync_grace_suppresses_tcp_offline_flap():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_device_heartbeat(
        "d1",
        network_ok=True,
        protocol_ok=True,
        ip_address="192.168.100.2",
        port=4370,
        name="K90",
    )
    states.mark_post_time_sync("d1", seconds=60)
    assert states.in_time_sync_grace("d1") is True
    link = states.record_device_heartbeat(
        "d1",
        network_ok=False,
        protocol_ok=False,
        error="timed out",
        timeout=True,
        ip_address="192.168.100.2",
        port=4370,
        name="K90",
        protocol_attempted=False,
    )
    assert link.network_status == DeviceNetworkStatus.CONNECTED
    assert link.status == DeviceLinkStatus.CONNECTED
    assert link.consecutive_failures == 0


def test_inconclusive_tcp_probe_messages():
    from services.connection_state import is_inconclusive_tcp_probe

    assert is_inconclusive_tcp_probe("connecting") is True
    assert is_inconclusive_tcp_probe("probe-in-flight") is True
    assert is_inconclusive_tcp_probe("timed out") is False
    assert is_inconclusive_tcp_probe("session-in-use") is False
    assert is_inconclusive_tcp_probe(
        "TCP connect failed: timed out waiting for 192.168.100.201:4370"
    ) is True


def test_hard_lan_unreachable_is_not_a_sleepy_syn():
    assert is_hard_lan_unreachable(
        "[WinError 10065] A socket operation was attempted to an unreachable host"
    )
    assert is_hard_lan_unreachable("WinError 10051")
    assert is_hard_lan_unreachable("timed out") is False
    assert is_hard_lan_unreachable("TCP connect failed: timed out") is False
    assert is_hard_lan_unreachable("connecting") is False
    assert is_hard_lan_unreachable("session-in-use") is False


def test_tcp_connect_failure_retries_then_gives_up():
    assert is_tcp_connect_failure("TCP connect failed: timed out") is True
    assert is_tcp_connect_failure("auth failed") is False
    assert is_tcp_connect_failure("session-in-use") is False
    assert is_tcp_connect_failure("x", failure_stage="tcp_connect") is True

    from services.device_session_manager import DeviceSessionManager

    mgr = DeviceSessionManager(connect_timeout=0.2, read_timeout=0.2)
    calls = {"n": 0}

    class Boom:
        def __enter__(self):
            calls["n"] += 1
            raise RuntimeError("TCP connect failed: timed out")

        def __exit__(self, *a):
            return False

    with patch("services.device_session_manager.create_adapter", return_value=Boom()), patch(
        "services.device_session_manager.time.sleep"
    ), patch("adapters.protocol.zk_transport.refresh_neighbor_arp"), patch(
        "adapters.protocol.zk_transport.wake_neighbor"
    ):
        with pytest.raises(RuntimeError, match="TCP connect failed"):
            mgr.execute(_device(), lambda _a: None, retries=3, owner="test")
    assert calls["n"] == 3


def test_tcp_connect_failure_recovers_on_retry():
    from services.device_session_manager import DeviceSessionManager

    mgr = DeviceSessionManager(connect_timeout=0.2, read_timeout=0.2)
    calls = {"n": 0}

    class Flaky:
        def __enter__(self):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("TCP connect failed: timed out")
            return self

        def __exit__(self, *a):
            return False

    with patch("services.device_session_manager.create_adapter", return_value=Flaky()), patch(
        "services.device_session_manager.time.sleep"
    ), patch("adapters.protocol.zk_transport.refresh_neighbor_arp"), patch(
        "adapters.protocol.zk_transport.wake_neighbor"
    ):
        assert mgr.execute(_device(), lambda _a: "ok", retries=3, owner="test") == "ok"
    assert calls["n"] == 2


def test_restart_clears_stale_unreachable_headline():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    for _ in range(3):
        states.record_device_heartbeat(
            "d1",
            network_ok=False,
            protocol_ok=False,
            latency_ms=3000.0,
            error="timed out",
            ip_address="192.168.100.2",
            port=4370,
            name="essl k90",
            timeout=True,
        )
    snap = states.snapshot()
    assert resolve_headline(snap).code == "DEVICE_CONNECTING"
    assert "Unreachable" not in resolve_headline(snap).title
    states.reset_device_probe_state()
    snap = states.snapshot()
    assert resolve_headline(snap).code == "DEVICE_CONNECTING"
    assert "Unreachable" not in resolve_headline(snap).title


def test_not_completed_protocol_is_not_unreachable_before_threshold():
    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_device_heartbeat(
        "d1",
        network_ok=False,
        protocol_ok=False,
        latency_ms=50.0,
        error="busy",
        ip_address="192.168.100.2",
        port=4370,
        name="essl k90",
        protocol_attempted=False,
    )
    resolved = resolve_headline(states.snapshot())
    assert resolved.code != "DEVICE_DISCONNECTED"
    assert "Unreachable" not in resolved.title


def test_offline_queue_survives_transient_api_failure(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1001",
        punch_time="2026-08-26 09:00:00",
        event_type="IN",
        idempotency_key=f"q-{new_id()}",
        status=QueueStatus.PENDING.value,
    )
    assert repo.enqueue(rec)[0] is True
    repo.mark_uploading([rec.id])
    repo.mark_retry_later([rec.id], "TLS handshake timeout")
    pending = repo.list_pending()
    assert any(r.id == rec.id for r in pending)
    assert pending[0].status == QueueStatus.PENDING.value
    assert pending[0].retry_count == 1


def test_duplicate_attendance_prevention(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1001",
        punch_time="2026-08-26 09:15:00",
        event_type="IN",
        idempotency_key="zkteco:d:1001:2026-08-26 09:15:00:IN",
    )
    assert repo.enqueue(rec)[0] is True
    rec2 = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1001",
        punch_time="2026-08-26 09:15:00",
        event_type="IN",
        idempotency_key="zkteco:d:1001:2026-08-26 09:15:00:IN",
    )
    assert repo.enqueue(rec2)[0] is False
    assert repo.pending_count() == 1


def test_upload_engine_transient_does_not_dead_letter(db, secrets):
    from api.cloud_client import CloudAPIError
    from services.upload_engine import UploadEngine
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    attendance = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="55",
        punch_time="2026-08-26 10:00:00",
        event_type="IN",
        idempotency_key=f"up-{new_id()}",
    )
    attendance.enqueue(rec)
    cloud = MagicMock()
    cloud.push_records_for_device.side_effect = CloudAPIError(
        "handshake timed out",
        error_kind="tls",
        duration_ms=10000,
    )
    engine = UploadEngine(
        cloud,
        attendance,
        devices,
        SyncLogRepository(db),
        retry_attempts=1,
        max_retries=2,
    )
    with patch("services.upload_engine.time.sleep", return_value=None):
        result = engine.process_queue()
    assert result["uploaded"] == 0
    pending = attendance.list_pending()
    assert any(r.id == rec.id for r in pending)
    assert attendance.dead_count() == 0


def test_upload_engine_401_pauses_without_disconnecting_device(db, secrets):
    from api.cloud_client import CloudAPIError
    from services.upload_engine import UploadEngine
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    attendance = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="56",
        punch_time="2026-08-26 10:05:00",
        event_type="IN",
        idempotency_key=f"auth-{new_id()}",
    )
    attendance.enqueue(rec)
    states = ConnectionStateRegistry()
    states.record_device_probe(device.id, ok=True, latency_ms=2, ip_address=device.ip_address, port=4370)
    cloud = MagicMock()
    cloud.push_records_for_device.side_effect = CloudAPIError(
        "Cloud API 401", status_code=401, error_kind="auth"
    )
    auth_hits = []
    engine = UploadEngine(
        cloud,
        attendance,
        devices,
        SyncLogRepository(db),
        retry_attempts=1,
        states=states,
        on_auth_failed=lambda msg: auth_hits.append(msg),
    )
    result = engine.process_queue()
    assert result.get("paused") is True
    assert states.device(device.id).status == DeviceLinkStatus.CONNECTED
    assert states.sync == SyncStatus.PAUSED
    assert auth_hits
    assert attendance.list_pending()


def test_upload_recovers_after_server_returns(db, secrets):
    from services.upload_engine import UploadEngine
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    attendance = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="57",
        punch_time="2026-08-26 10:10:00",
        event_type="IN",
        idempotency_key=f"recov-{new_id()}",
    )
    attendance.enqueue(rec)
    cloud = MagicMock()
    cloud.push_records_for_device.return_value = {"ok": True, "accepted": 1}
    engine = UploadEngine(cloud, attendance, devices, SyncLogRepository(db), retry_attempts=1)
    result = engine.process_queue()
    assert result["uploaded"] == 1
    assert attendance.pending_count() == 0


def test_http_client_timeouts_are_not_unrealistically_low():
    from api.cloud_client import CloudClient

    client = CloudClient(ConnectorSettings(verify_ssl=True, request_timeout=30.0))
    timeout = client._timeouts()
    assert timeout.connect >= 10.0
    assert timeout.read >= 30.0
    assert timeout.connect < 60.0
    assert timeout.read <= 60.0
    # SSL verification remains enabled by default
    assert client.settings.verify_ssl is True


def test_http_client_verify_not_disabled():
    from api.cloud_client import CloudClient
    from security.command_security import build_ssl_context

    ctx = build_ssl_context(verify_ssl=True)
    assert ctx is not False
    client = CloudClient(ConnectorSettings(verify_ssl=True))
    assert client._verify() is not False


def test_probe_api_health_timeout_kind():
    from api.cloud_client import CloudClient

    client = CloudClient(ConnectorSettings(cloud_base_url="https://brixigo.com"))
    with patch(
        "api.health_probe.probe_liveness",
        return_value={
            "ok": False,
            "kind": "tls",
            "latency_ms": 100.0,
            "status_code": None,
            "endpoint": "/liveness",
            "error": "handshake timed out",
        },
    ):
        result = client.probe_api_health()
    assert result["ok"] is False
    assert result["kind"] in {"tls", "timeout"}


def test_probe_api_health_500():
    from api.cloud_client import CloudClient

    client = CloudClient(ConnectorSettings())
    with patch(
        "api.health_probe.probe_liveness",
        return_value={
            "ok": False,
            "kind": "server",
            "latency_ms": 80.0,
            "status_code": 500,
            "endpoint": "/liveness",
        },
    ):
        result = client.probe_api_health()
    assert result["ok"] is False
    assert result["kind"] == "server"


def test_cloud_api_error_transient_kinds():
    from api.cloud_client import CloudAPIError

    assert CloudAPIError("x", error_kind="tls").transient is True
    assert CloudAPIError("x", error_kind="timeout").transient is True
    assert CloudAPIError("x", status_code=500, error_kind="server").transient is True
    assert CloudAPIError("x", status_code=401, error_kind="auth").transient is False
    assert is_transient_error_kind("tls") is True


def test_health_monitor_tcp_open_keeps_device_online(db, secrets):
    from models import DeviceHealthSnapshot
    from services.health_monitor import HealthMonitorService

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    cloud = MagicMock()
    health_repo = MagicMock()
    health_repo.save.side_effect = lambda s: s
    health_repo.list_unpushed.return_value = []
    svc = HealthMonitorService(
        cloud, devices, health_repo, MagicMock(), list_devices=lambda: [device]
    )

    fake_adapter = MagicMock()
    fake_adapter.collect_health_metrics.return_value = {
        "online": False,
        "ping_ms": 0,
        "message": "protocol timeout",
    }
    with patch("services.health_monitor.create_adapter", return_value=fake_adapter), patch(
        "services.health_monitor.tcp_probe", return_value=(True, 2.0, "open")
    ):
        snap = svc.collect_device(device)
    assert snap.online is True
    loaded = devices.get(device.id)
    assert loaded.status == "degraded"


def test_health_monitor_does_not_clobber_live_connected(db, secrets):
    from services.connection_state import ConnectionStateRegistry
    from services.health_monitor import HealthMonitorService

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    states = ConnectionStateRegistry()
    states.record_device_heartbeat(
        device.id,
        network_ok=True,
        protocol_ok=True,
        latency_ms=4.0,
        ip_address=device.ip_address,
        port=int(device.port or 4370),
        name=device.name,
    )
    devices.update_status(device.id, status="online")
    cloud = MagicMock()
    health_repo = MagicMock()
    health_repo.save.side_effect = lambda s: s
    health_repo.list_unpushed.return_value = []
    svc = HealthMonitorService(
        cloud,
        devices,
        health_repo,
        MagicMock(),
        list_devices=lambda: [device],
        states=states,
    )
    fake_adapter = MagicMock()
    fake_adapter.collect_health_metrics.return_value = {
        "online": False,
        "ping_ms": 0,
        "message": "protocol timeout",
    }
    with patch("services.health_monitor.create_adapter", return_value=fake_adapter), patch(
        "services.health_monitor.tcp_probe", return_value=(True, 2.0, "open")
    ):
        snap = svc.collect_device(device)
    assert snap.online is True
    loaded = devices.get(device.id)
    assert loaded.status == "online"


def test_operator_badge_maps_live_connected_to_online():
    from services.connection_state import operator_device_badge

    badge = operator_device_badge(
        {
            "status": "connected",
            "network_status": "connected",
            "protocol_status": "connected",
            "display_status": "connected",
        },
        fallback="degraded",
    )
    assert badge == "online"
    assert operator_device_badge(None, fallback="degraded") == "degraded"


def test_poll_service_tcp_open_does_not_mark_offline(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    attendance = AttendanceRepository(db)
    svc = PollService(devices, attendance, SyncLogRepository(db))
    fake_adapter = MagicMock()
    fake_adapter.__enter__.side_effect = RuntimeError("protocol boom")
    with patch("services.poll_service.create_adapter", return_value=fake_adapter), patch(
        "services.poll_service.tcp_probe", return_value=(True, 1.5, "open")
    ):
        result = svc.poll_device(device)
    assert result["success"] is False
    loaded = devices.get(device.id)
    assert loaded.status == "degraded"


def test_poll_does_not_clobber_live_connected(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    devices.update_status(device.id, status="online")
    states = ConnectionStateRegistry()
    states.record_device_heartbeat(
        device.id,
        network_ok=True,
        protocol_ok=True,
        latency_ms=3.0,
        ip_address=device.ip_address,
        port=int(device.port or 4370),
        name=device.name,
    )
    svc = PollService(devices, AttendanceRepository(db), SyncLogRepository(db), states=states)
    fake_adapter = MagicMock()
    fake_adapter.__enter__.side_effect = RuntimeError("protocol boom")
    with patch("services.poll_service.create_adapter", return_value=fake_adapter), patch(
        "services.poll_service.tcp_probe", return_value=(True, 1.5, "open")
    ):
        result = svc.poll_device(device)
    assert result["success"] is False
    loaded = devices.get(device.id)
    assert loaded.status == "online"


def test_device_heartbeat_service_thresholds(db, secrets):
    from app.constants import DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER
    from services.device_heartbeat_service import DeviceHeartbeatService

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    states = ConnectionStateRegistry()
    svc = DeviceHeartbeatService(
        devices, states, list_devices=lambda: [device], interval_seconds=12
    )
    with patch("services.device_heartbeat_service.tcp_probe", return_value=(True, 2.0, "open")):
        out = svc.probe_device(device)
    assert out["status"] == "connected"
    assert devices.get(device.id).status == "online"

    with patch(
        "services.device_heartbeat_service.tcp_probe",
        return_value=(False, 3000.0, "timed out"),
    ), patch("services.device_heartbeat_service.wake_neighbor"):
        last = None
        for _ in range(DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER):
            # Bypass coalesce + deferred backoff so the unit test can count.
            svc._next_probe_at.pop(device.id, None)
            svc._burst_left.pop(device.id, None)
            with states._lock:
                if device.id in states._devices:
                    states._devices[device.id].last_failure_mono = 0.0
            last = svc.probe_device(device)
    assert last is not None
    assert last["status"] == "disconnected"
    assert devices.get(device.id).status == "timeout"


def test_inconclusive_includes_lock_wait_timeout():
    from services.connection_state import is_inconclusive_tcp_probe

    assert is_inconclusive_tcp_probe(
        "TCP connect failed: timed out waiting for 192.168.100.201:4370"
    ) is True


def test_heartbeat_skips_protocol_while_tcp_offline_then_retries_on_recovery():
    from services.device_heartbeat_service import DeviceHeartbeatService
    from services.device_session_manager import ProtocolDiagnostic

    device = _device()
    devices = MagicMock()
    states = ConnectionStateRegistry()
    sessions = MagicMock()
    sessions.heartbeat.return_value = ProtocolDiagnostic(
        tcp_ok=True, protocol_ok=True, protocol_code="OK", tcp_message="open"
    )

    svc = DeviceHeartbeatService(
        devices, states, list_devices=lambda: [device], sessions=sessions
    )
    states.record_device_heartbeat(
        device.id,
        network_ok=False,
        protocol_ok=False,
        error="TCP connect failed: timed out",
        timeout=True,
        ip_address=device.ip_address,
        port=device.port,
        name=device.name,
        failure_stage="tcp_connect",
    )
    with patch(
        "services.device_heartbeat_service.tcp_probe",
        return_value=(False, 3000.0, "timed out"),
    ):
        out = svc.probe_device(device)
    assert out["network_ok"] is False
    sessions.heartbeat.assert_not_called()

    recovered = ProtocolDiagnostic(
        tcp_ok=True, protocol_ok=True, protocol_code="OK", tcp_message="open", latency_ms=12.0
    )
    sessions.heartbeat.return_value = recovered
    with patch(
        "services.device_heartbeat_service.tcp_probe",
        return_value=(True, 4.0, "open"),
    ), patch("services.device_heartbeat_service.time.sleep"):
        out = svc.probe_device(device)
    sessions.heartbeat.assert_called_once()
    assert out["ok"] is True
    assert out["status"] == "connected"


def test_poll_scheduler_skips_auto_poll_when_tcp_offline():
    from scheduler.poll_scheduler import PollScheduler

    device = _device()
    poll = MagicMock()
    upload = MagicMock()
    states = ConnectionStateRegistry()
    states.record_device_heartbeat(
        device.id,
        network_ok=False,
        protocol_ok=False,
        error="TCP connect failed: timed out",
        timeout=True,
        ip_address=device.ip_address,
        port=device.port,
    )
    sched = PollScheduler(poll, upload, list_devices=lambda: [device], states=states)

    def _submit(fn, target):
        fn(target)
        fut = MagicMock()
        fut.add_done_callback = lambda cb: None
        return fut

    sched._pool = MagicMock()
    sched._pool.submit.side_effect = _submit
    sched._tick()
    poll.poll_device.assert_not_called()

    states.record_device_heartbeat(
        device.id,
        network_ok=True,
        protocol_ok=True,
        latency_ms=5.0,
        ip_address=device.ip_address,
        port=device.port,
    )
    sched._tick()
    poll.poll_device.assert_called_once()
    poll.poll_device.assert_called_with(device, full=False)


def test_poll_scheduler_auto_polls_every_thirty_seconds():
    from scheduler.poll_scheduler import PollScheduler

    device = _device(polling_interval=30)
    poll = MagicMock()
    upload = MagicMock()
    sched = PollScheduler(poll, upload, list_devices=lambda: [device])

    def _submit(fn, target):
        fn(target)
        fut = MagicMock()
        fut.add_done_callback = lambda cb: None
        return fut

    sched._pool = MagicMock()
    sched._pool.submit.side_effect = _submit
    sched._last_poll[device.id] = time.monotonic()
    sched._tick()
    poll.poll_device.assert_not_called()

    sched._last_poll[device.id] = time.monotonic() - 31
    sched._in_flight.discard(device.id)
    sched._tick()
    poll.poll_device.assert_called_with(device, full=False)


def test_sync_now_uses_incremental_attendance_poll():
    from scheduler.poll_scheduler import PollScheduler

    device = _device()
    poll = MagicMock()
    poll.poll_device.return_value = {
        "device_id": device.id,
        "success": True,
        "status": "SUCCESS",
        "fetched": 2,
        "inserted": 1,
        "duplicates": 1,
        "uploaded": 0,
        "pending": 1,
        "failed": 0,
    }
    upload = MagicMock()
    upload.process_queue.side_effect = lambda: time.sleep(2) or {
        "uploaded": 0,
        "failed": 0,
        "pending": 0,
        "devices": {},
    }
    sched = PollScheduler(poll, upload, list_devices=lambda: [device])
    started = time.monotonic()
    results = sched.sync_now()
    elapsed = time.monotonic() - started
    poll.poll_device.assert_called_with(
        device, full=False, force_relocate=True, refresh_users=True
    )
    assert results[0]["success"] is True
    assert elapsed < 0.5


def test_heartbeat_yields_when_poll_owns_session():
    from adapters.protocol.zk_device_lock import DEVICE_PORT_LOCKS
    from services.device_session_manager import DeviceSessionManager

    device = _device()
    sessions = DeviceSessionManager()
    held = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        lock = sessions._lock_for(device.id)
        lock.acquire()
        DEVICE_PORT_LOCKS.acquire(device.ip_address, int(device.port), owner="poll")
        DEVICE_PORT_LOCKS.mark_established(device.ip_address, int(device.port))
        held.set()
        release.wait(3)
        DEVICE_PORT_LOCKS.release(device.ip_address, int(device.port))
        lock.release()

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    assert held.wait(2)
    try:
        diag = sessions.heartbeat(device)
    finally:
        release.set()
        holder.join(2)
    assert diag.protocol_ok is True
    assert diag.tcp_message == "session-in-use"


def test_existing_sixty_second_poll_interval_migrates_once(tmp_path):
    database = Database(tmp_path / "poll-mig.db")
    database.initialize()
    database.execute("DELETE FROM connector_settings WHERE key = 'poll_interval_v2'")
    database.execute(
        """
        INSERT INTO devices (
            id, name, manufacturer, model, ip_address, port, password_enc,
            branch, timezone, communication_type, polling_interval, status,
            terminal_code, serial_number, last_sync_at, last_sync_cursor,
            last_error, enabled, created_at, updated_at, extra_json
        ) VALUES (
            'd1', 'K90', 'essl', 'K90', '192.168.1.10', 4370, '',
            '', 'Asia/Kolkata', 'tcp', 60, 'unknown',
            'T1', '', NULL, '', '', 1, '2026-01-01', '2026-01-01', '{}'
        )
        """
    )
    database.close()
    again = Database(tmp_path / "poll-mig.db")
    again.initialize()
    row = again.fetchone("SELECT polling_interval FROM devices WHERE id='d1'")
    # v2 alone (v3 already applied on first init) rewrites legacy 60 → 30 once.
    assert int(row["polling_interval"]) == 30
    again.execute("UPDATE devices SET polling_interval = 60 WHERE id='d1'")
    again.close()
    third = Database(tmp_path / "poll-mig.db")
    third.initialize()
    kept = third.fetchone("SELECT polling_interval FROM devices WHERE id='d1'")
    assert int(kept["polling_interval"]) == 60
    third.close()


def test_existing_thirty_second_poll_interval_migrates_to_one_minute(tmp_path):
    database = Database(tmp_path / "poll-mig-v3.db")
    database.initialize()
    database.execute("DELETE FROM connector_settings WHERE key = 'poll_interval_v3'")
    database.execute(
        """
        INSERT INTO devices (
            id, name, manufacturer, model, ip_address, port, password_enc,
            branch, timezone, communication_type, polling_interval, status,
            terminal_code, serial_number, last_sync_at, last_sync_cursor,
            last_error, enabled, created_at, updated_at, extra_json
        ) VALUES (
            'd1', 'K90', 'essl', 'K90', '192.168.1.10', 4370, '',
            '', 'Asia/Kolkata', 'tcp', 30, 'unknown',
            'T1', '', NULL, '', '', 1, '2026-01-01', '2026-01-01', '{}'
        )
        """
    )
    database.close()
    again = Database(tmp_path / "poll-mig-v3.db")
    again.initialize()
    row = again.fetchone("SELECT polling_interval FROM devices WHERE id='d1'")
    assert int(row["polling_interval"]) == 60
    again.execute("UPDATE devices SET polling_interval = 30 WHERE id='d1'")
    again.close()
    third = Database(tmp_path / "poll-mig-v3.db")
    third.initialize()
    kept = third.fetchone("SELECT polling_interval FROM devices WHERE id='d1'")
    # Migration runs only once — intentional 30s after v3 stays 30.
    assert int(kept["polling_interval"]) == 30
    third.close()


def test_add_device_rejects_duplicate_enabled_ip_port(db, secrets):
    from services.device_manager import DeviceManager

    devices = DeviceRepository(db, secrets)
    mgr = DeviceManager(devices, MagicMock())
    first = mgr.add_device(
        name="eSSL K90",
        manufacturer="essl",
        ip_address="192.168.100.201",
        port=4370,
    )
    assert first.enabled is True
    with pytest.raises(ValueError, match="already registered"):
        mgr.add_device(
            name="infoessl",
            manufacturer="essl",
            ip_address="192.168.100.201",
            port=4370,
        )


def test_token_refresh_retries_without_jwt():
    from api.cloud_client import CloudClient

    settings = ConnectorSettings(jwt_token="expired-jwt", api_key="device-token", verify_ssl=True)
    client = CloudClient(settings)
    calls = {"n": 0}

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.content = b"{}"
            self.text = "{}"

        def json(self):
            return {}

    def fake_request(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(401)
        return _Resp(200)

    mock_http = MagicMock()
    mock_http.request.side_effect = fake_request
    client._client = mock_http
    data = client._request("GET", "https://brixigo.com/api/health")
    assert data == {}
    assert settings.jwt_token == ""
    assert calls["n"] == 2


def test_ssl_verify_build_never_false_when_enabled():
    from security.command_security import build_ssl_context

    assert build_ssl_context(verify_ssl=True) is True or build_ssl_context(verify_ssl=True) is not False


def _healthy_device(states: ConnectionStateRegistry) -> None:
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.set_internet(InternetStatus.ONLINE)
    states.set_sync(SyncStatus.SYNCED, pending=0, failed=0)
    states.record_device_heartbeat(
        "d1",
        network_ok=True,
        protocol_ok=True,
        latency_ms=2.0,
        ip_address="192.168.100.2",
        port=4370,
        name="eSSL K90",
    )


def test_classify_api_sample_thresholds():
    assert classify_api_sample("ok", 800) == "healthy"
    assert classify_api_sample("ok", 2200) == "acceptable"
    assert classify_api_sample("ok", 4000) == "slow"
    assert classify_api_sample("ok", 12000) == "slow"
    assert classify_api_sample("tls", 30000) == "tls"
    assert classify_api_sample("ok", 200, status_code=401) == "auth"


def test_public_cloud_status_never_maps_latency_to_degraded():
    assert public_cloud_status("connected") == "connected"
    assert public_cloud_status("acceptable") == "slightly slow"
    assert public_cloud_status("slow") == "slow"
    assert public_cloud_status("unavailable") == "unavailable"
    assert public_cloud_status("tls_error") == "tls_error"
    assert public_cloud_status("degraded") == "slow"
    assert public_cloud_status("healthy") == "connected"
    assert "degraded" not in {
        public_cloud_status(v)
        for v in (
            "connected",
            "acceptable",
            "slow",
            "timeout",
            "unavailable",
            "tls_error",
            "auth_error",
        )
    }


def test_hysteresis_healthy_stays_healthy():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    states.record_api_result(kind="ok", latency_ms=820)
    states.record_api_result(kind="ok", latency_ms=900)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert public_cloud_status(snap.server_status) == "connected"
    assert snap.devices[0]["status"] == DeviceLinkStatus.CONNECTED.value
    assert resolve_headline(snap).code == "CONNECTED"


def test_one_4s_response_does_not_fail_cloud():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    states.record_api_result(kind="ok", latency_ms=200)
    states.record_api_result(kind="ok", latency_ms=4000)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert snap.api_availability == "available"
    assert snap.consecutive_slow == 1
    assert resolve_headline(snap).code != "SERVER_UNAVAILABLE"
    assert persist_device_status(states.device("d1")) == "online"


def test_hysteresis_healthy_to_slow_requires_three_samples():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    states.record_api_result(kind="ok", latency_ms=400)
    states.record_api_result(kind="ok", latency_ms=4100)
    states.record_api_result(kind="ok", latency_ms=4200)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    states.record_api_result(kind="ok", latency_ms=4300)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.SLOW.value
    assert public_cloud_status(snap.server_status) == "slow"
    assert snap.api_availability == "available"
    assert snap.devices[0]["status"] == DeviceLinkStatus.CONNECTED.value
    assert persist_device_status(states.device("d1")) == "online"
    assert snap.devices[0]["network_status"] == DeviceNetworkStatus.CONNECTED.value
    assert snap.devices[0]["protocol_status"] == DeviceProtocolStatus.CONNECTED.value
    assert resolve_headline(snap).code == "SERVER_SLOW"
    assert "remains connected" in resolve_headline(snap).body.lower()


def test_hysteresis_slow_stays_slow():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    for _ in range(3):
        states.record_api_result(kind="ok", latency_ms=4500)
    states.record_api_result(kind="ok", latency_ms=4600)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.SLOW.value
    assert persist_device_status(states.device("d1")) == "online"


def test_hysteresis_slow_to_healthy():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    for _ in range(3):
        states.record_api_result(kind="ok", latency_ms=4500)
    assert states.snapshot().server_status == ServerStatus.SLOW.value
    states.record_api_result(kind="ok", latency_ms=400)
    assert states.snapshot().server_status == ServerStatus.SLOW.value
    states.record_api_result(kind="ok", latency_ms=420)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert persist_device_status(states.device("d1")) == "online"


def test_hysteresis_slow_to_unavailable():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    for _ in range(3):
        states.record_api_result(kind="ok", latency_ms=4500)
    states.record_api_result(kind="unavailable", error="connection refused")
    assert states.snapshot().server_status == ServerStatus.SLOW.value
    states.record_api_result(kind="unavailable", error="connection refused")
    states.record_api_result(kind="unavailable", error="connection refused")
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.UNAVAILABLE.value
    assert snap.api_availability == "unavailable"
    assert persist_device_status(states.device("d1")) == "online"
    assert resolve_headline(snap).code == "SERVER_UNAVAILABLE"


def test_hysteresis_unavailable_to_healthy():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    states.record_api_result(kind="ok", latency_ms=300)
    for _ in range(3):
        states.record_api_result(kind="unavailable", error="offline")
    assert states.snapshot().server_status == ServerStatus.UNAVAILABLE.value
    states.record_api_result(kind="ok", latency_ms=300)
    assert states.snapshot().server_status == ServerStatus.UNAVAILABLE.value
    states.record_api_result(kind="ok", latency_ms=310)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert persist_device_status(states.device("d1")) == "online"


def test_hysteresis_tls_error_to_healthy():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    states.record_api_result(kind="ok", latency_ms=300)
    for _ in range(3):
        states.record_api_result(kind="tls", error="handshake timed out", latency_ms=30000)
    assert states.snapshot().server_status == ServerStatus.TLS_ERROR.value
    states.record_api_result(kind="ok", latency_ms=300)
    assert states.snapshot().server_status == ServerStatus.TLS_ERROR.value
    states.record_api_result(kind="ok", latency_ms=280)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert persist_device_status(states.device("d1")) == "online"


def test_api_2_to_3_seconds_stays_connected_or_slightly_slow():
    states = ConnectionStateRegistry()
    _healthy_device(states)
    states.record_api_result(kind="ok", latency_ms=2500)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    states.record_api_result(kind="ok", latency_ms=2600)
    states.record_api_result(kind="ok", latency_ms=2700)
    snap = states.snapshot()
    assert snap.server_status in (ServerStatus.CONNECTED.value, ServerStatus.ACCEPTABLE.value)
    assert public_cloud_status(snap.server_status) in ("connected", "slightly slow")
    assert persist_device_status(states.device("d1")) == "online"


def test_expected_state_when_api_is_slow_device_stays_online():
    """Acceptance: Connector RUNNING, Internet ONLINE, Cloud SLOW, device ONLINE."""
    states = ConnectionStateRegistry()
    _healthy_device(states)
    for _ in range(3):
        states.record_api_result(kind="ok", latency_ms=4500)
    snap = states.snapshot()
    assert snap.connector_status == ConnectorRuntimeStatus.RUNNING.value
    assert snap.internet_status == InternetStatus.ONLINE.value
    assert public_cloud_status(snap.server_status) == "slow"
    assert snap.devices[0]["network_status"] == "connected"
    assert snap.devices[0]["protocol_status"] == "connected"
    assert persist_device_status(states.device("d1")) == "online"
    lines = "\n".join(snap.diagnostics_lines())
    assert "Cloud Status" in lines
    assert "Response Time" in lines
    assert "Consecutive Slow" in lines


def test_device_tcp_unavailable_does_not_change_cloud():
    from app.constants import DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER

    states = ConnectionStateRegistry()
    states.set_connector(ConnectorRuntimeStatus.RUNNING)
    states.record_api_result(kind="ok", latency_ms=400)
    states.record_api_result(kind="ok", latency_ms=410)
    _fail_probes(states, n=DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER)
    snap = states.snapshot()
    assert snap.server_status == ServerStatus.CONNECTED.value
    assert snap.devices[0]["status"] == DeviceLinkStatus.DISCONNECTED.value
    assert snap.devices[0]["network_status"] == DeviceNetworkStatus.OFFLINE.value


def test_api_health_interval_is_not_dashboard_rate():
    from app.constants import (
        DEFAULT_API_HEALTH_INTERVAL_SECONDS,
        DEFAULT_DASHBOARD_REFRESH_MS,
        DEFAULT_POLL_INTERVAL_SECONDS,
    )
    from services.api_health_monitor import ApiHealthMonitor

    assert DEFAULT_API_HEALTH_INTERVAL_SECONDS >= 30
    assert DEFAULT_DASHBOARD_REFRESH_MS == DEFAULT_POLL_INTERVAL_SECONDS * 1000
    assert DEFAULT_DASHBOARD_REFRESH_MS == 60_000
    assert DEFAULT_POLL_INTERVAL_SECONDS == 60
    from adapters.protocol.zk_transport import TCP_CONNECT_ATTEMPTS
    from app.constants import FAST_TCP_CONNECT_TIMEOUT, FAST_TCP_PROBE_TIMEOUT

    assert TCP_CONNECT_ATTEMPTS == 3
    assert FAST_TCP_CONNECT_TIMEOUT <= 8.0
    assert FAST_TCP_PROBE_TIMEOUT <= 4.0
    assert FAST_TCP_PROBE_TIMEOUT >= 3.0
    monitor = ApiHealthMonitor(MagicMock(), ConnectionStateRegistry(), interval_seconds=3)
    assert monitor.interval_seconds >= 15


def test_api_health_monitor_never_touches_device_status():
    from services.api_health_monitor import ApiHealthMonitor

    states = ConnectionStateRegistry()
    _healthy_device(states)
    cloud = MagicMock()
    cloud.settings.cloud_base_url = "https://brixigo.com"
    cloud.probe_api_health.return_value = {
        "ok": True,
        "latency_ms": 4500,
        "status_code": 200,
        "kind": "ok",
        "dns_ms": 5,
        "tcp_ms": 80,
        "tls_ms": 120,
        "ttfb_ms": 4000,
    }
    monitor = ApiHealthMonitor(cloud, states)
    with patch.object(monitor, "_check_internet", return_value=True), patch.object(
        monitor, "_check_dns", return_value=(True, "1.2.3.4")
    ):
        monitor.probe_once()
        monitor.probe_once()
        monitor.probe_once()
    snap = states.snapshot()
    assert persist_device_status(states.device("d1")) == "online"
    assert snap.server_status == ServerStatus.SLOW.value
    assert snap.devices[0]["network_status"] == "connected"
    assert snap.devices[0]["protocol_status"] == "connected"


def test_api_success_marks_internet_online_even_if_public_dns_blocked():
    from services.api_health_monitor import ApiHealthMonitor

    states = ConnectionStateRegistry()
    cloud = MagicMock()
    cloud.settings.cloud_base_url = "https://brixigo.com"
    cloud.probe_api_health.return_value = {
        "ok": True,
        "latency_ms": 900,
        "status_code": 200,
        "kind": "ok",
        "dns_ms": 5,
        "tcp_ms": 40,
        "tls_ms": 80,
        "ttfb_ms": 700,
    }
    monitor = ApiHealthMonitor(cloud, states)
    with patch.object(monitor, "_check_internet", return_value=False), patch.object(
        monitor, "_check_dns", return_value=(True, "1.2.3.4")
    ):
        monitor.probe_once()
    assert states.snapshot().internet_status == InternetStatus.ONLINE.value


def test_zk_transport_tcp_connect_fails_fast(monkeypatch):
    import socket

    from adapters.protocol.zk_packet import ZKProtocolError
    from adapters.protocol.zk_transport import ZKTransport

    calls = {"n": 0}

    class FakeSock:
        def __init__(self, sock_type=0):
            self.sock_type = sock_type

        def settimeout(self, _t):
            return None

        def setsockopt(self, *_a, **_k):
            return None

        def bind(self, _addr):
            return None

        def getsockname(self):
            return ("192.168.100.254", 0)

        def connect(self, addr):
            if self.sock_type == socket.SOCK_STREAM:
                calls["n"] += 1
                raise TimeoutError("timed out")

        def close(self):
            return None

    monkeypatch.setattr(
        "adapters.protocol.zk_transport.socket.socket",
        lambda family=0, sock_type=0, *a, **k: FakeSock(sock_type),
    )
    monkeypatch.setattr("adapters.protocol.zk_transport.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "adapters.protocol.zk_transport.refresh_neighbor_arp", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "adapters.protocol.zk_transport.wake_neighbor", lambda *_a, **_k: None
    )
    transport = ZKTransport(ip="192.168.100.201", port=4370, connect_timeout=0.2, read_timeout=0.2)
    try:
        with pytest.raises(ZKProtocolError, match="TCP connect failed"):
            transport.open()
        assert calls["n"] == 3
    finally:
        transport.close(silent=True)


def test_zk_transport_tcp_connect_succeeds_on_open_port():
    import socket as stdlib_socket

    from adapters.protocol.zk_transport import ZKTransport

    listener = stdlib_socket.socket(stdlib_socket.AF_INET, stdlib_socket.SOCK_STREAM)
    listener.setsockopt(stdlib_socket.SOL_SOCKET, stdlib_socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    transport = ZKTransport(ip="127.0.0.1", port=port, connect_timeout=1.0, read_timeout=1.0)
    try:
        transport.open()
        assert transport._sock is not None
        peer = transport._sock.getpeername()
        assert peer[0] == "127.0.0.1"
        assert peer[1] == port
    finally:
        transport.close(silent=True)
        listener.close()


def test_dashboard_refresh_does_not_call_http_health():
    from services.connector_engine import ConnectorEngine

    assert "probe_api_health" not in ConnectorEngine.dashboard.__code__.co_names
    assert "internet_ok" not in ConnectorEngine.dashboard.__code__.co_names


def test_lan_relocate_updates_stored_ip_when_serial_matches(db, secrets):
    from models import DiscoveredDevice
    from services.device_manager import DeviceManager
    from services.network_scanner import LanRelocateResult
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device(serial_number="SN-K90", ip_address="192.168.100.201")
    devices.create(device)
    manager = DeviceManager(devices, SyncLogRepository(db))
    found = DiscoveredDevice(
        ip_address="192.168.100.77",
        port=4370,
        serial_number="SN-K90",
        mac_address="aa:bb:cc:dd:ee:ff",
    )
    scanner = MagicMock()
    scanner.find_relocation_candidate.return_value = (
        found,
        ["192.168.100.1-254"],
        1,
    )
    manager.bind_scanner(scanner)
    with patch(
        "services.device_manager.tcp_probe",
        return_value=(False, 20.0, "WinError 10065"),
    ):
        result = manager.relocate_if_unreachable(device, force=True)
    assert isinstance(result, LanRelocateResult)
    assert result.relocated is not None
    assert result.relocated.ip_address == "192.168.100.77"
    assert devices.get(device.id).ip_address == "192.168.100.77"


def test_poll_explains_same_subnet_host_unreachable(db, secrets):
    from services.connection_state import DeviceNetworkStatus
    from services.network_scanner import LanRelocateResult
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device(ip_address="192.168.100.201")
    devices.create(device)
    link = MagicMock()
    link.network_status = DeviceNetworkStatus.OFFLINE
    states = MagicMock()
    states.device.return_value = link
    relocator = MagicMock(
        return_value=LanRelocateResult(
            scanned=True,
            scan_ranges=("192.168.100.1-254",),
            local_ips=("192.168.100.254",),
            open_host_count=0,
            same_subnet=True,
        )
    )
    service = PollService(
        devices,
        AttendanceRepository(db),
        SyncLogRepository(db),
        states=states,
        relocator=relocator,
    )
    with patch(
        "services.poll_service.tcp_probe", return_value=(False, 5.0, "WinError 10065")
    ):
        result = service.poll_device(device, full=False, force_relocate=True)
    assert result["success"] is False
    assert "no path to the terminal" in result["error"]
    assert "192.168.100.254" in result["error"]
    assert "192.168.100.201:4370" in result["error"]
    assert "LAN scan of 192.168.100.1-254" in result["error"]
    relocator.assert_called_once()


def test_sync_dialog_10065_asks_operator_to_check_lan():
    from services.sync_report import format_sync_dialog

    _title, body, ok = format_sync_dialog(
        [
            {
                "device_name": "eSSL K90",
                "manufacturer": "essl",
                "model": "ZLM60_TFT",
                "success": False,
                "status": "FAILED",
                "fetched": 0,
                "inserted": 0,
                "error": (
                    "TCP connect failed: [WinError 10065] A socket operation "
                    "was attempted to an unreachable host"
                ),
                "duration_sec": 2.8,
            }
        ]
    )
    assert ok is False
    assert "10065" in body
    assert "Discover devices" in body
    assert "Auto-sync retries every 1 minute" in body
