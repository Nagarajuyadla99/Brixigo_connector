"""Phase 3 repositories — employees, templates, device users, backups, access, reports."""

from __future__ import annotations

import json
from typing import Any, Optional

from models import new_id, utcnow
from models.phase3 import (
    AccessLevel,
    AccessSchedule,
    BiometricTemplate,
    DeviceBackup,
    DeviceUserRecord,
    DoorController,
    Employee,
    FirmwareJob,
    NotificationEvent,
    ReportRequest,
)
from security.encryption import SecretBox
from storage.database import Database


class EmployeeRepository:
    def __init__(self, db: Database, secrets: SecretBox) -> None:
        self._db = db
        self._secrets = secrets

    def upsert(self, employee: Employee) -> Employee:
        if not employee.id:
            employee.id = new_id()
        now = utcnow().isoformat()
        employee.updated_at = now
        if not employee.created_at:
            employee.created_at = now
        pin_enc = self._secrets.encrypt_str(employee.pin) if employee.pin else ""
        existing = self.get_by_code(employee.employee_code)
        if existing and not employee.id:
            employee.id = existing.id
        self._db.execute(
            """
            INSERT INTO employees (
                id, employee_code, name, department, designation, branch,
                organization, building, floor, gate, shift, card_number, pin_enc,
                status, device_ids_json, privilege, cloud_version, last_synced_at,
                created_at, updated_at, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                employee_code=excluded.employee_code,
                name=excluded.name,
                department=excluded.department,
                designation=excluded.designation,
                branch=excluded.branch,
                organization=excluded.organization,
                building=excluded.building,
                floor=excluded.floor,
                gate=excluded.gate,
                shift=excluded.shift,
                card_number=excluded.card_number,
                pin_enc=excluded.pin_enc,
                status=excluded.status,
                device_ids_json=excluded.device_ids_json,
                privilege=excluded.privilege,
                cloud_version=excluded.cloud_version,
                last_synced_at=excluded.last_synced_at,
                updated_at=excluded.updated_at,
                extra_json=excluded.extra_json
            """,
            (
                employee.id,
                employee.employee_code,
                employee.name,
                employee.department,
                employee.designation,
                employee.branch,
                employee.organization,
                employee.building,
                employee.floor,
                employee.gate,
                employee.shift,
                employee.card_number,
                pin_enc,
                employee.status,
                json.dumps(employee.device_ids or []),
                employee.privilege,
                employee.cloud_version,
                employee.last_synced_at,
                employee.created_at,
                employee.updated_at,
                json.dumps(employee.extra or {}),
            ),
        )
        return employee

    def get(self, employee_id: str) -> Optional[Employee]:
        row = self._db.fetchone("SELECT * FROM employees WHERE id = ?", (employee_id,))
        return self._row(row) if row else None

    def get_by_code(self, code: str) -> Optional[Employee]:
        row = self._db.fetchone(
            "SELECT * FROM employees WHERE employee_code = ?", (code,)
        )
        return self._row(row) if row else None

    def list_all(self, *, status: str | None = None) -> list[Employee]:
        if status:
            rows = self._db.fetchall(
                "SELECT * FROM employees WHERE status = ? ORDER BY name", (status,)
            )
        else:
            rows = self._db.fetchall("SELECT * FROM employees ORDER BY name")
        return [self._row(r) for r in rows]

    def list_for_device(self, device: Any) -> list[Employee]:
        """Return employees assigned to a device by explicit ids or hierarchy match."""
        result: list[Employee] = []
        for emp in self.list_all(status="active"):
            if emp.device_ids and device.id in emp.device_ids:
                result.append(emp)
                continue
            if emp.device_ids:
                continue
            # Hierarchy match — only sync relevant employees to assigned devices
            if emp.organization and device.organization and emp.organization != device.organization:
                continue
            if emp.branch and device.branch and emp.branch != device.branch:
                continue
            if emp.department and device.department and emp.department != device.department:
                continue
            if emp.building and device.building and emp.building != device.building:
                continue
            if emp.floor and device.floor and emp.floor != device.floor:
                continue
            if emp.gate and device.gate and emp.gate != device.gate:
                continue
            # If employee has no location filters, include only when device_ids empty
            # and at least one shared org/branch when device has them
            if device.branch and not emp.branch and not emp.organization:
                continue
            result.append(emp)
        return result

    def delete(self, employee_id: str) -> bool:
        with self._db.cursor() as cur:
            cur.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            return cur.rowcount > 0

    def mark_synced(self, employee_id: str) -> None:
        self._db.execute(
            "UPDATE employees SET last_synced_at = ?, updated_at = ? WHERE id = ?",
            (utcnow().isoformat(), utcnow().isoformat(), employee_id),
        )

    def _row(self, row) -> Employee:
        pin = ""
        if row["pin_enc"]:
            try:
                pin = self._secrets.decrypt_str(row["pin_enc"])
            except Exception:
                pin = ""
        try:
            device_ids = json.loads(row["device_ids_json"] or "[]")
        except json.JSONDecodeError:
            device_ids = []
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except json.JSONDecodeError:
            extra = {}
        return Employee(
            id=row["id"],
            employee_code=row["employee_code"],
            name=row["name"],
            department=row["department"] or "",
            designation=row["designation"] or "",
            branch=row["branch"] or "",
            organization=row["organization"] or "",
            building=row["building"] or "",
            floor=row["floor"] or "",
            gate=row["gate"] or "",
            shift=row["shift"] or "",
            card_number=row["card_number"] or "",
            pin=pin,
            status=row["status"] or "active",
            device_ids=device_ids if isinstance(device_ids, list) else [],
            privilege=int(row["privilege"] or 0),
            cloud_version=int(row["cloud_version"] or 0),
            last_synced_at=row["last_synced_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            extra=extra if isinstance(extra, dict) else {},
        )


class TemplateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, template: BiometricTemplate) -> BiometricTemplate:
        if not template.id:
            template.id = new_id()
        now = utcnow().isoformat()
        template.updated_at = now
        if not template.created_at:
            template.created_at = now
        self._db.execute(
            """
            INSERT INTO biometric_templates (
                id, employee_id, employee_code, template_type, finger_index,
                template_enc, checksum, vendor, size, device_id, cloud_version,
                created_at, updated_at, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                template_enc=excluded.template_enc,
                checksum=excluded.checksum,
                size=excluded.size,
                vendor=excluded.vendor,
                cloud_version=excluded.cloud_version,
                updated_at=excluded.updated_at,
                extra_json=excluded.extra_json
            """,
            (
                template.id,
                template.employee_id,
                template.employee_code,
                template.template_type,
                template.finger_index,
                template.template_enc,
                template.checksum,
                template.vendor,
                template.size,
                template.device_id,
                template.cloud_version,
                template.created_at,
                template.updated_at,
                json.dumps(template.extra or {}),
            ),
        )
        return template

    def list_for_employee(self, employee_id: str) -> list[BiometricTemplate]:
        rows = self._db.fetchall(
            "SELECT * FROM biometric_templates WHERE employee_id = ?",
            (employee_id,),
        )
        return [self._row(r) for r in rows]

    def list_all(self) -> list[BiometricTemplate]:
        return [self._row(r) for r in self._db.fetchall("SELECT * FROM biometric_templates")]

    def delete(self, template_id: str) -> bool:
        with self._db.cursor() as cur:
            cur.execute("DELETE FROM biometric_templates WHERE id = ?", (template_id,))
            return cur.rowcount > 0

    def _row(self, row) -> BiometricTemplate:
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except json.JSONDecodeError:
            extra = {}
        return BiometricTemplate(
            id=row["id"],
            employee_id=row["employee_id"],
            employee_code=row["employee_code"],
            template_type=row["template_type"],
            finger_index=int(row["finger_index"] or 0),
            template_enc=row["template_enc"] or "",
            checksum=row["checksum"] or "",
            vendor=row["vendor"] or "",
            size=int(row["size"] or 0),
            device_id=row["device_id"] or "",
            cloud_version=int(row["cloud_version"] or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            extra=extra if isinstance(extra, dict) else {},
        )


class DeviceUserCacheRepository:
    def __init__(self, db: Database, secrets: SecretBox) -> None:
        self._db = db
        self._secrets = secrets

    def upsert(self, record: DeviceUserRecord) -> DeviceUserRecord:
        if not record.id:
            record.id = new_id()
        record.updated_at = utcnow().isoformat()
        pwd_enc = self._secrets.encrypt_str(record.password) if record.password else ""
        self._db.execute(
            """
            INSERT INTO device_users (
                id, device_id, user_id, name, card, privilege, password_enc,
                enabled, employee_id, last_synced_at, sync_status, conflict,
                raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id, user_id) DO UPDATE SET
                name=excluded.name,
                card=excluded.card,
                privilege=excluded.privilege,
                password_enc=excluded.password_enc,
                enabled=excluded.enabled,
                employee_id=excluded.employee_id,
                last_synced_at=excluded.last_synced_at,
                sync_status=excluded.sync_status,
                conflict=excluded.conflict,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                record.id,
                record.device_id,
                record.user_id,
                record.name,
                record.card,
                record.privilege,
                pwd_enc,
                1 if record.enabled else 0,
                record.employee_id,
                record.last_synced_at,
                record.sync_status,
                record.conflict,
                json.dumps(record.raw or {}),
                record.updated_at,
            ),
        )
        return record

    def upsert_from_device_users(self, device_id: str, users: list) -> int:
        """Cache names from a biometric terminal without failing punch collection."""
        saved = 0
        for user in users or []:
            user_id = str(getattr(user, "user_id", "") or "").strip()
            if not user_id:
                continue
            try:
                self.upsert(
                    DeviceUserRecord(
                        id=new_id(),
                        device_id=device_id,
                        user_id=user_id,
                        name=str(getattr(user, "name", "") or ""),
                        card=str(getattr(user, "card", "") or ""),
                        privilege=int(getattr(user, "privilege", 0) or 0),
                        password=str(getattr(user, "password", "") or ""),
                        enabled=bool(getattr(user, "enabled", True)),
                        last_synced_at=utcnow().isoformat(),
                        sync_status="synced",
                        raw=getattr(user, "raw", None) or {},
                    )
                )
                saved += 1
            except Exception:
                continue
        return saved

    def find_name(self, user_id: str, device_id: str | None = None) -> str:
        code = str(user_id or "").strip()
        if not code:
            return ""
        if device_id:
            row = self._db.fetchone(
                """
                SELECT name FROM device_users
                WHERE device_id = ? AND user_id = ? AND TRIM(IFNULL(name, '')) != ''
                LIMIT 1
                """,
                (device_id, code),
            )
            if row and str(row["name"] or "").strip():
                return str(row["name"]).strip()
        return ""

    def list_named(self) -> list[DeviceUserRecord]:
        rows = self._db.fetchall(
            """
            SELECT * FROM device_users
            WHERE TRIM(IFNULL(name, '')) != ''
            ORDER BY user_id, updated_at DESC
            """
        )
        return [self._row(r) for r in rows]

    def list_for_device(self, device_id: str) -> list[DeviceUserRecord]:
        rows = self._db.fetchall(
            "SELECT * FROM device_users WHERE device_id = ? ORDER BY user_id",
            (device_id,),
        )
        return [self._row(r) for r in rows]

    def search(self, device_id: str, query: str) -> list[DeviceUserRecord]:
        q = f"%{query.strip()}%"
        rows = self._db.fetchall(
            """
            SELECT * FROM device_users
            WHERE device_id = ? AND (user_id LIKE ? OR name LIKE ? OR card LIKE ?)
            ORDER BY name
            """,
            (device_id, q, q, q),
        )
        return [self._row(r) for r in rows]

    def delete(self, device_id: str, user_id: str) -> bool:
        with self._db.cursor() as cur:
            cur.execute(
                "DELETE FROM device_users WHERE device_id = ? AND user_id = ?",
                (device_id, user_id),
            )
            return cur.rowcount > 0

    def _row(self, row) -> DeviceUserRecord:
        password = ""
        if row["password_enc"]:
            try:
                password = self._secrets.decrypt_str(row["password_enc"])
            except Exception:
                password = ""
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        return DeviceUserRecord(
            id=row["id"],
            device_id=row["device_id"],
            user_id=row["user_id"],
            name=row["name"] or "",
            card=row["card"] or "",
            privilege=int(row["privilege"] or 0),
            password=password,
            enabled=bool(row["enabled"]),
            employee_id=row["employee_id"] or "",
            last_synced_at=row["last_synced_at"],
            sync_status=row["sync_status"] or "unknown",
            conflict=row["conflict"] or "",
            raw=raw if isinstance(raw, dict) else {},
            updated_at=row["updated_at"],
        )


class BackupRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, backup: DeviceBackup) -> DeviceBackup:
        if not backup.id:
            backup.id = new_id()
        self._db.execute(
            """
            INSERT INTO device_backups (
                id, device_id, backup_path, backup_type, includes_users,
                includes_templates, includes_attendance, includes_config,
                size_bytes, checksum, encrypted, created_at, notes, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                backup.id,
                backup.device_id,
                backup.backup_path,
                backup.backup_type,
                1 if backup.includes_users else 0,
                1 if backup.includes_templates else 0,
                1 if backup.includes_attendance else 0,
                1 if backup.includes_config else 0,
                backup.size_bytes,
                backup.checksum,
                1 if backup.encrypted else 0,
                backup.created_at,
                backup.notes,
                json.dumps(backup.meta or {}),
            ),
        )
        return backup

    def list_for_device(self, device_id: str) -> list[DeviceBackup]:
        rows = self._db.fetchall(
            "SELECT * FROM device_backups WHERE device_id = ? ORDER BY created_at DESC",
            (device_id,),
        )
        return [self._row(r) for r in rows]

    def get(self, backup_id: str) -> Optional[DeviceBackup]:
        row = self._db.fetchone("SELECT * FROM device_backups WHERE id = ?", (backup_id,))
        return self._row(row) if row else None

    def _row(self, row) -> DeviceBackup:
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        return DeviceBackup(
            id=row["id"],
            device_id=row["device_id"],
            backup_path=row["backup_path"],
            backup_type=row["backup_type"],
            includes_users=bool(row["includes_users"]),
            includes_templates=bool(row["includes_templates"]),
            includes_attendance=bool(row["includes_attendance"]),
            includes_config=bool(row["includes_config"]),
            size_bytes=int(row["size_bytes"] or 0),
            checksum=row["checksum"] or "",
            encrypted=bool(row["encrypted"]),
            created_at=row["created_at"],
            notes=row["notes"] or "",
            meta=meta if isinstance(meta, dict) else {},
        )


class FirmwareJobRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, job: FirmwareJob) -> FirmwareJob:
        if not job.id:
            job.id = new_id()
        self._db.execute(
            """
            INSERT INTO firmware_jobs (
                id, device_id, action, from_version, to_version, package_path,
                status, message, created_at, completed_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.device_id,
                job.action,
                job.from_version,
                job.to_version,
                job.package_path,
                job.status,
                job.message,
                job.created_at,
                job.completed_at,
                json.dumps(job.details or {}),
            ),
        )
        return job

    def update(self, job: FirmwareJob) -> None:
        self._db.execute(
            """
            UPDATE firmware_jobs SET status=?, message=?, completed_at=?, details_json=?
            WHERE id=?
            """,
            (
                job.status,
                job.message,
                job.completed_at,
                json.dumps(job.details or {}),
                job.id,
            ),
        )

    def recent(self, limit: int = 100) -> list[FirmwareJob]:
        rows = self._db.fetchall(
            "SELECT * FROM firmware_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        result = []
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                details = {}
            result.append(
                FirmwareJob(
                    id=row["id"],
                    device_id=row["device_id"],
                    action=row["action"],
                    from_version=row["from_version"] or "",
                    to_version=row["to_version"] or "",
                    package_path=row["package_path"] or "",
                    status=row["status"],
                    message=row["message"] or "",
                    created_at=row["created_at"],
                    completed_at=row["completed_at"],
                    details=details if isinstance(details, dict) else {},
                )
            )
        return result


class AccessControlRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_level(self, level: AccessLevel) -> AccessLevel:
        if not level.id:
            level.id = new_id()
        self._db.execute(
            """
            INSERT INTO access_levels (
                id, name, organization, branch, schedule_json, door_ids_json,
                created_at, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                organization=excluded.organization,
                branch=excluded.branch,
                schedule_json=excluded.schedule_json,
                door_ids_json=excluded.door_ids_json,
                extra_json=excluded.extra_json
            """,
            (
                level.id,
                level.name,
                level.organization,
                level.branch,
                level.schedule_json,
                json.dumps(level.door_ids or []),
                level.created_at,
                json.dumps(level.extra or {}),
            ),
        )
        return level

    def upsert_door(self, door: DoorController) -> DoorController:
        if not door.id:
            door.id = new_id()
        self._db.execute(
            """
            INSERT INTO door_controllers (
                id, name, device_id, ip_address, port, organization, branch,
                building, floor, gate, status, created_at, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                device_id=excluded.device_id,
                ip_address=excluded.ip_address,
                port=excluded.port,
                organization=excluded.organization,
                branch=excluded.branch,
                building=excluded.building,
                floor=excluded.floor,
                gate=excluded.gate,
                status=excluded.status,
                extra_json=excluded.extra_json
            """,
            (
                door.id,
                door.name,
                door.device_id,
                door.ip_address,
                door.port,
                door.organization,
                door.branch,
                door.building,
                door.floor,
                door.gate,
                door.status,
                door.created_at,
                json.dumps(door.extra or {}),
            ),
        )
        return door

    def upsert_schedule(self, schedule: AccessSchedule) -> AccessSchedule:
        if not schedule.id:
            schedule.id = new_id()
        self._db.execute(
            """
            INSERT INTO access_schedules (id, name, timezone, rules_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                timezone=excluded.timezone,
                rules_json=excluded.rules_json
            """,
            (
                schedule.id,
                schedule.name,
                schedule.timezone,
                schedule.rules_json,
                schedule.created_at,
            ),
        )
        return schedule

    def list_levels(self) -> list[AccessLevel]:
        rows = self._db.fetchall("SELECT * FROM access_levels ORDER BY name")
        out = []
        for row in rows:
            try:
                doors = json.loads(row["door_ids_json"] or "[]")
            except json.JSONDecodeError:
                doors = []
            try:
                extra = json.loads(row["extra_json"] or "{}")
            except json.JSONDecodeError:
                extra = {}
            out.append(
                AccessLevel(
                    id=row["id"],
                    name=row["name"],
                    organization=row["organization"] or "",
                    branch=row["branch"] or "",
                    schedule_json=row["schedule_json"] or "{}",
                    door_ids=doors if isinstance(doors, list) else [],
                    created_at=row["created_at"],
                    extra=extra if isinstance(extra, dict) else {},
                )
            )
        return out

    def list_doors(self) -> list[DoorController]:
        rows = self._db.fetchall("SELECT * FROM door_controllers ORDER BY name")
        out = []
        for row in rows:
            try:
                extra = json.loads(row["extra_json"] or "{}")
            except json.JSONDecodeError:
                extra = {}
            out.append(
                DoorController(
                    id=row["id"],
                    name=row["name"],
                    device_id=row["device_id"] or "",
                    ip_address=row["ip_address"] or "",
                    port=int(row["port"] or 4370),
                    organization=row["organization"] or "",
                    branch=row["branch"] or "",
                    building=row["building"] or "",
                    floor=row["floor"] or "",
                    gate=row["gate"] or "",
                    status=row["status"] or "unknown",
                    created_at=row["created_at"],
                    extra=extra if isinstance(extra, dict) else {},
                )
            )
        return out


class NotificationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, event: NotificationEvent) -> NotificationEvent:
        if not event.id:
            event.id = new_id()
        self._db.execute(
            """
            INSERT INTO notifications (
                id, event_type, title, message, severity, device_id,
                channel, delivered, created_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.event_type,
                event.title,
                event.message[:2000],
                event.severity,
                event.device_id,
                event.channel,
                1 if event.delivered else 0,
                event.created_at,
                json.dumps(event.details or {}),
            ),
        )
        self._db.execute(
            """
            DELETE FROM notifications WHERE id IN (
                SELECT id FROM notifications ORDER BY created_at DESC LIMIT -1 OFFSET 5000
            )
            """
        )
        return event

    def mark_delivered(self, event_id: str) -> None:
        self._db.execute("UPDATE notifications SET delivered = 1 WHERE id = ?", (event_id,))

    def recent(self, limit: int = 100) -> list[NotificationEvent]:
        rows = self._db.fetchall(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        out = []
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                details = {}
            out.append(
                NotificationEvent(
                    id=row["id"],
                    event_type=row["event_type"],
                    title=row["title"],
                    message=row["message"],
                    severity=row["severity"],
                    device_id=row["device_id"] or "",
                    channel=row["channel"] or "cloud",
                    delivered=bool(row["delivered"]),
                    created_at=row["created_at"],
                    details=details if isinstance(details, dict) else {},
                )
            )
        return out


class ReportRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, req: ReportRequest) -> ReportRequest:
        if not req.id:
            req.id = new_id()
        self._db.execute(
            """
            INSERT INTO report_jobs (
                id, report_type, parameters_json, status, result_path, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req.id,
                req.report_type,
                json.dumps(req.parameters or {}),
                req.status,
                req.result_path,
                req.created_at,
                req.completed_at,
            ),
        )
        return req

    def update(self, req: ReportRequest) -> None:
        self._db.execute(
            """
            UPDATE report_jobs SET status=?, result_path=?, completed_at=? WHERE id=?
            """,
            (req.status, req.result_path, req.completed_at, req.id),
        )

    def recent(self, limit: int = 50) -> list[ReportRequest]:
        rows = self._db.fetchall(
            "SELECT * FROM report_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        out = []
        for row in rows:
            try:
                params = json.loads(row["parameters_json"] or "{}")
            except json.JSONDecodeError:
                params = {}
            out.append(
                ReportRequest(
                    id=row["id"],
                    report_type=row["report_type"],
                    parameters=params if isinstance(params, dict) else {},
                    status=row["status"],
                    result_path=row["result_path"] or "",
                    created_at=row["created_at"],
                    completed_at=row["completed_at"],
                )
            )
        return out


class TimeSyncLogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        *,
        device_id: str,
        connector_time: str,
        cloud_time: str,
        device_time: str,
        drift_seconds: float,
        corrected: bool,
        success: bool,
        message: str,
    ) -> None:
        self._db.execute(
            """
            INSERT INTO time_sync_logs (
                id, device_id, connector_time, cloud_time, device_time,
                drift_seconds, corrected, success, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                device_id,
                connector_time,
                cloud_time,
                device_time,
                drift_seconds,
                1 if corrected else 0,
                1 if success else 0,
                message[:1000],
                utcnow().isoformat(),
            ),
        )
        self._db.execute(
            """
            DELETE FROM time_sync_logs WHERE id IN (
                SELECT id FROM time_sync_logs ORDER BY created_at DESC LIMIT -1 OFFSET 5000
            )
            """
        )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.fetchall(
            "SELECT * FROM time_sync_logs ORDER BY created_at DESC LIMIT ?", (limit,)
        )]


class EmployeeSyncQueueRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def enqueue(
        self,
        *,
        employee_id: str,
        action: str,
        device_id: str = "",
        payload: dict | None = None,
    ) -> str:
        item_id = new_id()
        now = utcnow().isoformat()
        self._db.execute(
            """
            INSERT INTO employee_sync_queue (
                id, employee_id, device_id, action, status, attempts,
                last_error, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', 0, '', ?, ?, ?)
            """,
            (
                item_id,
                employee_id,
                device_id,
                action,
                json.dumps(payload or {}),
                now,
                now,
            ),
        )
        return item_id

    def list_pending(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT * FROM employee_sync_queue
            WHERE status IN ('pending', 'failed') AND attempts < 8
            ORDER BY created_at ASC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    def mark(self, item_id: str, *, status: str, error: str = "") -> None:
        self._db.execute(
            """
            UPDATE employee_sync_queue
            SET status=?, last_error=?, attempts=attempts+1, updated_at=?
            WHERE id=?
            """,
            (status, error[:1000], utcnow().isoformat(), item_id),
        )
