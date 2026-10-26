"""SQLite database access layer with Phase 2 schema migrations."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("brixigo")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manufacturer TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 4370,
    password_enc TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    communication_type TEXT NOT NULL DEFAULT 'tcp',
    polling_interval INTEGER NOT NULL DEFAULT 60,
    status TEXT NOT NULL DEFAULT 'unknown',
    terminal_code TEXT NOT NULL DEFAULT '',
    serial_number TEXT NOT NULL DEFAULT '',
    last_sync_at TEXT,
    last_sync_cursor TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_terminal
    ON devices(terminal_code) WHERE terminal_code != '';

CREATE TABLE IF NOT EXISTS attendance_queue (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    device_employee_code TEXT NOT NULL,
    punch_time TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'IN',
    verification_method TEXT NOT NULL DEFAULT 'fingerprint',
    biometric_reference TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    raw_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    uploaded_at TEXT,
    uploading_at TEXT,
    last_attempt_at TEXT,
    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_idem
    ON attendance_queue(idempotency_key);

CREATE INDEX IF NOT EXISTS idx_attendance_status
    ON attendance_queue(status, created_at);

CREATE INDEX IF NOT EXISTS idx_attendance_device
    ON attendance_queue(device_id, punch_time);

CREATE INDEX IF NOT EXISTS idx_attendance_punch_time
    ON attendance_queue(punch_time);

CREATE INDEX IF NOT EXISTS idx_attendance_created
    ON attendance_queue(created_at);

CREATE INDEX IF NOT EXISTS idx_attendance_employee
    ON attendance_queue(device_employee_code, punch_time);

CREATE TABLE IF NOT EXISTS sync_logs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    success INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    records_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sync_logs_created
    ON sync_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS connector_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    connector_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    cpu_percent REAL NOT NULL DEFAULT 0,
    ram_percent REAL NOT NULL DEFAULT 0,
    disk_percent REAL NOT NULL DEFAULT 0,
    connected_devices INTEGER NOT NULL DEFAULT 0,
    offline_devices INTEGER NOT NULL DEFAULT 0,
    last_sync TEXT,
    pending_queue INTEGER NOT NULL DEFAULT 0,
    last_sent_at TEXT,
    last_payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_health (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    online INTEGER NOT NULL DEFAULT 0,
    ping_ms REAL NOT NULL DEFAULT 0,
    memory_usage_percent REAL NOT NULL DEFAULT 0,
    storage_usage_percent REAL NOT NULL DEFAULT 0,
    user_capacity INTEGER NOT NULL DEFAULT 0,
    log_capacity INTEGER NOT NULL DEFAULT 0,
    current_users INTEGER NOT NULL DEFAULT 0,
    current_logs INTEGER NOT NULL DEFAULT 0,
    firmware_version TEXT NOT NULL DEFAULT '',
    device_time TEXT NOT NULL DEFAULT '',
    battery_level REAL,
    cpu_temperature REAL,
    restart_count INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL,
    pushed INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_health_device
    ON device_health(device_id, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_device_health_pushed
    ON device_health(pushed, collected_at);

CREATE TABLE IF NOT EXISTS remote_commands (
    id TEXT PRIMARY KEY,
    cloud_command_id TEXT NOT NULL DEFAULT '',
    command_type TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    result_message TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    signature TEXT NOT NULL DEFAULT '',
    nonce TEXT NOT NULL DEFAULT '',
    issued_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_commands_cloud
    ON remote_commands(cloud_command_id) WHERE cloud_command_id != '';

CREATE INDEX IF NOT EXISTS idx_remote_commands_status
    ON remote_commands(status, created_at);

CREATE TABLE IF NOT EXISTS command_nonces (
    nonce TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_alerts_created
    ON alerts(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_type
    ON alerts(alert_type, acknowledged);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    success INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    resource_type TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_created
    ON audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS discovery_ignore (
    ip_address TEXT NOT NULL,
    port INTEGER NOT NULL,
    ignored_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (ip_address, port)
);

CREATE TABLE IF NOT EXISTS discovery_cache (
    ip_address TEXT NOT NULL,
    port INTEGER NOT NULL,
    manufacturer TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    firmware TEXT NOT NULL DEFAULT '',
    mac_address TEXT NOT NULL DEFAULT '',
    sdk_version TEXT NOT NULL DEFAULT '',
    device_name TEXT NOT NULL DEFAULT '',
    serial_number TEXT NOT NULL DEFAULT '',
    communication_type TEXT NOT NULL DEFAULT 'tcp',
    latency_ms REAL NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT 'pending',
    discovered_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (ip_address, port)
);

CREATE TABLE IF NOT EXISTS heartbeat_history (
    id TEXT PRIMARY KEY,
    connector_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    cpu_percent REAL NOT NULL DEFAULT 0,
    ram_percent REAL NOT NULL DEFAULT 0,
    disk_percent REAL NOT NULL DEFAULT 0,
    connected_devices INTEGER NOT NULL DEFAULT 0,
    offline_devices INTEGER NOT NULL DEFAULT 0,
    pending_queue INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_heartbeat_history_created
    ON heartbeat_history(created_at DESC);

CREATE TABLE IF NOT EXISTS upload_metrics (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_uploaded INTEGER NOT NULL DEFAULT 0,
    total_failed INTEGER NOT NULL DEFAULT 0,
    total_upload_ms REAL NOT NULL DEFAULT 0,
    upload_samples INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- Phase 3 enterprise device-management tables
CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY,
    employee_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    designation TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    organization TEXT NOT NULL DEFAULT '',
    building TEXT NOT NULL DEFAULT '',
    floor TEXT NOT NULL DEFAULT '',
    gate TEXT NOT NULL DEFAULT '',
    shift TEXT NOT NULL DEFAULT '',
    card_number TEXT NOT NULL DEFAULT '',
    pin_enc TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    device_ids_json TEXT NOT NULL DEFAULT '[]',
    privilege INTEGER NOT NULL DEFAULT 0,
    cloud_version INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_employees_branch ON employees(branch, status);
CREATE INDEX IF NOT EXISTS idx_employees_code ON employees(employee_code);

CREATE TABLE IF NOT EXISTS biometric_templates (
    id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL,
    employee_code TEXT NOT NULL,
    template_type TEXT NOT NULL,
    finger_index INTEGER NOT NULL DEFAULT 0,
    template_enc TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL DEFAULT '',
    vendor TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    device_id TEXT NOT NULL DEFAULT '',
    cloud_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_templates_employee
    ON biometric_templates(employee_id, template_type);

CREATE TABLE IF NOT EXISTS device_users (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    card TEXT NOT NULL DEFAULT '',
    privilege INTEGER NOT NULL DEFAULT 0,
    password_enc TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    employee_id TEXT NOT NULL DEFAULT '',
    last_synced_at TEXT,
    sync_status TEXT NOT NULL DEFAULT 'unknown',
    conflict TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(device_id, user_id),
    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_users_device ON device_users(device_id);

CREATE TABLE IF NOT EXISTS employee_sync_queue (
    id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_emp_sync_status
    ON employee_sync_queue(status, created_at);

CREATE TABLE IF NOT EXISTS device_backups (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    backup_type TEXT NOT NULL DEFAULT 'full',
    includes_users INTEGER NOT NULL DEFAULT 1,
    includes_templates INTEGER NOT NULL DEFAULT 1,
    includes_attendance INTEGER NOT NULL DEFAULT 1,
    includes_config INTEGER NOT NULL DEFAULT 1,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    checksum TEXT NOT NULL DEFAULT '',
    encrypted INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_backups_device ON device_backups(device_id, created_at DESC);

CREATE TABLE IF NOT EXISTS firmware_jobs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    action TEXT NOT NULL,
    from_version TEXT NOT NULL DEFAULT '',
    to_version TEXT NOT NULL DEFAULT '',
    package_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS access_levels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    organization TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    schedule_json TEXT NOT NULL DEFAULT '{}',
    door_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS door_controllers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL DEFAULT 4370,
    organization TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    building TEXT NOT NULL DEFAULT '',
    floor TEXT NOT NULL DEFAULT '',
    gate TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS access_schedules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    rules_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'warning',
    device_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'cloud',
    delivered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_notifications_created
    ON notifications(created_at DESC);

CREATE TABLE IF NOT EXISTS report_jobs (
    id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    result_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS time_sync_logs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    connector_time TEXT NOT NULL,
    cloud_time TEXT NOT NULL DEFAULT '',
    device_time TEXT NOT NULL DEFAULT '',
    drift_seconds REAL NOT NULL DEFAULT 0,
    corrected INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_time_sync_device
    ON time_sync_logs(device_id, created_at DESC);
"""

# Additive columns for existing Phase 1 installations (idempotent via PRAGMA checks).
DEVICE_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("organization", "TEXT NOT NULL DEFAULT ''"),
    ("department", "TEXT NOT NULL DEFAULT ''"),
    ("building", "TEXT NOT NULL DEFAULT ''"),
    ("floor", "TEXT NOT NULL DEFAULT ''"),
    ("gate", "TEXT NOT NULL DEFAULT ''"),
    ("mac_address", "TEXT NOT NULL DEFAULT ''"),
    ("firmware", "TEXT NOT NULL DEFAULT ''"),
    ("sdk_version", "TEXT NOT NULL DEFAULT ''"),
    ("last_attendance_at", "TEXT"),
    ("current_users", "INTEGER NOT NULL DEFAULT 0"),
    ("current_logs", "INTEGER NOT NULL DEFAULT 0"),
    ("health_status", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("last_health_at", "TEXT"),
    ("warranty_until", "TEXT NOT NULL DEFAULT ''"),
    ("installation_date", "TEXT NOT NULL DEFAULT ''"),
    ("last_maintenance_at", "TEXT NOT NULL DEFAULT ''"),
    ("last_backup_at", "TEXT NOT NULL DEFAULT ''"),
    ("last_time_sync_at", "TEXT NOT NULL DEFAULT ''"),
    ("last_employee_sync_at", "TEXT NOT NULL DEFAULT ''"),
    ("uptime_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("failed_auth_count", "INTEGER NOT NULL DEFAULT 0"),
]

HEARTBEAT_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("failed_uploads", "INTEGER NOT NULL DEFAULT 0"),
    ("today_punches", "INTEGER NOT NULL DEFAULT 0"),
    ("average_upload_ms", "REAL NOT NULL DEFAULT 0"),
    ("last_error", "TEXT NOT NULL DEFAULT ''"),
    ("last_restart_at", "TEXT"),
]

ATTENDANCE_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("uploading_at", "TEXT"),
    ("last_attempt_at", "TEXT"),
]


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._connect()
            conn.executescript(SCHEMA_SQL)
            self._apply_column_migrations(conn, "devices", DEVICE_COLUMN_MIGRATIONS)
            self._apply_column_migrations(conn, "heartbeat", HEARTBEAT_COLUMN_MIGRATIONS)
            self._apply_column_migrations(conn, "attendance_queue", ATTENDANCE_COLUMN_MIGRATIONS)
            conn.execute(
                """
                INSERT OR IGNORE INTO heartbeat (
                    id, connector_version, status, updated_at
                ) VALUES (1, '', 'starting', datetime('now'))
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO upload_metrics (
                    id, total_uploaded, total_failed, total_upload_ms,
                    upload_samples, last_error, updated_at
                ) VALUES (1, 0, 0, 0, 0, '', datetime('now'))
                """
            )
            migrated = conn.execute(
                "SELECT value FROM connector_settings WHERE key = 'poll_interval_v2'"
            ).fetchone()
            if migrated is None:
                conn.execute(
                    "UPDATE devices SET polling_interval = 30 WHERE polling_interval = 60"
                )
                conn.execute(
                    """
                    INSERT INTO connector_settings (key, value, updated_at)
                    VALUES ('poll_interval_v2', '30', datetime('now'))
                    """
                )
            # Move default device poll from 30s → 60s (1 minute) once.
            migrated_v3 = conn.execute(
                "SELECT value FROM connector_settings WHERE key = 'poll_interval_v3'"
            ).fetchone()
            if migrated_v3 is None:
                conn.execute(
                    "UPDATE devices SET polling_interval = 60 WHERE polling_interval = 30"
                )
                conn.execute(
                    """
                    INSERT INTO connector_settings (key, value, updated_at)
                    VALUES ('poll_interval_v3', '60', datetime('now'))
                    """
                )
            conn.commit()

    def _apply_column_migrations(
        self,
        conn: sqlite3.Connection,
        table: str,
        migrations: list[tuple[str, str]],
    ) -> None:
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, ddl in migrations:
            if column in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                logger.info("Migrated %s.%s", table, column)
            except sqlite3.OperationalError as exc:
                logger.warning("Skip migration %s.%s: %s", table, column, exc)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Cursor:
        with self.cursor() as cur:
            return cur.execute(sql, params)

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        with self.cursor() as cur:
            cur.executemany(sql, seq)

    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Optional[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
