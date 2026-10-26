"""Live soak / self-check for poll → queue → upload path."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from api.cloud_client import CloudClient
from app.constants import APP_VERSION
from app.container import Container
from models import utcnow
from security.production_guard import validate_connector_settings
from services.device_manager import DeviceManager
from services.poll_service import PollService
from services.upload_engine import UploadEngine

logger = logging.getLogger("brixigo.service")


class SoakService:
    """
    One-shot or timed validation of the live attendance spine.
    Safe for pilot sites: does not wipe devices or clear attendance.
    """

    def __init__(self, container: Container, engine: Any | None = None) -> None:
        self.container = container
        self.engine = engine
        settings = container.settings.as_model()
        self.cloud = CloudClient(settings) if engine is None else engine.cloud
        self.poll = (
            engine.poll_service
            if engine is not None
            else PollService(
                container.devices,
                container.attendance,
                container.sync_logs,
                connect_timeout=settings.device_connect_timeout,
                read_timeout=settings.device_read_timeout,
            )
        )
        self.upload = (
            engine.upload_engine
            if engine is not None
            else UploadEngine(
                self.cloud,
                container.attendance,
                container.devices,
                container.sync_logs,
                batch_size=settings.upload_batch_size,
                max_retries=settings.max_upload_retries,
                retry_attempts=settings.retry_count,
            )
        )
        self.devices = (
            engine.device_manager
            if engine is not None
            else DeviceManager(container.devices, container.sync_logs)
        )

    def run(
        self,
        *,
        cycles: int = 1,
        sleep_seconds: float = 0,
        device_ids: list[str] | None = None,
        upload: bool = True,
    ) -> dict[str, Any]:
        settings = self.container.settings.as_model()
        guard = validate_connector_settings(settings)
        report: dict[str, Any] = {
            "started_at": utcnow().isoformat(),
            "version": APP_VERSION,
            "cycles": cycles,
            "guard": {"ok": guard.ok, "errors": guard.errors, "warnings": guard.warnings},
            "internet": False,
            "results": [],
            "summary": {},
        }

        try:
            report["internet"] = bool(self.cloud.internet_ok())
        except Exception as exc:
            report["internet_error"] = str(exc)

        total_fetched = 0
        total_inserted = 0
        total_uploaded = 0
        total_failed = 0
        device_errors = 0

        for cycle in range(1, max(1, cycles) + 1):
            cycle_row: dict[str, Any] = {"cycle": cycle, "devices": [], "upload": None}
            targets = self.container.devices.list_all(enabled_only=True)
            if device_ids:
                wanted = set(device_ids)
                targets = [d for d in targets if d.id in wanted or d.terminal_code in wanted]

            if not targets:
                cycle_row["warning"] = "No enabled devices to poll"
            for device in targets:
                try:
                    result = self.poll.poll_device(device)
                    cycle_row["devices"].append(result)
                    total_fetched += int(result.get("fetched") or 0)
                    total_inserted += int(result.get("inserted") or 0)
                    if not result.get("success", True) and not result.get("skipped"):
                        device_errors += 1
                except Exception as exc:
                    device_errors += 1
                    cycle_row["devices"].append(
                        {"device_id": device.id, "success": False, "error": str(exc)}
                    )

            if upload:
                try:
                    up = self.upload.process_queue()
                    cycle_row["upload"] = up
                    total_uploaded += int(up.get("uploaded") or 0)
                    total_failed += int(up.get("failed") or 0)
                except Exception as exc:
                    cycle_row["upload"] = {"error": str(exc)}
                    total_failed += 1

            report["results"].append(cycle_row)
            if sleep_seconds > 0 and cycle < cycles:
                time.sleep(sleep_seconds)

        pending = self.container.attendance.pending_count()
        report["summary"] = {
            "devices_enabled": len(self.container.devices.list_all(enabled_only=True)),
            "fetched": total_fetched,
            "inserted": total_inserted,
            "uploaded": total_uploaded,
            "upload_failed": total_failed,
            "device_errors": device_errors,
            "pending_queue": pending,
            "ok": device_errors == 0 and total_failed == 0 and report["internet"],
        }
        report["finished_at"] = utcnow().isoformat()
        logger.info("Soak complete summary=%s", report["summary"])
        return report

    def print_report(self, report: dict[str, Any]) -> None:
        print(json.dumps(report, indent=2, ensure_ascii=False))
