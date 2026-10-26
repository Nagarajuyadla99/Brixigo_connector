"""Domain models for the connector agent (Phase 1 + 2 + Enterprise Phase 3)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Manufacturer(str, Enum):
    ESSL = "essl"
    ZKTECO = "zkteco"
    MATRIX = "matrix"
    HIKVISION = "hikvision"
    SUPREMA = "suprema"
    REALTIME = "realtime"
    ANVIZ = "anviz"


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    DISABLED = "disabled"
    UNKNOWN = "unknown"
    DEGRADED = "degraded"
    TIMEOUT = "timeout"
    AUTH_ERROR = "auth_error"


class CommunicationType(str, Enum):
    TCP = "tcp"
    UDP = "udp"
    HTTP = "http"
    ADMS = "adms"
    SDK = "sdk"


class QueueStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    DEAD = "dead"  # poison / exhausted retries — excluded from upload pending


class CommandType(str, Enum):
    RESTART_DEVICE = "restart_device"
    SYNC_TIME = "sync_time"
    DOWNLOAD_ATTENDANCE = "download_attendance"
    CLEAR_ATTENDANCE = "clear_attendance"
    ENABLE_DEVICE = "enable_device"
    DISABLE_DEVICE = "disable_device"
    REFRESH_DEVICE_INFO = "refresh_device_info"
    TEST_CONNECTION = "test_connection"
    SYNC_EMPLOYEES = "sync_employees"
    SYNC_TEMPLATES = "sync_templates"
    DOWNLOAD_USERS = "download_users"
    UPLOAD_USERS = "upload_users"
    DELETE_USERS = "delete_users"
    BULK_SYNC_USERS = "bulk_sync_users"
    BACKUP_DEVICE = "backup_device"
    RESTORE_DEVICE = "restore_device"
    PROVISION_DEVICE = "provision_device"
    FIRMWARE_UPGRADE = "firmware_upgrade"
    FIRMWARE_ROLLBACK = "firmware_rollback"
    PUSH_INVENTORY = "push_inventory"
    REMOTE_LOG_DOWNLOAD = "remote_log_download"
    REMOTE_DIAGNOSTICS = "remote_diagnostics"
    REMOTE_RESTART_CONNECTOR = "remote_restart_connector"
    REMOTE_CACHE_CLEANUP = "remote_cache_cleanup"
    REMOTE_DB_BACKUP = "remote_db_backup"
    REMOTE_DB_RESTORE = "remote_db_restore"
    GENERATE_REPORT = "generate_report"


class CommandStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class AlertType(str, Enum):
    DEVICE_OFFLINE = "device_offline"
    INTERNET_LOST = "internet_lost"
    QUEUE_GROWING = "queue_growing"
    UPLOAD_FAILED = "upload_failed"
    LOW_STORAGE = "low_storage"
    CONNECTOR_RESTARTED = "connector_restarted"
    API_AUTH_FAILED = "api_auth_failed"
    CONNECTOR_OFFLINE = "connector_offline"
    QUEUE_OVERFLOW = "queue_overflow"
    TEMPLATE_FAILURE = "template_failure"
    SYNC_FAILURE = "sync_failure"
    FIRMWARE_UPDATE_AVAILABLE = "firmware_update_available"
    BACKUP_FAILED = "backup_failed"
    RESTORE_FAILED = "restore_failed"
    AUTHENTICATION_FAILURE = "authentication_failure"
    HIGH_TEMPERATURE = "high_temperature"
    LOW_BATTERY = "low_battery"
    CAPACITY_WARNING = "capacity_warning"
    CLOCK_MISMATCH = "clock_mismatch"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DiscoveryDecision(str, Enum):
    PENDING = "pending"
    ADDED = "added"
    IGNORED = "ignored"


class EmployeeStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class SyncAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    DEACTIVATE = "deactivate"
    REACTIVATE = "reactivate"


class TemplateType(str, Enum):
    FINGERPRINT = "fingerprint"
    FACE = "face"
    PALM = "palm"
    RFID = "rfid"
    PIN = "pin"


class SyncJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class Device:
    """Registered biometric device with optional multi-branch hierarchy."""

    id: str
    name: str
    manufacturer: str
    model: str
    ip_address: str
    port: int
    password: str
    branch: str
    timezone: str = "Asia/Kolkata"
    communication_type: str = CommunicationType.TCP.value
    polling_interval: int = 60
    status: str = DeviceStatus.UNKNOWN.value
    terminal_code: str = ""
    serial_number: str = ""
    last_sync_at: Optional[str] = None
    last_sync_cursor: str = ""
    last_error: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: utcnow().isoformat())
    extra: dict[str, Any] = field(default_factory=dict)
    # Enterprise Phase 2 hierarchy + telemetry fields
    organization: str = ""
    department: str = ""
    building: str = ""
    floor: str = ""
    gate: str = ""
    mac_address: str = ""
    firmware: str = ""
    sdk_version: str = ""
    last_attendance_at: Optional[str] = None
    current_users: int = 0
    current_logs: int = 0
    health_status: str = "unknown"
    last_health_at: Optional[str] = None
    warranty_until: str = ""
    installation_date: str = ""
    last_maintenance_at: str = ""
    last_backup_at: str = ""
    last_time_sync_at: str = ""
    last_employee_sync_at: str = ""
    uptime_seconds: int = 0
    failed_auth_count: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["password"] = "***" if self.password else ""
        return data

    def location_label(self) -> str:
        parts = [
            p
            for p in (
                self.organization,
                self.branch,
                self.department,
                self.building,
                self.floor,
                self.gate,
            )
            if p
        ]
        return " / ".join(parts) if parts else (self.branch or "—")


@dataclass
class AttendanceRecord:
    id: str
    device_id: str
    device_employee_code: str
    punch_time: str
    event_type: str = "IN"
    verification_method: str = "not_reported"
    biometric_reference: str = ""
    idempotency_key: str = ""
    status: str = QueueStatus.PENDING.value
    retry_count: int = 0
    last_error: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    uploaded_at: Optional[str] = None


@dataclass
class SyncLog:
    id: str
    device_id: str
    action: str
    success: bool
    message: str
    records_count: int = 0
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorSettings:
    connector_id: str = ""
    organisation_id: str = ""
    api_key: str = ""
    jwt_token: str = ""
    cloud_base_url: str = "https://brixigo.com"
    heartbeat_interval: int = 60
    upload_batch_size: int = 200
    auto_start: bool = True
    auto_update: bool = False
    verify_ssl: bool = True
    proxy_url: str = ""
    device_connect_timeout: float = 12.0
    device_read_timeout: float = 30.0
    max_worker_threads: int = 8
    # Phase 2 remote/runtime settings
    retry_count: int = 3
    request_timeout: float = 30.0
    logging_level: str = "INFO"
    health_interval: int = 60
    command_poll_interval: int = 30
    remote_config_interval: int = 300
    queue_alert_threshold: int = 5000
    command_signing_secret: str = ""
    ca_bundle_path: str = ""
    max_upload_retries: int = 8
    cleanup_uploaded_days: int = 14
    cleanup_log_days: int = 30
    employee_sync_interval: int = 120
    time_sync_interval: int = 300
    time_drift_threshold_seconds: int = 30
    backup_interval: int = 86400
    inventory_push_interval: int = 600
    client_cert_path: str = ""
    client_key_path: str = ""
    mtls_enabled: bool = False
    role: str = "admin"
    # Feature flags (v1.0 hardening)
    enable_firmware_flash: bool = False
    enable_access_control: bool = False
    http_connect_timeout: float = 10.0
    http_read_timeout: float = 30.0
    device_heartbeat_interval: int = 12
    api_health_interval: int = 30


@dataclass
class DeviceInfo:
    serial_number: str = ""
    firmware: str = ""
    platform: str = ""
    user_count: int = 0
    attendance_count: int = 0
    device_name: str = ""
    mac_address: str = ""
    sdk_version: str = ""
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceUser:
    user_id: str
    name: str = ""
    card: str = ""
    privilege: int = 0
    password: str = ""
    group_id: str = ""
    user_uid: int = 0
    enabled: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttendanceLog:
    user_id: str
    punch_time: datetime
    status: int = 0
    punch: int = 0
    uid: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthResult:
    online: bool
    message: str = ""
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HeartbeatSnapshot:
    connector_version: str
    status: str
    cpu_percent: float
    ram_percent: float
    disk_percent: float
    connected_devices: int
    offline_devices: int
    last_sync: Optional[str]
    pending_queue: int
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    failed_uploads: int = 0
    today_punches: int = 0
    average_upload_ms: float = 0.0
    last_error: str = ""
    last_restart_at: Optional[str] = None


@dataclass
class DiscoveredDevice:
    """Candidate device found by network scan — requires admin approval."""

    ip_address: str
    port: int
    manufacturer: str = ""
    model: str = ""
    firmware: str = ""
    mac_address: str = ""
    sdk_version: str = ""
    device_name: str = ""
    serial_number: str = ""
    communication_type: str = CommunicationType.TCP.value
    latency_ms: float = 0.0
    decision: str = DiscoveryDecision.PENDING.value
    discovered_at: str = field(default_factory=lambda: utcnow().isoformat())
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.ip_address}:{self.port}"


@dataclass
class DeviceHealthSnapshot:
    """Per-minute device health sample pushed to cloud."""

    id: str
    device_id: str
    online: bool
    ping_ms: float = 0.0
    memory_usage_percent: float = 0.0
    storage_usage_percent: float = 0.0
    user_capacity: int = 0
    log_capacity: int = 0
    current_users: int = 0
    current_logs: int = 0
    firmware_version: str = ""
    device_time: str = ""
    battery_level: Optional[float] = None
    cpu_temperature: Optional[float] = None
    restart_count: int = 0
    message: str = ""
    collected_at: str = field(default_factory=lambda: utcnow().isoformat())
    pushed: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    failed_auth_count: int = 0
    network_quality: str = ""
    uptime_seconds: int = 0


@dataclass
class RemoteCommand:
    id: str
    command_type: str
    device_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = CommandStatus.PENDING.value
    result_message: str = ""
    result_payload: dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    nonce: str = ""
    issued_at: str = ""
    expires_at: str = ""
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    completed_at: Optional[str] = None
    cloud_command_id: str = ""


@dataclass
class Alert:
    id: str
    alert_type: str
    severity: str
    title: str
    message: str
    device_id: str = ""
    acknowledged: bool = False
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditLog:
    id: str
    action: str
    actor: str
    success: bool
    message: str
    resource_type: str = ""
    resource_id: str = ""
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    details: dict[str, Any] = field(default_factory=dict)


# Re-export Phase 3 models for a single import surface.
from models.phase3 import (  # noqa: E402
    AccessLevel,
    AccessSchedule,
    BiometricTemplate,
    DeviceBackup,
    DeviceUserRecord,
    DoorController,
    Employee,
    FirmwareJob,
    NotificationEvent,
    ReportRequest,
)

__all__ = [
    "utcnow",
    "new_id",
    "Manufacturer",
    "DeviceStatus",
    "CommunicationType",
    "QueueStatus",
    "CommandType",
    "CommandStatus",
    "AlertType",
    "AlertSeverity",
    "DiscoveryDecision",
    "EmployeeStatus",
    "SyncAction",
    "TemplateType",
    "SyncJobStatus",
    "Device",
    "AttendanceRecord",
    "SyncLog",
    "ConnectorSettings",
    "DeviceInfo",
    "DeviceUser",
    "AttendanceLog",
    "HealthResult",
    "HeartbeatSnapshot",
    "DiscoveredDevice",
    "DeviceHealthSnapshot",
    "RemoteCommand",
    "Alert",
    "AuditLog",
    "Employee",
    "BiometricTemplate",
    "DeviceUserRecord",
    "DeviceBackup",
    "FirmwareJob",
    "AccessLevel",
    "DoorController",
    "AccessSchedule",
    "NotificationEvent",
    "ReportRequest",
]
