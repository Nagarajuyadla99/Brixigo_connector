"""Pydantic v2 schemas for admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    email: str
    full_name: str
    role: str
    organisation_id: Optional[str] = None
    organisation_name: str = ""


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    organisation_id: Optional[str] = None
    organisation_name: str = ""
    branch_id: Optional[str] = None
    is_active: bool
    last_login_at: Optional[datetime] = None


class OrganisationCreate(BaseModel):
    name: str
    code: str
    timezone: str = "Asia/Kolkata"
    status: str = "active"
    settings_json: dict[str, Any] = Field(default_factory=dict)


class OrganisationUpdate(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[str] = None
    settings_json: Optional[dict[str, Any]] = None


class OrganisationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    status: str
    timezone: str
    settings_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class BranchCreate(BaseModel):
    organisation_id: str
    name: str
    code: str
    address: str = ""
    status: str = "active"


class BranchUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    status: Optional[str] = None


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    name: str
    code: str
    address: str
    status: str
    created_at: datetime
    updated_at: datetime


class DepartmentCreate(BaseModel):
    organisation_id: str
    branch_id: str
    name: str
    code: str = ""


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    branch_id: str
    name: str
    code: str
    created_at: datetime
    updated_at: datetime


class BuildingCreate(BaseModel):
    organisation_id: str
    branch_id: str
    name: str
    department_id: Optional[str] = None


class BuildingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    branch_id: str
    department_id: Optional[str] = None
    name: str
    created_at: datetime
    updated_at: datetime


class FloorCreate(BaseModel):
    organisation_id: str
    building_id: str
    name: str


class FloorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    building_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class GateCreate(BaseModel):
    organisation_id: str
    floor_id: str
    name: str
    code: str = ""


class GateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    floor_id: str
    name: str
    code: str
    created_at: datetime
    updated_at: datetime


class ConnectorRegister(BaseModel):
    organisation_name: str
    name: str
    branch_name: Optional[str] = None
    hostname: str = ""
    version: str = ""


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    branch_name: Optional[str] = None
    hostname: Optional[str] = None
    version: Optional[str] = None
    ip_address: Optional[str] = None
    is_active: Optional[bool] = None


class ConnectorAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    organisation_name: str = ""
    branch_id: Optional[str] = None
    branch_name: str = ""
    name: str
    api_key_prefix: str
    version: str
    hostname: str
    operating_system: str
    ip_address: str
    status: str
    internet_status: str
    cloud_status: str
    certificate_status: str
    last_heartbeat_at: Optional[datetime] = None
    cpu_percent: float = 0
    ram_percent: float = 0
    disk_percent: float = 0
    queue_size: int = 0
    connected_devices: int = 0
    offline_devices: int = 0
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ConnectorRegisterResponse(BaseModel):
    connector: ConnectorAdminOut
    api_key: str


class DeviceAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    organisation_name: str = ""
    connector_id: Optional[str] = None
    connector_name: str = ""
    branch_id: Optional[str] = None
    branch_name: str = ""
    department_id: Optional[str] = None
    name: str
    manufacturer: str
    model: str
    serial_number: str
    firmware: str
    terminal_code: str
    ip_address: str
    port: int
    status: str
    health_status: str
    health_score: float
    enabled: bool
    last_sync_at: Optional[datetime] = None
    last_attendance_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class DeviceBulkAction(BaseModel):
    device_ids: list[str]
    enabled: Optional[bool] = None


class DeviceCreate(BaseModel):
    organisation_name: str
    name: str
    connector_name: Optional[str] = None
    connector_id: Optional[str] = None
    branch_name: Optional[str] = None
    terminal_code: str = ""
    ip_address: str = ""
    port: int = 4370
    manufacturer: str = ""
    model: str = ""
    serial_number: str = ""
    firmware: str = ""
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    connector_id: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    terminal_code: Optional[str] = None
    ip_address: Optional[str] = None
    port: Optional[int] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    firmware: Optional[str] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None


class EmployeeCreate(BaseModel):
    organisation_name: str
    employee_code: str
    name: str
    branch_name: Optional[str] = None
    department_id: Optional[str] = None
    designation: str = ""
    shift: str = ""
    card_number: str = ""
    pin: Optional[str] = None
    status: str = "active"
    privilege: int = 0
    device_ids: list[str] = Field(default_factory=list)
    extra_json: dict[str, Any] = Field(default_factory=dict)


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    branch_id: Optional[str] = None
    department_id: Optional[str] = None
    designation: Optional[str] = None
    shift: Optional[str] = None
    card_number: Optional[str] = None
    pin: Optional[str] = None
    status: Optional[str] = None
    privilege: Optional[int] = None
    device_ids: Optional[list[str]] = None
    extra_json: Optional[dict[str, Any]] = None


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    organisation_name: str = ""
    branch_id: Optional[str] = None
    branch_name: str = ""
    department_id: Optional[str] = None
    employee_code: str
    name: str
    designation: str
    shift: str
    card_number: str
    status: str
    privilege: int
    version: int
    device_ids_json: list[str] = Field(default_factory=list)
    extra_json: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    device_user_id: str = ""
    name_reported: bool = False
    punch_count: int = 0
    last_punch_at: str = ""
    created_at: datetime
    updated_at: datetime


class EmployeeBulkAssign(BaseModel):
    employee_ids: list[str]
    device_ids: list[str]


class EmployeeTransfer(BaseModel):
    branch_id: Optional[str] = None
    department_id: Optional[str] = None


class CommandCreate(BaseModel):
    connector_name: str
    command_type: str
    device_name: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: Optional[int] = None


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    organisation_name: str = ""
    connector_id: str
    connector_name: str = ""
    device_id: str
    device_name: str = ""
    command_type: str
    payload_json: dict[str, Any]
    status: str
    signature: str
    nonce: str
    issued_at: datetime
    expires_at: Optional[datetime] = None
    result_message: str = ""
    created_by: str = ""
    created_at: datetime
    completed_at: Optional[datetime] = None


class TemplateCreate(BaseModel):
    organisation_id: str
    employee_id: str
    employee_code: str = ""
    template_type: str
    finger_index: int = 0
    template_data: str
    vendor: str = ""
    checksum: str = ""


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    employee_id: str
    employee_code: str
    template_type: str
    finger_index: int
    checksum: str
    vendor: str
    size: int
    version: int
    created_at: datetime
    updated_at: datetime


class FirmwarePackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: Optional[str] = None
    manufacturer: str
    model: str
    version: str
    checksum: str
    size_bytes: int
    release_notes: str
    is_latest: bool
    created_at: datetime
    updated_at: datetime


class FirmwareJobCreate(BaseModel):
    device_ids: list[str]
    package_id: str
    scheduled_at: Optional[datetime] = None


class FirmwareJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    device_id: str
    connector_id: str
    package_id: str
    action: str
    from_version: str
    to_version: str
    status: str
    scheduled_at: Optional[datetime] = None
    message: str
    created_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    connector_id: str
    device_id: str
    event_type: str
    severity: str
    title: str
    message: str
    acknowledged: bool
    details_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    actor_id: str
    actor_email: str
    action: str
    resource_type: str
    resource_id: str
    success: bool
    message: str
    ip_address: str
    details_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ReportCreate(BaseModel):
    report_type: str
    organisation_name: Optional[str] = None
    format: str = "csv"
    parameters: dict[str, Any] = Field(default_factory=dict)


class ReportJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    report_type: str
    parameters_json: dict[str, Any]
    status: str
    file_path: str
    format: str
    created_by: str
    created_at: datetime
    completed_at: Optional[datetime] = None


class PolicyUpdate(BaseModel):
    organisation_id: Optional[str] = None
    policy_key: str
    policy_json: dict[str, Any] = Field(default_factory=dict)


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    policy_key: str
    policy_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProvisioningWizardStep(BaseModel):
    step: str
    status: str
    message: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProvisioningApprove(BaseModel):
    connector_id: str
    branch_id: Optional[str] = None


class ProvisioningAssign(BaseModel):
    device_id: str
    branch_id: Optional[str] = None
    department_id: Optional[str] = None
    building_id: Optional[str] = None
    floor_id: Optional[str] = None
    gate_id: Optional[str] = None


class AnalyticsSummary(BaseModel):
    connectors_online: int = 0
    connectors_offline: int = 0
    devices_healthy: int = 0
    devices_warning: int = 0
    devices_critical: int = 0
    devices_offline: int = 0
    attendance_today: int = 0
    attendance_week: list[dict[str, Any]] = Field(default_factory=list)
    uptime_percent: float = 0.0
    avg_health_score: float = 0.0
    queue_alerts: int = 0
    pending_commands: int = 0


class MonitoringDashboard(BaseModel):
    online: int = 0
    offline: int = 0
    warning: int = 0
    critical: int = 0
    degraded_connectors: int = 0
    total_devices: int = 0
    total_connectors: int = 0


class WebhookCreate(BaseModel):
    organisation_id: str
    url: str
    secret: str = ""
    event_types: list[str] = Field(default_factory=list)
    is_active: bool = True


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    url: str
    event_types_json: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupportArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    connector_id: str
    artifact_type: str
    filename: str
    size_bytes: int
    created_at: datetime
