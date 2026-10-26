"""Multi-device poll scheduler with persistent ThreadPoolExecutor."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from app.constants import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
)
from models import Device
from services.connection_state import DeviceLinkStatus, DeviceNetworkStatus
from services.poll_service import PollService
from services.upload_engine import UploadEngine

logger = logging.getLogger("brixigo.sync")


class PollScheduler:
    """
    Independent per-device polling.
    A slow device occupies one worker; others continue on remaining workers.
    """

    def __init__(
        self,
        poll_service: PollService,
        upload_engine: UploadEngine,
        list_devices: Callable[[], list[Device]],
        *,
        max_workers: int = 16,
        upload_every_cycles: int = 1,
        states=None,
    ) -> None:
        self._poll = poll_service
        self._upload = upload_engine
        self._list_devices = list_devices
        self._max_workers = max(2, max_workers)
        self._upload_every_cycles = max(1, upload_every_cycles)
        self._states = states
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_poll: dict[str, float] = {}
        self._in_flight: set[str] = set()
        self._cycle = 0
        self._lock = threading.Lock()
        self._pool: Optional[ThreadPoolExecutor] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="brixigo-poll",
        )
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-poll-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("Poll scheduler started workers=%s", self._max_workers)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        if self._pool is not None:
            try:
                self._pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._pool.shutdown(wait=False)
            self._pool = None
        with self._lock:
            self._in_flight.clear()
        logger.info("Poll scheduler stopped")

    def configure_workers(self, max_workers: int) -> None:
        """Resize worker pool safely on remote config changes."""
        max_workers = max(2, int(max_workers))
        with self._lock:
            if max_workers == self._max_workers and self._pool is not None:
                return
            self._max_workers = max_workers
            old = self._pool
            self._pool = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="brixigo-poll",
            )
            if old is not None:
                try:
                    old.shutdown(wait=False, cancel_futures=False)
                except TypeError:
                    old.shutdown(wait=False)

    def sync_now(self, device_ids: list[str] | None = None) -> list[dict]:
        devices = self._list_devices()
        if device_ids:
            devices = [d for d in devices if d.id in device_ids]
        # Wait out any overlapping auto-poll so Sync now always does a real
        # download instead of returning a fake "success" with fetched=0.
        deadline = time.monotonic() + 75.0
        while time.monotonic() < deadline:
            with self._lock:
                busy = any(d.id in self._in_flight for d in devices)
            if not busy:
                break
            time.sleep(0.15)
        now = time.monotonic()
        claimed: list[Device] = []
        results: list[dict] = []
        with self._lock:
            for device in devices:
                if device.id in self._in_flight:
                    results.append(
                        {
                            "device_id": device.id,
                            "device_name": getattr(device, "name", ""),
                            "manufacturer": getattr(device, "manufacturer", ""),
                            "model": getattr(device, "model", ""),
                            "success": False,
                            "status": "SKIPPED",
                            "fetched": 0,
                            "inserted": 0,
                            "duplicates": 0,
                            "uploaded": 0,
                            "pending": 0,
                            "failed": 0,
                            "skipped": True,
                            "reason": "poll-in-progress",
                            "error": (
                                "Another poll was still running after waiting. "
                                "Try Sync now again in a few seconds."
                            ),
                            "duration_sec": 0,
                        }
                    )
                    continue
                claimed.append(device)
                self._in_flight.add(device.id)
                self._last_poll[device.id] = now
        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {
                    pool.submit(
                        self._poll.poll_device,
                        d,
                        full=False,
                        force_relocate=True,
                        refresh_users=True,
                    ): d.id
                    for d in claimed
                }
                for fut in as_completed(futures):
                    try:
                        results.append(fut.result())
                    except Exception as exc:
                        results.append(
                            {
                                "device_id": futures[fut],
                                "success": False,
                                "status": "FAILED",
                                "error": str(exc),
                                "fetched": 0,
                                "inserted": 0,
                                "uploaded": 0,
                                "pending": 0,
                                "failed": 0,
                            }
                        )
        finally:
            with self._lock:
                for device in claimed:
                    self._in_flight.discard(device.id)
        # Do not wait on cloud upload — the 1s scheduler already drains the
        # queue. Blocking here is what made Sync now sit on "Uploading".
        threading.Thread(
            target=self._safe_upload,
            name="brixigo-sync-upload",
            daemon=True,
        ).start()
        return results

    def _safe_upload(self) -> None:
        try:
            self._upload.process_queue()
        except Exception as exc:
            logger.error("Background upload after sync_now failed: %s", exc)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Scheduler tick failed: %s", exc)
            self._stop.wait(1.0)

    def _tick(self) -> None:
        now = time.monotonic()
        due: list[Device] = []
        with self._lock:
            for device in self._list_devices():
                if device.id in self._in_flight:
                    continue
                if self._states is not None:
                    link = self._states.device(device.id)
                    if link is not None and (
                        link.network_status == DeviceNetworkStatus.OFFLINE
                        or link.status == DeviceLinkStatus.DISCONNECTED
                        or (
                            link.status
                            in (DeviceLinkStatus.TIMEOUT, DeviceLinkStatus.DEGRADED)
                            and link.network_status != DeviceNetworkStatus.CONNECTED
                        )
                    ):
                        # Heartbeat owns offline / soft-timeout reconnect. Full poll
                        # here (3×3 SYN) held the port for minutes and caused flaps.
                        continue
                interval = max(
                    MIN_POLL_INTERVAL_SECONDS,
                    int(device.polling_interval or DEFAULT_POLL_INTERVAL_SECONDS),
                )
                last = self._last_poll.get(device.id, 0.0)
                if now - last >= interval:
                    due.append(device)
                    self._in_flight.add(device.id)
                    self._last_poll[device.id] = now

        pool = self._pool
        if due and pool is not None:
            for device in due:
                fut = pool.submit(self._poll_safe, device)
                fut.add_done_callback(lambda f, d=device: self._on_done(d, f))

        self._cycle += 1
        if self._cycle % self._upload_every_cycles == 0:
            try:
                self._upload.process_queue()
            except Exception as exc:
                logger.error("Upload cycle failed: %s", exc)

    def _poll_safe(self, device: Device) -> dict:
        return self._poll.poll_device(device, full=False)

    def _on_done(self, device: Device, fut: Future) -> None:
        try:
            fut.result()
        except Exception as exc:
            logger.error("Poll worker error device=%s: %s", device.id, exc)
        finally:
            with self._lock:
                self._in_flight.discard(device.id)


def _merge_sync_results(results: list[dict], upload: dict) -> list[dict]:
    """Attach upload outcomes to poll results. Failed uploads are not 'flushed'."""
    by_device = upload.get("devices") or {}
    for row in results:
        device_id = str(row.get("device_id") or "")
        stats = by_device.get(device_id) or {}
        row["uploaded"] = int(stats.get("uploaded") or 0)
        failed = int(stats.get("failed") or 0)
        row["upload_failed"] = failed
        row["failed"] = failed
        if "pending" in stats:
            row["pending"] = int(stats.get("pending") or 0)
        elif len(results) == 1:
            row["pending"] = int(upload.get("pending") or row.get("pending") or 0)
        serialization = bool(
            stats.get("serialization_error") or upload.get("serialization_error")
        )
        if serialization:
            row["serialization_error"] = True
        error = str(stats.get("error") or upload.get("error") or row.get("error") or "")
        if error:
            row["upload_error"] = error
            row.setdefault("error", error)
        poll_ok = bool(row.get("success"))
        upload_failed = failed > 0 or serialization
        if poll_ok and not upload_failed:
            row["success"] = True
            row["status"] = "SUCCESS"
        else:
            row["success"] = False
            row["status"] = "FAILED"
            if serialization and not row.get("error"):
                row["error"] = "Attendance timestamp could not be serialized to JSON."
    return results

