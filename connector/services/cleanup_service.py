"""Automatic cleanup for uploaded punches, health samples, and old logs."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from models import utcnow
from storage.database import Database
from storage.repositories.health_repo import DeviceHealthRepository

logger = logging.getLogger("brixigo.service")


class CleanupService:
    """
    Memory/disk optimization for high-volume deployments
    (100+ devices / 500k punches per day).
    """

    def __init__(
        self,
        db: Database,
        health_repo: DeviceHealthRepository,
        log_dir: Path,
        *,
        uploaded_retention_days: int = 14,
        log_retention_days: int = 30,
        interval_seconds: int = 3600,
    ) -> None:
        self._db = db
        self._health_repo = health_repo
        self._log_dir = log_dir
        self.uploaded_retention_days = max(1, uploaded_retention_days)
        self.log_retention_days = max(1, log_retention_days)
        self.interval_seconds = max(600, interval_seconds)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-cleanup", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def run_once(self) -> dict:
        cutoff = (utcnow() - timedelta(days=self.uploaded_retention_days)).isoformat()
        with self._db.cursor() as cur:
            cur.execute(
                """
                DELETE FROM attendance_queue
                WHERE status = 'uploaded' AND uploaded_at IS NOT NULL AND uploaded_at < ?
                """,
                (cutoff,),
            )
            deleted_uploads = cur.rowcount
            # Recover stuck uploading rows after crash (prefer uploading_at)
            cur.execute(
                """
                UPDATE attendance_queue
                SET status = 'pending', uploading_at = NULL
                WHERE status = 'uploading'
                  AND COALESCE(uploading_at, created_at) < datetime('now', '-1 hour')
                """
            )
            recovered = cur.rowcount
            # Retain dead-letter rows for investigation, prune very old ones
            cur.execute(
                """
                DELETE FROM attendance_queue
                WHERE status = 'dead'
                  AND created_at < datetime('now', ?)
                """,
                (f"-{max(30, self.uploaded_retention_days * 2)} days",),
            )
            deleted_dead = cur.rowcount

        health_deleted = self._health_repo.cleanup(keep_per_device=288)
        logs_deleted = self._cleanup_files()
        # Compact WAL opportunistically
        try:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:
            logger.debug("WAL checkpoint skipped: %s", exc)

        result = {
            "deleted_uploads": deleted_uploads,
            "recovered_uploading": recovered,
            "deleted_dead": deleted_dead,
            "deleted_health": health_deleted,
            "deleted_log_files": logs_deleted,
            "ran_at": utcnow().isoformat(),
        }
        logger.info("Cleanup complete %s", result)
        return result

    def _cleanup_files(self) -> int:
        if not self._log_dir.exists():
            return 0
        cutoff = datetime.now() - timedelta(days=self.log_retention_days)
        deleted = 0
        for path in self._log_dir.glob("*"):
            if not path.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if mtime < cutoff and path.suffix in {".log", ".txt", ".gz"}:
                    # Keep active primary logs
                    if path.name in {
                        "connector.log",
                        "connection.log",
                        "sync.log",
                        "upload.log",
                        "error.log",
                        "health.log",
                        "heartbeat.log",
                        "service.log",
                        "ui.log",
                        "crash.log",
                    }:
                        continue
                    path.unlink(missing_ok=True)
                    deleted += 1
            except Exception:
                continue
        return deleted

    def _loop(self) -> None:
        self.run_once()
        while not self._stop.is_set():
            self._stop.wait(self.interval_seconds)
            if self._stop.is_set():
                break
            try:
                self.run_once()
            except Exception as exc:
                logger.warning("Cleanup failed: %s", exc)
