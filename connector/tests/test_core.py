"""Unit tests for connector foundations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.registry import create_adapter, list_adapters
from models import Device, new_id
from security.encryption import SecretBox, derive_machine_key
from storage.database import Database
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.device_repo import DeviceRepository
from models import AttendanceRecord, QueueStatus


@pytest.fixture()
def secrets():
    return SecretBox(derive_machine_key(app_id="test-connector"))


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    yield database
    database.close()


def test_secret_roundtrip(secrets):
    token = secrets.encrypt_str("comm-key-123")
    assert secrets.decrypt_str(token) == "comm-key-123"
    blob = secrets.encrypt_json({"api_key": "secret", "org": "abc"})
    assert secrets.decrypt_json(blob)["api_key"] == "secret"


def test_device_password_encrypted(db, secrets):
    repo = DeviceRepository(db, secrets)
    device = Device(
        id=new_id(),
        name="Gate-1",
        manufacturer="essl",
        model="K30",
        ip_address="192.168.1.50",
        port=4370,
        password="9999",
        branch="HQ",
        terminal_code="GATE1",
    )
    repo.create(device)
    loaded = repo.get(device.id)
    assert loaded is not None
    assert loaded.password == "9999"
    row = db.fetchone("SELECT password_enc FROM devices WHERE id=?", (device.id,))
    assert row["password_enc"] != "9999"
    assert "9999" not in row["password_enc"]


def test_attendance_dedup(db):
    repo = AttendanceRepository(db)
    # Need a device for FK
    db.execute(
        """
        INSERT INTO devices (
            id, name, manufacturer, model, ip_address, port, password_enc,
            branch, timezone, communication_type, polling_interval, status,
            terminal_code, serial_number, last_sync_at, last_sync_cursor,
            last_error, enabled, created_at, updated_at, extra_json
        ) VALUES (
            'd1', 'D', 'essl', '', '10.0.0.1', 4370, '',
            '', 'Asia/Kolkata', 'tcp', 60, 'unknown',
            'T1', '', NULL, '', '', 1, '2024-01-01', '2024-01-01', '{}'
        )
        """
    )
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1001",
        punch_time="2024-06-01 09:00:00",
        event_type="IN",
        idempotency_key="essl:d1:1001:2024-06-01 09:00:00:IN",
        status=QueueStatus.PENDING.value,
    )
    ok1, _ = repo.enqueue(rec)
    ok2, _ = repo.enqueue(rec)
    assert ok1 is True
    assert ok2 is False
    assert repo.pending_count() == 1


def test_enqueue_many_ignores_duplicates(db):
    repo = AttendanceRepository(db)
    db.execute(
        """
        INSERT INTO devices (
            id, name, manufacturer, model, ip_address, port, password_enc,
            branch, timezone, communication_type, polling_interval, status,
            terminal_code, serial_number, last_sync_at, last_sync_cursor,
            last_error, enabled, created_at, updated_at, extra_json
        ) VALUES (
            'd-many', 'D', 'essl', '', '10.0.0.1', 4370, '',
            '', 'Asia/Kolkata', 'tcp', 30, 'unknown',
            'T-MANY', '', NULL, '', '', 1, '2024-01-01', '2024-01-01', '{}'
        )
        """
    )
    records = [
        AttendanceRecord(
            id=new_id(),
            device_id="d-many",
            device_employee_code=str(i),
            punch_time=f"2026-08-31 09:00:{i:02d}",
            event_type="IN",
            idempotency_key=f"bulk-{i}",
            status=QueueStatus.PENDING.value,
        )
        for i in range(5)
    ]
    records.append(records[0])
    inserted = repo.enqueue_many(records)
    assert inserted == 5
    assert repo.pending_count() == 5
    assert repo.enqueue_many(records) == 0


def test_adapter_registry():
    codes = {a["manufacturer"] for a in list_adapters()}
    assert "essl" in codes
    assert "zkteco" in codes
    assert "anviz" in codes
    assert "matrix" in codes
    device = Device(
        id="x",
        name="Z",
        manufacturer="zkteco",
        model="",
        ip_address="127.0.0.1",
        port=4370,
        password="0",
        branch="",
    )
    adapter = create_adapter(device)
    assert adapter.manufacturer == "zkteco"

    from adapters.registry import UnsupportedManufacturerError

    try:
        create_adapter(
            Device(
                id="a",
                name="A",
                manufacturer="anviz",
                model="",
                ip_address="127.0.0.1",
                port=5010,
                password="0",
                branch="",
            )
        )
        assert False, "scaffold adapters must not be creatable"
    except UnsupportedManufacturerError as exc:
        assert "scaffold" in str(exc).lower() or "production-ready" in str(exc).lower()


def test_payroll_punch_contract():
    from contracts.payroll_punch import (
        PUNCH_CONTRACT_VERSION,
        build_payroll_punch,
        normalize_event_type,
        normalize_verification_method,
    )
    from models import AttendanceRecord

    assert normalize_event_type(None, status=1) == "UNKNOWN"
    assert normalize_verification_method(None, punch=1) == "fingerprint"
    rec = AttendanceRecord(
        id=new_id(),
        device_id="d1",
        device_employee_code="1001",
        punch_time="2026-07-28 09:00:00",
        event_type="IN",
        verification_method="fingerprint",
        idempotency_key="k1",
        raw_payload={"vendor": "zkteco"},
    )
    punch = build_payroll_punch(rec, terminal_code="GATE1", manufacturer="zkteco")
    assert punch["contract_version"] == PUNCH_CONTRACT_VERSION
    assert punch["payroll_ready"] is True
    assert punch["terminal_code"] == "GATE1"
    assert punch["device_employee_code"] == "1001"


def test_settings_encrypted(tmp_path, secrets):
    from config.settings_manager import SettingsManager

    path = tmp_path / "cfg.enc"
    mgr = SettingsManager(path, secrets)
    mgr.update(organisation_id="org-1", api_key="super-secret-key")
    raw = path.read_text(encoding="utf-8")
    assert "super-secret-key" not in raw
    loaded = mgr.load()
    assert loaded.api_key == "super-secret-key"
    assert loaded.organisation_id == "org-1"


def test_phase2_device_hierarchy(db, secrets):
    repo = DeviceRepository(db, secrets)
    device = Device(
        id=new_id(),
        name="Gate-A",
        manufacturer="essl",
        model="K30",
        ip_address="192.168.1.10",
        port=4370,
        password="0",
        organization="Acme",
        branch="HQ",
        department="Security",
        building="A",
        floor="1",
        gate="Main",
    )
    repo.create(device)
    loaded = repo.get(device.id)
    assert loaded is not None
    assert loaded.organization == "Acme"
    assert loaded.gate == "Main"
    assert "HQ" in loaded.location_label()


def test_ip_range_parser():
    from services.network_scanner import parse_ip_range

    ips = parse_ip_range("192.168.1.1-3")
    assert ips == ["192.168.1.1", "192.168.1.2", "192.168.1.3"]
    single = parse_ip_range("10.0.0.5")
    assert single == ["10.0.0.5"]


def test_default_discovery_range_follows_local_nic(monkeypatch):
    from services.network_scanner import (
        default_discovery_range,
        host_on_local_subnets,
        identities_match,
        local_subnet_specs,
    )
    from models import Device, DiscoveredDevice, new_id

    monkeypatch.setattr(
        "services.network_scanner.local_ipv4_nics",
        lambda: [("192.168.100.254", 24)],
    )
    assert local_subnet_specs() == ["192.168.100.1-254"]
    assert default_discovery_range() == "192.168.100.1-254"
    assert host_on_local_subnets("192.168.100.201") is True
    assert host_on_local_subnets("10.0.0.8") is False

    device = Device(
        id=new_id(),
        name="K90",
        manufacturer="essl",
        model="K90",
        ip_address="192.168.100.201",
        port=4370,
        password="0",
        branch="main",
        serial_number="SN-K90",
        mac_address="AA:BB:CC:DD:EE:FF",
    )
    found = DiscoveredDevice(
        ip_address="192.168.100.77",
        port=4370,
        serial_number="sn-k90",
        mac_address="aa-bb-cc-dd-ee-ff",
    )
    assert identities_match(device, found) is True
    found.serial_number = "OTHER"
    found.mac_address = "00:11:22:33:44:55"
    assert identities_match(device, found) is False


def test_command_signature_and_replay(db):
    from models import RemoteCommand
    from security.command_security import CommandSecurityGate, sign_command
    from storage.repositories.command_repo import NonceRepository
    from storage.repositories.enterprise_repo import AuditRepository

    secret = "test-signing-secret"
    nonces = NonceRepository(db)
    audit = AuditRepository(db)
    gate = CommandSecurityGate(nonces, audit, signing_secret=secret)

    cmd = RemoteCommand(
        id=new_id(),
        cloud_command_id="cmd-1",
        command_type="test_connection",
        device_id="d1",
        nonce="nonce-abc",
        issued_at="2099-01-01T00:00:00+00:00",
        expires_at="2099-01-02T00:00:00+00:00",
    )
    cmd.signature = sign_command(cmd, secret)
    ok, reason = gate.validate(cmd)
    assert ok is True, reason

    replay = RemoteCommand(
        id=new_id(),
        cloud_command_id="cmd-2",
        command_type="test_connection",
        device_id="d1",
        nonce="nonce-abc",
        issued_at="2099-01-01T00:00:00+00:00",
        expires_at="2099-01-02T00:00:00+00:00",
    )
    replay.signature = sign_command(replay, secret)
    ok2, reason2 = gate.validate(replay)
    assert ok2 is False
    assert "Replay" in reason2


def test_command_fail_closed_without_secret(db):
    from models import RemoteCommand
    from security.command_security import CommandSecurityGate
    from storage.repositories.command_repo import NonceRepository
    from storage.repositories.enterprise_repo import AuditRepository

    gate = CommandSecurityGate(NonceRepository(db), AuditRepository(db), signing_secret="")
    cmd = RemoteCommand(
        id=new_id(),
        cloud_command_id="cmd-x",
        command_type="test_connection",
        device_id="d1",
        nonce="nonce-no-secret",
        signature="anything",
    )
    ok, reason = gate.validate(cmd)
    assert ok is False
    assert "secret" in reason.lower()


def test_firmware_flash_feature_flag_blocks():
    from models import RemoteCommand
    from services.phase3_command_dispatcher import Phase3CommandDispatcher

    dispatcher = Phase3CommandDispatcher(
        employee_sync=None,
        templates=None,
        users=None,
        backup=None,
        provisioning=None,
        firmware=None,
        inventory=None,
        reporting=None,
        remote_support=None,
        devices=None,
        enable_firmware_flash=False,
    )
    with pytest.raises(PermissionError):
        dispatcher.dispatch(
            RemoteCommand(id=new_id(), command_type="firmware_upgrade", device_id="d1"),
            None,
        )


def test_production_guard_blocks_insecure_prod_settings():
    from models import ConnectorSettings
    from security.production_guard import validate_connector_settings

    # Unconfigured: HTTPS/TLS still hard-fail; secrets warn until cloud is set
    bare = ConnectorSettings(
        command_signing_secret="",
        verify_ssl=False,
        cloud_base_url="http://insecure.example",
    )
    bare_result = validate_connector_settings(bare, force_production=True)
    assert bare_result.ok is False
    assert any("verify_ssl" in e for e in bare_result.errors)
    assert any("HTTPS" in e for e in bare_result.errors)

    # Configured customer site: signing secret + master key become blocking
    settings = ConnectorSettings(
        organisation_id="org-1",
        api_key="token-1",
        command_signing_secret="",
        verify_ssl=True,
        cloud_base_url="https://secure.example",
    )
    result = validate_connector_settings(settings, force_production=True)
    assert result.ok is False
    assert any("command_signing_secret" in e for e in result.errors)


def test_attendance_dead_letter_and_stale_uploading(db, secrets):
    from models import AttendanceRecord, Device, QueueStatus, new_id, utcnow
    from storage.repositories.attendance_repo import AttendanceRepository
    from storage.repositories.device_repo import DeviceRepository

    devices = DeviceRepository(db, secrets)
    device = Device(
        id=new_id(),
        name="Gate",
        manufacturer="zkteco",
        model="",
        ip_address="127.0.0.1",
        port=4370,
        password="0",
        branch="",
        terminal_code="T1",
    )
    devices.create(device)

    repo = AttendanceRepository(db)
    rec = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1001",
        punch_time=utcnow().isoformat(),
        event_type="IN",
        verification_method="fingerprint",
        idempotency_key=f"dead-{new_id()}",
        status=QueueStatus.FAILED.value,
        retry_count=99,
        last_error="boom",
    )
    ok, _ = repo.enqueue(rec)
    assert ok is True
    repo.mark_dead([rec.id], "exhausted")
    assert repo.dead_count() == 1
    assert repo.pending_count() == 0

    stuck = AttendanceRecord(
        id=new_id(),
        device_id=device.id,
        device_employee_code="1002",
        punch_time=utcnow().isoformat(),
        event_type="IN",
        verification_method="fingerprint",
        idempotency_key=f"stuck-{new_id()}",
        status=QueueStatus.PENDING.value,
    )
    repo.enqueue(stuck)
    repo.mark_uploading([stuck.id])
    db.execute(
        "UPDATE attendance_queue SET uploading_at = datetime('now', '-2 hours') WHERE id = ?",
        (stuck.id,),
    )
    recovered = repo.reset_stale_uploading(60)
    assert recovered >= 1
    pending = repo.list_pending(limit=10)
    assert any(r.id == stuck.id for r in pending)


def test_config_validation_rejects_bad_url():
    from config.validation import validate_settings_dict

    bad = validate_settings_dict(
        {
            "organisation_id": "org",
            "api_key": "key",
            "cloud_base_url": "not-a-url",
        },
        require_cloud=True,
    )
    assert bad.ok is False
    assert any("https://" in e.lower() or "Cloud Base URL" in e for e in bad.errors)

    good = validate_settings_dict(
        {
            "organisation_id": "org",
            "api_key": "key",
            "cloud_base_url": "https://brixigo.com",
        },
        require_cloud=True,
    )
    assert good.ok is True

    comm_key_as_api = validate_settings_dict(
        {
            "organisation_id": "org",
            "api_key": "0",
            "cloud_base_url": "https://brixigo.com",
        },
        require_cloud=True,
    )
    assert comm_key_as_api.ok is False
    assert any("comm key" in e.lower() for e in comm_key_as_api.errors)

    named = validate_settings_dict(
        {
            "organisation_id": "Brixigo Demo Organisation",
            "api_key": "bxk_test",
            "connector_id": "Branch Connector",
            "cloud_base_url": "http://127.0.0.1:8080",
        },
        require_cloud=True,
    )
    assert named.ok is True


def test_discovery_ignore_and_alerts(db):
    from models import Alert, AlertSeverity, AlertType
    from storage.repositories.enterprise_repo import AlertRepository, DiscoveryRepository

    discovery = DiscoveryRepository(db)
    discovery.ignore("192.168.1.50", 4370, reason="lab device")
    assert discovery.is_ignored("192.168.1.50", 4370) is True

    alerts = AlertRepository(db)
    alerts.add(
        Alert(
            id=new_id(),
            alert_type=AlertType.DEVICE_OFFLINE.value,
            severity=AlertSeverity.CRITICAL.value,
            title="Offline",
            message="Device down",
            device_id="d1",
        )
    )
    recent = alerts.recent(10)
    assert len(recent) == 1
    assert alerts.has_recent(AlertType.DEVICE_OFFLINE.value, device_id="d1", within_seconds=600)


def test_schema_phase2_tables(db):
    tables = {
        r["name"]
        for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for required in (
        "device_health",
        "remote_commands",
        "command_nonces",
        "alerts",
        "audit_logs",
        "discovery_cache",
        "discovery_ignore",
        "heartbeat_history",
        "upload_metrics",
    ):
        assert required in tables
    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(devices)")}
    assert "organization" in cols
    assert "gate" in cols
    assert "health_status" in cols


def test_phase3_schema_and_employee_repo(db, secrets):
    from models.phase3 import Employee
    from storage.repositories.phase3_repo import EmployeeRepository, TemplateRepository

    tables = {
        r["name"]
        for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for required in (
        "employees",
        "biometric_templates",
        "device_users",
        "device_backups",
        "firmware_jobs",
        "access_levels",
        "door_controllers",
        "notifications",
        "report_jobs",
        "time_sync_logs",
    ):
        assert required in tables

    employees = EmployeeRepository(db, secrets)
    emp = employees.upsert(
        Employee(
            id=new_id(),
            employee_code="E100",
            name="Ada Lovelace",
            branch="HQ",
            organization="Acme",
            pin="1234",
            card_number="7788",
        )
    )
    loaded = employees.get_by_code("E100")
    assert loaded is not None
    assert loaded.pin == "1234"
    assert loaded.name == "Ada Lovelace"

    templates = TemplateRepository(db)
    from models.phase3 import BiometricTemplate
    from security.encryption import SecretBox

    # Store encrypted hex via SecretBox in service path — repository stores enc string
    box = secrets
    raw = b"\x01\x02\x03\x04"
    tpl = templates.upsert(
        BiometricTemplate(
            id=new_id(),
            employee_id=emp.id,
            employee_code=emp.employee_code,
            template_type="fingerprint",
            template_enc=box.encrypt_str(raw.hex()),
            checksum="abc",
            size=len(raw),
            vendor="zkteco",
        )
    )
    assert templates.list_for_employee(emp.id)[0].id == tpl.id


def test_phase3_command_dispatcher_report(db, secrets, tmp_path):
    from api.cloud_client import CloudClient
    from models import ConnectorSettings, RemoteCommand
    from services.phase3_command_dispatcher import Phase3CommandDispatcher
    from services.phase3_platform import ReportingService
    from storage.repositories.phase3_repo import ReportRepository

    reports = ReportRepository(db)
    reporting = ReportingService(db, reports, tmp_path / "reports", CloudClient(ConnectorSettings()))
    dispatcher = Phase3CommandDispatcher(
        employee_sync=None,
        templates=None,
        users=None,
        backup=None,
        provisioning=None,
        firmware=None,
        inventory=None,
        reporting=reporting,
        remote_support=None,
        devices=None,
    )
    result = dispatcher.dispatch(
        RemoteCommand(
            id=new_id(),
            command_type="generate_report",
            payload={"report_type": "performance"},
        ),
        None,
    )
    assert result is not None
    assert result["status"] == "succeeded"
