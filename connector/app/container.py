"""Dependency injection container for the connector agent (Phase 1–3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.constants import APP_VERSION
from config.settings_manager import SettingsManager
from security.encryption import SecretBox
from storage.database import Database
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.command_repo import CommandRepository, NonceRepository
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import (
    AlertRepository,
    AuditRepository,
    DiscoveryRepository,
    UploadMetricsRepository,
)
from storage.repositories.health_repo import DeviceHealthRepository
from storage.repositories.heartbeat_repo import HeartbeatRepository
from storage.repositories.phase3_repo import (
    AccessControlRepository,
    BackupRepository,
    DeviceUserCacheRepository,
    EmployeeRepository,
    EmployeeSyncQueueRepository,
    FirmwareJobRepository,
    NotificationRepository,
    ReportRepository,
    TemplateRepository,
    TimeSyncLogRepository,
)
from storage.repositories.settings_repo import SettingsRepository
from storage.repositories.sync_repo import SyncLogRepository


@dataclass
class AppPaths:
    root: Path
    data: Path
    logs: Path
    config_file: Path
    db_file: Path
    backups: Path = field(default_factory=Path)
    reports: Path = field(default_factory=Path)


@dataclass
class Container:
    """Composition root — wire once, inject everywhere."""

    paths: AppPaths
    secrets: SecretBox
    settings: SettingsManager
    db: Database
    devices: DeviceRepository
    attendance: AttendanceRepository
    sync_logs: SyncLogRepository
    settings_repo: SettingsRepository
    heartbeat_repo: HeartbeatRepository
    health_repo: DeviceHealthRepository
    commands: CommandRepository
    nonces: NonceRepository
    alerts: AlertRepository
    audit: AuditRepository
    discovery: DiscoveryRepository
    upload_metrics: UploadMetricsRepository
    # Phase 3
    employees: EmployeeRepository
    templates: TemplateRepository
    device_users: DeviceUserCacheRepository
    employee_sync_queue: EmployeeSyncQueueRepository
    backups: BackupRepository
    firmware_jobs: FirmwareJobRepository
    access_control: AccessControlRepository
    notifications: NotificationRepository
    reports: ReportRepository
    time_sync_logs: TimeSyncLogRepository
    version: str = field(default=APP_VERSION)
    _started: bool = field(default=False, init=False, repr=False)

    def start(self) -> None:
        if self._started:
            return
        self.paths.data.mkdir(parents=True, exist_ok=True)
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        self.paths.backups.mkdir(parents=True, exist_ok=True)
        self.paths.reports.mkdir(parents=True, exist_ok=True)
        self.db.initialize()
        self.settings.load()
        self._started = True

    def shutdown(self) -> None:
        if not self._started:
            return
        self.db.close()
        self._started = False


_container: Optional[Container] = None


def get_container() -> Container:
    if _container is None:
        raise RuntimeError("Container not initialized. Call bootstrap.create_container() first.")
    return _container


def set_container(container: Container) -> Container:
    global _container
    _container = container
    return container
