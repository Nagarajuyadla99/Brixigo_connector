"""Upload engine — batch, compress, retry attendance to Brixigo Cloud."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Callable, Optional

from api.cloud_client import CloudAPIError, CloudClient
from app.constants import (
    MAX_UPLOAD_RETRIES,
    UPLOAD_BACKOFF_BASE_SECONDS,
    UPLOAD_BACKOFF_MAX_SECONDS,
)
from models import AttendanceRecord
from services.connection_state import ConnectionStateRegistry, SyncStatus
from services.retry_policy import next_backoff_seconds
from services.punch_normalizer import is_retryable_upload_error
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import UploadMetricsRepository
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.upload")


class UploadEngine:
    def __init__(
        self,
        cloud: CloudClient,
        attendance: AttendanceRepository,
        devices: DeviceRepository,
        sync_logs: SyncLogRepository,
        *,
        batch_size: int = 200,
        max_retries: int = MAX_UPLOAD_RETRIES,
        retry_attempts: int = 3,
        metrics: UploadMetricsRepository | None = None,
        on_upload_failed: Optional[Callable[[str, str], None]] = None,
        on_auth_failed: Optional[Callable[[str], None]] = None,
        states: Optional["ConnectionStateRegistry"] = None,
        resolve_employee_name: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        self._cloud = cloud
        self._attendance = attendance
        self._devices = devices
        self._sync_logs = sync_logs
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.retry_attempts = max(1, retry_attempts)
        self._metrics = metrics
        self.on_upload_failed = on_upload_failed
        self.on_auth_failed = on_auth_failed
        self._states = states
        self._resolve_employee_name = resolve_employee_name

    def process_queue(self) -> dict:
        self._attendance.reset_stale_uploading(60)
        pending = self._attendance.list_pending(limit=self.batch_size * 5)
        if not pending:
            return {
                "uploaded": 0,
                "failed": 0,
                "batches": 0,
                "dead": 0,
                "paused": False,
                "pending": 0,
                "serialization_error": False,
                "error": "",
                "devices": {},
            }

        by_device: dict[str, list[AttendanceRecord]] = defaultdict(list)
        dead_ids: list[str] = []
        dead_errors: list[str] = []
        for rec in pending:
            if rec.retry_count >= self.max_retries and not is_retryable_upload_error(rec.last_error):
                dead_ids.append(rec.id)
                dead_errors.append(rec.last_error or "max retries exceeded")
                continue
            by_device[rec.device_id].append(rec)

        dead = 0
        if dead_ids:
            err = dead_errors[0] if len(set(dead_errors)) == 1 else "max retries exceeded"
            self._attendance.mark_dead(
                dead_ids,
                f"Dead-lettered after {self.max_retries} retries: {err}",
            )
            dead = len(dead_ids)
            logger.error(
                "Dead-lettered %s attendance rows after exhausting retries",
                dead,
            )
            if self.on_upload_failed:
                self.on_upload_failed(
                    f"{dead} punches dead-lettered after {self.max_retries} retries",
                    pending[0].device_id if pending else "",
                )

        uploaded = 0
        failed = 0
        batches = 0
        paused = False
        serialization_error = False
        last_error = ""
        devices_out: dict[str, dict] = {}

        for device_id, records in by_device.items():
            device = self._devices.get(device_id)
            if not device:
                ids = [r.id for r in records]
                self._attendance.mark_failed(ids, "Device missing locally")
                failed += len(ids)
                devices_out[device_id] = {
                    "uploaded": 0,
                    "failed": len(ids),
                    "pending": 0,
                    "error": "Device missing locally",
                    "serialization_error": False,
                }
                continue

            device_uploaded = 0
            device_failed = 0
            device_error = ""
            device_serialization = False
            for i in range(0, len(records), self.batch_size):
                chunk = records[i : i + self.batch_size]
                result = self._upload_batch(device.terminal_code, chunk, device=device)
                batches += 1
                uploaded += result["uploaded"]
                failed += result["failed"]
                device_uploaded += result["uploaded"]
                device_failed += result["failed"]
                if result.get("error"):
                    device_error = str(result.get("error") or "")
                    last_error = device_error
                if result.get("serialization_error"):
                    serialization_error = True
                    device_serialization = True
                if result.get("paused"):
                    paused = True
            devices_out[device_id] = {
                "uploaded": device_uploaded,
                "failed": device_failed,
                "pending": self._attendance.pending_count_for_device(device_id),
                "error": device_error,
                "serialization_error": device_serialization,
            }

        return {
            "uploaded": uploaded,
            "failed": failed,
            "batches": batches,
            "dead": dead,
            "paused": paused,
            "pending": self._attendance.pending_count(),
            "serialization_error": serialization_error,
            "error": last_error,
            "devices": devices_out,
        }

    def _upload_batch(self, terminal_code: str, records: list[AttendanceRecord], *, device=None) -> dict:
        ids = [r.id for r in records]
        self._attendance.mark_uploading(ids)
        attempt = 0
        last_error = ""
        started = time.perf_counter()

        while attempt < self.retry_attempts:
            attempt += 1
            try:
                if self._states:
                    self._states.set_sync(SyncStatus.SYNCING, pending=len(ids))
                names: dict[str, str] = {}
                if self._resolve_employee_name:
                    for rec in records:
                        code = str(rec.device_employee_code or "").strip()
                        if not code or code in names:
                            continue
                        try:
                            names[code] = str(
                                self._resolve_employee_name(rec.device_id, code) or ""
                            ).strip()
                        except Exception:
                            names[code] = ""
                resp = self._cloud.push_records_for_device(
                    terminal_code=terminal_code,
                    records=records,
                    manufacturer=getattr(device, "manufacturer", "") if device else "",
                    device_name=getattr(device, "name", "") if device else "",
                    branch=getattr(device, "branch", "") if device else "",
                    device_id=getattr(device, "id", "") if device else "",
                    timezone=getattr(device, "timezone", "") if device else "",
                    employee_names=names,
                )
                duration_ms = (time.perf_counter() - started) * 1000
                self._attendance.mark_uploaded(ids)
                if self._states:
                    self._states.set_last_sync(utcnow_iso())
                    remaining = self._attendance.pending_count()
                    self._states.set_sync(
                        SyncStatus.SYNCED if remaining == 0 else SyncStatus.PENDING,
                        pending=remaining,
                    )
                    self._states.record_api_result(kind="ok", latency_ms=duration_ms)
                if self._metrics:
                    self._metrics.record_success(len(ids), duration_ms)
                newest = max((r.punch_time for r in records), default=None)
                if newest:
                    self._devices.update_telemetry(
                        records[0].device_id, last_attendance_at=newest
                    )
                self._sync_logs.add(
                    device_id=records[0].device_id,
                    action="upload",
                    success=True,
                    message=f"Uploaded {len(ids)} punches",
                    records_count=len(ids),
                    details={"response": _safe(resp), "duration_ms": duration_ms},
                )
                logger.info(
                    "Uploaded %s punches terminal=%s",
                    len(ids),
                    terminal_code,
                    extra={"device_id": records[0].device_id},
                )
                return {"uploaded": len(ids), "failed": 0, "error": ""}
            except CloudAPIError as exc:
                last_error = str(exc)
                kind = exc.error_kind or "network"
                if self._states:
                    self._states.record_api_result(
                        kind=kind,
                        latency_ms=float(exc.duration_ms or 0),
                        error=last_error,
                        status_code=exc.status_code,
                    )
                if kind == "serialization":
                    # Keep rows PENDING. Serialization must never discard attendance.
                    self._attendance.mark_retry_later(ids, last_error, increment_retry=False)
                    if self._states:
                        self._states.set_sync(
                            SyncStatus.PENDING, pending=self._attendance.pending_count()
                        )
                    if self._metrics:
                        self._metrics.record_failure(len(ids), last_error)
                    if self.on_upload_failed:
                        self.on_upload_failed(last_error, records[0].device_id)
                    self._sync_logs.add(
                        device_id=records[0].device_id,
                        action="upload",
                        success=False,
                        message=last_error,
                        records_count=len(ids),
                    )
                    logger.error(
                        "Upload serialization failed terminal=%s count=%s err=%s",
                        terminal_code,
                        len(ids),
                        last_error,
                        extra={"device_id": records[0].device_id},
                    )
                    return {
                        "uploaded": 0,
                        "failed": len(ids),
                        "serialization_error": True,
                        "error": last_error,
                    }
                if exc.status_code in (401, 403) or kind == "auth":
                    if self._states:
                        self._states.set_sync(SyncStatus.PAUSED)
                    if self.on_auth_failed:
                        self.on_auth_failed(last_error)
                    self._attendance.mark_retry_later(ids, last_error, increment_retry=False)
                    logger.error(
                        "Upload paused — authentication required terminal=%s count=%s",
                        terminal_code,
                        len(ids),
                        extra={"device_id": records[0].device_id},
                    )
                    return {"uploaded": 0, "failed": 0, "paused": True}
                if exc.status_code == 404:
                    break
                if exc.transient:
                    delay = next_backoff_seconds(attempt)
                    logger.warning(
                        "SYNC_RETRY pending_records=%s next_retry_seconds=%.1f kind=%s",
                        len(ids),
                        delay,
                        kind,
                    )
                    time.sleep(delay)
                    continue
                delay = min(
                    UPLOAD_BACKOFF_BASE_SECONDS ** attempt,
                    UPLOAD_BACKOFF_MAX_SECONDS,
                )
                time.sleep(delay)
            except TypeError as exc:
                last_error = str(exc)
                if "serializ" in last_error.lower() or "datetime" in last_error.lower():
                    last_error = "Attendance timestamp could not be serialized to JSON."
                    self._attendance.mark_retry_later(ids, last_error, increment_retry=False)
                    if self._states:
                        self._states.set_sync(
                            SyncStatus.PENDING, pending=self._attendance.pending_count()
                        )
                    if self._metrics:
                        self._metrics.record_failure(len(ids), last_error)
                    if self.on_upload_failed:
                        self.on_upload_failed(last_error, records[0].device_id)
                    self._sync_logs.add(
                        device_id=records[0].device_id,
                        action="upload",
                        success=False,
                        message=last_error,
                        records_count=len(ids),
                    )
                    return {
                        "uploaded": 0,
                        "failed": len(ids),
                        "serialization_error": True,
                        "error": last_error,
                    }
                delay = next_backoff_seconds(attempt)
                logger.warning(
                    "SYNC_RETRY pending_records=%s next_retry_seconds=%.1f error=%s",
                    len(ids),
                    delay,
                    last_error[:160],
                )
                time.sleep(delay)
            except Exception as exc:
                last_error = str(exc)
                delay = next_backoff_seconds(attempt)
                logger.warning(
                    "SYNC_RETRY pending_records=%s next_retry_seconds=%.1f error=%s",
                    len(ids),
                    delay,
                    last_error[:160],
                )
                time.sleep(delay)

        # Transient API/TLS/network failures stay PENDING so attendance is never discarded.
        self._attendance.mark_retry_later(ids, last_error or "upload failed")
        if self._states:
            self._states.set_sync(SyncStatus.PENDING, pending=self._attendance.pending_count())
        if self._metrics:
            self._metrics.record_failure(len(ids), last_error or "upload failed")
        if self.on_upload_failed:
            self.on_upload_failed(last_error or "upload failed", records[0].device_id)
        self._sync_logs.add(
            device_id=records[0].device_id,
            action="upload",
            success=False,
            message=last_error or "upload failed",
            records_count=len(ids),
        )
        logger.error(
            "Upload deferred terminal=%s count=%s err=%s",
            terminal_code,
            len(ids),
            last_error,
            extra={"device_id": records[0].device_id},
        )
        return {"uploaded": 0, "failed": len(ids), "error": last_error or "upload failed"}


def utcnow_iso() -> str:
    from models import utcnow

    return utcnow().isoformat()


def _looks_transient(error: str) -> bool:
    text = (error or "").lower()
    markers = (
        "timeout",
        "timed out",
        "handshake",
        "ssl",
        "tls",
        "network",
        "temporarily",
        "unavailable",
        "connection reset",
        "503",
        "502",
        "504",
        "500",
    )
    return any(m in text for m in markers)


def _safe(payload) -> dict:
    if isinstance(payload, dict):
        return {k: payload[k] for k in list(payload)[:20]}
    return {"value": str(payload)[:500]}
