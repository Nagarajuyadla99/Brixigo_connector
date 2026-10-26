"""Attendance queue repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from api.json_codec import (
    UNSET_DEVICE_CLOCK_YEAR,
    correct_unset_device_clock,
    dumps,
    format_api_datetime,
    parse_datetime,
)
from models import AttendanceRecord, QueueStatus, new_id, utcnow
from storage.database import Database


class AttendanceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def _row_to_record(self, row) -> AttendanceRecord:
        try:
            raw = json.loads(row["raw_payload"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        return AttendanceRecord(
            id=row["id"],
            device_id=row["device_id"],
            device_employee_code=row["device_employee_code"],
            punch_time=row["punch_time"],
            event_type=row["event_type"],
            verification_method=row["verification_method"],
            biometric_reference=row["biometric_reference"] or "",
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            retry_count=int(row["retry_count"] or 0),
            last_error=row["last_error"] or "",
            raw_payload=raw if isinstance(raw, dict) else {},
            created_at=row["created_at"],
            uploaded_at=row["uploaded_at"],
        )

    def enqueue(self, record: AttendanceRecord) -> tuple[bool, AttendanceRecord]:
        """Insert if new. Returns (inserted, record)."""
        if not record.id:
            record.id = new_id()
        if not record.idempotency_key:
            record.idempotency_key = (
                f"{record.device_id}:{record.device_employee_code}:"
                f"{record.punch_time}:{record.event_type}"
            )
        existing = self._db.fetchone(
            "SELECT id FROM attendance_queue WHERE idempotency_key = ?",
            (record.idempotency_key,),
        )
        if existing:
            return False, record

        self._db.execute(
            """
            INSERT INTO attendance_queue (
                id, device_id, device_employee_code, punch_time, event_type,
                verification_method, biometric_reference, idempotency_key, status,
                retry_count, last_error, raw_payload, created_at, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.device_id,
                record.device_employee_code,
                record.punch_time,
                record.event_type,
                record.verification_method,
                record.biometric_reference,
                record.idempotency_key,
                record.status or QueueStatus.PENDING.value,
                record.retry_count,
                record.last_error,
                dumps(record.raw_payload or {}),
                record.created_at or utcnow().isoformat(),
                record.uploaded_at,
            ),
        )
        return True, record

    def _punch_identity(self, record: AttendanceRecord) -> tuple[str, str, str]:
        raw = record.raw_payload if isinstance(record.raw_payload, dict) else {}
        stamp = str(raw.get("device_punch_time") or record.punch_time)
        return (str(record.device_id), str(record.device_employee_code), stamp)

    def _drop_identity_duplicates(
        self, records: list[AttendanceRecord]
    ) -> list[AttendanceRecord]:
        """Skip punches already stored for the same device/employee/device stamp.

        Classification (IN vs OUT) is not part of punch identity, so a later
        reclassify + sync cannot insert a second row.
        """
        device_ids = list({str(record.device_id) for record in records if record.device_id})
        seen: set[tuple[str, str, str]] = set()
        if device_ids:
            placeholders = ",".join("?" for _ in device_ids)
            rows = self._db.fetchall(
                f"""
                SELECT device_id, device_employee_code,
                       COALESCE(json_extract(raw_payload, '$.device_punch_time'), punch_time) AS stamp
                FROM attendance_queue
                WHERE device_id IN ({placeholders})
                """,
                tuple(device_ids),
            )
            seen = {
                (str(row["device_id"]), str(row["device_employee_code"]), str(row["stamp"]))
                for row in rows
            }
        out: list[AttendanceRecord] = []
        for record in records:
            ident = self._punch_identity(record)
            if ident in seen:
                continue
            seen.add(ident)
            out.append(record)
        return out

    def list_for_classification(
        self, device_id: str, employee_codes: list[str]
    ) -> list[dict]:
        codes = [str(code).strip() for code in employee_codes if str(code).strip()]
        if not device_id or not codes:
            return []
        placeholders = ",".join("?" for _ in codes)
        rows = self._db.fetchall(
            f"""
            {self._PUNCH_SELECT}
            WHERE a.device_id = ?
              AND a.device_employee_code IN ({placeholders})
            ORDER BY a.punch_time ASC
            """,
            (device_id, *codes),
        )
        return [self._punch_dict(row) for row in rows]

    def reclassify_attendance_events(self, device_id: str | None = None) -> int:
        """Recompute IN/OUT for stored punches without inventing device-reported state."""
        from adapters.protocol.zk_attendance_fields import (
            interpret_zk_log_fields,
            zk_logs_report_direction,
        )
        from services.punch_classifier import apply_classification, classify_punch_groups

        if device_id:
            rows = self._db.fetchall(
                f"{self._PUNCH_SELECT} WHERE a.device_id = ? ORDER BY a.punch_time ASC",
                (device_id,),
            )
        else:
            rows = self._db.fetchall(
                f"{self._PUNCH_SELECT} ORDER BY a.punch_time ASC"
            )
        items = [self._punch_dict(row) for row in rows]
        if not items:
            return 0
        prepared: list[dict] = []
        for item in items:
            raw = dict(item.get("raw_payload") or {}) if isinstance(item.get("raw_payload"), dict) else {}
            if raw.get("device_status") is None and (
                raw.get("status") is not None or raw.get("punch") is not None
            ):
                raw.update(interpret_zk_log_fields(raw.get("status"), raw.get("punch")))
            item["raw_payload"] = raw
            item["device_direction"] = raw.get("device_direction")
            item["direction_reported"] = bool(raw.get("direction_reported"))
            if str(item.get("verification_method") or "not_reported") == "not_reported":
                method = str(raw.get("verification_method") or "").strip()
                if method and method != "not_reported":
                    item["verification_method"] = method
            prepared.append(item)
        by_device: dict[str, list[dict]] = {}
        for item in prepared:
            by_device.setdefault(str(item.get("device_id") or ""), []).append(item)
        updated = 0
        for group in by_device.values():
            reports = zk_logs_report_direction(group)
            for item, classified in classify_punch_groups(
                group, device_reports_direction=reports
            ):
                original_event = str(item.get("event_type") or "")
                original_method = str(item.get("verification_method") or "")
                original_raw = (
                    dict(item.get("raw_payload"))
                    if isinstance(item.get("raw_payload"), dict)
                    else {}
                )
                apply_classification(item, classified)
                method = str(item.get("verification_method") or original_method or "not_reported")
                raw = item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}
                if (
                    classified.event_type == original_event
                    and method == original_method
                    and original_raw.get("classification_method") == classified.classification_method
                    and original_raw.get("classification_reason_code")
                    == classified.classification_reason_code
                ):
                    continue
                self._db.execute(
                    """
                    UPDATE attendance_queue
                    SET event_type = ?, verification_method = ?, raw_payload = ?
                    WHERE id = ?
                    """,
                    (classified.event_type, method, dumps(raw), item["id"]),
                )
                updated += 1
        return updated

    def enqueue_many(self, records: list[AttendanceRecord]) -> int:
        """Bulk insert with one SQLite transaction. Duplicates are ignored."""
        if not records:
            return 0
        records = self._drop_identity_duplicates(records)
        if not records:
            return 0
        rows: list[tuple] = []
        for record in records:
            if not record.id:
                record.id = new_id()
            if not record.idempotency_key:
                record.idempotency_key = (
                    f"{record.device_id}:{record.device_employee_code}:"
                    f"{record.punch_time}:{record.event_type}"
                )
            rows.append(
                (
                    record.id,
                    record.device_id,
                    record.device_employee_code,
                    record.punch_time,
                    record.event_type,
                    record.verification_method,
                    record.biometric_reference,
                    record.idempotency_key,
                    record.status or QueueStatus.PENDING.value,
                    record.retry_count,
                    record.last_error,
                    dumps(record.raw_payload or {}),
                    record.created_at or utcnow().isoformat(),
                    record.uploaded_at,
                )
            )
        with self._db.cursor() as cur:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT COUNT(*) AS c FROM attendance_queue")
            before = int(cur.fetchone()["c"])
            cur.executemany(
                """
                INSERT OR IGNORE INTO attendance_queue (
                    id, device_id, device_employee_code, punch_time, event_type,
                    verification_method, biometric_reference, idempotency_key, status,
                    retry_count, last_error, raw_payload, created_at, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            cur.execute("SELECT COUNT(*) AS c FROM attendance_queue")
            after = int(cur.fetchone()["c"])
        return max(0, after - before)

    def list_recent_for_device(self, device_id: str, limit: int = 100) -> list[dict]:
        """Stored punch history for one terminal, including wrong device-clock dates."""
        return self._list_punches(
            where="a.device_id = ?",
            params=(device_id,),
            limit=limit,
        )

    def list_pending(self, limit: int = 200) -> list[AttendanceRecord]:
        rows = self._db.fetchall(
            """
            SELECT * FROM attendance_queue
            WHERE status IN ('pending', 'failed')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_record(r) for r in rows]

    def list_pending_for_device(self, device_id: str, limit: int = 200) -> list[AttendanceRecord]:
        rows = self._db.fetchall(
            """
            SELECT * FROM attendance_queue
            WHERE device_id = ? AND status IN ('pending', 'failed')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (device_id, limit),
        )
        return [self._row_to_record(r) for r in rows]

    def mark_uploading(self, ids: list[str]) -> None:
        if not ids:
            return
        now = utcnow().isoformat()
        placeholders = ",".join("?" for _ in ids)
        self._db.execute(
            f"""
            UPDATE attendance_queue
            SET status='uploading', uploading_at=?
            WHERE id IN ({placeholders})
            """,
            (now, *ids),
        )

    def mark_uploaded(self, ids: list[str]) -> None:
        if not ids:
            return
        now = utcnow().isoformat()
        placeholders = ",".join("?" for _ in ids)
        self._db.execute(
            f"""
            UPDATE attendance_queue
            SET status='uploaded', uploaded_at=?, last_error=''
            WHERE id IN ({placeholders})
            """,
            (now, *ids),
        )

    def mark_retry_later(self, ids: list[str], error: str, *, increment_retry: bool = True) -> None:
        """Return rows to PENDING after a transient API/TLS failure. Never discards them."""
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        retry_sql = "retry_count = retry_count + 1, " if increment_retry else ""
        self._db.execute(
            f"""
            UPDATE attendance_queue
            SET status='pending', {retry_sql}last_error=?, uploading_at=NULL, last_attempt_at=?
            WHERE id IN ({placeholders})
            """,
            (error[:2000], utcnow().isoformat(), *ids),
        )

    def mark_failed(self, ids: list[str], error: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._db.execute(
            f"""
            UPDATE attendance_queue
            SET status='failed', retry_count = retry_count + 1, last_error=?, uploading_at=NULL
            WHERE id IN ({placeholders})
            """,
            (error[:2000], *ids),
        )

    def mark_dead(self, ids: list[str], error: str) -> None:
        """Move poison / exhausted-retry rows out of the active upload queue."""
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._db.execute(
            f"""
            UPDATE attendance_queue
            SET status='dead', last_error=?, uploading_at=NULL
            WHERE id IN ({placeholders})
            """,
            (error[:2000], *ids),
        )

    def pending_count(self) -> int:
        row = self._db.fetchone(
            "SELECT COUNT(*) AS c FROM attendance_queue WHERE status IN ('pending', 'failed', 'uploading')"
        )
        return int(row["c"]) if row else 0

    def dead_count(self) -> int:
        row = self._db.fetchone(
            "SELECT COUNT(*) AS c FROM attendance_queue WHERE status = 'dead'"
        )
        return int(row["c"]) if row else 0

    def failed_count(self) -> int:
        row = self._db.fetchone(
            "SELECT COUNT(*) AS c FROM attendance_queue WHERE status = 'failed'"
        )
        return int(row["c"]) if row else 0

    LIVE_REMAP_MAX_SKEW_SECONDS = 15 * 60
    LIVE_REMAP_MAX_BATCH = 5

    @staticmethod
    def _remapped_unset_clock_sql(alias: str = "") -> str:
        """Year-2000 / unset RTC stamps (including date remapped onto download day)."""
        col = f"{alias}." if alias else ""
        return (
            "("
            f" CAST(substr({col}punch_time, 1, 4) AS INTEGER) < {UNSET_DEVICE_CLOCK_YEAR}"
            f" OR ("
            f"  json_extract({col}raw_payload, '$.device_punch_time') IS NOT NULL"
            f"  AND CAST(substr(json_extract({col}raw_payload, '$.device_punch_time'), 1, 4)"
            f"      AS INTEGER) < {UNSET_DEVICE_CLOCK_YEAR}"
            f" )"
            ")"
        )

    @classmethod
    def _device_stamp_year(cls, item: dict) -> int:
        raw = item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}
        stamp = str(item.get("device_punch_time") or raw.get("device_punch_time") or "")
        if stamp:
            year = stamp[:4]
            if year.isdigit():
                return int(year)
        punch_year = str(item.get("punch_time") or item.get("normalized_timestamp") or "")[:4]
        return int(punch_year) if punch_year.isdigit() else 9999

    @classmethod
    def _collection_bucket(cls, item: dict) -> str:
        return str(item.get("created_at") or "")[:19]

    @classmethod
    def _near_collection(cls, item: dict) -> bool:
        punch = parse_datetime(item.get("normalized_timestamp") or item.get("punch_time"))
        created = parse_datetime(item.get("created_at"))
        if not isinstance(punch, datetime) or not isinstance(created, datetime):
            return False
        if punch.tzinfo is not None and created.tzinfo is None:
            created = created.replace(tzinfo=punch.tzinfo)
        elif punch.tzinfo is None and created.tzinfo is not None:
            punch = punch.replace(tzinfo=created.tzinfo)
        try:
            skew = abs((punch - created).total_seconds())
        except TypeError:
            return False
        if skew <= float(cls.LIVE_REMAP_MAX_SKEW_SECONDS):
            return True
        # Date-segregated remap: same calendar day as collection still counts
        # as a live row for small batches (history dumps are excluded by size).
        try:
            return punch.date() == created.date()
        except Exception:
            return False

    @classmethod
    def _filter_calendar_rows(cls, rows: list[dict]) -> list[dict]:
        """Keep valid-dated punches; keep only small live unset-clock remaps.

        A first sync often downloads the whole terminal history stamped year
        2000/2002. Those land in one created_at second and must not fill Daily.
        """
        from collections import Counter

        unset_buckets = Counter(
            cls._collection_bucket(row)
            for row in rows
            if cls._device_stamp_year(row) < UNSET_DEVICE_CLOCK_YEAR
        )
        out: list[dict] = []
        for row in rows:
            year = cls._device_stamp_year(row)
            if year >= UNSET_DEVICE_CLOCK_YEAR:
                punch_year = str(row.get("punch_time") or "")[:4]
                if punch_year.isdigit() and int(punch_year) < UNSET_DEVICE_CLOCK_YEAR:
                    continue
                out.append(row)
                continue
            bucket = cls._collection_bucket(row)
            if unset_buckets[bucket] > cls.LIVE_REMAP_MAX_BATCH:
                continue
            if cls._near_collection(row):
                out.append(row)
        return out

    @classmethod
    def _counts_on_calendar(cls, item: dict) -> bool:
        return bool(cls._filter_calendar_rows([item]))

    @staticmethod
    def _today_punch_sql(alias: str = "") -> str:
        # Candidate rows for "today". Calendar eligibility is applied in Python
        # so live unset-clock remaps can count while history dumps cannot.
        col = f"{alias}." if alias else ""
        today = "date('now', 'localtime')"
        return f"substr({col}punch_time, 1, 10) = {today}"

    def rewrite_unset_clock_punch_times(self) -> int:
        """Re-map stuck year-2000/2002 punch_time values onto the collection day.

        Only rewrites rows whose *stored* punch_time is still pre-2010. Mixed
        2000/2002 history that was already shifted onto other modern dates is
        left alone so Daily is not flooded with a first-sync dump.
        """
        rows = self._db.fetchall(
            """
            SELECT id, device_id, punch_time, created_at, raw_payload
            FROM attendance_queue
            WHERE CAST(substr(punch_time, 1, 4) AS INTEGER) < 2010
            """
        )
        updated = 0
        for row in rows:
            raw: dict = {}
            payload = row["raw_payload"]
            if isinstance(payload, str):
                try:
                    raw = json.loads(payload or "{}")
                except json.JSONDecodeError:
                    raw = {"raw": payload}
            elif isinstance(payload, dict):
                raw = dict(payload)
            source = raw.get("device_punch_time") or row["punch_time"]
            created_dt = parse_datetime(row["created_at"]) or datetime.now()
            # Use the real collection clock so Daily/Live show the sync time,
            # not a fake year-2000 time-of-day (e.g. 18:37) on today's date.
            corrected = correct_unset_device_clock(
                source,
                created_dt,
                device_now=None,
            )
            if corrected is None:
                corrected = created_dt if isinstance(created_dt, datetime) else datetime.now()
            new_iso = format_api_datetime(corrected)
            if new_iso[:4].isdigit() and int(new_iso[:4]) < UNSET_DEVICE_CLOCK_YEAR:
                new_iso = format_api_datetime(created_dt)
            if new_iso == row["punch_time"] and raw.get("normalized_timestamp") == new_iso:
                continue
            raw.setdefault("device_punch_time", source)
            raw["clock_corrected"] = True
            raw["normalized_timestamp"] = new_iso
            raw["date_segregated"] = True
            self._db.execute(
                "UPDATE attendance_queue SET punch_time = ?, raw_payload = ? WHERE id = ?",
                (new_iso, dumps(raw), row["id"]),
            )
            updated += 1
        # Clear device telemetry that still advertises year-2000/2002 last punches.
        try:
            self._db.execute(
                """
                UPDATE devices
                SET last_attendance_at = ''
                WHERE last_attendance_at IS NOT NULL
                  AND last_attendance_at != ''
                  AND CAST(substr(last_attendance_at, 1, 4) AS INTEGER) < 2010
                """
            )
            self._db.execute(
                """
                UPDATE devices
                SET last_sync_cursor = ''
                WHERE last_sync_cursor IS NOT NULL
                  AND last_sync_cursor != ''
                  AND CAST(substr(last_sync_cursor, 1, 4) AS INTEGER) < 2010
                """
            )
        except Exception:
            pass
        return updated

    def today_count(self) -> int:
        return len(self.list_today(limit=20000))

    def list_today(self, limit: int = 1000) -> list[dict]:
        """Today's calendar punches (valid stamps + live unset-clock remaps)."""
        rows = self._list_punches(
            where=self._today_punch_sql("a"),
            limit=max(1, int(limit)) * 5,
        )
        return self._filter_calendar_rows(rows)[: max(1, int(limit))]

    def count_today_for_device(self, device_id: str) -> int:
        rows = self._list_punches(
            where=f"a.device_id = ? AND {self._today_punch_sql('a')}",
            params=(device_id,),
            limit=20000,
        )
        return len(self._filter_calendar_rows(rows))

    def count_for_device(self, device_id: str) -> int:
        row = self._db.fetchone(
            "SELECT COUNT(*) AS c FROM attendance_queue WHERE device_id = ?",
            (device_id,),
        )
        return int(row["c"]) if row else 0

    def list_recent(self, limit: int = 200) -> list[dict]:
        """Recent punches regardless of device clock date (newest first)."""
        return self._list_punches(where="", limit=limit)

    _PUNCH_SELECT = """
            SELECT
                a.id,
                a.device_id,
                a.device_employee_code,
                a.punch_time,
                a.event_type,
                a.verification_method,
                a.status,
                a.retry_count,
                a.last_error,
                a.raw_payload,
                a.created_at,
                a.uploaded_at,
                COALESCE(d.name, '') AS device_name,
                COALESCE(d.terminal_code, '') AS terminal_code,
                COALESCE(d.branch, '') AS branch,
                COALESCE(d.organization, '') AS organization,
                COALESCE(NULLIF(e.name, ''), '') AS hrms_employee_name,
                COALESCE(NULLIF(du.name, ''), '') AS device_user_name,
                COALESCE(
                    NULLIF(e.name, ''),
                    NULLIF(du.name, ''),
                    ''
                ) AS employee_name
            FROM attendance_queue a
            LEFT JOIN devices d ON d.id = a.device_id
            LEFT JOIN employees e ON e.employee_code = a.device_employee_code
            LEFT JOIN device_users du
                ON du.device_id = a.device_id AND du.user_id = a.device_employee_code
    """

    def _list_punches(self, *, where: str, params: tuple = (), limit: int = 200) -> list[dict]:
        clause = f"WHERE {where}" if where else ""
        rows = self._db.fetchall(
            f"""
            {self._PUNCH_SELECT}
            {clause}
            ORDER BY a.punch_time DESC, a.created_at DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        )
        return self._newest_punch_first([self._punch_dict(row) for row in rows])

    @staticmethod
    def _newest_punch_first(rows: list[dict]) -> list[dict]:
        def key(row: dict) -> tuple[str, str]:
            return (
                str(row.get("normalized_timestamp") or row.get("punch_time") or ""),
                str(row.get("created_at") or ""),
            )

        return sorted(rows, key=key, reverse=True)

    def _punch_dict(self, row) -> dict:
        from services.employee_directory import enrich_employee_fields
        from services.punch_normalizer import enrich_punch_record

        item = dict(row)
        raw = item.get("raw_payload")
        if isinstance(raw, str):
            try:
                item["raw_payload"] = json.loads(raw or "{}")
            except json.JSONDecodeError:
                item["raw_payload"] = {"raw": raw}
        elif not isinstance(raw, dict):
            item["raw_payload"] = {}
        raw_device = (item.get("raw_payload") or {}).get("device_punch_time")
        device_punch = raw_device or item.get("punch_time")
        item["device_punch_time"] = device_punch
        stored = str(item.get("punch_time") or "")
        stored_year = int(stored[:4]) if stored[:4].isdigit() else 0
        # Do not re-collapse already date-corrected rows onto created_at.
        if stored_year >= UNSET_DEVICE_CLOCK_YEAR:
            raw_payload = item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}
            item["normalized_timestamp"] = raw_payload.get("normalized_timestamp") or stored
        else:
            corrected = correct_unset_device_clock(device_punch, item.get("created_at"))
            if corrected is not None:
                item["punch_time"] = format_api_datetime(corrected)
                item["normalized_timestamp"] = item["punch_time"]
        enrich_employee_fields(item)
        enrich_punch_record(item)
        return item

    def pending_count_for_device(self, device_id: str) -> int:
        row = self._db.fetchone(
            """
            SELECT COUNT(*) AS c FROM attendance_queue
            WHERE device_id = ? AND status IN ('pending', 'failed', 'uploading')
            """,
            (device_id,),
        )
        return int(row["c"]) if row else 0

    def uploaded_today_count(self) -> int:
        row = self._db.fetchone(
            """
            SELECT COUNT(*) AS c FROM attendance_queue
            WHERE status = 'uploaded'
              AND date(COALESCE(uploaded_at, created_at)) = date('now', 'localtime')
            """
        )
        return int(row["c"]) if row else 0

    def last_uploaded_at(self) -> Optional[str]:
        row = self._db.fetchone(
            "SELECT uploaded_at FROM attendance_queue WHERE status='uploaded' ORDER BY uploaded_at DESC LIMIT 1"
        )
        return row["uploaded_at"] if row else None

    def uploaded_count(self) -> int:
        row = self._db.fetchone(
            "SELECT COUNT(*) AS c FROM attendance_queue WHERE status = 'uploaded'"
        )
        return int(row["c"]) if row else 0

    def count_by_status(self) -> dict[str, int]:
        rows = self._db.fetchall(
            "SELECT status, COUNT(*) AS c FROM attendance_queue GROUP BY status"
        )
        out = {s: 0 for s in ("pending", "uploading", "uploaded", "failed", "dead", "duplicate")}
        for row in rows:
            out[str(row["status"] or "")] = int(row["c"] or 0)
        return out

    def list_filtered(
        self,
        *,
        search: str = "",
        device_id: str = "",
        branch: str = "",
        event_type: str = "",
        status: str = "",
        employee: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        where: list[str] = []
        params: list = []
        if device_id:
            where.append("a.device_id = ?")
            params.append(device_id)
        if branch:
            where.append("d.branch = ?")
            params.append(branch)
        if event_type:
            where.append("UPPER(a.event_type) = ?")
            params.append(event_type.upper())
        if status:
            where.append("a.status = ?")
            params.append(status)
        if employee:
            where.append(
                "(a.device_employee_code LIKE ? OR IFNULL(e.name,'') LIKE ? OR IFNULL(du.name,'') LIKE ?)"
            )
            like = f"%{employee.strip()}%"
            params.extend([like, like, like])
        if search:
            like = f"%{search.strip()}%"
            where.append(
                "(a.device_employee_code LIKE ? OR IFNULL(e.name,'') LIKE ? OR IFNULL(du.name,'') LIKE ?"
                " OR IFNULL(d.name,'') LIKE ? OR IFNULL(d.terminal_code,'') LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        if date_from:
            where.append("substr(COALESCE(a.punch_time, a.created_at), 1, 10) >= ?")
            params.append(date_from[:10])
        if date_to:
            where.append("substr(COALESCE(a.punch_time, a.created_at), 1, 10) <= ?")
            params.append(date_to[:10])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        count_row = self._db.fetchone(
            f"""
            SELECT COUNT(*) AS c
            FROM attendance_queue a
            LEFT JOIN devices d ON d.id = a.device_id
            LEFT JOIN employees e ON e.employee_code = a.device_employee_code
            LEFT JOIN device_users du
                ON du.device_id = a.device_id AND du.user_id = a.device_employee_code
            {clause}
            """,
            tuple(params),
        )
        total = int(count_row["c"]) if count_row else 0
        rows = self._db.fetchall(
            f"""
            {self._PUNCH_SELECT}
            {clause}
            ORDER BY a.created_at DESC, a.punch_time DESC
            LIMIT ? OFFSET ?
            """,
            (*params, max(1, int(limit)), max(0, int(offset))),
        )
        return {
            "total": total,
            "limit": int(limit),
            "offset": int(offset),
            "items": [self._punch_dict(row) for row in rows],
        }

    def list_for_day(self, day: str, limit: int = 5000) -> list[dict]:
        day = str(day or "")[:10]
        rows = self._list_punches(
            where="substr(a.punch_time, 1, 10) = ?",
            params=(day,),
            limit=max(1, int(limit)) * 5,
        )
        return self._filter_calendar_rows(rows)[: max(1, int(limit))]

    def daily_attendance(self, day: str) -> list[dict]:
        from services.attendance_analytics import build_daily_rows

        return build_daily_rows(self.list_for_day(day), day=str(day)[:10])

    def monthly_attendance(self, year: int, month: int) -> dict:
        from services.attendance_analytics import build_monthly_rows

        return build_monthly_rows(self.list_for_month(year, month), year=year, month=month)

    def list_for_month(self, year: int, month: int, limit: int = 20000) -> list[dict]:
        prefix = f"{int(year):04d}-{int(month):02d}"
        rows = self._list_punches(
            where="substr(a.punch_time, 1, 7) = ?",
            params=(prefix,),
            limit=max(1, int(limit)) * 5,
        )
        return self._filter_calendar_rows(rows)[: max(1, int(limit))]

    def punch_trend(self, days: int = 7) -> list[dict]:
        days = max(1, min(31, int(days)))
        rows = self._db.fetchall(
            """
            SELECT
                substr(COALESCE(punch_time, created_at), 1, 10) AS day,
                COUNT(*) AS collected,
                SUM(CASE WHEN status = 'uploaded' THEN 1 ELSE 0 END) AS uploaded,
                SUM(CASE WHEN status IN ('failed', 'dead') THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status IN ('pending', 'uploading') THEN 1 ELSE 0 END) AS pending
            FROM attendance_queue
            WHERE substr(COALESCE(punch_time, created_at), 1, 10)
                  >= date('now', 'localtime', ?)
            GROUP BY day
            ORDER BY day ASC
            """,
            (f"-{days - 1} day",),
        )
        by_day = {str(r["day"]): r for r in rows}
        from datetime import date, timedelta

        today = date.today()
        out = []
        for offset in range(days - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            row = by_day.get(day)
            out.append(
                {
                    "day": day,
                    "collected": int(row["collected"]) if row else 0,
                    "uploaded": int(row["uploaded"]) if row else 0,
                    "failed": int(row["failed"]) if row else 0,
                    "pending": int(row["pending"]) if row else 0,
                }
            )
        return out

    def punch_trend_hourly(self, hours: int = 24) -> list[dict]:
        """Hourly collected/uploaded/failed counts for the last N hours."""
        hours = max(1, min(48, int(hours)))
        from datetime import datetime, timedelta

        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        slots = [now - timedelta(hours=offset) for offset in range(hours - 1, -1, -1)]
        start_key = slots[0].strftime("%Y-%m-%dT%H")
        rows = self._db.fetchall(
            """
            SELECT
                substr(replace(COALESCE(punch_time, created_at), ' ', 'T'), 1, 13) AS hour,
                COUNT(*) AS collected,
                SUM(CASE WHEN status = 'uploaded' THEN 1 ELSE 0 END) AS uploaded,
                SUM(CASE WHEN status IN ('failed', 'dead') THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status IN ('pending', 'uploading') THEN 1 ELSE 0 END) AS pending
            FROM attendance_queue
            WHERE replace(COALESCE(punch_time, created_at), ' ', 'T') >= ?
            GROUP BY hour
            """,
            (start_key,),
        )
        by_hour = {str(r["hour"]): r for r in rows}
        out = []
        for slot in slots:
            key = slot.strftime("%Y-%m-%dT%H")
            row = by_hour.get(key)
            out.append(
                {
                    "day": slot.strftime("%H:00"),
                    "hour": key,
                    "collected": int(row["collected"]) if row else 0,
                    "uploaded": int(row["uploaded"]) if row else 0,
                    "failed": int(row["failed"]) if row else 0,
                    "pending": int(row["pending"]) if row else 0,
                }
            )
        return out

    def clock_mismatches(self, limit: int = 50) -> list[dict]:
        """Punches whose stored attendance time is still in the unset-clock epoch.

        Original year-2000 ``device_punch_time`` values are provenance only.
        After punch_time is rewritten onto a modern calendar date they must not
        keep the dashboard in a permanent ~27-year mismatch.
        """
        rows = self._db.fetchall(
            f"""
            {self._PUNCH_SELECT}
            WHERE CAST(substr(a.punch_time, 1, 4) AS INTEGER) < ?
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (UNSET_DEVICE_CLOCK_YEAR, max(1, int(limit))),
        )
        items = [self._punch_dict(row) for row in rows]
        return [p for p in items if (p.get("clock") or {}).get("mismatch")]

    def _live_unset_rtc_samples(self, limit: int = 5) -> list[dict]:
        """Terminals whose latest health snapshot still reports an unset RTC."""
        rows = self._db.fetchall(
            """
            SELECT d.id AS device_id,
                   d.name AS device_name,
                   h.device_time AS device_time,
                   h.collected_at AS collected_at
            FROM devices d
            JOIN device_health h ON h.device_id = d.id
            WHERE d.enabled = 1
              AND h.device_time IS NOT NULL
              AND h.device_time != ''
              AND CAST(substr(h.device_time, 1, 4) AS INTEGER) BETWEEN 1 AND ?
              AND h.collected_at = (
                  SELECT MAX(h2.collected_at)
                  FROM device_health h2
                  WHERE h2.device_id = d.id
              )
              AND replace(substr(h.collected_at, 1, 19), 'T', ' ')
                  >= datetime('now', '-15 minutes')
              AND (
                  COALESCE(d.last_time_sync_at, '') = ''
                  OR h.collected_at > d.last_time_sync_at
              )
            ORDER BY h.collected_at DESC
            LIMIT ?
            """,
            (UNSET_DEVICE_CLOCK_YEAR - 1, max(1, int(limit))),
        )
        if not rows:
            return []
        from services.punch_normalizer import analyze_clock, connector_wall_clock

        now = connector_wall_clock()
        samples: list[dict] = []
        for row in rows:
            clock = analyze_clock(row["device_time"], now)
            samples.append(
                {
                    "device_id": row["device_id"],
                    "device_name": row["device_name"] or "Device",
                    "device_timestamp": clock.device_timestamp,
                    "created_at": row["collected_at"],
                    "clock": clock.to_dict(),
                }
            )
        return samples

    def clock_mismatch_summary(self) -> dict:
        row = self._db.fetchone(
            """
            SELECT COUNT(*) AS c
            FROM attendance_queue a
            WHERE CAST(substr(a.punch_time, 1, 4) AS INTEGER) < ?
            """,
            (UNSET_DEVICE_CLOCK_YEAR,),
        )
        unhealed = int(row["c"]) if row else 0
        samples = self.clock_mismatches(limit=5)
        live = self._live_unset_rtc_samples(limit=5)
        if live:
            seen = {str(item.get("device_id") or "") for item in live}
            merged = list(live)
            for item in samples:
                device_id = str(item.get("device_id") or "")
                if device_id not in seen:
                    merged.append(item)
                    seen.add(device_id)
            samples = merged[:5]
        count = unhealed if unhealed > 0 else len(live)
        return {
            "count": count,
            "critical": unhealed > 0 or bool(live),
            "samples": samples,
        }

    def list_queue(self, *, status: str = "", limit: int = 200) -> list[dict]:
        if status:
            return self._list_punches(where="a.status = ?", params=(status,), limit=limit)
        return self._list_punches(
            where="a.status IN ('pending', 'uploading', 'failed', 'dead')",
            limit=limit,
        )

    def p95_upload_latency_ms(self) -> float | None:
        """Best-effort P95 from recent successful upload sync logs."""
        rows = self._db.fetchall(
            """
            SELECT details_json FROM sync_logs
            WHERE action = 'upload' AND success = 1
            ORDER BY created_at DESC
            LIMIT 200
            """
        )
        samples: list[float] = []
        for row in rows:
            try:
                payload = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                continue
            ms = payload.get("duration_ms")
            if isinstance(ms, (int, float)) and ms > 0:
                samples.append(float(ms))
        if not samples:
            return None
        samples.sort()
        idx = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
        return samples[idx]

    def reset_stale_uploading(self, older_than_minutes: int = 60) -> int:
        """
        Recover rows left in 'uploading' after a crash/power loss.
        Uses uploading_at when present; falls back to created_at for legacy rows.
        """
        cutoff = f"-{int(older_than_minutes)} minutes"
        with self._db.cursor() as cur:
            cur.execute(
                """
                UPDATE attendance_queue
                SET status = 'pending', uploading_at = NULL
                WHERE status = 'uploading'
                  AND COALESCE(uploading_at, created_at) < datetime('now', ?)
                """,
                (cutoff,),
            )
            return cur.rowcount
