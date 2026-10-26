"""Application bootstrap — paths, logging, DI container."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from app.constants import (
    APP_NAME,
    BACKUP_DIRNAME,
    CONFIG_FILENAME,
    DATA_DIRNAME,
    DB_FILENAME,
    LOG_DIRNAME,
)
from app.container import AppPaths, Container, set_container
from app.logging_setup import setup_logging
from config.settings_manager import SettingsManager
from security.encryption import SecretBox, derive_machine_key
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


def resolve_root_dir() -> Path:
    """Prefer install location; fall back to package parent; allow override."""
    override = os.environ.get("BRIXIGO_CONNECTOR_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent.parent


def build_paths(root: Path | None = None) -> AppPaths:
    root = root or resolve_root_dir()
    data = root / DATA_DIRNAME
    logs = root / LOG_DIRNAME
    return AppPaths(
        root=root,
        data=data,
        logs=logs,
        config_file=data / CONFIG_FILENAME,
        db_file=data / DB_FILENAME,
        backups=data / BACKUP_DIRNAME,
        reports=data / "reports",
    )


def create_container(root: Path | None = None) -> Container:
    paths = build_paths(root)
    paths.data.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)
    paths.backups.mkdir(parents=True, exist_ok=True)
    paths.reports.mkdir(parents=True, exist_ok=True)

    setup_logging(paths.logs)
    logger = logging.getLogger("brixigo.bootstrap")
    logger.info("%s starting — root=%s", APP_NAME, paths.root)

    key = derive_machine_key(app_id="brixigo-connector")
    secrets = SecretBox(key)
    from security.encryption import has_master_key_env, is_production_env

    if has_master_key_env():
        logger.info("Using BRIXIGO_CONNECTOR_MASTER_KEY for secret encryption")
    else:
        logger.warning(
            "BRIXIGO_CONNECTOR_MASTER_KEY not set — using machine-derived key "
            "(set master key for production installs)"
        )
    if is_production_env():
        logger.info("Connector environment=production")
    settings = SettingsManager(paths.config_file, secrets)
    db = Database(paths.db_file)

    container = Container(
        paths=paths,
        secrets=secrets,
        settings=settings,
        db=db,
        devices=DeviceRepository(db, secrets),
        attendance=AttendanceRepository(db),
        sync_logs=SyncLogRepository(db),
        settings_repo=SettingsRepository(db),
        heartbeat_repo=HeartbeatRepository(db),
        health_repo=DeviceHealthRepository(db),
        commands=CommandRepository(db),
        nonces=NonceRepository(db),
        alerts=AlertRepository(db),
        audit=AuditRepository(db),
        discovery=DiscoveryRepository(db),
        upload_metrics=UploadMetricsRepository(db),
        employees=EmployeeRepository(db, secrets),
        templates=TemplateRepository(db),
        device_users=DeviceUserCacheRepository(db, secrets),
        employee_sync_queue=EmployeeSyncQueueRepository(db),
        backups=BackupRepository(db),
        firmware_jobs=FirmwareJobRepository(db),
        access_control=AccessControlRepository(db),
        notifications=NotificationRepository(db),
        reports=ReportRepository(db),
        time_sync_logs=TimeSyncLogRepository(db),
    )
    container.start()
    return set_container(container)
