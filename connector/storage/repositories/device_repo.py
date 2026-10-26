"""Device repository — Phase 1 CRUD extended for enterprise hierarchy/health."""

from __future__ import annotations

import json
from typing import Any, Optional

from models import Device, DeviceStatus, new_id, utcnow
from security.encryption import SecretBox
from storage.database import Database


class DeviceRepository:
    def __init__(self, db: Database, secrets: SecretBox) -> None:
        self._db = db
        self._secrets = secrets

    def _row_to_device(self, row) -> Device:
        password = ""
        if row["password_enc"]:
            try:
                password = self._secrets.decrypt_str(row["password_enc"])
            except Exception:
                password = ""
        extra = {}
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except json.JSONDecodeError:
            extra = {}

        def _col(name: str, default: Any = "") -> Any:
            try:
                value = row[name]
            except (IndexError, KeyError):
                return default
            return default if value is None else value

        return Device(
            id=row["id"],
            name=row["name"],
            manufacturer=row["manufacturer"],
            model=row["model"] or "",
            ip_address=row["ip_address"],
            port=int(row["port"]),
            password=password,
            branch=row["branch"] or "",
            timezone=row["timezone"] or "Asia/Kolkata",
            communication_type=row["communication_type"] or "tcp",
            polling_interval=int(row["polling_interval"] or 60),
            status=row["status"] or DeviceStatus.UNKNOWN.value,
            terminal_code=row["terminal_code"] or "",
            serial_number=row["serial_number"] or "",
            last_sync_at=row["last_sync_at"],
            last_sync_cursor=row["last_sync_cursor"] or "",
            last_error=row["last_error"] or "",
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            extra=extra if isinstance(extra, dict) else {},
            organization=str(_col("organization", "")),
            department=str(_col("department", "")),
            building=str(_col("building", "")),
            floor=str(_col("floor", "")),
            gate=str(_col("gate", "")),
            mac_address=str(_col("mac_address", "")),
            firmware=str(_col("firmware", "")),
            sdk_version=str(_col("sdk_version", "")),
            last_attendance_at=_col("last_attendance_at", None),
            current_users=int(_col("current_users", 0) or 0),
            current_logs=int(_col("current_logs", 0) or 0),
            health_status=str(_col("health_status", "unknown") or "unknown"),
            last_health_at=_col("last_health_at", None),
            warranty_until=str(_col("warranty_until", "")),
            installation_date=str(_col("installation_date", "")),
            last_maintenance_at=str(_col("last_maintenance_at", "")),
            last_backup_at=str(_col("last_backup_at", "")),
            last_time_sync_at=str(_col("last_time_sync_at", "")),
            last_employee_sync_at=str(_col("last_employee_sync_at", "")),
            uptime_seconds=int(_col("uptime_seconds", 0) or 0),
            failed_auth_count=int(_col("failed_auth_count", 0) or 0),
        )

    def list_all(self, enabled_only: bool = False) -> list[Device]:
        sql = "SELECT * FROM devices"
        params: tuple = ()
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY organization COLLATE NOCASE, branch COLLATE NOCASE, name COLLATE NOCASE"
        return [self._row_to_device(r) for r in self._db.fetchall(sql, params)]

    def get(self, device_id: str) -> Optional[Device]:
        row = self._db.fetchone("SELECT * FROM devices WHERE id = ?", (device_id,))
        return self._row_to_device(row) if row else None

    def get_by_ip_port(self, ip_address: str, port: int) -> Optional[Device]:
        row = self._db.fetchone(
            """
            SELECT * FROM devices
            WHERE ip_address = ? AND port = ?
            ORDER BY enabled DESC, created_at ASC
            """,
            (ip_address, int(port)),
        )
        return self._row_to_device(row) if row else None

    def get_by_terminal(self, terminal_code: str) -> Optional[Device]:
        row = self._db.fetchone(
            "SELECT * FROM devices WHERE terminal_code = ?", (terminal_code,)
        )
        return self._row_to_device(row) if row else None

    def create(self, device: Device) -> Device:
        if not device.id:
            device.id = new_id()
        now = utcnow().isoformat()
        device.created_at = now
        device.updated_at = now
        if not device.terminal_code:
            device.terminal_code = f"DEV-{device.id[:8].upper()}"
        password_enc = self._secrets.encrypt_str(device.password) if device.password else ""
        self._db.execute(
            """
            INSERT INTO devices (
                id, name, manufacturer, model, ip_address, port, password_enc,
                branch, timezone, communication_type, polling_interval, status,
                terminal_code, serial_number, last_sync_at, last_sync_cursor,
                last_error, enabled, created_at, updated_at, extra_json,
                organization, department, building, floor, gate, mac_address,
                firmware, sdk_version, last_attendance_at, current_users,
                current_logs, health_status, last_health_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                device.id,
                device.name,
                device.manufacturer.lower(),
                device.model,
                device.ip_address,
                device.port,
                password_enc,
                device.branch,
                device.timezone,
                device.communication_type,
                device.polling_interval,
                device.status,
                device.terminal_code,
                device.serial_number,
                device.last_sync_at,
                device.last_sync_cursor,
                device.last_error,
                1 if device.enabled else 0,
                device.created_at,
                device.updated_at,
                json.dumps(device.extra or {}),
                device.organization,
                device.department,
                device.building,
                device.floor,
                device.gate,
                device.mac_address,
                device.firmware,
                device.sdk_version,
                device.last_attendance_at,
                device.current_users,
                device.current_logs,
                device.health_status,
                device.last_health_at,
            ),
        )
        return device

    def update(self, device: Device) -> Device:
        device.updated_at = utcnow().isoformat()
        password_enc = self._secrets.encrypt_str(device.password) if device.password else ""
        self._db.execute(
            """
            UPDATE devices SET
                name=?, manufacturer=?, model=?, ip_address=?, port=?, password_enc=?,
                branch=?, timezone=?, communication_type=?, polling_interval=?, status=?,
                terminal_code=?, serial_number=?, last_sync_at=?, last_sync_cursor=?,
                last_error=?, enabled=?, updated_at=?, extra_json=?,
                organization=?, department=?, building=?, floor=?, gate=?,
                mac_address=?, firmware=?, sdk_version=?, last_attendance_at=?,
                current_users=?, current_logs=?, health_status=?, last_health_at=?
            WHERE id=?
            """,
            (
                device.name,
                device.manufacturer.lower(),
                device.model,
                device.ip_address,
                device.port,
                password_enc,
                device.branch,
                device.timezone,
                device.communication_type,
                device.polling_interval,
                device.status,
                device.terminal_code,
                device.serial_number,
                device.last_sync_at,
                device.last_sync_cursor,
                device.last_error,
                1 if device.enabled else 0,
                device.updated_at,
                json.dumps(device.extra or {}),
                device.organization,
                device.department,
                device.building,
                device.floor,
                device.gate,
                device.mac_address,
                device.firmware,
                device.sdk_version,
                device.last_attendance_at,
                device.current_users,
                device.current_logs,
                device.health_status,
                device.last_health_at,
                device.id,
            ),
        )
        return device

    def delete(self, device_id: str) -> bool:
        with self._db.cursor() as cur:
            cur.execute("DELETE FROM devices WHERE id = ?", (device_id,))
            return cur.rowcount > 0

    def update_status(
        self,
        device_id: str,
        *,
        status: str,
        last_error: str = "",
        serial_number: str | None = None,
    ) -> None:
        fields = ["status = ?", "last_error = ?", "updated_at = ?"]
        params: list = [status, last_error, utcnow().isoformat()]
        if serial_number is not None:
            fields.append("serial_number = ?")
            params.append(serial_number)
        params.append(device_id)
        self._db.execute(
            f"UPDATE devices SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )

    def update_sync_cursor(
        self,
        device_id: str,
        *,
        cursor: str,
        last_sync_at: str | None = None,
    ) -> None:
        self._db.execute(
            """
            UPDATE devices SET last_sync_cursor=?, last_sync_at=?, updated_at=?
            WHERE id=?
            """,
            (
                cursor,
                last_sync_at or utcnow().isoformat(),
                utcnow().isoformat(),
                device_id,
            ),
        )

    def update_telemetry(
        self,
        device_id: str,
        *,
        firmware: str | None = None,
        mac_address: str | None = None,
        sdk_version: str | None = None,
        model: str | None = None,
        serial_number: str | None = None,
        current_users: int | None = None,
        current_logs: int | None = None,
        last_attendance_at: str | None = None,
        health_status: str | None = None,
        last_health_at: str | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        params: list[Any] = [utcnow().isoformat()]
        mapping = {
            "firmware": firmware,
            "mac_address": mac_address,
            "sdk_version": sdk_version,
            "model": model,
            "serial_number": serial_number,
            "current_users": current_users,
            "current_logs": current_logs,
            "last_attendance_at": last_attendance_at,
            "health_status": health_status,
            "last_health_at": last_health_at,
        }
        for column, value in mapping.items():
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        params.append(device_id)
        self._db.execute(
            f"UPDATE devices SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )

    def set_enabled(self, device_id: str, enabled: bool) -> None:
        status = DeviceStatus.UNKNOWN.value if enabled else DeviceStatus.DISABLED.value
        self._db.execute(
            """
            UPDATE devices SET enabled=?, status=?, updated_at=? WHERE id=?
            """,
            (1 if enabled else 0, status, utcnow().isoformat(), device_id),
        )

    def touch_field(self, device_id: str, field: str, value: str | int | None = None) -> None:
        """Safely update a single allowlisted telemetry/inventory timestamp field."""
        allowed = {
            "last_backup_at",
            "last_time_sync_at",
            "last_employee_sync_at",
            "last_maintenance_at",
            "warranty_until",
            "installation_date",
            "uptime_seconds",
            "failed_auth_count",
        }
        if field not in allowed:
            raise ValueError(f"Unsupported device field: {field}")
        if value is None:
            value = utcnow().isoformat()
        self._db.execute(
            f"UPDATE devices SET {field}=?, updated_at=? WHERE id=?",
            (value, utcnow().isoformat(), device_id),
        )

    def counts_by_status(self) -> dict[str, int]:
        rows = self._db.fetchall(
            "SELECT status, COUNT(*) AS c FROM devices WHERE enabled=1 GROUP BY status"
        )
        return {r["status"]: int(r["c"]) for r in rows}
