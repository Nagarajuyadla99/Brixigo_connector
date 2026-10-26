"""IN/OUT classification: device-reported vs system inference."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.protocol.zk_attendance_fields import (
    interpret_zk_log_fields,
    zk_logs_report_direction,
)
from contracts.payroll_punch import normalize_event_type, validate_payroll_punch
from models import AttendanceLog, AttendanceRecord, Device, QueueStatus, new_id
from services.punch_classifier import (
    CLASSIFICATION_DEVICE,
    CLASSIFICATION_INFERENCE,
    apply_classification,
    classify_punch_groups,
)
from services.punch_normalizer import event_display, event_label
from storage.database import Database
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.device_repo import DeviceRepository


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    yield database
    database.close()


@pytest.fixture()
def secrets():
    from security.encryption import SecretBox, derive_machine_key

    return SecretBox(derive_machine_key(app_id="test-classifier"))


def _ensure_device(db: Database, device_id: str, *, name: str = "", branch: str = "") -> None:
    db.execute(
        """
        INSERT INTO devices (
            id, name, manufacturer, model, ip_address, port, password_enc,
            branch, timezone, communication_type, polling_interval, status,
            terminal_code, serial_number, last_sync_at, last_sync_cursor,
            last_error, enabled, created_at, updated_at, extra_json
        ) VALUES (
            ?, ?, 'essl', 'K90', '192.168.100.201', 4370, '',
            ?, 'Asia/Kolkata', 'tcp', 30, 'unknown',
            ?, '', NULL, '', '', 1, '2026-09-01', '2026-09-01', '{}'
        )
        """,
        (device_id, name or device_id, branch, device_id),
    )


def _punch(
    employee: str,
    when: str,
    *,
    ident: str = "",
    org: str = "",
    direction: str | None = None,
    reported: bool = False,
    **extra,
) -> dict:
    item = {
        "id": ident or f"{org}:{employee}:{when}",
        "device_employee_code": employee,
        "punch_time": when,
        "organisation_id": org,
        "device_direction": direction,
        "direction_reported": reported,
        "raw_payload": extra.pop("raw_payload", {}),
    }
    item.update(extra)
    return item


def _events(rows: list[tuple[dict, object]]) -> list[str]:
    return [classified.event_type for _item, classified in rows]


def _methods(rows: list[tuple[dict, object]]) -> list[str]:
    return [classified.classification_method for _item, classified in rows]


def test_status_byte_is_not_checkout():
    assert normalize_event_type(None, status=1) == "UNKNOWN"
    assert normalize_event_type("", status=1) == "UNKNOWN"
    ok, _reason = validate_payroll_punch(
        {
            "device_employee_code": "5",
            "punch_time": "2026-09-08T09:00:00",
            "event_type": "UNKNOWN",
        }
    )
    assert ok is True


def test_k90_status_1_workcode_punch_is_fingerprint_not_out():
    fields = interpret_zk_log_fields(1, 89)
    assert fields["verification_method"] == "fingerprint"
    assert fields["device_direction"] is None
    assert fields["direction_reported"] is False
    assert fields["device_status"] == 1
    assert fields["device_punch"] == 89
    rows = [
        {"device_status": 1, "device_punch": 89},
        {"device_status": 1, "device_punch": 145},
        {"device_status": 2, "device_punch": 238},
    ]
    assert zk_logs_report_direction(rows) is False


def test_zk_punch_states_0_and_1_mean_device_reports_direction():
    rows = [
        {"device_status": 1, "device_punch": 0},
        {"device_status": 1, "device_punch": 1},
    ]
    assert zk_logs_report_direction(rows) is True
    assert interpret_zk_log_fields(1, 0)["device_direction"] == "IN"
    assert interpret_zk_log_fields(1, 1)["device_direction"] == "OUT"
    assert interpret_zk_log_fields(1, 0)["verification_method"] == "fingerprint"


def test_first_four_punches_alternate_in_out():
    punches = [
        _punch("5", "2026-09-08T09:00:00", ident="1"),
        _punch("5", "2026-09-08T13:00:00", ident="2"),
        _punch("5", "2026-09-08T14:00:00", ident="3"),
        _punch("5", "2026-09-08T18:00:00", ident="4"),
    ]
    rows = classify_punch_groups(punches, device_reports_direction=False)
    assert _events(rows) == ["IN", "OUT", "IN", "OUT"]
    assert set(_methods(rows)) == {CLASSIFICATION_INFERENCE}


def test_lunch_cycle_and_open_day_without_out():
    lunch = classify_punch_groups(
        [
            _punch("5", "2026-09-08T09:00:00", ident="in"),
            _punch("5", "2026-09-08T13:05:00", ident="lunch-out"),
            _punch("5", "2026-09-08T13:55:00", ident="lunch-in"),
            _punch("5", "2026-09-08T18:10:00", ident="out"),
        ],
        device_reports_direction=False,
    )
    assert _events(lunch) == ["IN", "OUT", "IN", "OUT"]

    open_day = classify_punch_groups(
        [_punch("5", "2026-09-08T09:12:00", ident="only")],
        device_reports_direction=False,
    )
    assert _events(open_day) == ["IN"]
    assert open_day[0][1].classification_reason_code == "first_or_after_out"


def test_duplicate_window_does_not_toggle():
    rows = classify_punch_groups(
        [
            _punch("5", "2026-09-08T09:00:00", ident="a"),
            _punch("5", "2026-09-08T09:00:45", ident="b"),
            _punch("5", "2026-09-08T09:05:00", ident="c"),
        ],
        device_reports_direction=False,
    )
    assert _events(rows) == ["IN", "IN", "OUT"]
    assert rows[1][1].classification_reason_code == "duplicate_window"


def test_employees_classified_independently():
    rows = classify_punch_groups(
        [
            _punch("5", "2026-09-08T09:00:00", ident="sai-1"),
            _punch("1", "2026-09-08T09:00:10", ident="shiva-1"),
            _punch("5", "2026-09-08T18:00:00", ident="sai-2"),
            _punch("1", "2026-09-08T18:01:00", ident="shiva-2"),
        ],
        device_reports_direction=False,
    )
    by_id = {item["id"]: classified.event_type for item, classified in rows}
    assert by_id["sai-1"] == "IN"
    assert by_id["shiva-1"] == "IN"
    assert by_id["sai-2"] == "OUT"
    assert by_id["shiva-2"] == "OUT"


def test_tenants_classified_independently():
    rows = classify_punch_groups(
        [
            _punch("5", "2026-09-08T09:00:00", ident="a1", org="org-a"),
            _punch("5", "2026-09-08T09:00:00", ident="b1", org="org-b"),
            _punch("5", "2026-09-08T18:00:00", ident="a2", org="org-a"),
        ],
        device_reports_direction=False,
    )
    by_id = {item["id"]: classified.event_type for item, classified in rows}
    assert by_id["a1"] == "IN"
    assert by_id["b1"] == "IN"
    assert by_id["a2"] == "OUT"


def test_calendar_midnight_does_not_close_previous_day():
    rows = classify_punch_groups(
        [
            _punch("5", "2026-09-07T23:50:00", ident="late"),
            _punch("5", "2026-09-08T00:10:00", ident="early"),
        ],
        device_reports_direction=False,
    )
    by_id = {item["id"]: classified.event_type for item, classified in rows}
    assert by_id["late"] == "IN"
    assert by_id["early"] == "IN"


def test_device_reported_direction_is_preserved():
    rows = classify_punch_groups(
        [
            _punch(
                "5",
                "2026-09-08T09:00:00",
                ident="out-first",
                direction="OUT",
                reported=True,
            ),
            _punch(
                "5",
                "2026-09-08T18:00:00",
                ident="in-second",
                direction="IN",
                reported=True,
            ),
        ],
        device_reports_direction=True,
    )
    assert _events(rows) == ["OUT", "IN"]
    assert _methods(rows) == [CLASSIFICATION_DEVICE, CLASSIFICATION_DEVICE]


def test_directionless_device_uses_inference_even_with_candidate_bytes():
    rows = classify_punch_groups(
        [
            _punch(
                "5",
                "2026-09-08T09:00:00",
                ident="1",
                direction="OUT",
                reported=False,
                raw_payload={"device_status": 1, "device_punch": 89},
            ),
            _punch(
                "5",
                "2026-09-08T18:00:00",
                ident="2",
                direction="OUT",
                reported=False,
                raw_payload={"device_status": 1, "device_punch": 145},
            ),
        ],
        device_reports_direction=False,
    )
    assert _events(rows) == ["IN", "OUT"]
    assert _methods(rows) == [CLASSIFICATION_INFERENCE, CLASSIFICATION_INFERENCE]


def test_event_display_labels():
    assert event_label("UNKNOWN") == "Unclassified"
    assert event_display("IN", CLASSIFICATION_INFERENCE) == "IN · inferred"
    assert event_display("OUT", CLASSIFICATION_DEVICE) == "OUT · device"


def _record(
    *,
    device_id: str,
    employee: str,
    punch_time: str,
    event_type: str = "OUT",
    status: int = 1,
    punch: int = 89,
    idem: str = "",
    upload_status: str = QueueStatus.PENDING.value,
) -> AttendanceRecord:
    return AttendanceRecord(
        id=new_id(),
        device_id=device_id,
        device_employee_code=employee,
        punch_time=punch_time,
        event_type=event_type,
        verification_method="not_reported",
        idempotency_key=idem or f"{device_id}:{employee}:{punch_time}:{event_type}",
        status=upload_status,
        raw_payload={
            "vendor": "essl",
            "status": status,
            "punch": punch,
            "device_punch_time": punch_time,
        },
    )


def test_reclassify_heals_k90_all_out_history(db):
    _ensure_device(db, "infoessl9999", name="infoessl9999", branch="info9999")
    repo = AttendanceRepository(db)
    sai = [
        _record(device_id="infoessl9999", employee="5", punch_time="2026-09-08T09:01:00", punch=89),
        _record(device_id="infoessl9999", employee="5", punch_time="2026-09-08T13:10:00", punch=145),
        _record(device_id="infoessl9999", employee="5", punch_time="2026-09-08T14:02:00", punch=238),
        _record(device_id="infoessl9999", employee="5", punch_time="2026-09-08T18:40:00", punch=255),
    ]
    shiva = [
        _record(device_id="infoessl9999", employee="1", punch_time="2026-09-08T09:05:00", punch=89),
        _record(device_id="infoessl9999", employee="1", punch_time="2026-09-08T18:11:00", punch=145),
    ]
    assert repo.enqueue_many(sai + shiva) == 6
    updated = repo.reclassify_attendance_events(device_id="infoessl9999")
    assert updated == 6
    items = {
        (row["device_employee_code"], row["punch_time"][:19]): row
        for row in repo.list_recent_for_device("infoessl9999", 20)
    }
    assert items[("5", "2026-09-08T09:01:00")]["event_type"] == "IN"
    assert items[("5", "2026-09-08T13:10:00")]["event_type"] == "OUT"
    assert items[("5", "2026-09-08T14:02:00")]["event_type"] == "IN"
    assert items[("5", "2026-09-08T18:40:00")]["event_type"] == "OUT"
    assert items[("1", "2026-09-08T09:05:00")]["event_type"] == "IN"
    assert items[("1", "2026-09-08T18:11:00")]["event_type"] == "OUT"
    sai_in = items[("5", "2026-09-08T09:01:00")]
    assert sai_in["verification_method"] == "fingerprint"
    assert sai_in["classification_method"] == CLASSIFICATION_INFERENCE
    assert sai_in["event_display"] == "IN · inferred"
    assert sai_in["verification_method_label"] == "Fingerprint"


def test_identity_skips_reclassified_event_on_second_sync(db):
    _ensure_device(db, "d1")
    repo = AttendanceRepository(db)
    first = _record(
        device_id="d1",
        employee="5",
        punch_time="2026-09-08T09:00:00",
        event_type="OUT",
        idem="essl:d1:5:2026-09-08T09:00:00:OUT",
    )
    second = _record(
        device_id="d1",
        employee="5",
        punch_time="2026-09-08T09:00:00",
        event_type="IN",
        idem="essl:d1:5:2026-09-08T09:00:00:IN",
    )
    assert repo.enqueue_many([first]) == 1
    assert repo.enqueue_many([second]) == 0
    assert repo.pending_count() == 1
    repo.reclassify_attendance_events("d1")
    stored = repo.list_pending()[0]
    assert stored.event_type == "IN"
    assert repo.enqueue_many([second]) == 0
    assert repo.pending_count() == 1


def test_poll_k90_logs_twice_is_idempotent(db, secrets):
    from services.poll_service import PollService
    from storage.repositories.sync_repo import SyncLogRepository

    devices = DeviceRepository(db, secrets)
    device = Device(
        id="infoessl9999",
        name="infoessl9999",
        manufacturer="essl",
        model="K90",
        ip_address="192.168.100.201",
        port=4370,
        password="0",
        branch="info9999",
        terminal_code="infoessl9999",
        timezone="Asia/Kolkata",
    )
    devices.create(device)
    attendance = AttendanceRepository(db)
    service = PollService(devices, attendance, SyncLogRepository(db))
    logs = [
        AttendanceLog(
            user_id="5",
            punch_time=datetime(2026, 9, 8, 9, 1, 0),
            status=1,
            punch=89,
            uid=1,
            raw={"vendor": "essl", "status": 1, "punch": 89, "user_id": "5"},
        ),
        AttendanceLog(
            user_id="5",
            punch_time=datetime(2026, 9, 8, 18, 40, 0),
            status=1,
            punch=145,
            uid=2,
            raw={"vendor": "essl", "status": 1, "punch": 145, "user_id": "5"},
        ),
        AttendanceLog(
            user_id="1",
            punch_time=datetime(2026, 9, 8, 9, 5, 0),
            status=1,
            punch=238,
            uid=3,
            raw={"vendor": "essl", "status": 1, "punch": 238, "user_id": "1"},
        ),
    ]
    fetched, inserted, *_ = service._enqueue_logs(device, logs)
    assert fetched == 3
    assert inserted == 3
    fetched2, inserted2, *_ = service._enqueue_logs(device, logs)
    assert fetched2 == 3
    assert inserted2 == 0
    rows = attendance.list_recent_for_device(device.id, 10)
    assert len(rows) == 3
    by_emp_time = {
        (row["device_employee_code"], row["punch_time"][:19]): row for row in rows
    }
    assert by_emp_time[("5", "2026-09-08T09:01:00")]["event_type"] == "IN"
    assert by_emp_time[("5", "2026-09-08T18:40:00")]["event_type"] == "OUT"
    assert by_emp_time[("1", "2026-09-08T09:05:00")]["event_type"] == "IN"
    first = by_emp_time[("5", "2026-09-08T09:01:00")]
    assert first["verification_method"] == "fingerprint"
    assert first["classification_method"] == CLASSIFICATION_INFERENCE
    assert first["raw_payload"]["device_status"] == 1
    assert first["raw_payload"]["device_punch"] == 89


def test_apply_classification_is_non_destructive():
    item = _punch("5", "2026-09-08T09:00:00", ident="x", raw_payload={"status": 1, "punch": 89})
    classified = classify_punch_groups([item], device_reports_direction=False)[0][1]
    apply_classification(item, classified)
    assert item["raw_payload"]["status"] == 1
    assert item["raw_payload"]["punch"] == 89
    assert item["raw_payload"]["classification_method"] == CLASSIFICATION_INFERENCE
    assert item["event_type"] == "IN"
