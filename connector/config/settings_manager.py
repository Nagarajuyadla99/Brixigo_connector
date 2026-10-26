"""Encrypted JSON configuration manager with Phase 2 remote settings."""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.constants import (
    DEFAULT_BACKUP_INTERVAL_SECONDS,
    DEFAULT_CLOUD_BASE_URL,
    DEFAULT_COMMAND_POLL_INTERVAL_SECONDS,
    DEFAULT_EMPLOYEE_SYNC_INTERVAL_SECONDS,
    DEFAULT_ENABLE_ACCESS_CONTROL,
    DEFAULT_ENABLE_FIRMWARE_FLASH,
    DEFAULT_HEALTH_INTERVAL_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_INVENTORY_PUSH_INTERVAL_SECONDS,
    DEFAULT_MAX_WORKER_THREADS,
    DEFAULT_QUEUE_ALERT_THRESHOLD,
    DEFAULT_REMOTE_CONFIG_INTERVAL_SECONDS,
    DEFAULT_TIME_DRIFT_THRESHOLD_SECONDS,
    DEFAULT_TIME_SYNC_INTERVAL_SECONDS,
    DEFAULT_UPLOAD_BATCH_SIZE,
    MAX_UPLOAD_RETRIES,
)
from models import ConnectorSettings
from security.encryption import SecretBox

logger = logging.getLogger("brixigo")


DEFAULTS: dict[str, Any] = {
    "connector_id": "",
    "organisation_id": "",
    "api_key": "",
    "jwt_token": "",
    "cloud_base_url": DEFAULT_CLOUD_BASE_URL,
    "heartbeat_interval": DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    "upload_batch_size": DEFAULT_UPLOAD_BATCH_SIZE,
    "auto_start": True,
    "auto_update": False,
    "verify_ssl": True,
    "proxy_url": "",
    "device_connect_timeout": 12.0,
    "device_read_timeout": 30.0,
    "max_worker_threads": DEFAULT_MAX_WORKER_THREADS,
    "retry_count": 3,
    "request_timeout": 30.0,
    "logging_level": "INFO",
    "health_interval": DEFAULT_HEALTH_INTERVAL_SECONDS,
    "command_poll_interval": DEFAULT_COMMAND_POLL_INTERVAL_SECONDS,
    "remote_config_interval": DEFAULT_REMOTE_CONFIG_INTERVAL_SECONDS,
    "queue_alert_threshold": DEFAULT_QUEUE_ALERT_THRESHOLD,
    "command_signing_secret": "",
    "ca_bundle_path": "",
    "max_upload_retries": MAX_UPLOAD_RETRIES,
    "cleanup_uploaded_days": 14,
    "cleanup_log_days": 30,
    "employee_sync_interval": DEFAULT_EMPLOYEE_SYNC_INTERVAL_SECONDS,
    "time_sync_interval": DEFAULT_TIME_SYNC_INTERVAL_SECONDS,
    "time_drift_threshold_seconds": DEFAULT_TIME_DRIFT_THRESHOLD_SECONDS,
    "backup_interval": DEFAULT_BACKUP_INTERVAL_SECONDS,
    "inventory_push_interval": DEFAULT_INVENTORY_PUSH_INTERVAL_SECONDS,
    "client_cert_path": "",
    "client_key_path": "",
    "mtls_enabled": False,
    "role": "admin",
    "enable_firmware_flash": DEFAULT_ENABLE_FIRMWARE_FLASH,
    "enable_access_control": DEFAULT_ENABLE_ACCESS_CONTROL,
    "http_connect_timeout": 10.0,
    "http_read_timeout": 30.0,
    "device_heartbeat_interval": 12,
    "api_health_interval": 30,
}


class SettingsManager:
    def __init__(self, config_path: Path, secrets: SecretBox) -> None:
        self._path = config_path
        self._secrets = secrets
        self._data: dict[str, Any] = deepcopy(DEFAULTS)
        self._loaded_ok = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def load_error(self) -> str | None:
        return None if self._loaded_ok else "configuration not loaded"

    def load(self) -> ConnectorSettings:
        if not self._path.exists():
            self._data = deepcopy(DEFAULTS)
            self.save()
            self._loaded_ok = True
            return self.as_model()

        try:
            token = self._path.read_text(encoding="utf-8").strip()
            if not token:
                logger.warning("Config file is empty — initializing defaults")
                self._data = deepcopy(DEFAULTS)
            else:
                self._data = {**DEFAULTS, **self._secrets.decrypt_json(token)}
            self._loaded_ok = True
        except Exception as exc:
            # Fail closed: never silently replace a corrupt/encrypted file with defaults
            # (that would overwrite good config on the next save()).
            if self._loaded_ok:
                logger.error(
                    "Config reload failed (%s); retaining last good in-memory settings",
                    exc,
                )
                return self.as_model()
            raise RuntimeError(
                f"Cannot decrypt connector configuration at {self._path}. "
                f"Verify BRIXIGO_CONNECTOR_MASTER_KEY matches the key used to encrypt "
                f"this file, or restore a backup from the data folder. "
                f"Underlying error: {exc}"
            ) from exc
        return self.as_model()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**DEFAULTS, **self._data}
        token = self._secrets.encrypt_json(payload)
        self._path.write_text(token, encoding="utf-8")
        self._loaded_ok = True

    def as_model(self) -> ConnectorSettings:
        return ConnectorSettings(
            connector_id=str(self._data.get("connector_id") or ""),
            organisation_id=str(self._data.get("organisation_id") or ""),
            api_key=str(self._data.get("api_key") or ""),
            jwt_token=str(self._data.get("jwt_token") or ""),
            cloud_base_url=str(
                self._data.get("cloud_base_url") or DEFAULT_CLOUD_BASE_URL
            ).rstrip("/"),
            heartbeat_interval=int(
                self._data.get("heartbeat_interval") or DEFAULT_HEARTBEAT_INTERVAL_SECONDS
            ),
            upload_batch_size=int(
                self._data.get("upload_batch_size") or DEFAULT_UPLOAD_BATCH_SIZE
            ),
            auto_start=bool(self._data.get("auto_start", True)),
            auto_update=bool(self._data.get("auto_update", True)),
            verify_ssl=bool(self._data.get("verify_ssl", True)),
            proxy_url=str(self._data.get("proxy_url") or ""),
            device_connect_timeout=float(self._data.get("device_connect_timeout") or 12.0),
            device_read_timeout=float(self._data.get("device_read_timeout") or 30.0),
            max_worker_threads=int(
                self._data.get("max_worker_threads") or DEFAULT_MAX_WORKER_THREADS
            ),
            retry_count=int(self._data.get("retry_count") or 3),
            request_timeout=float(self._data.get("request_timeout") or 30.0),
            logging_level=str(self._data.get("logging_level") or "INFO").upper(),
            health_interval=int(
                self._data.get("health_interval") or DEFAULT_HEALTH_INTERVAL_SECONDS
            ),
            command_poll_interval=int(
                self._data.get("command_poll_interval")
                or DEFAULT_COMMAND_POLL_INTERVAL_SECONDS
            ),
            remote_config_interval=int(
                self._data.get("remote_config_interval")
                or DEFAULT_REMOTE_CONFIG_INTERVAL_SECONDS
            ),
            queue_alert_threshold=int(
                self._data.get("queue_alert_threshold") or DEFAULT_QUEUE_ALERT_THRESHOLD
            ),
            command_signing_secret=str(self._data.get("command_signing_secret") or ""),
            ca_bundle_path=str(self._data.get("ca_bundle_path") or ""),
            max_upload_retries=int(self._data.get("max_upload_retries") or MAX_UPLOAD_RETRIES),
            cleanup_uploaded_days=int(self._data.get("cleanup_uploaded_days") or 14),
            cleanup_log_days=int(self._data.get("cleanup_log_days") or 30),
            employee_sync_interval=int(
                self._data.get("employee_sync_interval")
                or DEFAULT_EMPLOYEE_SYNC_INTERVAL_SECONDS
            ),
            time_sync_interval=int(
                self._data.get("time_sync_interval") or DEFAULT_TIME_SYNC_INTERVAL_SECONDS
            ),
            time_drift_threshold_seconds=int(
                self._data.get("time_drift_threshold_seconds")
                or DEFAULT_TIME_DRIFT_THRESHOLD_SECONDS
            ),
            backup_interval=int(
                self._data.get("backup_interval") or DEFAULT_BACKUP_INTERVAL_SECONDS
            ),
            inventory_push_interval=int(
                self._data.get("inventory_push_interval")
                or DEFAULT_INVENTORY_PUSH_INTERVAL_SECONDS
            ),
            client_cert_path=str(self._data.get("client_cert_path") or ""),
            client_key_path=str(self._data.get("client_key_path") or ""),
            mtls_enabled=bool(self._data.get("mtls_enabled", False)),
            role=str(self._data.get("role") or "admin"),
            enable_firmware_flash=bool(
                self._data.get("enable_firmware_flash", DEFAULT_ENABLE_FIRMWARE_FLASH)
            ),
            enable_access_control=bool(
                self._data.get("enable_access_control", DEFAULT_ENABLE_ACCESS_CONTROL)
            ),
            http_connect_timeout=float(self._data.get("http_connect_timeout") or 10.0),
            http_read_timeout=float(self._data.get("http_read_timeout") or 30.0),
            device_heartbeat_interval=int(self._data.get("device_heartbeat_interval") or 12),
            api_health_interval=int(self._data.get("api_health_interval") or 30),
        )

    def update(self, **kwargs: Any) -> ConnectorSettings:
        for key, value in kwargs.items():
            if key in DEFAULTS:
                self._data[key] = value
        self.save()
        return self.as_model()

    def apply_remote_config(self, remote: dict[str, Any]) -> ConnectorSettings:
        """
        Apply cloud-delivered configuration safely.
        Only known keys are accepted; secrets require explicit presence.
        """
        allowed = {
            "heartbeat_interval",
            "upload_batch_size",
            "retry_count",
            "request_timeout",
            "logging_level",
            "device_connect_timeout",
            "device_read_timeout",
            "max_worker_threads",
            "health_interval",
            "command_poll_interval",
            "remote_config_interval",
            "queue_alert_threshold",
            "proxy_url",
            "auto_update",
            "max_upload_retries",
            "cleanup_uploaded_days",
            "cleanup_log_days",
            "ca_bundle_path",
            "employee_sync_interval",
            "time_sync_interval",
            "time_drift_threshold_seconds",
            "backup_interval",
            "inventory_push_interval",
            "client_cert_path",
            "client_key_path",
            "mtls_enabled",
            "enable_access_control",
            "http_connect_timeout",
            "http_read_timeout",
            "device_heartbeat_interval",
            "api_health_interval",
        }
        # Security-sensitive keys are applied only under strict rules
        changed: dict[str, Any] = {}
        for key in allowed:
            if key in remote and remote[key] is not None:
                changed[key] = remote[key]
                self._data[key] = remote[key]

        # verify_ssl: remote may only force-enable, never disable
        if "verify_ssl" in remote and remote["verify_ssl"] is not None:
            if bool(remote["verify_ssl"]) is True:
                changed["verify_ssl"] = True
                self._data["verify_ssl"] = True
            else:
                logger.warning("Ignored remote config attempt to disable verify_ssl")

        # command_signing_secret: only accept non-empty updates
        if remote.get("command_signing_secret"):
            changed["command_signing_secret"] = "***"
            self._data["command_signing_secret"] = remote["command_signing_secret"]

        # firmware flash must stay local/opt-in — never enable via remote alone
        if remote.get("enable_firmware_flash") is True and not self._data.get(
            "enable_firmware_flash"
        ):
            logger.warning("Ignored remote config attempt to enable firmware flash")
        elif "enable_firmware_flash" in remote and remote["enable_firmware_flash"] is False:
            changed["enable_firmware_flash"] = False
            self._data["enable_firmware_flash"] = False

        if changed:
            self.save()
            logger.info("Applied remote configuration keys=%s", sorted(changed.keys()))
        return self.as_model()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
