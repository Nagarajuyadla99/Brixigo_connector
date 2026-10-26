"""Remote configuration download and apply service."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from api.cloud_client import CloudAPIError, CloudClient
from config.settings_manager import SettingsManager
from storage.repositories.enterprise_repo import AuditRepository

logger = logging.getLogger("brixigo.service")


class RemoteConfigService:
    """
    Periodically downloads connector configuration from Brixigo Cloud.
    No manual config editing is required once cloud policy is published.
    """

    def __init__(
        self,
        cloud: CloudClient,
        settings: SettingsManager,
        audit: AuditRepository,
        *,
        interval_seconds: int = 300,
        on_applied: Optional[Callable[[], None]] = None,
    ) -> None:
        self._cloud = cloud
        self._settings = settings
        self._audit = audit
        self.interval_seconds = max(60, interval_seconds)
        self.on_applied = on_applied
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_config: dict = {}
        self.last_error: str = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-remote-config", daemon=True
        )
        self._thread.start()
        logger.info("Remote config service started interval=%ss", self.interval_seconds)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def refresh(self) -> dict:
        if not self._cloud.settings.connector_id:
            self.last_error = "connector_id not configured"
            return {}
        try:
            remote = self._cloud.download_configuration()
            self.last_config = remote if isinstance(remote, dict) else {}
            self.last_error = ""
            # Support either flat config or nested {"settings": {...}}
            payload = self.last_config.get("settings", self.last_config)
            if isinstance(payload, dict):
                before = self._settings.as_model()
                self._settings.apply_remote_config(payload)
                after = self._settings.as_model()
                self._audit.add(
                    action="remote_config_applied",
                    actor="cloud",
                    success=True,
                    message="Remote configuration applied",
                    resource_type="config",
                    resource_id=self._cloud.settings.connector_id,
                    details={
                        "heartbeat_interval": after.heartbeat_interval,
                        "upload_batch_size": after.upload_batch_size,
                        "retry_count": after.retry_count,
                        "logging_level": after.logging_level,
                        "previous_batch": before.upload_batch_size,
                    },
                )
                if self.on_applied:
                    self.on_applied()
            # Optional per-device config map
            device_cfg = self.last_config.get("devices")
            if isinstance(device_cfg, dict) and self.on_applied:
                # Device-level application is handled by caller via on_applied reload.
                pass
            return self.last_config
        except CloudAPIError as exc:
            self.last_error = str(exc)
            logger.warning("Remote config download failed: %s", exc)
            return {}
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("Remote config error: %s", exc)
            return {}

    def _loop(self) -> None:
        # Apply once shortly after start, then on interval.
        self.refresh()
        while not self._stop.is_set():
            self._stop.wait(self.interval_seconds)
            if self._stop.is_set():
                break
            self.refresh()
