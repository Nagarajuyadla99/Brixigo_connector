"""Operations platform: health score, clocks, names, daily/monthly attendance."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models import AttendanceRecord, Device, QueueStatus, new_id
from services.attendance_analytics import build_daily_rows, build_monthly_rows, resolve_daily_day
from services.employee_directory import employee_display_name, enrich_employee_fields
from services.health_score import compute_health_score, status_for_score
from services.punch_normalizer import (
    analyze_clock,
    is_retryable_upload_error,
    method_label,
    normalize_method_for_storage,
)
from storage.database import Database
from storage.repositories.attendance_repo import AttendanceRepository


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "ops.db")
    database.initialize()
    yield database
    database.close()


def _insert_device(db: Database, device_id: str = "d1", name: str = "eSSL K90") -> None:
    db.execute(
        """
        INSERT INTO devices (
            id, name, manufacturer, model, ip_address, port, password_enc,
            branch, timezone, communication_type, polling_interval, status,
            terminal_code, serial_number, last_sync_at, last_sync_cursor,
            last_error, enabled, created_at, updated_at, extra_json
        ) VALUES (
            ?, ?, 'essl', 'K90', '192.168.100.201', 4370, '',
            'HQ', 'Asia/Kolkata', 'tcp', 60, 'online',
            'DEV-B250C5B1', 'NFZ1', NULL, '', '', 1, '2026-08-31', '2026-08-31', '{}'
        )
        """,
        (device_id, name),
    )


def test_health_score_healthy_when_all_green():
    result = compute_health_score(
        internet_status="online",
        cloud_status="connected",
        online_devices=1,
        enabled_devices=1,
        total_devices=1,
        uploaded_today=10,
        failed_uploads=0,
        pending_uploads=0,
        average_upload_ms=41,
        clock_mismatch_count=0,
        critical_clock_mismatch=False,
    )
    assert result.overall >= 85
    assert result.status == "healthy"
    assert result.connectivity == 100
    assert result.device_availability == 100
    assert result.upload_success == 100
    assert result.clock_sync == 100


def test_health_score_drops_for_clock_mismatch_and_offline_device():
    result = compute_health_score(
        internet_status="online",
        cloud_status="connected",
        online_devices=0,
        enabled_devices=1,
        total_devices=1,
        uploaded_today=1,
        failed_uploads=0,
        pending_uploads=0,
        average_upload_ms=41,
        clock_mismatch_count=1,
        critical_clock_mismatch=True,
    )
    assert result.device_availability == 0
    assert result.clock_sync == 25
    assert result.overall < 85
    assert result.status in {"warning", "critical"}


def test_health_status_bands():
    assert status_for_score(98) == ("healthy", "Healthy")
    assert status_for_score(70)[0] == "warning"
    assert status_for_score(40)[0] == "critical"


def test_clock_mismatch_year_2000_is_critical():
    clock = analyze_clock(
        "2000-08-31T17:56:32",
        "2026-08-31T14:43:00+05:30",
    )
    assert clock.mismatch is True
    assert clock.critical is True
    assert clock.device_year == 2000
    assert "2000" in clock.device_timestamp
    assert clock.normalized_timestamp.startswith("2026-08-31T14:43:00")
    assert "year" in clock.difference_label or "years" in clock.difference_label


def test_corrected_year_2000_logs_are_not_a_live_mismatch(db):
    """Original device stamps stay year-2000; attendance time is already modern."""
    _insert_device(db)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1",
        punch_time="2026-09-08T12:02:26+05:30",
        event_type="IN",
        idempotency_key=f"clk-{new_id()}",
        raw_payload={"device_punch_time": "2000-02-08T17:57:14+05:30"},
        created_at="2026-09-08T06:32:26+00:00",
    )
    assert repo.enqueue(rec)[0] is True
    summary = repo.clock_mismatch_summary()
    assert summary["critical"] is False
    assert summary["count"] == 0
    assert summary["samples"] == []


def test_unhealed_year_2000_punch_time_is_a_mismatch(db):
    _insert_device(db)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1",
        punch_time="2000-02-08T17:57:14+05:30",
        event_type="IN",
        idempotency_key=f"clk-{new_id()}",
        raw_payload={"device_punch_time": "2000-02-08T17:57:14+05:30"},
        created_at="2026-09-08T06:32:26+00:00",
    )
    assert repo.enqueue(rec)[0] is True
    summary = repo.clock_mismatch_summary()
    assert summary["critical"] is True
    assert summary["count"] >= 1


def test_clock_mismatch_clears_after_successful_time_sync(db):
    _insert_device(db)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1",
        punch_time="2026-08-31T17:57:14+05:30",
        event_type="IN",
        idempotency_key=f"clk-{new_id()}",
        raw_payload={"device_punch_time": "2000-02-08T17:57:14+05:30"},
        created_at="2026-08-31T12:00:00+00:00",
    )
    assert repo.enqueue(rec)[0] is True
    collected = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT INTO device_health (
            id, device_id, online, ping_ms, memory_usage_percent,
            storage_usage_percent, user_capacity, log_capacity,
            current_users, current_logs, firmware_version, device_time,
            restart_count, message, collected_at, pushed, details_json
        ) VALUES (
            'h1', 'd1', 1, 8, 0, 0, 0, 0, 0, 0, '',
            '2000-02-08 17:57:14', 0, '', ?, 0, '{}'
        )
        """,
        (collected,),
    )
    summary = repo.clock_mismatch_summary()
    assert summary["critical"] is True
    db.execute(
        "UPDATE devices SET last_time_sync_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), "d1"),
    )
    cleared = repo.clock_mismatch_summary()
    assert cleared["critical"] is False
    assert cleared["count"] == 0


def test_historical_year_2000_logs_after_rtc_sync_do_not_reflag(db):
    """Buffer logs downloaded after SET_TIME must not bring the 27-year banner back."""
    _insert_device(db)
    db.execute(
        "UPDATE devices SET last_time_sync_at=? WHERE id=?",
        ("2026-09-08T06:00:00+00:00", "d1"),
    )
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1",
        punch_time="2026-09-08T12:02:26+05:30",
        event_type="IN",
        idempotency_key=f"clk-late-{new_id()}",
        raw_payload={"device_punch_time": "2000-02-08T17:57:14+05:30"},
        created_at="2026-09-08T06:32:26+00:00",
    )
    assert repo.enqueue(rec)[0] is True
    summary = repo.clock_mismatch_summary()
    assert summary["critical"] is False
    assert summary["count"] == 0


def test_year_2000_clock_is_corrected_to_local_wall_clock():
    from services.punch_normalizer import (
        connector_wall_clock,
        device_clock_needs_correction,
        write_device_clock,
    )

    now = connector_wall_clock()
    assert device_clock_needs_correction(datetime(2000, 2, 8, 17, 57, 14), now) is True
    assert device_clock_needs_correction(now, now, threshold_seconds=30) is False

    class _Clock:
        def __init__(self):
            self.time = datetime(2000, 2, 8, 17, 57, 14)

        def get(self):
            return self.time

        def set(self, when):
            self.time = when

    clock = _Clock()
    result = write_device_clock(get_time=clock.get, set_time=clock.set, when=now)
    assert result["success"] is True
    assert result["corrected"] is True
    assert clock.time.year == now.year
    assert clock.time.month == now.month
    assert clock.time.day == now.day
    assert clock.time.tzinfo is None


def test_time_sync_service_writes_local_clock_through_session():
    from services.phase3_services import TimeSyncService

    now = datetime.now().replace(microsecond=0)

    class _Adapter:
        def __init__(self):
            self.time = datetime(2000, 2, 8, 17, 57, 14)

        def get_device_time(self):
            return self.time

        def sync_time(self, when):
            self.time = when

    class _Sessions:
        def __init__(self, adapter):
            self.adapter = adapter

        def execute(self, device, operation, retries=None, owner=""):
            return operation(self.adapter)

    class _Sink:
        def __init__(self):
            self.touched = []
            self.rows = []

        def touch_field(self, *args, **kwargs):
            self.touched.append((args, kwargs))

        def add(self, **kwargs):
            self.rows.append(kwargs)

    adapter = _Adapter()
    devices = _Sink()
    logs = _Sink()
    svc = TimeSyncService.__new__(TimeSyncService)
    svc._sessions = _Sessions(adapter)
    svc._devices = devices
    svc._logs = logs
    svc._sync_logs = logs
    svc.drift_threshold_seconds = 30
    svc._on_clock_mismatch = None
    result = svc.sync_device(type("D", (), {"id": "d1"})())
    assert result["success"] is True
    assert result["corrected"] is True
    assert adapter.time.year == now.year
    assert adapter.time.tzinfo is None
    assert abs((adapter.time - now).total_seconds()) < 5
    assert devices.touched


def test_time_sync_probe_timeout_still_opens_session():
    from services.phase3_services import TimeSyncService

    class _Adapter:
        def __init__(self):
            self.called = False

        def get_device_time(self):
            self.called = True
            return datetime.now().replace(microsecond=0)

        def sync_time(self, when):
            self.time = when

    class _Sessions:
        def __init__(self, adapter):
            self.adapter = adapter
            self.owners = []

        def execute(self, device, operation, retries=None, owner=""):
            self.owners.append(owner)
            return operation(self.adapter)

    class _Sink:
        def touch_field(self, *args, **kwargs):
            return None

        def add(self, **kwargs):
            return None

    adapter = _Adapter()
    sessions = _Sessions(adapter)
    svc = TimeSyncService.__new__(TimeSyncService)
    svc._sessions = sessions
    svc._devices = _Sink()
    svc._logs = _Sink()
    svc._sync_logs = _Sink()
    svc.drift_threshold_seconds = 30
    svc._on_clock_mismatch = None
    device = type("D", (), {"id": "d1", "ip_address": "192.168.100.201", "port": 4370})()
    with patch(
        "services.connection_state.tcp_probe",
        return_value=(False, 2000.0, "timed out"),
    ):
        result = svc.sync_device(device)
    assert sessions.owners == ["time-sync"]
    assert adapter.called is True
    assert result["success"] is True


def test_clock_in_sync_is_not_a_mismatch():
    clock = analyze_clock(
        "2026-08-31T14:43:10+05:30",
        "2026-08-31T14:43:12+05:30",
    )
    assert clock.mismatch is False
    assert clock.critical is False


def test_method_not_reported_when_vendor_silent():
    assert normalize_method_for_storage(None) == "not_reported"
    assert normalize_method_for_storage("unknown") == "not_reported"
    assert normalize_method_for_storage("", punch=1) == "fingerprint"
    assert method_label("unknown") == "Not reported by device"
    assert method_label("not_reported") == "Not reported by device"
    assert method_label("fingerprint") == "Fingerprint"


def test_punch_trend_hourly_fills_missing_hours(db):
    _insert_device(db)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1",
        punch_time=datetime.now(timezone.utc).isoformat(),
        event_type="IN",
        verification_method="fingerprint",
        idempotency_key="hourly-1",
        status=QueueStatus.UPLOADED.value,
    )
    assert repo.enqueue(rec)[0] is True
    series = repo.punch_trend_hourly(24)
    assert len(series) == 24
    assert sum(p["collected"] for p in series) >= 1
    assert all("collected" in p and "uploaded" in p and "failed" in p for p in series)


def test_retry_classifies_permanent_vs_transient():
    assert is_retryable_upload_error("TLS handshake timeout") is True
    assert is_retryable_upload_error("503 unavailable") is True
    assert is_retryable_upload_error("validation: missing employee") is False
    assert is_retryable_upload_error("Device missing locally") is False


def test_employee_display_name_does_not_invent_identity():
    assert employee_display_name("Ravi Kumar", "1") == "Ravi Kumar (1)"
    assert employee_display_name("", "1") == "Device user 1"
    row = enrich_employee_fields({"device_employee_code": "28", "employee_name": ""})
    assert row["employee_name_reported"] is False
    assert "not reported" in row["employee_name_hint"].lower()


def test_daily_and_monthly_attendance_use_employee_names(db):
    _insert_device(db)
    db.execute(
        """
        INSERT INTO employees (
            id, employee_code, name, department, designation, branch,
            organization, building, floor, gate, shift, card_number, pin_enc,
            status, device_ids_json, privilege, cloud_version, last_synced_at,
            created_at, updated_at, extra_json
        ) VALUES (
            'e1', '1', 'Anita Sharma', '', '', 'HQ',
            '', '', '', '', '', '', '',
            'active', '[]', 0, 0, NULL,
            '2026-08-01', '2026-08-01', '{}'
        )
        """
    )
    db.execute(
        """
        INSERT INTO device_users (
            id, device_id, user_id, name, card, privilege, password_enc,
            enabled, employee_id, last_synced_at, sync_status, conflict,
            raw_json, updated_at
        ) VALUES (
            'u2', 'd1', '2', 'Device User Two', '', 0, '',
            1, '', NULL, 'synced', '', '{}', '2026-08-31'
        )
        """
    )
    repo = AttendanceRepository(db)
    for code, event, hour in (("1", "IN", 9), ("1", "OUT", 18), ("2", "IN", 10)):
        rec = AttendanceRecord(
            id=new_id(),
            device_id="d1",
            device_employee_code=code,
            punch_time=f"2026-08-31T{hour:02d}:00:00+05:30",
            event_type=event,
            verification_method="fingerprint",
            idempotency_key=f"k-{code}-{event}-{hour}",
            status=QueueStatus.UPLOADED.value,
        )
        ok, _ = repo.enqueue(rec)
        assert ok is True

    listed = repo.list_recent(10)
    names = {p["device_employee_code"]: p["employee_display_name"] for p in listed}
    assert "Anita Sharma" in names["1"]
    assert "Device User Two" in names["2"]

    daily = repo.daily_attendance("2026-08-31")
    by_code = {r["employee_code"]: r for r in daily}
    assert by_code["1"]["punch_count"] == 2
    assert by_code["1"]["present"] is True
    assert "Anita Sharma" in by_code["1"]["employee_display_name"]

    monthly = repo.monthly_attendance(2026, 8)
    assert monthly["employee_count"] == 2
    anita = next(r for r in monthly["rows"] if r["employee_code"] == "1")
    assert anita["days"]["2026-08-31"]["present"] is True
    assert anita["present_days"] == 1


def test_daily_attendance_falls_back_to_device_user_on_other_terminal(db):
    _insert_device(db)
    db.execute(
        """
        INSERT INTO devices (
            id, name, manufacturer, model, ip_address, port, password_enc,
            branch, timezone, communication_type, polling_interval, status,
            terminal_code, serial_number, last_sync_at, last_sync_cursor,
            last_error, enabled, created_at, updated_at, extra_json
        ) VALUES (
            'd2', 'gate-2', 'essl', 'K90', '192.168.100.202', 4370, '',
            'HQ', 'Asia/Kolkata', 'tcp', 30, 'online',
            'GATE2', '', NULL, '', '', 1, '2026-08-01', '2026-08-01', '{}'
        )
        """
    )
    db.execute(
        """
        INSERT INTO device_users (
            id, device_id, user_id, name, card, privilege, password_enc,
            enabled, employee_id, last_synced_at, sync_status, conflict,
            raw_json, updated_at
        ) VALUES (
            'u9', 'd2', '3', 'Ravi Kumar', '', 0, '',
            1, '', NULL, 'synced', '', '{}', '2026-08-31'
        )
        """
    )
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="3",
        punch_time="2026-08-31T09:00:00+05:30",
        event_type="IN",
        idempotency_key=f"k-fallback-{new_id()}",
        status=QueueStatus.UPLOADED.value,
    )
    assert repo.enqueue(rec)[0] is True
    daily = repo.daily_attendance("2026-08-31")
    row = next(r for r in daily if r["employee_code"] == "3")
    assert "Ravi Kumar" in row["employee_display_name"]


def test_resolve_daily_day_snaps_future_picker_to_today():
    assert resolve_daily_day("2028-08-31", today="2026-08-31") == "2026-08-31"
    assert resolve_daily_day("2026-08-31", today="2026-08-31") == "2026-08-31"
    assert resolve_daily_day("2026-08-30", today="2026-08-31") == "2026-08-30"
    assert resolve_daily_day("", today="2026-08-31") == "2026-08-31"


def test_duplicate_punch_still_blocked(db):
    _insert_device(db)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1",
        punch_time="2026-08-31T09:00:00+05:30",
        event_type="IN",
        idempotency_key="essl:d1:1:2026-08-31T09:00:00+05:30:IN",
        status=QueueStatus.PENDING.value,
    )
    assert repo.enqueue(rec)[0] is True
    assert repo.enqueue(rec)[0] is False
    assert repo.pending_count() == 1


def test_upload_status_transitions(db):
    _insert_device(db)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id="p1",
        device_id="d1",
        device_employee_code="1",
        punch_time="2026-08-31T09:00:00+05:30",
        event_type="IN",
        idempotency_key="k-status",
        status=QueueStatus.PENDING.value,
    )
    repo.enqueue(rec)
    repo.mark_uploading(["p1"])
    assert repo._row_to_record(db.fetchone("SELECT * FROM attendance_queue WHERE id='p1'")).status == "uploading"
    repo.mark_uploaded(["p1"])
    loaded = repo._row_to_record(db.fetchone("SELECT * FROM attendance_queue WHERE id='p1'"))
    assert loaded.status == "uploaded"
    assert loaded.uploaded_at


def test_attendance_filter_by_employee_name(db):
    _insert_device(db)
    db.execute(
        """
        INSERT INTO employees (
            id, employee_code, name, department, designation, branch,
            organization, building, floor, gate, shift, card_number, pin_enc,
            status, device_ids_json, privilege, cloud_version, last_synced_at,
            created_at, updated_at, extra_json
        ) VALUES (
            'e1', '1', 'Anita Sharma', '', '', 'HQ',
            '', '', '', '', '', '', '',
            'active', '[]', 0, 0, NULL,
            '2026-08-01', '2026-08-01', '{}'
        )
        """
    )
    repo = AttendanceRepository(db)
    repo.enqueue(
        AttendanceRecord(
            id=new_id(),
            device_id="d1",
            device_employee_code="1",
            punch_time="2026-08-31T09:00:00+05:30",
            event_type="IN",
            idempotency_key="flt-1",
            status=QueueStatus.UPLOADED.value,
        )
    )
    found = repo.list_filtered(search="Anita", limit=20)
    assert found["total"] == 1
    assert found["items"][0]["employee_name"] == "Anita Sharma"


def test_admin_role_helper_semantics():
    from services.connector_engine import ConnectorEngine

    class _Settings:
        def as_model(self):
            from models import ConnectorSettings

            return ConnectorSettings(role="viewer")

    class _Container:
        settings = _Settings()

    engine = ConnectorEngine.__new__(ConnectorEngine)
    engine.container = _Container()
    assert engine.is_admin() is False
    engine.container.settings.as_model = lambda: type("S", (), {"role": "admin"})()
    assert engine.is_admin() is True
