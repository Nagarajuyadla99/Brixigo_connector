"""SQLAlchemy domain models — multi-tenant enterprise cloud schema."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Role(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    PLATFORM_ADMIN = "platform_admin"
    ORG_ADMIN = "organisation_admin"
    HR_MANAGER = "hr_manager"
    IT_ADMIN = "it_administrator"
    BRANCH_ADMIN = "branch_admin"
    SUPPORT_ENGINEER = "support_engineer"
    AUDITOR = "auditor"


class ConnectorStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    DEACTIVATED = "deactivated"
    UNKNOWN = "unknown"


class DeviceHealthState(str, enum.Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class CommandState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Organisation(Base, TimestampMixin):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)

    branches: Mapped[list["Branch"]] = relationship(back_populates="organisation")
    users: Mapped[list["User"]] = relationship(back_populates="organisation")


class Branch(Base, TimestampMixin):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("organisation_id", "code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")

    organisation: Mapped[Organisation] = relationship(back_populates="branches")
    departments: Mapped[list["Department"]] = relationship(back_populates="branch")


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), default="")

    branch: Mapped[Branch] = relationship(back_populates="departments")
    buildings: Mapped[list["Building"]] = relationship(back_populates="department")


class Building(Base, TimestampMixin):
    __tablename__ = "buildings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    department_id: Mapped[Optional[str]] = mapped_column(ForeignKey("departments.id"), nullable=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    department: Mapped[Optional[Department]] = relationship(back_populates="buildings")
    floors: Mapped[list["Floor"]] = relationship(back_populates="building")


class Floor(Base, TimestampMixin):
    __tablename__ = "floors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    building: Mapped[Building] = relationship(back_populates="floors")
    gates: Mapped[list["Gate"]] = relationship(back_populates="floor")


class Gate(Base, TimestampMixin):
    __tablename__ = "gates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    floor_id: Mapped[str] = mapped_column(ForeignKey("floors.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), default="")

    floor: Mapped[Floor] = relationship(back_populates="gates")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("organisation_id", "email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("organisations.id"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), default=Role.ORG_ADMIN.value)
    branch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    organisation: Mapped[Optional[Organisation]] = relationship(back_populates="users")


class Connector(Base, TimestampMixin):
    __tablename__ = "connectors"
    __table_args__ = (Index("ix_connectors_org_branch", "organisation_id", "branch_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    branch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(16), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")
    operating_system: Mapped[str] = mapped_column(String(128), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default=ConnectorStatus.UNKNOWN.value)
    internet_status: Mapped[str] = mapped_column(String(32), default="unknown")
    cloud_status: Mapped[str] = mapped_column(String(32), default="unknown")
    certificate_status: Mapped[str] = mapped_column(String(32), default="unknown")
    installation_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_restart_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cpu_percent: Mapped[float] = mapped_column(Float, default=0)
    ram_percent: Mapped[float] = mapped_column(Float, default=0)
    disk_percent: Mapped[float] = mapped_column(Float, default=0)
    queue_size: Mapped[int] = mapped_column(Integer, default=0)
    pending_upload: Mapped[int] = mapped_column(Integer, default=0)
    connected_devices: Mapped[int] = mapped_column(Integer, default=0)
    offline_devices: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Device(Base, TimestampMixin):
    __tablename__ = "devices"
    __table_args__ = (
        Index("ix_devices_org_branch", "organisation_id", "branch_id"),
        UniqueConstraint("organisation_id", "terminal_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    connector_id: Mapped[Optional[str]] = mapped_column(ForeignKey("connectors.id"), index=True)
    branch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    department_id: Mapped[Optional[str]] = mapped_column(ForeignKey("departments.id"), nullable=True)
    building_id: Mapped[Optional[str]] = mapped_column(ForeignKey("buildings.id"), nullable=True)
    floor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("floors.id"), nullable=True)
    gate_id: Mapped[Optional[str]] = mapped_column(ForeignKey("gates.id"), nullable=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    serial_number: Mapped[str] = mapped_column(String(128), default="", index=True)
    firmware: Mapped[str] = mapped_column(String(128), default="")
    sdk_version: Mapped[str] = mapped_column(String(128), default="")
    mac_address: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    port: Mapped[int] = mapped_column(Integer, default=4370)
    terminal_code: Mapped[str] = mapped_column(String(64), default="")
    communication_type: Mapped[str] = mapped_column(String(32), default="tcp")
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    health_status: Mapped[str] = mapped_column(String(32), default=DeviceHealthState.UNKNOWN.value)
    health_score: Mapped[float] = mapped_column(Float, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    memory_usage_percent: Mapped[float] = mapped_column(Float, default=0)
    storage_usage_percent: Mapped[float] = mapped_column(Float, default=0)
    battery_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cpu_temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    attendance_count: Mapped[int] = mapped_column(Integer, default=0)
    user_count: Mapped[int] = mapped_column(Integer, default=0)
    template_count: Mapped[int] = mapped_column(Integer, default=0)
    restart_count: Mapped[int] = mapped_column(Integer, default=0)
    response_time_ms: Mapped[float] = mapped_column(Float, default=0)

    warranty_until: Mapped[str] = mapped_column(String(64), default="")
    installation_date: Mapped[str] = mapped_column(String(64), default="")
    last_maintenance_at: Mapped[str] = mapped_column(String(64), default="")
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_attendance_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_health_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    inventory_json: Mapped[dict] = mapped_column(JSON, default=dict)
    location_labels: Mapped[dict] = mapped_column(JSON, default=dict)


class Employee(Base, TimestampMixin):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("organisation_id", "employee_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    branch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("branches.id"), nullable=True)
    department_id: Mapped[Optional[str]] = mapped_column(ForeignKey("departments.id"), nullable=True)
    employee_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    designation: Mapped[str] = mapped_column(String(128), default="")
    shift: Mapped[str] = mapped_column(String(64), default="")
    card_number: Mapped[str] = mapped_column(String(64), default="")
    pin_enc: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    privilege: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    device_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    extra_json: Mapped[dict] = mapped_column(JSON, default=dict)


class BiometricTemplate(Base, TimestampMixin):
    __tablename__ = "biometric_templates"
    __table_args__ = (Index("ix_templates_emp_type", "employee_id", "template_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    employee_code: Mapped[str] = mapped_column(String(64), default="")
    template_type: Mapped[str] = mapped_column(String(32), nullable=False)
    finger_index: Mapped[int] = mapped_column(Integer, default=0)
    template_enc: Mapped[str] = mapped_column(Text, default="")
    checksum: Mapped[str] = mapped_column(String(128), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_version_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)


class EmployeeSyncEvent(Base, TimestampMixin):
    __tablename__ = "employee_sync_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class RemoteCommand(Base, TimestampMixin):
    __tablename__ = "remote_commands"
    __table_args__ = (Index("ix_commands_connector_status", "connector_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(36), default="")
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=CommandState.QUEUED.value)
    signature: Mapped[str] = mapped_column(String(128), default="")
    nonce: Mapped[str] = mapped_column(String(64), default="", unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    result_message: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(36), default="")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AttendancePunch(Base, TimestampMixin):
    __tablename__ = "attendance_punches"
    __table_args__ = (
        UniqueConstraint("organisation_id", "idempotency_key"),
        Index("ix_punches_time", "organisation_id", "punch_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    connector_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    device_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    terminal_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    device_employee_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    punch_time: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), default="IN")
    verification_method: Mapped[str] = mapped_column(String(64), default="fingerprint")
    biometric_reference: Mapped[str] = mapped_column(String(255), default="")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class HrmsSyncStatus(str, enum.Enum):
    PENDING = "pending"
    SENDING = "sending"
    SYNCED = "synced"
    FAILED = "failed"
    ORGANISATION_UNMATCHED = "organisation_unmatched"
    EMPLOYEE_UNMATCHED = "employee_unmatched"
    INVALID = "invalid"


class HrmsAttendanceOutbox(Base, TimestampMixin):
    """Outbound Cloud → Brixigo.com attendance delivery, independent of device ingest."""

    __tablename__ = "hrms_attendance_outbox"
    __table_args__ = (
        UniqueConstraint("punch_id", name="ux_hrms_outbox_punch"),
        Index("ix_hrms_outbox_status", "status", "next_attempt_at"),
        Index("ix_hrms_outbox_org", "organisation_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    punch_id: Mapped[str] = mapped_column(ForeignKey("attendance_punches.id"), nullable=False)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    organisation_code: Mapped[str] = mapped_column(String(64), default="")
    employee_code: Mapped[str] = mapped_column(String(64), default="")
    employee_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(40), default=HrmsSyncStatus.PENDING.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_reason: Mapped[str] = mapped_column(String(128), default="")
    hrms_event_id: Mapped[str] = mapped_column(String(64), default="")
    hrms_employee_id: Mapped[str] = mapped_column(String(64), default="")
    hrms_organisation_id: Mapped[str] = mapped_column(String(64), default="")
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)


class HeartbeatHistory(Base):
    __tablename__ = "heartbeat_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    connector_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DeviceHealthSample(Base):
    __tablename__ = "device_health_samples"
    __table_args__ = (Index("ix_health_device_time", "device_id", "collected_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    connector_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    ping_ms: Mapped[float] = mapped_column(Float, default=0)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FirmwarePackage(Base, TimestampMixin):
    __tablename__ = "firmware_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    release_notes: Mapped[str] = mapped_column(Text, default="")
    is_latest: Mapped[bool] = mapped_column(Boolean, default=False)
    compatibility_json: Mapped[dict] = mapped_column(JSON, default=dict)


class FirmwareJob(Base, TimestampMixin):
    __tablename__ = "firmware_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    connector_id: Mapped[str] = mapped_column(String(36), index=True)
    package_id: Mapped[str] = mapped_column(String(36), default="")
    action: Mapped[str] = mapped_column(String(32), default="upgrade")
    from_version: Mapped[str] = mapped_column(String(64), default="")
    to_version: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text, default="")


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    connector_id: Mapped[str] = mapped_column(String(36), default="")
    device_id: Mapped[str] = mapped_column(String(36), default="")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), default="warning")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_org_created", "organisation_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    actor_id: Mapped[str] = mapped_column(String(36), default="")
    actor_email: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SupportArtifact(Base, TimestampMixin):
    __tablename__ = "support_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    connector_id: Mapped[str] = mapped_column(String(36), index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), default="")
    file_path: Mapped[str] = mapped_column(String(512), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SystemPolicy(Base, TimestampMixin):
    __tablename__ = "system_policies"
    __table_args__ = (UniqueConstraint("organisation_id", "policy_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True, default="")
    policy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ReportJob(Base, TimestampMixin):
    __tablename__ = "report_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    file_path: Mapped[str] = mapped_column(String(512), default="")
    format: Mapped[str] = mapped_column(String(16), default="json")
    created_by: Mapped[str] = mapped_column(String(36), default="")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class WebhookSubscription(Base, TimestampMixin):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), default="")
    event_types_json: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiClient(Base, TimestampMixin):
    __tablename__ = "api_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organisation_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    scopes_json: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
