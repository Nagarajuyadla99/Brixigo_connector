"""Brixigo Connector Agent — application constants (Phase 1–3)."""

from __future__ import annotations

APP_NAME = "Brixigo Connector Agent"
APP_VENDOR = "Brixigo"
APP_ID = "com.brixigo.connector"
APP_VERSION = "3.1.0"

# Feature flags — incomplete capabilities stay off until hardware-proven
DEFAULT_ENABLE_FIRMWARE_FLASH = False
DEFAULT_ENABLE_ACCESS_CONTROL = False

DEFAULT_CLOUD_BASE_URL = "https://brixigo.com"
API_PREFIX = "/api/wf/biometric-enterprise"

DEFAULT_POLL_INTERVAL_SECONDS = 60
MIN_POLL_INTERVAL_SECONDS = 15
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60
DEFAULT_UPLOAD_BATCH_SIZE = 200
DEFAULT_DEVICE_PORT = 4370
# eSSL/ZK terminals need a brief pause after a probe SYN before the real session.
POST_TCP_CLOSE_DELAY_SECONDS = 0.40
DEFAULT_CONNECT_TIMEOUT = 12.0
DEFAULT_READ_TIMEOUT = 30.0
# LAN pull devices usually answer a SYN quickly, but sleepy K90 / Incomplete
# ARP often needs 5–8s. Cap the *first* poll/health attempt so Sync Now does
# not stack into multi-minute hangs; retries escalate to full connect_timeout.
FAST_TCP_CONNECT_TIMEOUT = 8.0
# Cheap pre-check only (poll hard-unreachable / relocate). Heartbeat uses
# DEVICE_TCP_PROBE_TIMEOUT so sleepy K90 / Incomplete ARP can answer.
# 2s was too aggressive on Incomplete ARP / sleepy K90 and caused false skips.
FAST_TCP_PROBE_TIMEOUT = 4.0
DEFAULT_HEALTH_INTERVAL_SECONDS = 60

# HTTP / TLS — separate connect, handshake, and read budgets.
# Do not use sub-2s API timeouts; observed Brixigo latency is often 1.5–6s.
DEFAULT_HTTP_CONNECT_TIMEOUT = 10.0
DEFAULT_HTTP_TLS_TIMEOUT = 10.0
DEFAULT_HTTP_REQUEST_TIMEOUT = 30.0
DEFAULT_HTTP_READ_TIMEOUT = 30.0
DEFAULT_HTTP_POOL_TIMEOUT = 10.0

# Independent device heartbeat (LAN TCP 4370) — not gated on API health.
DEFAULT_DEVICE_HEARTBEAT_INTERVAL_SECONDS = 12
# Require real SYN failures (not lock contention) before marking DISCONNECTED.
DEVICE_HEARTBEAT_DEGRADED_AFTER = 2
DEVICE_HEARTBEAT_DISCONNECT_AFTER = 5
# Soft SYN timeouts (sleepy terminal / Incomplete ARP) need more evidence
# than hard WinError 10065 before flipping the operator UI to TCP DOWN.
DEVICE_HEARTBEAT_SOFT_DISCONNECT_AFTER = 8
# Poll + heartbeat + time-sync can all fail the same outage within seconds —
# collapse those into one consecutive_failures tick.
DEVICE_FAILURE_COALESCE_SECONDS = 12.0
DEVICE_TCP_PROBE_TIMEOUT = 6.0
# After a soft TCP drop, a short reconnect burst; hard 10065 uses long backoff.
DEVICE_TCP_RECONNECT_BURST_ATTEMPTS = 2
DEVICE_TCP_RECONNECT_BURST_GAP_SECONDS = 3.5
# Offline recovery is owned by heartbeat. Scheduler must not SYN-flood.
DEVICE_OFFLINE_RECONNECT_POLL_SECONDS = 60
# After hard LAN unreachable, give ARP / Wi-Fi time before the next SYN.
DEVICE_HARD_LAN_BACKOFF_SECONDS = 20.0
# After CMD_SET_TIME the terminal drops TCP briefly — suppress offline flaps.
DEVICE_TIME_SYNC_GRACE_SECONDS = 90.0

# Independent API health monitor (dashboard must only consume cached state).
DEFAULT_API_HEALTH_INTERVAL_SECONDS = 30
# UI refresh matches the default device poll so the page shows real punches
# without blocking the operator on a 4-second SQLite/dashboard rebuild.
DEFAULT_DASHBOARD_REFRESH_MS = 60_000
DEFAULT_SYNC_WATCHDOG_MS = 90_000
# Production measurements (2026-08-26, https://brixigo.com/api/health, 15 cold samples):
#   min=0.45s  avg=1.04s  max=3.01s
# Prior live diagnostics: min=1.59s avg=2.95s max=5.55s
# A 1.0s "healthy" cutoff therefore false-alarms on a normal internet path.
API_LATENCY_HEALTHY_MS = 1500.0
API_LATENCY_ACCEPTABLE_MS = 3000.0
API_LATENCY_NORMAL_MS = API_LATENCY_ACCEPTABLE_MS  # alias
API_LATENCY_SLOW_MS = 10000.0
API_SLOW_CONSECUTIVE_THRESHOLD = 3
API_FAILURE_CONSECUTIVE_THRESHOLD = 3
API_RECOVERY_CONSECUTIVE_THRESHOLD = 2
API_ACCEPTABLE_CONSECUTIVE_THRESHOLD = 3
API_HEALTH_PATHS = ("/liveness", "/api/health")

# Exponential backoff with jitter (seconds)
RETRY_BACKOFF_SCHEDULE = (2.0, 5.0, 10.0, 20.0, 30.0)
RETRY_BACKOFF_CAP_SECONDS = 45.0
DEFAULT_COMMAND_POLL_INTERVAL_SECONDS = 30
DEFAULT_REMOTE_CONFIG_INTERVAL_SECONDS = 300
DEFAULT_QUEUE_ALERT_THRESHOLD = 5000
DEFAULT_MAX_WORKER_THREADS = 16
DEFAULT_EMPLOYEE_SYNC_INTERVAL_SECONDS = 120
DEFAULT_TIME_SYNC_INTERVAL_SECONDS = 300
DEFAULT_TIME_DRIFT_THRESHOLD_SECONDS = 30
DEFAULT_BACKUP_INTERVAL_SECONDS = 86400
DEFAULT_INVENTORY_PUSH_INTERVAL_SECONDS = 600

MAX_UPLOAD_RETRIES = 8
UPLOAD_BACKOFF_BASE_SECONDS = 2.0
UPLOAD_BACKOFF_MAX_SECONDS = 300.0

DISCOVERY_PORTS = (4370, 4371)
DISCOVERY_CONNECT_TIMEOUT = 0.8
DISCOVERY_MAX_WORKERS = 64
# Re-scan the local LAN for a moved ZK/eSSL terminal after TCP 10065/10051.
LAN_RELOCATE_COOLDOWN_SECONDS = 60.0
RELOCATE_TCP_TIMEOUT = 0.8
RELOCATE_SCAN_BUDGET_SECONDS = 12.0

CONFIG_FILENAME = "connector.config.enc"
DB_FILENAME = "connector.db"
LOG_DIRNAME = "logs"
DATA_DIRNAME = "data"
BACKUP_DIRNAME = "backups"
TEMPLATE_DIRNAME = "templates"

SUPPORTED_MANUFACTURERS = (
    "essl",
    "zkteco",
    "anviz",
    "matrix",
    "suprema",
    "realtime",
    "hikvision",
)

COMMUNICATION_TYPES = ("tcp", "udp", "http", "adms", "sdk")
DEVICE_STATUSES = (
    "online",
    "offline",
    "error",
    "disabled",
    "unknown",
    "degraded",
    "timeout",
    "auth_error",
)

REMOTE_COMMAND_TYPES = (
    "restart_device",
    "sync_time",
    "download_attendance",
    "clear_attendance",
    "enable_device",
    "disable_device",
    "refresh_device_info",
    "test_connection",
    # Phase 3
    "sync_employees",
    "sync_templates",
    "download_users",
    "upload_users",
    "delete_users",
    "bulk_sync_users",
    "backup_device",
    "restore_device",
    "provision_device",
    "firmware_upgrade",
    "firmware_rollback",
    "push_inventory",
    "remote_log_download",
    "remote_diagnostics",
    "remote_restart_connector",
    "remote_cache_cleanup",
    "remote_db_backup",
    "remote_db_restore",
    "generate_report",
)

ALERT_TYPES = (
    "device_offline",
    "internet_lost",
    "queue_growing",
    "upload_failed",
    "low_storage",
    "connector_restarted",
    "api_auth_failed",
    # Phase 3
    "connector_offline",
    "queue_overflow",
    "template_failure",
    "sync_failure",
    "firmware_update_available",
    "backup_failed",
    "restore_failed",
    "authentication_failure",
    "high_temperature",
    "low_battery",
    "capacity_warning",
    "clock_mismatch",
)

TEMPLATE_TYPES = ("fingerprint", "face", "palm", "rfid", "pin")
EMPLOYEE_SYNC_ACTIONS = ("create", "update", "delete", "deactivate", "reactivate")
BACKUP_DIRNAME_DEFAULT = "backups"
