"""Alerts, audit logs, discovery cache, and upload metrics repositories."""

from __future__ import annotations

import json
from typing import Any, Optional

from models import (
    Alert,
    AuditLog,
    DiscoveredDevice,
    DiscoveryDecision,
    new_id,
    utcnow,
)
from storage.database import Database


class AlertRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, alert: Alert) -> Alert:
        if not alert.id:
            alert.id = new_id()
        self._db.execute(
            """
            INSERT INTO alerts (
                id, alert_type, severity, title, message, device_id,
                acknowledged, created_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert.id,
                alert.alert_type,
                alert.severity,
                alert.title,
                alert.message[:2000],
                alert.device_id,
                1 if alert.acknowledged else 0,
                alert.created_at or utcnow().isoformat(),
                json.dumps(alert.details or {}),
            ),
        )
        self._db.execute(
            """
            DELETE FROM alerts WHERE id IN (
                SELECT id FROM alerts ORDER BY created_at DESC LIMIT -1 OFFSET 2000
            )
            """
        )
        return alert

    def recent(self, limit: int = 100, *, unacked_only: bool = False) -> list[Alert]:
        sql = "SELECT * FROM alerts"
        if unacked_only:
            sql += " WHERE acknowledged = 0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        return [self._row_to_alert(r) for r in self._db.fetchall(sql, (limit,))]

    def acknowledge(self, alert_id: str) -> None:
        self._db.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))

    def has_recent(
        self,
        alert_type: str,
        *,
        device_id: str = "",
        within_seconds: int = 300,
    ) -> bool:
        row = self._db.fetchone(
            """
            SELECT id FROM alerts
            WHERE alert_type = ?
              AND device_id = ?
              AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC LIMIT 1
            """,
            (alert_type, device_id, f"-{int(within_seconds)} seconds"),
        )
        return row is not None

    def _row_to_alert(self, row) -> Alert:
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        return Alert(
            id=row["id"],
            alert_type=row["alert_type"],
            severity=row["severity"],
            title=row["title"],
            message=row["message"],
            device_id=row["device_id"] or "",
            acknowledged=bool(row["acknowledged"]),
            created_at=row["created_at"],
            details=details if isinstance(details, dict) else {},
        )


class AuditRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        *,
        action: str,
        actor: str,
        success: bool,
        message: str,
        resource_type: str = "",
        resource_id: str = "",
        details: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            id=new_id(),
            action=action,
            actor=actor or "system",
            success=success,
            message=message[:2000],
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
        self._db.execute(
            """
            INSERT INTO audit_logs (
                id, action, actor, success, message, resource_type,
                resource_id, created_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.action,
                entry.actor,
                1 if entry.success else 0,
                entry.message,
                entry.resource_type,
                entry.resource_id,
                entry.created_at,
                json.dumps(entry.details),
            ),
        )
        self._db.execute(
            """
            DELETE FROM audit_logs WHERE id IN (
                SELECT id FROM audit_logs ORDER BY created_at DESC LIMIT -1 OFFSET 10000
            )
            """
        )
        return entry

    def recent(self, limit: int = 200) -> list[AuditLog]:
        rows = self._db.fetchall(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        result: list[AuditLog] = []
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                details = {}
            result.append(
                AuditLog(
                    id=row["id"],
                    action=row["action"],
                    actor=row["actor"],
                    success=bool(row["success"]),
                    message=row["message"],
                    resource_type=row["resource_type"] or "",
                    resource_id=row["resource_id"] or "",
                    created_at=row["created_at"],
                    details=details if isinstance(details, dict) else {},
                )
            )
        return result


class DiscoveryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def is_ignored(self, ip_address: str, port: int) -> bool:
        row = self._db.fetchone(
            "SELECT 1 FROM discovery_ignore WHERE ip_address = ? AND port = ?",
            (ip_address, int(port)),
        )
        return row is not None

    def ignore(self, ip_address: str, port: int, reason: str = "") -> None:
        self._db.execute(
            """
            INSERT OR REPLACE INTO discovery_ignore (ip_address, port, ignored_at, reason)
            VALUES (?, ?, ?, ?)
            """,
            (ip_address, int(port), utcnow().isoformat(), reason),
        )
        self._db.execute(
            """
            UPDATE discovery_cache SET decision = ?
            WHERE ip_address = ? AND port = ?
            """,
            (DiscoveryDecision.IGNORED.value, ip_address, int(port)),
        )

    def unignore(self, ip_address: str, port: int) -> None:
        self._db.execute(
            "DELETE FROM discovery_ignore WHERE ip_address = ? AND port = ?",
            (ip_address, int(port)),
        )

    def save_discovered(self, devices: list[DiscoveredDevice]) -> None:
        for d in devices:
            if self.is_ignored(d.ip_address, d.port):
                d.decision = DiscoveryDecision.IGNORED.value
            self._db.execute(
                """
                INSERT INTO discovery_cache (
                    ip_address, port, manufacturer, model, firmware, mac_address,
                    sdk_version, device_name, serial_number, communication_type,
                    latency_ms, decision, discovered_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ip_address, port) DO UPDATE SET
                    manufacturer=excluded.manufacturer,
                    model=excluded.model,
                    firmware=excluded.firmware,
                    mac_address=excluded.mac_address,
                    sdk_version=excluded.sdk_version,
                    device_name=excluded.device_name,
                    serial_number=excluded.serial_number,
                    communication_type=excluded.communication_type,
                    latency_ms=excluded.latency_ms,
                    decision=CASE
                        WHEN discovery_cache.decision = 'ignored' THEN 'ignored'
                        WHEN discovery_cache.decision = 'added' THEN 'added'
                        ELSE excluded.decision
                    END,
                    discovered_at=excluded.discovered_at,
                    raw_json=excluded.raw_json
                """,
                (
                    d.ip_address,
                    int(d.port),
                    d.manufacturer,
                    d.model,
                    d.firmware,
                    d.mac_address,
                    d.sdk_version,
                    d.device_name,
                    d.serial_number,
                    d.communication_type,
                    d.latency_ms,
                    d.decision,
                    d.discovered_at,
                    json.dumps(d.raw or {}),
                ),
            )

    def mark_added(self, ip_address: str, port: int) -> None:
        self._db.execute(
            """
            UPDATE discovery_cache SET decision = ?
            WHERE ip_address = ? AND port = ?
            """,
            (DiscoveryDecision.ADDED.value, ip_address, int(port)),
        )

    def list_pending(self) -> list[DiscoveredDevice]:
        rows = self._db.fetchall(
            """
            SELECT * FROM discovery_cache
            WHERE decision = ?
            ORDER BY discovered_at DESC
            """,
            (DiscoveryDecision.PENDING.value,),
        )
        return [self._row_to_discovered(r) for r in rows]

    def list_all(self) -> list[DiscoveredDevice]:
        rows = self._db.fetchall(
            "SELECT * FROM discovery_cache ORDER BY discovered_at DESC"
        )
        return [self._row_to_discovered(r) for r in rows]

    def get(self, ip_address: str, port: int) -> Optional[DiscoveredDevice]:
        row = self._db.fetchone(
            "SELECT * FROM discovery_cache WHERE ip_address = ? AND port = ?",
            (ip_address, int(port)),
        )
        return self._row_to_discovered(row) if row else None

    def clear_pending(self) -> None:
        self._db.execute(
            "DELETE FROM discovery_cache WHERE decision = ?",
            (DiscoveryDecision.PENDING.value,),
        )

    def _row_to_discovered(self, row) -> DiscoveredDevice:
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        return DiscoveredDevice(
            ip_address=row["ip_address"],
            port=int(row["port"]),
            manufacturer=row["manufacturer"] or "",
            model=row["model"] or "",
            firmware=row["firmware"] or "",
            mac_address=row["mac_address"] or "",
            sdk_version=row["sdk_version"] or "",
            device_name=row["device_name"] or "",
            serial_number=row["serial_number"] or "",
            communication_type=row["communication_type"] or "tcp",
            latency_ms=float(row["latency_ms"] or 0),
            decision=row["decision"] or DiscoveryDecision.PENDING.value,
            discovered_at=row["discovered_at"],
            raw=raw if isinstance(raw, dict) else {},
        )


class UploadMetricsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record_success(self, count: int, duration_ms: float) -> None:
        self._db.execute(
            """
            UPDATE upload_metrics SET
                total_uploaded = total_uploaded + ?,
                total_upload_ms = total_upload_ms + ?,
                upload_samples = upload_samples + 1,
                updated_at = ?
            WHERE id = 1
            """,
            (count, duration_ms, utcnow().isoformat()),
        )

    def record_failure(self, count: int, error: str) -> None:
        self._db.execute(
            """
            UPDATE upload_metrics SET
                total_failed = total_failed + ?,
                last_error = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (count, error[:2000], utcnow().isoformat()),
        )

    def get(self) -> dict[str, Any]:
        row = self._db.fetchone("SELECT * FROM upload_metrics WHERE id = 1")
        if not row:
            return {
                "total_uploaded": 0,
                "total_failed": 0,
                "average_upload_ms": 0.0,
                "last_error": "",
            }
        samples = int(row["upload_samples"] or 0)
        avg = float(row["total_upload_ms"] or 0) / samples if samples else 0.0
        return {
            "total_uploaded": int(row["total_uploaded"] or 0),
            "total_failed": int(row["total_failed"] or 0),
            "average_upload_ms": avg,
            "last_error": row["last_error"] or "",
            "upload_samples": samples,
            "updated_at": row["updated_at"],
        }
