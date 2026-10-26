"""Attendance JSON serialization, queue retention, and sync-result tests."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.json_codec import (
    JSONSerializationError,
    correct_unset_device_clock,
    datetime_to_iso,
    dumps,
    format_api_datetime,
    to_json_value,
)
from models import AttendanceLog, AttendanceRecord, Device, QueueStatus, new_id
from storage.database import Database
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.device_repo import DeviceRepository


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "att-ser.db")
    database.initialize()
    yield database
    database.close()


@pytest.fixture()
def secrets():
    from security.encryption import SecretBox, derive_machine_key

    return SecretBox(derive_machine_key(app_id="test-att-ser"))


def _device(**kwargs) -> Device:
    defaults = dict(
        id=new_id(),
        name="eSSL K90",
        manufacturer="essl",
        model="K90",
        ip_address="192.168.100.2",
        port=4370,
        password="0",
        branch="HQ",
        terminal_code="K90-1",
        timezone="Asia/Kolkata",
    )
    defaults.update(kwargs)
    return Device(**defaults)


def test_timezone_aware_datetime_iso():
    dt = datetime(2026, 8, 26, 16, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert datetime_to_iso(dt) == "2026-08-26T16:00:00+05:30"
    payload = json.loads(dumps({"attendance_time": dt}))
    assert payload["attendance_time"] == "2026-08-26T16:00:00+05:30"


def test_utc_datetime_uses_z_suffix():
    dt = datetime(2026, 8, 26, 10, 36, 32, tzinfo=timezone.utc)
    assert datetime_to_iso(dt) == "2026-08-26T10:36:32Z"
    payload = json.loads(dumps({"created_at": dt}))
    assert payload["created_at"] == "2026-08-26T10:36:32Z"


def test_date_serializes_as_iso_date():
    value = date(2026, 8, 26)
    assert datetime_to_iso(value) == "2026-08-26"
    payload = json.loads(dumps({"day": value}))
    assert payload["day"] == "2026-08-26"


def test_nested_datetime_serializes():
    dt = datetime(2026, 8, 26, 16, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    payload = json.loads(dumps({"attendance": {"timestamp": dt}}))
    assert payload["attendance"]["timestamp"] == "2026-08-26T16:00:00+05:30"


def test_naive_device_time_keeps_wall_clock_when_localized():
    """K90 `_decode_time` is naive local time — do not convert to UTC."""
    punch = datetime(2026, 8, 26, 15, 55, 21)
    text = format_api_datetime(punch, default_timezone="Asia/Kolkata")
    assert text == "2026-08-26T15:55:21+05:30"


def test_k90_attendance_payload_serializes(db, secrets):
    from contracts.payroll_punch import build_payroll_punch

    punch_time = datetime(2026, 8, 26, 15, 55, 21)
    raw = to_json_value(
        {
            "uid": 12,
            "user_id": "1001",
            "status": 0,
            "punch": 1,
            "timestamp": "2026-08-26 15:55:21",
            "punch_time": punch_time,
            "vendor": "essl",
        },
        default_timezone="Asia/Kolkata",
    )
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1001",
        punch_time=format_api_datetime(punch_time, default_timezone=device.timezone),
        event_type="IN",
        verification_method="fingerprint",
        idempotency_key=f"essl:{device.id}:1001:2026-08-26T15:55:21+05:30:IN",
        raw_payload=raw,
        status=QueueStatus.PENDING.value,
    )
    repo = AttendanceRepository(db)
    inserted, stored = repo.enqueue(rec)
    assert inserted is True
    pending = repo.list_pending()
    assert len(pending) == 1
    punch = build_payroll_punch(
        pending[0],
        terminal_code=device.terminal_code,
        manufacturer="essl",
        device_name=device.name,
        timezone=device.timezone,
    )
    body = {"punches": [punch], "source": "brixigo-connector", "version": "test"}
    encoded = dumps(body)
    parsed = json.loads(encoded)
    assert parsed["punches"][0]["punch_time"] == "2026-08-26T15:55:21+05:30"
    assert "datetime" not in encoded
    meta_punch = parsed["punches"][0]["metadata"]["punch_time"]
    assert meta_punch == "2026-08-26T15:55:21+05:30"


def test_enqueue_accepts_datetime_in_raw_payload(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="55",
        punch_time="2026-08-26T16:06:32+05:30",
        event_type="IN",
        idempotency_key=f"raw-{new_id()}",
        raw_payload={"punch_time": datetime(2026, 8, 26, 16, 6, 32)},
    )
    ok, _ = repo.enqueue(rec)
    assert ok is True
    loaded = repo.list_pending()[0]
    assert loaded.raw_payload["punch_time"] == "2026-08-26T16:06:32"


def test_poll_enqueue_keeps_device_punch_time(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    attendance = AttendanceRepository(db)
    service = PollService(devices, attendance, SyncLogRepository(db))
    punch = datetime(2026, 8, 26, 15, 55, 21)
    logs = [
        AttendanceLog(
            user_id="1001",
            punch_time=punch,
            status=0,
            punch=1,
            uid=1,
            raw={
                "uid": 1,
                "user_id": "1001",
                "status": 0,
                "punch": 1,
                "timestamp": "2026-08-26 15:55:21",
                "punch_time": punch,
                "vendor": "essl",
            },
        )
    ]
    fetched, inserted, newest, _clock = service._enqueue_logs(device, logs)
    assert fetched == 1
    assert inserted == 1
    assert newest == punch
    stored = attendance.list_pending()[0]
    assert stored.punch_time == "2026-08-26T15:55:21+05:30"
    assert stored.raw_payload["punch_time"] == "2026-08-26T15:55:21+05:30"


def test_poll_enqueue_rewrites_year_2000_clock(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    attendance = AttendanceRepository(db)
    service = PollService(devices, attendance, SyncLogRepository(db))
    punch = datetime(2000, 2, 8, 17, 46, 55)
    logs = [
        AttendanceLog(
            user_id="1",
            punch_time=punch,
            status=0,
            punch=1,
            uid=1,
            raw={"uid": 1, "user_id": "1", "timestamp": "2000-02-08 17:46:55"},
        )
    ]
    fetched, inserted, *_ = service._enqueue_logs(device, logs)
    assert fetched == 1
    assert inserted == 1
    stored = attendance.list_pending()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    assert stored.punch_time.startswith(today)
    parsed = datetime.fromisoformat(stored.punch_time)
    local = parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    assert abs((local - datetime.now()).total_seconds()) < 10
    assert stored.raw_payload["device_punch_time"].startswith("2000-02-08")
    assert stored.idempotency_key.endswith("2000-02-08T17:46:55+05:30:IN") or "2000-02-08" in stored.idempotency_key
    fetched2, inserted2, *_ = service._enqueue_logs(device, logs)
    assert fetched2 == 1
    assert inserted2 == 0
    assert attendance.pending_count() == 1


def test_poll_enqueue_maps_unset_logs_after_modern_rtc_sync(db, secrets):
    """New Sai punches must count on Daily after the terminal RTC is synced."""
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    attendance = AttendanceRepository(db)
    service = PollService(devices, attendance, SyncLogRepository(db))
    punch = datetime(2000, 2, 8, 18, 37, 12)
    modern_rtc = datetime.now().replace(microsecond=0)
    logs = [
        AttendanceLog(
            user_id="5",
            punch_time=punch,
            status=0,
            punch=1,
            uid=5,
            raw={"uid": 5, "user_id": "5", "timestamp": "2000-02-08 18:37:12"},
        )
    ]
    fetched, inserted, newest, clock = service._enqueue_logs(
        device, logs, device_now=modern_rtc
    )
    assert fetched == 1
    assert inserted == 1
    assert clock is None or clock.mismatch is False
    stored = attendance.list_pending()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    assert stored.punch_time.startswith(today)
    assert "2000" not in stored.punch_time[:10]
    assert stored.raw_payload["device_punch_time"].startswith("2000-02-08")
    daily = attendance.daily_attendance(today)
    sai = next(
        (row for row in daily if str(row.get("employee_code") or "") == "5"),
        None,
    )
    assert sai is not None
    assert int(sai.get("punch_count") or 0) >= 1
    summary = attendance.clock_mismatch_summary()
    assert summary["critical"] is False
    reloaded = devices.get(device.id)
    assert str(getattr(reloaded, "last_time_sync_at", "") or "").strip()


def test_incremental_poll_always_downloads_logs(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device(current_logs=12, last_sync_cursor="2026-08-31 20:00:00")
    devices.create(device)
    attendance = AttendanceRepository(db)
    service = PollService(devices, attendance, SyncLogRepository(db))

    class _Adapter:
        def __init__(self):
            self.device = device
            self.downloaded = 0
            self.since = "unset"

        def download_logs(self, since=None):
            self.downloaded += 1
            self.since = since
            return []

        def get_device_time(self):
            return None

        def device_info(self):
            raise RuntimeError("info unavailable")

    adapter = _Adapter()
    logs, info, users, clock = service._collect_and_correct_clock(
        adapter, None, full=False
    )
    assert logs == []
    assert clock.get("skipped") is not True
    assert adapter.downloaded == 1


def test_since_cursor_ignored_when_year_is_connector_clock():
    from services.poll_service import PollService

    device = _device(last_sync_cursor="2026-08-31 20:49:00")
    assert PollService._since_for_device(device) is None
    parsed = PollService._since_for_device(
        device, device_now=datetime(2026, 8, 31, 21, 0, 0)
    )
    assert parsed is not None
    assert parsed.year == 2026
    device.last_sync_cursor = "2000-02-08 17:57:48"
    parsed = PollService._since_for_device(
        device, device_now=datetime(2000, 2, 8, 18, 0, 0)
    )
    assert parsed is not None
    assert parsed.year == 2000
    # Cursor ahead of unset device clock would drop new punches — disable it.
    device.last_sync_cursor = "2002-09-18 16:01:15"
    assert (
        PollService._since_for_device(
            device, device_now=datetime(2000, 2, 8, 18, 36, 0)
        )
        is None
    )
    # Ancient cursor with modern device clock → full fetch.
    assert (
        PollService._since_for_device(
            device, device_now=datetime(2026, 9, 7, 13, 0, 0)
        )
        is None
    )


def test_poll_always_downloads_when_log_count_unchanged(db, secrets):
    """Unchanged attendance_count must not skip download — circular buffers stay flat."""
    from models import DeviceInfo
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device(current_logs=12, last_sync_cursor="2026-08-31 20:00:00")
    devices.create(device)
    service = PollService(devices, AttendanceRepository(db), SyncLogRepository(db))

    class _Adapter:
        def __init__(self):
            self.device = device
            self.downloaded = 0
            self.since = object()

        def download_logs(self, since=None):
            self.downloaded += 1
            self.since = since
            return []

        def get_device_time(self):
            return datetime(2026, 8, 31, 21, 0, 0)

        def device_info(self):
            return DeviceInfo(attendance_count=12)

    adapter = _Adapter()
    logs, info, users, clock = service._collect_and_correct_clock(adapter, full=False)
    assert logs == []
    assert adapter.downloaded == 1
    assert clock.get("skipped_unchanged") is not True
    assert info is not None
    assert info.attendance_count == 12
    assert adapter.since is not None


def test_poll_unset_clock_does_not_skip_unchanged_log_count(db, secrets):
    """Unset device clocks must always download — counters alone miss new punches."""
    from models import DeviceInfo
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device(current_logs=12, last_sync_cursor="2000-02-08 17:00:00")
    devices.create(device)
    service = PollService(devices, AttendanceRepository(db), SyncLogRepository(db))

    class _Adapter:
        def __init__(self):
            self.device = device
            self.downloaded = 0
            self.time = datetime(2000, 2, 8, 17, 0, 0)

        def download_logs(self, since=None):
            self.downloaded += 1
            # Cursor equal to device clock → since disabled when clock <= cursor.
            assert since is None
            return []

        def get_device_time(self):
            return self.time

        def sync_time(self, when):
            self.time = when

        def device_info(self):
            return DeviceInfo(attendance_count=12)

    adapter = _Adapter()
    logs, info, users, clock = service._collect_and_correct_clock(adapter, full=False)
    assert logs == []
    assert adapter.downloaded == 1
    assert clock.get("skipped_unchanged") is not True
    assert clock.get("corrected") is True
    assert adapter.time.year >= 2026


def test_poll_fails_fast_when_device_tcp_is_down(db, secrets):
    from services.connection_state import DeviceNetworkStatus
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    link = MagicMock()
    link.network_status = DeviceNetworkStatus.OFFLINE
    states = MagicMock()
    states.device.return_value = link
    service = PollService(
        devices, AttendanceRepository(db), SyncLogRepository(db), states=states
    )
    with patch(
        "services.poll_service.tcp_probe", return_value=(False, 5.0, "WinError 10065")
    ):
        result = service.poll_device(device, full=False)
    assert result["success"] is False
    assert "TCP connect failed" in result["error"]
    assert result["duration_sec"] < 1.0


def test_poll_fails_fast_before_protocol_even_if_heartbeat_unknown(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    service = PollService(devices, AttendanceRepository(db), SyncLogRepository(db))
    fake_adapter = MagicMock()
    fake_adapter.__enter__.side_effect = RuntimeError("should not open protocol")
    with patch("services.poll_service.create_adapter", return_value=fake_adapter), patch(
        "services.poll_service.tcp_probe", return_value=(False, 12.0, "WinError 10065")
    ):
        result = service.poll_device(device, full=True)
    assert result["success"] is False
    assert result["duration_sec"] < 1.0
    fake_adapter.__enter__.assert_not_called()


def test_poll_timeout_still_opens_protocol_session(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    service = PollService(devices, AttendanceRepository(db), SyncLogRepository(db))
    fake_adapter = MagicMock()
    fake_adapter.__enter__.side_effect = RuntimeError("TCP connect failed: timed out")
    with patch("services.poll_service.create_adapter", return_value=fake_adapter), patch(
        "services.poll_service.tcp_probe", return_value=(False, 2000.0, "timed out")
    ):
        result = service.poll_device(device, full=False)
    assert result["success"] is False
    assert "TCP connect failed" in result["error"]
    fake_adapter.__enter__.assert_called()


def test_tcp_failure_dialog_keeps_local_attendance_note():
    from services.sync_report import format_sync_dialog

    title, body, ok = format_sync_dialog(
        [
            {
                "device_name": "eSSL K90",
                "manufacturer": "essl",
                "model": "ZLM60_TFT",
                "success": False,
                "status": "FAILED",
                "fetched": 0,
                "inserted": 0,
                "error": "TCP connect failed: timed out",
                "duration_sec": 2.1,
            }
        ]
    )
    assert ok is False
    assert title == "Sync Failed"
    assert "TCP connect failed: timed out" in body
    assert "Auto-sync retries every 1 minute" in body


def test_list_today_orders_newest_punch_first(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    today = db.fetchone("SELECT date('now', 'localtime') AS d")["d"]
    older = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1",
        punch_time=f"{today}T10:00:00+05:30",
        event_type="IN",
        idempotency_key=f"order-old-{new_id()}",
        created_at=f"{today}T20:50:00",
    )
    newer = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="2",
        punch_time=f"{today}T20:45:00+05:30",
        event_type="IN",
        idempotency_key=f"order-new-{new_id()}",
        created_at=f"{today}T10:01:00",
    )
    repo.enqueue(older)
    repo.enqueue(newer)
    listed = repo.list_today()
    assert listed[0]["device_employee_code"] == "2"
    assert listed[1]["device_employee_code"] == "1"


def test_failed_upload_retains_pending(db, secrets):
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
        device_employee_code="1001",
        punch_time="2026-08-26T15:55:21+05:30",
        event_type="IN",
        idempotency_key=f"fail-{new_id()}",
        raw_payload={"punch_time": datetime(2026, 8, 26, 15, 55, 21)},
    )
    attendance.enqueue(rec)
    cloud = MagicMock()
    cloud.push_records_for_device.side_effect = CloudAPIError(
        "Attendance timestamp could not be serialized to JSON.",
        error_kind="serialization",
    )
    engine = UploadEngine(
        cloud,
        attendance,
        devices,
        SyncLogRepository(db),
        retry_attempts=3,
        max_retries=2,
    )
    result = engine.process_queue()
    assert result["uploaded"] == 0
    assert result["failed"] == 1
    assert result["serialization_error"] is True
    pending = attendance.list_pending()
    assert any(r.id == rec.id for r in pending)
    assert pending[0].status == QueueStatus.PENDING.value
    assert attendance.dead_count() == 0


def test_successful_retry_marks_synced(db, secrets):
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
        device_employee_code="1001",
        punch_time="2026-08-26T15:55:21+05:30",
        event_type="IN",
        idempotency_key=f"retry-{new_id()}",
    )
    attendance.enqueue(rec)
    cloud = MagicMock()
    cloud.push_records_for_device.side_effect = [
        CloudAPIError(
            "Attendance timestamp could not be serialized to JSON.",
            error_kind="serialization",
        ),
        {"ok": True, "accepted": 1, "duplicates": 0},
    ]
    engine = UploadEngine(
        cloud, attendance, devices, SyncLogRepository(db), retry_attempts=1
    )
    first = engine.process_queue()
    assert first["uploaded"] == 0
    assert attendance.list_pending()
    second = engine.process_queue()
    assert second["uploaded"] == 1
    assert attendance.pending_count() == 0
    row = db.fetchone("SELECT status FROM attendance_queue WHERE id=?", (rec.id,))
    assert row["status"] == QueueStatus.UPLOADED.value


def test_unknown_object_is_not_silently_stringified():
    with pytest.raises(JSONSerializationError):
        dumps({"bad": object()})


def test_space_separated_timestamp_becomes_iso_t():
    assert format_api_datetime("2026-08-26 16:06:32+05:30") == "2026-08-26T16:06:32+05:30"


def test_sync_dialog_does_not_claim_flushed_on_failure():
    from services.sync_report import format_sync_dialog

    title, body, ok = format_sync_dialog(
        [
            {
                "device_name": "eSSL K90",
                "manufacturer": "essl",
                "model": "K90",
                "success": False,
                "status": "FAILED",
                "fetched": 3,
                "inserted": 3,
                "duplicates": 0,
                "uploaded": 0,
                "pending": 3,
                "failed": 3,
                "serialization_error": True,
            }
        ]
    )
    assert ok is False
    assert title == "Sync Failed"
    assert "flushed upload queue" not in body.lower()
    assert "Pending attendance records have been retained" in body
    assert "Attendance records read: 3" in body
    assert "Pending: 3" in body


def test_cloud_client_wraps_datetime_typeerror():
    from api.cloud_client import CloudAPIError, CloudClient
    from models import ConnectorSettings

    client = CloudClient(ConnectorSettings(verify_ssl=True))
    with patch("api.cloud_client.dumps_bytes", side_effect=TypeError("Object of type datetime is not JSON serializable")):
        with pytest.raises(CloudAPIError) as exc:
            client._request("POST", "https://example.test/push", json_body={"punches": []})
    assert exc.value.error_kind == "serialization"
    assert "serialized to JSON" in str(exc.value)
    assert exc.value.transient is False


def test_today_punches_counts_stored_records(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    today = db.fetchone("SELECT date('now', 'localtime') AS d")["d"]
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="9",
        punch_time=f"{today}T10:00:00+05:30",
        event_type="IN",
        idempotency_key=f"today-{new_id()}",
    )
    repo.enqueue(rec)
    assert repo.today_count() >= 1
    listed = repo.list_today()
    assert len(listed) >= 1
    assert listed[0]["device_employee_code"] == "9"
    assert listed[0]["device_name"] == "eSSL K90"
    assert repo.pending_count() == 1
    assert repo.uploaded_today_count() == 0


def test_today_punches_keeps_morning_offset_timestamps(db, secrets):
    """IST 04:00 is still today locally; UTC conversion would make it yesterday."""
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    today = db.fetchone("SELECT date('now', 'localtime') AS d")["d"]
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1",
        punch_time=f"{today}T04:00:00+05:30",
        event_type="IN",
        idempotency_key=f"morning-{new_id()}",
    )
    repo.enqueue(rec)
    assert repo.today_count() == 1
    assert repo.list_today()[0]["punch_time"].startswith(today)


def test_list_recent_includes_non_today_device_clock_punches(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1",
        punch_time="2000-02-08T17:28:48+05:30",
        event_type="OUT",
        idempotency_key=f"hist-{new_id()}",
    )
    repo.enqueue(rec)
    # Unset-clock history must not pollute Daily / Live "today" unless the
    # remapped stamp is a small live poll batch near collection time.
    assert repo.today_count() == 0
    assert repo.list_today() == []
    assert repo.daily_attendance(db.fetchone("SELECT date('now', 'localtime') AS d")["d"]) == []
    rewritten = repo.rewrite_unset_clock_punch_times()
    assert rewritten == 1
    today = db.fetchone("SELECT date('now', 'localtime') AS d")["d"]
    stored = db.fetchone("SELECT punch_time, created_at FROM attendance_queue WHERE id = ?", (rec.id,))
    assert stored["punch_time"].startswith(today)
    # Single-row remap near created_at counts as a live punch for today.
    assert repo.today_count() == 1
    assert len(repo.list_today()) == 1
    assert len(repo.daily_attendance(today)) == 1
    # Bulk history dump in the same created_at second is excluded.
    for i in range(6):
        extra = AttendanceRecord(
            id=new_id(),
            device_id=device.id,
            device_employee_code=str(i + 2),
            punch_time="2000-02-08T17:28:48+05:30",
            event_type="OUT",
            idempotency_key=f"hist-bulk-{i}-{new_id()}",
        )
        repo.enqueue(extra)
    repo.rewrite_unset_clock_punch_times()
    # Force shared created_at so the unset-clock batch looks like a history dump.
    db.execute(
        "UPDATE attendance_queue SET created_at = ? WHERE device_id = ?",
        (f"{today}T10:00:00+00:00", device.id),
    )
    db.execute(
        "UPDATE attendance_queue SET punch_time = ? WHERE device_id = ?",
        (f"{today}T10:00:00+05:30", device.id),
    )
    assert repo.today_count() == 0
    assert repo.daily_attendance(today) == []
    recent = repo.list_recent(50)
    assert len(recent) >= 7
    assert recent[0]["device_punch_time"].startswith("2000-02-08") or any(
        str(r.get("device_punch_time") or "").startswith("2000-02-08") for r in recent
    )


def test_rewrite_fixes_time_on_date_grafted_year_2000_punches(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    # Already-modern punch_time is left alone (even if device stamp is year 2000).
    modern = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="4",
        punch_time="2026-08-31T17:57:48+05:30",
        event_type="OUT",
        idempotency_key=f"graft-modern-{new_id()}",
        raw_payload={"device_punch_time": "2000-02-08T17:57:48+05:30"},
        created_at="2026-08-31T14:45:07+00:00",
    )
    repo.enqueue(modern)
    assert repo.rewrite_unset_clock_punch_times() == 0
    # Stuck year-2000 storage is grafted onto the collection calendar day.
    stuck = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="5",
        punch_time="2000-02-08T18:37:13+05:30",
        event_type="OUT",
        idempotency_key=f"graft-stuck-{new_id()}",
        raw_payload={"device_punch_time": "2000-02-08T18:37:22+05:30"},
        created_at="2026-09-07T09:56:26+00:00",
    )
    repo.enqueue(stuck)
    rewritten = repo.rewrite_unset_clock_punch_times()
    assert rewritten == 1
    stored = db.fetchone("SELECT punch_time FROM attendance_queue WHERE id = ?", (stuck.id,))
    assert stored["punch_time"].startswith("2026-09-07")
    assert "2000" not in stored["punch_time"][:10]


def test_list_punches_includes_raw_payload_data_column(db, secrets):
    devices = DeviceRepository(db, secrets)
    device = _device()
    devices.create(device)
    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="7",
        punch_time="2000-02-08T16:04:48+05:30",
        event_type="IN",
        verification_method="fingerprint",
        idempotency_key=f"data-{new_id()}",
        raw_payload={
            "uid": 12,
            "punch": 0,
            "status": 1,
            "vendor": "essl",
            "terminal_code": "K90-1",
        },
    )
    repo.enqueue(rec)
    recent = repo.list_recent_for_device(device.id, 10)
    assert recent[0]["raw_payload"]["uid"] == 12
    assert recent[0]["raw_payload"]["vendor"] == "essl"


def test_correct_unset_device_clock_uses_collection_clock():
    kept = correct_unset_device_clock("2026-08-26T15:55:21+05:30")
    assert kept is not None
    assert kept.year == 2026
    assert kept.day == 26
    assert kept.hour == 15
    ist = timezone(timedelta(hours=5, minutes=30))
    collected = datetime(2026, 8, 29, 15, 57, 22, tzinfo=timezone.utc)
    fixed = correct_unset_device_clock(
        "2000-02-08T17:46:55+05:30",
        collected.isoformat(),
    )
    assert fixed is not None
    local_fixed = fixed.astimezone(ist)
    assert local_fixed.year == 2026
    assert local_fixed.month == 8
    assert local_fixed.day == 29
    # Without an unset-era anchor, use the collection clock (real sync time).
    assert local_fixed.hour == 21  # 15:57 UTC + 05:30
    assert local_fixed.minute == 27
    assert local_fixed.second == 22
    fallback = correct_unset_device_clock("2000-02-08T16:04:48+05:30")
    assert fallback is not None
    now = datetime.now(fallback.tzinfo)
    assert fallback.year == now.year
    assert fallback.month == now.month
    assert fallback.day == now.day
    # Falls back to "now" as collection clock — within a few seconds.
    assert abs((fallback - now).total_seconds()) < 5


def test_correct_unset_device_clock_keeps_relative_dates_across_days():
    collected = datetime(2026, 9, 7, 12, 25, 38)
    newer = datetime(2002, 9, 18, 16, 1, 15)
    older = datetime(2000, 2, 8, 17, 57, 22)
    newer_fixed = correct_unset_device_clock(
        newer, collected, default_timezone="Asia/Kolkata", device_now=newer
    )
    older_fixed = correct_unset_device_clock(
        older, collected, default_timezone="Asia/Kolkata", device_now=newer
    )
    assert newer_fixed is not None and older_fixed is not None
    assert newer_fixed.date() == collected.date()
    assert older_fixed.date() < newer_fixed.date()
    assert (newer_fixed - older_fixed).days == (newer - older).days


def test_correct_unset_device_clock_keeps_relative_offset_with_device_now():
    collected = datetime(2026, 8, 31, 20, 15, 7)
    device_now = datetime(2000, 2, 8, 17, 57, 50)
    punch = datetime(2000, 2, 8, 17, 57, 22)
    fixed = correct_unset_device_clock(
        punch,
        collected,
        default_timezone="Asia/Kolkata",
        device_now=device_now,
    )
    assert fixed is not None
    ist = timezone(timedelta(hours=5, minutes=30))
    local = fixed.astimezone(ist)
    assert local.year == 2026
    assert local.month == 8
    assert local.day == 31
    assert local.hour == 20
    assert local.minute == 14
    assert local.second == 39


def test_correct_unset_device_clock_grafts_when_rtc_already_modern():
    """After RTC sync, year-2000 log stamps must use the collection clock."""
    collected = datetime(2026, 9, 7, 15, 26, 51)
    modern_rtc = datetime(2026, 9, 7, 15, 26, 50)
    punch = datetime(2000, 2, 8, 18, 37, 12)
    fixed = correct_unset_device_clock(
        punch,
        collected,
        default_timezone="Asia/Kolkata",
        device_now=modern_rtc,
    )
    assert fixed is not None
    assert fixed.year == 2026
    assert fixed.month == 9
    assert fixed.day == 7
    assert fixed.hour == 15
    assert fixed.minute == 26
    assert fixed.second == 51


def test_correct_unset_device_clock_does_not_shift_modern_punch_with_unset_anchor():
    collected = datetime(2026, 9, 7, 15, 26, 51)
    modern_punch = datetime(2026, 9, 7, 10, 15, 0)
    unset_anchor = datetime(2000, 2, 8, 18, 0, 0)
    fixed = correct_unset_device_clock(
        modern_punch,
        collected,
        default_timezone="Asia/Kolkata",
        device_now=unset_anchor,
    )
    assert fixed is not None
    assert fixed.year == 2026
    assert fixed.month == 9
    assert fixed.day == 7
    assert fixed.hour == 10
    assert fixed.minute == 15


def test_punch_display_helpers_show_full_time_and_data():
    from ui.table_utils import (
        comm_key_display,
        format_clock_timestamp_display,
        format_datetime_display,
        format_punch_data_display,
        format_punch_time_display,
    )

    assert comm_key_display("") == "0"
    assert comm_key_display("0") == "0"
    assert comm_key_display("1234") == "1234"
    collected = datetime(2026, 8, 29, 15, 57, 22, tzinfo=timezone.utc)
    raw = format_punch_time_display("2000-02-08T16:04:48+05:30")
    assert "2000" not in raw
    assert raw.startswith(datetime.now().strftime("%d %b %Y"))
    corrected = format_punch_time_display(
        "2000-02-08T16:04:48+05:30",
        collected.isoformat(),
    )
    assert corrected.startswith("29 Aug 2026")
    # Collection clock (15:57 UTC → 21:27 IST), not the year-2000 time-of-day.
    assert corrected.endswith("21:27:22")
    last_sync = format_datetime_display("2026-08-29T15:57:22.119836+00:00")
    assert last_sync.startswith("29 Aug 2026")
    assert last_sync.endswith(("15:57:22", "21:27:22")) or ", " in last_sync
    assert not last_sync.startswith(":")
    raw_clock = format_clock_timestamp_display("2000-02-08T16:04:48+05:30")
    assert "2000" in raw_clock
    assert "08 Feb 2000" in raw_clock
    data = format_punch_data_display(
        {"uid": 12, "punch": 0, "vendor": "essl", "terminal_code": "K90-1"}
    )
    assert "uid=12" in data
    assert "vendor=essl" in data
    assert "terminal_code=K90-1" in data
