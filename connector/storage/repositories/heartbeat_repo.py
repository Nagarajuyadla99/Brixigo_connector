"""Heartbeat snapshot repository with Phase 2 metric fields."""

from __future__ import annotations

import json
from typing import Any, Optional

from models import HeartbeatSnapshot, utcnow
from storage.database import Database


class HeartbeatRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_local(self, snap: HeartbeatSnapshot, *, sent: bool = False) -> None:
        payload = {
            "connector_version": snap.connector_version,
            "status": snap.status,
            "cpu_percent": snap.cpu_percent,
            "ram_percent": snap.ram_percent,
            "disk_percent": snap.disk_percent,
            "connected_devices": snap.connected_devices,
            "offline_devices": snap.offline_devices,
            "last_sync": snap.last_sync,
            "pending_queue": snap.pending_queue,
            "failed_uploads": snap.failed_uploads,
            "today_punches": snap.today_punches,
            "average_upload_ms": snap.average_upload_ms,
            "last_error": snap.last_error,
            "last_restart_at": snap.last_restart_at,
            "created_at": snap.created_at,
        }
        now = utcnow().isoformat()
        self._db.execute(
            """
            UPDATE heartbeat SET
                connector_version=?, status=?, cpu_percent=?, ram_percent=?,
                disk_percent=?, connected_devices=?, offline_devices=?,
                last_sync=?, pending_queue=?,
                failed_uploads=?, today_punches=?, average_upload_ms=?,
                last_error=?, last_restart_at=?,
                last_sent_at=CASE WHEN ? THEN ? ELSE last_sent_at END,
                last_payload_json=?, updated_at=?
            WHERE id=1
            """,
            (
                snap.connector_version,
                snap.status,
                snap.cpu_percent,
                snap.ram_percent,
                snap.disk_percent,
                snap.connected_devices,
                snap.offline_devices,
                snap.last_sync,
                snap.pending_queue,
                snap.failed_uploads,
                snap.today_punches,
                snap.average_upload_ms,
                snap.last_error,
                snap.last_restart_at,
                1 if sent else 0,
                now,
                json.dumps(payload),
                now,
            ),
        )

    def get_latest(self) -> Optional[dict[str, Any]]:
        row = self._db.fetchone("SELECT * FROM heartbeat WHERE id=1")
        if not row:
            return None
        return dict(row)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            "SELECT * FROM heartbeat_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]
