"""Device health snapshot repository."""

from __future__ import annotations

import json
from typing import Optional

from models import DeviceHealthSnapshot, new_id, utcnow
from storage.database import Database


class DeviceHealthRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, snap: DeviceHealthSnapshot) -> DeviceHealthSnapshot:
        if not snap.id:
            snap.id = new_id()
        self._db.execute(
            """
            INSERT INTO device_health (
                id, device_id, online, ping_ms, memory_usage_percent,
                storage_usage_percent, user_capacity, log_capacity,
                current_users, current_logs, firmware_version, device_time,
                battery_level, cpu_temperature, restart_count, message,
                collected_at, pushed, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.id,
                snap.device_id,
                1 if snap.online else 0,
                snap.ping_ms,
                snap.memory_usage_percent,
                snap.storage_usage_percent,
                snap.user_capacity,
                snap.log_capacity,
                snap.current_users,
                snap.current_logs,
                snap.firmware_version,
                snap.device_time,
                snap.battery_level,
                snap.cpu_temperature,
                snap.restart_count,
                snap.message,
                snap.collected_at or utcnow().isoformat(),
                1 if snap.pushed else 0,
                json.dumps(snap.details or {}),
            ),
        )
        return snap

    def latest_for_device(self, device_id: str) -> Optional[DeviceHealthSnapshot]:
        row = self._db.fetchone(
            """
            SELECT * FROM device_health
            WHERE device_id = ?
            ORDER BY collected_at DESC LIMIT 1
            """,
            (device_id,),
        )
        return self._row_to_snap(row) if row else None

    def recent_for_device(self, device_id: str, limit: int = 20) -> list[DeviceHealthSnapshot]:
        rows = self._db.fetchall(
            """
            SELECT * FROM device_health
            WHERE device_id = ?
            ORDER BY collected_at DESC LIMIT ?
            """,
            (device_id, max(1, int(limit))),
        )
        return [self._row_to_snap(r) for r in rows]

    def list_unpushed(self, limit: int = 200) -> list[DeviceHealthSnapshot]:
        rows = self._db.fetchall(
            """
            SELECT * FROM device_health
            WHERE pushed = 0
            ORDER BY collected_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_snap(r) for r in rows]

    def mark_pushed(self, ids: list[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._db.execute(
            f"UPDATE device_health SET pushed = 1 WHERE id IN ({placeholders})",
            tuple(ids),
        )

    def cleanup(self, keep_per_device: int = 288) -> int:
        """Keep roughly one day of minute samples per device (default 288)."""
        rows = self._db.fetchall("SELECT DISTINCT device_id FROM device_health")
        deleted = 0
        for row in rows:
            device_id = row["device_id"]
            with self._db.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM device_health
                    WHERE device_id = ?
                      AND id NOT IN (
                        SELECT id FROM device_health
                        WHERE device_id = ?
                        ORDER BY collected_at DESC
                        LIMIT ?
                      )
                    """,
                    (device_id, device_id, keep_per_device),
                )
                deleted += cur.rowcount
        return deleted

    def _row_to_snap(self, row) -> DeviceHealthSnapshot:
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        return DeviceHealthSnapshot(
            id=row["id"],
            device_id=row["device_id"],
            online=bool(row["online"]),
            ping_ms=float(row["ping_ms"] or 0),
            memory_usage_percent=float(row["memory_usage_percent"] or 0),
            storage_usage_percent=float(row["storage_usage_percent"] or 0),
            user_capacity=int(row["user_capacity"] or 0),
            log_capacity=int(row["log_capacity"] or 0),
            current_users=int(row["current_users"] or 0),
            current_logs=int(row["current_logs"] or 0),
            firmware_version=row["firmware_version"] or "",
            device_time=row["device_time"] or "",
            battery_level=row["battery_level"],
            cpu_temperature=row["cpu_temperature"],
            restart_count=int(row["restart_count"] or 0),
            message=row["message"] or "",
            collected_at=row["collected_at"],
            pushed=bool(row["pushed"]),
            details=details if isinstance(details, dict) else {},
        )
