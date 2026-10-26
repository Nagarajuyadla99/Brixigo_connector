"""Sync log repository."""

from __future__ import annotations

import json

from models import SyncLog, new_id, utcnow
from storage.database import Database


class SyncLogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        *,
        device_id: str,
        action: str,
        success: bool,
        message: str,
        records_count: int = 0,
        details: dict | None = None,
    ) -> SyncLog:
        entry = SyncLog(
            id=new_id(),
            device_id=device_id,
            action=action,
            success=success,
            message=message[:2000],
            records_count=records_count,
            created_at=utcnow().isoformat(),
            details=details or {},
        )
        self._db.execute(
            """
            INSERT INTO sync_logs (
                id, device_id, action, success, message, records_count, created_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.device_id,
                entry.action,
                1 if entry.success else 0,
                entry.message,
                entry.records_count,
                entry.created_at,
                json.dumps(entry.details),
            ),
        )
        # Keep table bounded
        self._db.execute(
            """
            DELETE FROM sync_logs WHERE id IN (
                SELECT id FROM sync_logs ORDER BY created_at DESC LIMIT -1 OFFSET 5000
            )
            """
        )
        return entry

    def recent(self, limit: int = 100) -> list[SyncLog]:
        return self._rows_to_logs(
            self._db.fetchall(
                "SELECT * FROM sync_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        )

    def recent_for_device(self, device_id: str, limit: int = 50) -> list[SyncLog]:
        return self._rows_to_logs(
            self._db.fetchall(
                """
                SELECT * FROM sync_logs
                WHERE device_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (device_id, limit),
            )
        )

    def _rows_to_logs(self, rows) -> list[SyncLog]:
        result: list[SyncLog] = []
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                details = {}
            result.append(
                SyncLog(
                    id=row["id"],
                    device_id=row["device_id"],
                    action=row["action"],
                    success=bool(row["success"]),
                    message=row["message"],
                    records_count=int(row["records_count"] or 0),
                    created_at=row["created_at"],
                    details=details if isinstance(details, dict) else {},
                )
            )
        return result
