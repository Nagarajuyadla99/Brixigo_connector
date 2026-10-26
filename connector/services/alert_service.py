"""Alert generation for enterprise operational events."""

from __future__ import annotations

import logging
from typing import Optional

from api.cloud_client import CloudClient
from models import (
    Alert,
    AlertSeverity,
    AlertType,
    Device,
    DeviceHealthSnapshot,
    new_id,
    utcnow,
)
from storage.repositories.enterprise_repo import AlertRepository, AuditRepository

logger = logging.getLogger("brixigo")


class AlertService:
    def __init__(
        self,
        alerts: AlertRepository,
        audit: AuditRepository,
        cloud: Optional[CloudClient] = None,
        *,
        queue_threshold: int = 5000,
    ) -> None:
        self._alerts = alerts
        self._audit = audit
        self._cloud = cloud
        self.queue_threshold = queue_threshold

    def raise_alert(
        self,
        *,
        alert_type: str,
        title: str,
        message: str,
        severity: str = AlertSeverity.WARNING.value,
        device_id: str = "",
        details: dict | None = None,
        dedupe_seconds: int = 300,
    ) -> Optional[Alert]:
        if self._alerts.has_recent(
            alert_type, device_id=device_id, within_seconds=dedupe_seconds
        ):
            return None
        alert = Alert(
            id=new_id(),
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            device_id=device_id,
            created_at=utcnow().isoformat(),
            details=details or {},
        )
        self._alerts.add(alert)
        self._audit.add(
            action="alert_raised",
            actor="system",
            success=True,
            message=title,
            resource_type="alert",
            resource_id=alert.id,
            details={"alert_type": alert_type, "device_id": device_id},
        )
        self._push(alert)
        logger.warning("ALERT [%s] %s — %s", alert_type, title, message)
        return alert

    def device_offline(self, device: Device, snap: DeviceHealthSnapshot) -> None:
        self.raise_alert(
            alert_type=AlertType.DEVICE_OFFLINE.value,
            title=f"Device offline: {device.name}",
            message=snap.message or f"{device.ip_address}:{device.port} unreachable",
            severity=AlertSeverity.CRITICAL.value,
            device_id=device.id,
            details={"ip": device.ip_address, "ping_ms": snap.ping_ms},
            dedupe_seconds=600,
        )

    def low_storage(self, device: Device, snap: DeviceHealthSnapshot) -> None:
        self.raise_alert(
            alert_type=AlertType.LOW_STORAGE.value,
            title=f"Low storage: {device.name}",
            message=f"Log storage at {snap.storage_usage_percent:.1f}%",
            severity=AlertSeverity.WARNING.value,
            device_id=device.id,
            details={"storage_usage_percent": snap.storage_usage_percent},
            dedupe_seconds=3600,
        )

    def internet_lost(self) -> None:
        self.raise_alert(
            alert_type=AlertType.INTERNET_LOST.value,
            title="Internet connectivity lost",
            message="Connector cannot reach Brixigo Cloud",
            severity=AlertSeverity.CRITICAL.value,
            dedupe_seconds=300,
        )

    def queue_growing(self, queue_size: int) -> None:
        if queue_size < self.queue_threshold:
            return
        self.raise_alert(
            alert_type=AlertType.QUEUE_GROWING.value,
            title="Upload queue growing",
            message=f"Pending queue size is {queue_size} (threshold {self.queue_threshold})",
            severity=AlertSeverity.WARNING.value,
            details={"queue_size": queue_size, "threshold": self.queue_threshold},
            dedupe_seconds=600,
        )

    def upload_failed(self, message: str, device_id: str = "") -> None:
        self.raise_alert(
            alert_type=AlertType.UPLOAD_FAILED.value,
            title="Upload failed",
            message=message,
            severity=AlertSeverity.WARNING.value,
            device_id=device_id,
            dedupe_seconds=300,
        )

    def connector_restarted(self) -> None:
        self.raise_alert(
            alert_type=AlertType.CONNECTOR_RESTARTED.value,
            title="Connector restarted",
            message="Connector engine started or restarted",
            severity=AlertSeverity.INFO.value,
            dedupe_seconds=60,
        )

    def api_auth_failed(self, message: str) -> None:
        self.raise_alert(
            alert_type=AlertType.API_AUTH_FAILED.value,
            title="API authentication failed",
            message=message,
            severity=AlertSeverity.CRITICAL.value,
            dedupe_seconds=300,
        )

    def sync_failure(self, message: str, device_id: str = "") -> None:
        self.raise_alert(
            alert_type=AlertType.SYNC_FAILURE.value,
            title="Synchronization failure",
            message=message,
            severity=AlertSeverity.WARNING.value,
            device_id=device_id,
            dedupe_seconds=300,
        )

    def template_failure(self, message: str, device_id: str = "") -> None:
        self.raise_alert(
            alert_type=AlertType.TEMPLATE_FAILURE.value,
            title="Template failure",
            message=message,
            severity=AlertSeverity.WARNING.value,
            device_id=device_id,
            dedupe_seconds=300,
        )

    def backup_failed(self, message: str, device_id: str = "") -> None:
        self.raise_alert(
            alert_type=AlertType.BACKUP_FAILED.value,
            title="Backup failed",
            message=message,
            severity=AlertSeverity.WARNING.value,
            device_id=device_id,
            dedupe_seconds=600,
        )

    def firmware_update_available(self, message: str, device_id: str = "") -> None:
        self.raise_alert(
            alert_type=AlertType.FIRMWARE_UPDATE_AVAILABLE.value,
            title="Firmware update available",
            message=message,
            severity=AlertSeverity.INFO.value,
            device_id=device_id,
            dedupe_seconds=3600,
        )

    def clock_mismatch(
        self,
        *,
        device_name: str,
        device_id: str,
        device_time: str,
        server_time: str,
        difference_label: str,
        details: dict | None = None,
    ) -> Optional[Alert]:
        return self.raise_alert(
            alert_type=AlertType.CLOCK_MISMATCH.value,
            title=f"Device clock mismatch: {device_name}",
            message=(
                f"{device_name} clock is {device_time}. "
                f"Server clock is {server_time}. Difference: {difference_label}."
            ),
            severity=AlertSeverity.WARNING.value,
            device_id=device_id,
            details=details or {
                "device_time": device_time,
                "server_time": server_time,
                "difference": difference_label,
            },
            dedupe_seconds=3600,
        )

    def recent(self, limit: int = 40):
        return self._alerts.recent(limit=limit)

    def acknowledge(self, alert_id: str) -> None:
        self._alerts.acknowledge(alert_id)

    def _push(self, alert: Alert) -> None:
        if not self._cloud or not self._cloud.settings.connector_id:
            return
        try:
            self._cloud.push_alert(
                {
                    "id": alert.id,
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "title": alert.title,
                    "message": alert.message,
                    "device_id": alert.device_id,
                    "created_at": alert.created_at,
                    "details": alert.details,
                }
            )
        except Exception as exc:
            logger.debug("Alert cloud push skipped/failed: %s", exc)
