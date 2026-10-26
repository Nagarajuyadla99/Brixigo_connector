"""Poll devices and enqueue new attendance logs."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional

from adapters.registry import create_adapter
from api.json_codec import (
    UNSET_DEVICE_CLOCK_YEAR,
    correct_unset_device_clock,
    format_api_datetime,
    to_json_value,
)
from adapters.protocol.zk_attendance_fields import (
    interpret_zk_log_fields,
    zk_logs_report_direction,
)
from services.punch_classifier import (
    apply_classification,
    classify_punch_groups,
)
from app.constants import (
    DEFAULT_TIME_DRIFT_THRESHOLD_SECONDS,
    FAST_TCP_CONNECT_TIMEOUT,
    FAST_TCP_PROBE_TIMEOUT,
    POST_TCP_CLOSE_DELAY_SECONDS,
)
from contracts.payroll_punch import build_idempotency_key
from models import AttendanceRecord, Device, DeviceStatus, QueueStatus, new_id
from services.connection_state import (
    is_hard_lan_unreachable,
    is_tcp_connect_failure,
    live_heartbeat_connected,
    tcp_probe,
)
from services.device_session_manager import DeviceSessionManager
from services.punch_normalizer import (
    analyze_clock,
    device_clock_is_modern,
    normalize_method_for_storage,
    write_device_clock,
)
from storage.repositories.attendance_repo import AttendanceRepository
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.sync")


class PollService:
    def __init__(
        self,
        devices: DeviceRepository,
        attendance: AttendanceRepository,
        sync_logs: SyncLogRepository,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        sessions: DeviceSessionManager | None = None,
        states=None,
        device_users=None,
        on_clock_mismatch=None,
        relocator: Callable | None = None,
    ) -> None:
        self._devices = devices
        self._attendance = attendance
        self._sync_logs = sync_logs
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._sessions = sessions
        self._states = states
        self._device_users = device_users
        self._on_clock_mismatch = on_clock_mismatch
        self._relocator = relocator
        self._user_refresh_after: dict[str, float] = {}

    def set_relocator(self, relocator: Callable | None) -> None:
        self._relocator = relocator

    def _publish_live_state(
        self,
        device: Device,
        *,
        network_ok: bool,
        protocol_ok: bool,
        latency_ms: float = 0.0,
        error: str = "",
        timeout: bool = False,
    ) -> None:
        """Keep Devices / header badges in sync with Sync Now and auto-poll."""
        if self._states is None:
            return
        try:
            self._states.record_device_heartbeat(
                device.id,
                network_ok=network_ok,
                protocol_ok=protocol_ok,
                latency_ms=float(latency_ms or 0.0),
                error="" if (network_ok and protocol_ok) else (error or ""),
                ip_address=device.ip_address,
                port=int(device.port or 4370),
                name=device.name,
                timeout=timeout,
                protocol_code="OK" if protocol_ok else "",
                failure_stage="" if protocol_ok else ("tcp_connect" if not network_ok else "protocol"),
                protocol_attempted=True,
            )
        except Exception:
            logger.debug(
                "Live state publish skipped device=%s",
                device.name,
                extra={"device_id": device.id},
                exc_info=True,
            )

    @staticmethod
    def _live_device_clock(clock_fix) -> Optional[datetime]:
        """Clock currently on the terminal, preferring a successful post-write reading."""
        if not isinstance(clock_fix, dict):
            return None
        candidates: list = []
        if clock_fix.get("corrected") and clock_fix.get("success"):
            candidates.append(clock_fix.get("device_time"))
        candidates.extend(
            [
                clock_fix.get("device_time"),
                clock_fix.get("read_device_time"),
                clock_fix.get("previous_time"),
            ]
        )
        modern = next(
            (
                value
                for value in candidates
                if isinstance(value, datetime) and device_clock_is_modern(value)
            ),
            None,
        )
        if modern is not None:
            return modern
        return next(
            (value for value in candidates if isinstance(value, datetime)),
            None,
        )

    def _confirm_live_device_clock(self, device: Device, clock_fix) -> Optional[datetime]:
        """Record last_time_sync_at when the live RTC is modern so old logs cannot re-flag."""
        observed = self._live_device_clock(clock_fix)
        wrote_ok = bool(
            isinstance(clock_fix, dict)
            and clock_fix.get("corrected")
            and clock_fix.get("success")
        )
        try:
            if wrote_ok or device_clock_is_modern(observed):
                self._devices.touch_field(device.id, "last_time_sync_at")
            elif observed is not None and not device_clock_is_modern(observed):
                # RTC is still (or again) in the unset epoch — banner may return.
                self._devices.touch_field(device.id, "last_time_sync_at", value="")
        except Exception:
            logger.debug(
                "last_time_sync_at update skipped device=%s", device.name
            )
        return observed

    def poll_device(
        self,
        device: Device,
        *,
        full: bool = True,
        force_relocate: bool = False,
        refresh_users: bool = False,
        _relocated: bool = False,
    ) -> dict:
        if not device.enabled:
            return {
                "device_id": device.id,
                "device_name": device.name,
                "skipped": True,
                "success": False,
                "reason": "disabled",
                "fetched": 0,
                "inserted": 0,
                "duplicates": 0,
                "uploaded": 0,
                "pending": 0,
                "failed": 0,
            }

        started = time.perf_counter()
        unreachable = self._fast_unreachable(device)
        if unreachable:
            moved, error = self._maybe_relocate(
                device, unreachable, force=force_relocate, already=_relocated
            )
            if moved is not None:
                return self.poll_device(
                    moved,
                    full=full,
                    force_relocate=False,
                    refresh_users=refresh_users,
                    _relocated=True,
                )
            return self._fail_result(device, error, started)
        # When heartbeat already confirmed LAN down, one attempt only —
        # 3×3 SYN stacks held the port for minutes and blocked recovery.
        session_retries = 3
        if self._states is not None:
            from services.connection_state import DeviceLinkStatus, DeviceNetworkStatus

            link = self._states.device(device.id)
            if link is not None and (
                link.network_status == DeviceNetworkStatus.OFFLINE
                or link.status == DeviceLinkStatus.DISCONNECTED
            ):
                session_retries = 1
        try:
            if self._sessions is not None:
                logs, info, users, clock_fix = self._sessions.execute(
                    device,
                    lambda adapter: self._collect_and_correct_clock(
                        adapter, full=full, refresh_users=refresh_users
                    ),
                    retries=session_retries,
                    owner="poll",
                )
            else:
                adapter = create_adapter(
                    device,
                    connect_timeout=min(self.connect_timeout, FAST_TCP_CONNECT_TIMEOUT),
                    read_timeout=self.read_timeout,
                )
                with adapter:
                    logs, info, users, clock_fix = self._collect_and_correct_clock(
                        adapter, full=full, refresh_users=refresh_users
                    )

            device_now = self._confirm_live_device_clock(device, clock_fix)
            if clock_fix and clock_fix.get("corrected") and clock_fix.get("success"):
                # After writing a modern clock, drop ancient unset-domain cursors so
                # the next incremental poll cannot filter out new punches.
                try:
                    old_cursor = PollService._parse_cursor(device.last_sync_cursor)
                    if old_cursor is not None and old_cursor.year < 2010:
                        self._devices.update_sync_cursor(device.id, cursor="")
                        device.last_sync_cursor = ""
                except Exception:
                    logger.debug(
                        "ancient cursor clear skipped device=%s", device.name
                    )

            if users and self._device_users is not None:
                try:
                    self._device_users.upsert_from_device_users(device.id, users)
                except Exception:
                    logger.debug("Device user cache update skipped device=%s", device.name)

            serial = (info.serial_number if info else "") or device.serial_number
            fetched, inserted, newest, clock = self._enqueue_logs(
                device, logs, device_now=device_now
            )
            # Historical year-2000 log buffer is not a live mismatch once RTC is modern.
            if (
                clock
                and clock.mismatch
                and self._on_clock_mismatch
                and not device_clock_is_modern(device_now)
            ):
                try:
                    self._on_clock_mismatch(device, clock)
                except Exception:
                    logger.debug("Clock mismatch callback failed device=%s", device.name)
            # Never re-anchor the sync cursor in the unset-clock epoch (year < 2010)
            # after the terminal itself is on a modern clock — that drops new punches.
            if newest is not None:
                modern_device = (
                    device_now is not None
                    and getattr(device_now, "year", 0) >= 2010
                )
                if modern_device and newest.year < 2010:
                    cursor = ""
                else:
                    cursor = newest.isoformat(sep=" ")
            else:
                cursor = device.last_sync_cursor
            self._devices.update_sync_cursor(device.id, cursor=cursor)
            self._devices.update_status(
                device.id,
                status=DeviceStatus.ONLINE.value,
                serial_number=serial,
                last_error="",
            )
            stored = self._attendance.count_for_device(device.id)
            # Never publish an unset-era stamp as "last attendance" (shows as year 2000/2002).
            last_att = ""
            if newest is not None and getattr(newest, "year", 0) >= UNSET_DEVICE_CLOCK_YEAR:
                last_att = newest.isoformat(sep=" ")
            elif inserted or fetched:
                last_att = datetime.now().isoformat(sep=" ", timespec="seconds")
            self._devices.update_telemetry(
                device.id,
                serial_number=serial or None,
                current_users=(info.user_count if info else None),
                current_logs=max(int(info.attendance_count) if info else 0, stored, fetched),
                last_attendance_at=last_att or None,
            )
            self._sync_logs.add(
                device_id=device.id,
                action="poll",
                success=True,
                message=f"Fetched {fetched}, enqueued {inserted} new punches",
                records_count=inserted,
            )
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._publish_live_state(
                device,
                network_ok=True,
                protocol_ok=True,
                latency_ms=duration_ms,
            )
            skipped = bool(clock_fix and clock_fix.get("skipped_unchanged"))
            logger.info(
                "Poll ok device=%s fetched=%s inserted=%s skipped_unchanged=%s",
                device.name,
                fetched,
                inserted,
                skipped,
                extra={"device_id": device.id},
            )
            return {
                "device_id": device.id,
                "device_name": device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "success": True,
                "status": "SUCCESS",
                "fetched": fetched,
                "inserted": inserted,
                "duplicates": max(0, fetched - inserted),
                "uploaded": 0,
                "pending": inserted,
                "failed": 0,
                "duration_sec": time.perf_counter() - started,
            }
        except Exception as exc:
            if not _relocated and is_tcp_connect_failure(exc):
                moved, error = self._maybe_relocate(
                    device, str(exc), force=force_relocate, already=False
                )
                if moved is not None:
                    return self.poll_device(
                        moved,
                        full=full,
                        force_relocate=False,
                        refresh_users=refresh_users,
                        _relocated=True,
                    )
                error_text = error
            else:
                error_text = str(exc)
            if is_tcp_connect_failure(exc):
                tcp_ok, tcp_msg = False, str(exc)
            else:
                tcp_ok, _, tcp_msg = tcp_probe(device.ip_address, int(device.port or 4370))
            if tcp_ok and tcp_msg != "session-in-use":
                logger.error(
                    "Poll failed device=%s but TCP %s:%s is open: %s",
                    device.name,
                    device.ip_address,
                    device.port,
                    exc,
                    extra={"device_id": device.id},
                )
                live = self._states.device(device.id) if self._states else None
                if live_heartbeat_connected(live):
                    logger.info(
                        "Poll error ignored for status; live heartbeat connected device=%s",
                        device.name,
                        extra={"device_id": device.id},
                    )
                else:
                    self._devices.update_status(
                        device.id,
                        status=DeviceStatus.DEGRADED.value,
                        last_error=f"poll error (device reachable): {exc}",
                    )
                    self._publish_live_state(
                        device,
                        network_ok=True,
                        protocol_ok=False,
                        error=str(exc),
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
            else:
                logger.error(
                    "Poll failed device=%s TCP closed (%s): %s",
                    device.name,
                    tcp_msg,
                    exc,
                    extra={"device_id": device.id},
                )
                self._devices.update_status(
                    device.id,
                    status=device.status or DeviceStatus.UNKNOWN.value,
                    last_error=str(exc),
                )
                self._publish_live_state(
                    device,
                    network_ok=False,
                    protocol_ok=False,
                    error=str(exc),
                    timeout="timed out" in str(exc).lower(),
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )
            self._sync_logs.add(
                device_id=device.id,
                action="poll",
                success=False,
                message=str(exc),
            )
            logger.error(
                "Poll failed device=%s: %s",
                device.name,
                exc,
                extra={"device_id": device.id},
            )
            serialization = (
                "serializ" in error_text.lower() or "datetime" in error_text.lower()
            )
            if serialization:
                error_text = "Attendance timestamp could not be serialized to JSON."
            return {
                "device_id": device.id,
                "device_name": device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "success": False,
                "status": "FAILED",
                "error": error_text,
                "serialization_error": serialization,
                "fetched": 0,
                "inserted": 0,
                "duplicates": 0,
                "uploaded": 0,
                "pending": 0,
                "failed": 0,
                "duration_sec": time.perf_counter() - started,
            }

    def _maybe_relocate(
        self,
        device: Device,
        probe_error: str,
        *,
        force: bool,
        already: bool,
    ) -> tuple[Optional[Device], str]:
        from services.network_scanner import (
            format_lan_unreachable,
            host_on_local_subnets,
            local_ipv4_addresses,
            local_ipv4_nics,
            local_subnet_specs,
        )

        result = None
        if not already and self._relocator is not None:
            try:
                result = self._relocator(device, force)
            except Exception:
                logger.debug(
                    "LAN relocate failed device=%s", device.name, exc_info=True
                )
                result = None
        if result is not None and result.relocated is not None:
            logger.info(
                "Poll retry after relocate device=%s ip=%s",
                device.name,
                result.relocated.ip_address,
            )
            return result.relocated, ""
        local_ips = tuple(result.local_ips) if result is not None else tuple(local_ipv4_addresses())
        scan_ranges = tuple(result.scan_ranges) if result is not None else tuple(local_subnet_specs())
        scanned = bool(result.scanned) if result is not None else False
        open_hosts = int(result.open_host_count) if result is not None else 0
        same = (
            bool(result.same_subnet)
            if result is not None
            else host_on_local_subnets(device.ip_address, local_ipv4_nics())
        )
        return None, format_lan_unreachable(
            device,
            probe_error,
            local_ips=local_ips,
            scan_ranges=scan_ranges,
            scanned=scanned,
            open_host_count=open_hosts,
            same_subnet=same,
        )

    def _fast_unreachable(self, device: Device) -> str:
        """Skip protocol only when the host is provably off this LAN.

        A SYN timeout is not proof the K90 is gone. Overnight Wi-Fi sleep
        leaves Windows ARP incomplete; a 2s probe then fails while an 8s
        protocol connect (after ARP refresh) still succeeds.
        """
        ok, _, msg = tcp_probe(
            device.ip_address,
            int(device.port or 4370),
            timeout=FAST_TCP_PROBE_TIMEOUT,
        )
        if ok:
            if msg != "session-in-use":
                time.sleep(POST_TCP_CLOSE_DELAY_SECONDS)
            return ""
        if msg in {"connecting", "probe-in-flight"}:
            return ""
        if is_hard_lan_unreachable(msg):
            return f"TCP connect failed: {msg}"
        return ""

    def _fail_result(self, device: Device, error: str, started: float) -> dict:
        logger.error(
            "Poll skipped device=%s: %s",
            device.name,
            error,
            extra={"device_id": device.id},
        )
        self._devices.update_status(
            device.id,
            status=device.status or DeviceStatus.UNKNOWN.value,
            last_error=error,
        )
        self._sync_logs.add(
            device_id=device.id,
            action="poll",
            success=False,
            message=error,
        )
        self._publish_live_state(
            device,
            network_ok=False,
            protocol_ok=False,
            error=error,
            timeout="timed out" in error.lower(),
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return {
            "device_id": device.id,
            "device_name": device.name,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "success": False,
            "status": "FAILED",
            "error": error,
            "fetched": 0,
            "inserted": 0,
            "duplicates": 0,
            "uploaded": 0,
            "pending": 0,
            "failed": 0,
            "duration_sec": time.perf_counter() - started,
        }

    def _enqueue_logs(
        self,
        device: Device,
        logs,
        device_now: Optional[datetime] = None,
    ) -> tuple[int, int, Optional[datetime], object]:
        fetched = len(logs)
        records: list[AttendanceRecord] = []
        newest: Optional[datetime] = None
        worst_clock = None
        collected_at = datetime.now()
        unset_punches = [
            log.punch_time
            for log in logs
            if getattr(log.punch_time, "year", 9999) < UNSET_DEVICE_CLOCK_YEAR
        ]
        device_rtc_modern = (
            device_now is not None
            and getattr(device_now, "year", 9999) >= UNSET_DEVICE_CLOCK_YEAR
        )
        # Anchor unset history to the newest *unset* stamp so relative days stay
        # split while the terminal RTC itself is still in the year-2000 epoch.
        # After the RTC is synced to a modern date, never use that modern reading
        # as the offset anchor — it yields ~0 shift and leaves punches on 2000.
        unset_batch_anchor = None
        if unset_punches:
            if device_rtc_modern:
                unset_batch_anchor = None
            elif device_now is not None:
                unset_batch_anchor = device_now
            else:
                unset_batch_anchor = max(unset_punches)
        pending_items: list[dict] = []
        for log in logs:
            raw = log.raw if isinstance(log.raw, dict) else {}
            if "device_status" not in raw:
                raw = {
                    **raw,
                    **interpret_zk_log_fields(log.status, log.punch),
                }
            method = normalize_method_for_storage(
                raw.get("verification_method") or raw.get("verify_mode"),
                punch=int(log.punch) if log.punch is not None else None,
            )
            if method == "not_reported" and raw.get("verification_method"):
                method = normalize_method_for_storage(raw.get("verification_method"))
            # Unset ZK clocks (year 2000) are shifted onto the connector clock.
            # Idempotency still uses the original device stamp so a later poll
            # is not a duplicate.
            device_punch_iso = format_api_datetime(
                log.punch_time, default_timezone=device.timezone
            )
            punch_unset = getattr(log.punch_time, "year", 9999) < UNSET_DEVICE_CLOCK_YEAR
            anchor = unset_batch_anchor if punch_unset else device_now
            corrected = correct_unset_device_clock(
                log.punch_time,
                collected_at,
                default_timezone=device.timezone,
                device_now=anchor,
            )
            punch_iso = format_api_datetime(
                corrected or log.punch_time, default_timezone=device.timezone
            )
            # Hard guard: never persist an unset-era punch_time (Daily would hide it).
            if punch_iso[:4].isdigit() and int(punch_iso[:4]) < UNSET_DEVICE_CLOCK_YEAR:
                punch_iso = format_api_datetime(
                    collected_at, default_timezone=device.timezone
                )
            clock = analyze_clock(
                log.punch_time, collected_at, default_timezone=device.timezone
            )
            # Year-2000 stamps left in the attendance buffer after RTC sync are
            # historical provenance, not a live terminal clock failure.
            if (
                clock.mismatch
                and not (punch_unset and device_rtc_modern)
                and (worst_clock is None or clock.critical)
            ):
                worst_clock = clock
            pending_items.append(
                {
                    "id": f"new-{len(pending_items)}",
                    "is_new": True,
                    "device_id": device.id,
                    "device_employee_code": str(log.user_id),
                    "punch_time": punch_iso,
                    "verification_method": method,
                    "device_direction": raw.get("device_direction"),
                    "direction_reported": bool(raw.get("direction_reported")),
                    "device_status": raw.get("device_status", log.status),
                    "device_punch": raw.get("device_punch", log.punch),
                    "raw_payload": raw,
                    "device_punch_iso": device_punch_iso,
                    "punch_unset": punch_unset,
                    "log": log,
                }
            )
            if newest is None or log.punch_time > newest:
                newest = log.punch_time

        name_cache: dict[str, str] = {}

        def _person_name(user_id: str) -> str:
            code = str(user_id or "").strip()
            if not code:
                return ""
            if code in name_cache:
                return name_cache[code]
            name = ""
            if self._device_users is not None and hasattr(self._device_users, "find_name"):
                try:
                    name = str(self._device_users.find_name(code, device_id=device.id) or "").strip()
                except Exception:
                    name = ""
            if name and name.lower() == code.lower():
                name = ""
            name_cache[code] = name
            return name

        existing: list[dict] = []
        try:
            codes = [str(item["device_employee_code"]) for item in pending_items]
            existing = self._attendance.list_for_classification(device.id, codes)
        except Exception:
            existing = []
        reports_direction = zk_logs_report_direction([*existing, *pending_items])
        classified_map = {
            str(item.get("id") or ""): classified
            for item, classified in classify_punch_groups(
                [*existing, *pending_items],
                device_reports_direction=reports_direction,
            )
        }
        for item in pending_items:
            classified = classified_map.get(str(item["id"]))
            if classified is None:
                from services.punch_classifier import ClassifiedPunch, CLASSIFICATION_INFERENCE

                classified = ClassifiedPunch(
                    event_type="IN",
                    classification_method=CLASSIFICATION_INFERENCE,
                    classification_reason="First observed punch.",
                    classification_reason_code="first_or_after_out",
                )
            apply_classification(item, classified)
            event = classified.event_type
            method = str(item.get("verification_method") or "not_reported")
            raw = item["raw_payload"] if isinstance(item["raw_payload"], dict) else {}
            log = item["log"]
            device_punch_iso = item["device_punch_iso"]
            punch_iso = item["punch_time"]
            person_name = _person_name(log.user_id)
            idem = build_idempotency_key(
                manufacturer=device.manufacturer,
                device_id=device.id,
                employee_code=str(log.user_id),
                punch_time=device_punch_iso,
                event_type=event,
            )
            raw_payload = to_json_value(
                {
                    **{k: v for k, v in raw.items() if k not in {"event_type", "verification_method"}},
                    "vendor": device.manufacturer,
                    "uid": log.uid,
                    "status": log.status,
                    "punch": log.punch,
                    "terminal_code": device.terminal_code,
                    "event_type": event,
                    "verification_method": method,
                    "device_timezone": device.timezone,
                    "device_punch_time": device_punch_iso,
                    "clock_corrected": punch_iso != device_punch_iso,
                    "normalized_timestamp": punch_iso,
                    "server_received_at": format_api_datetime(collected_at),
                    "classification_method": classified.classification_method,
                    "classification_reason": classified.classification_reason,
                    "classification_reason_code": classified.classification_reason_code,
                    "direction_reported": classified.direction_reported,
                    "device_status": item.get("device_status", log.status),
                    "device_punch": item.get("device_punch", log.punch),
                    "device_direction": item.get("device_direction"),
                    **({"employee_name": person_name} if person_name else {}),
                },
                default_timezone=device.timezone,
            )
            records.append(
                AttendanceRecord(
                    id=new_id(),
                    device_id=device.id,
                    device_employee_code=str(log.user_id),
                    punch_time=punch_iso,
                    event_type=event,
                    verification_method=method,
                    idempotency_key=idem,
                    status=QueueStatus.PENDING.value,
                    raw_payload=raw_payload if isinstance(raw_payload, dict) else {},
                )
            )
        inserted = self._attendance.enqueue_many(records)
        if device_rtc_modern:
            try:
                self._devices.touch_field(device.id, "last_time_sync_at")
            except Exception:
                logger.debug(
                    "last_time_sync_at confirm skipped device=%s", device.name
                )
        # Always heal stuck year-2000 rows (including when this poll inserted 0).
        try:
            self._attendance.rewrite_unset_clock_punch_times()
        except Exception:
            logger.debug(
                "unset-clock punch rewrite skipped device=%s",
                device.name,
                exc_info=True,
            )
        try:
            self._attendance.reclassify_attendance_events(device_id=device.id)
        except Exception:
            logger.debug("attendance reclassify skipped device=%s", device.name)
        return fetched, inserted, newest, worst_clock

    def poll_all(self, devices: list[Device] | None = None) -> list[dict]:
        targets = devices if devices is not None else self._devices.list_all(enabled_only=True)
        return [self.poll_device(d) for d in targets]

    @staticmethod
    def _parse_cursor(cursor: str) -> Optional[datetime]:
        if not cursor:
            return None
        parsed: Optional[datetime] = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                parsed = datetime.strptime(cursor, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            except ValueError:
                return None
        # Device logs are naive local time; compare apples-to-apples.
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed

    @staticmethod
    def _since_for_device(
        device: Device, device_now: Optional[datetime] = None
    ) -> Optional[datetime]:
        """Cursor used only when it is still in the device's own clock domain.

        Unset ZK/eSSL clocks report year 2000/2002. A connector-corrected 2026
        cursor must not be applied to year-2000 logs (that would drop every new
        punch). Likewise, a year-2002 cursor must not filter year-2000 punches
        when the terminal clock has jumped backwards within the unset domain.
        """
        parsed = PollService._parse_cursor(device.last_sync_cursor)
        if parsed is None:
            return None

        clock_year = getattr(device_now, "year", None)

        # Ancient cursor + modern/unknown device clock → full fetch (idempotent).
        if parsed.year < 2010:
            if clock_year is not None and int(clock_year) < 2010:
                # Device clock moved backwards vs cursor → cursor would drop punches.
                if device_now is not None and device_now <= parsed:
                    return None
                return parsed
            return None

        if clock_year is not None and int(clock_year) >= 2010:
            return parsed
        if str(getattr(device, "last_time_sync_at", "") or "").strip():
            # Clock was written modern earlier, but read failed this cycle — keep cursor.
            if clock_year is not None and int(clock_year) < 2010:
                return None
            return parsed
        return None

    def _needs_user_refresh(self, device: Device, *, force: bool = False) -> bool:
        """Pull terminal user names when the local cache has none."""
        if self._device_users is None:
            return False
        if force:
            return True
        try:
            cached = self._device_users.list_for_device(device.id)
        except Exception:
            return True
        if any(str(getattr(u, "name", "") or "").strip() for u in cached):
            return False
        due = self._user_refresh_after.get(device.id, 0.0)
        return time.monotonic() >= due

    def refresh_device_user_names(self, device: Device) -> int:
        """Download the terminal user table and cache names for attendance views."""
        if self._device_users is None or self._sessions is None:
            return 0
        try:
            users = self._sessions.execute(
                device,
                lambda adapter: list(adapter.download_users() or []),
                retries=2,
                owner="users",
            )
            saved = self._device_users.upsert_from_device_users(device.id, users or [])
            named = sum(1 for u in users or [] if str(getattr(u, "name", "") or "").strip())
            logger.info(
                "Cached %s device users (%s named) device=%s",
                saved,
                named,
                device.name,
                extra={"device_id": device.id},
            )
            if named:
                self._user_refresh_after.pop(device.id, None)
            else:
                self._user_refresh_after[device.id] = time.monotonic() + 300.0
            return saved
        except Exception as exc:
            logger.warning(
                "Device user name refresh failed device=%s: %s",
                device.name,
                exc,
                extra={"device_id": device.id},
            )
            self._user_refresh_after[device.id] = time.monotonic() + 120.0
            return 0

    def _download_users(self, adapter) -> list:
        try:
            users = list(adapter.download_users() or [])
            named = sum(1 for u in users if str(getattr(u, "name", "") or "").strip())
            logger.info(
                "Downloaded %s device users named=%s device=%s",
                len(users),
                named,
                adapter.device.name,
                extra={"device_id": adapter.device.id},
            )
            if users:
                self._user_refresh_after.pop(adapter.device.id, None)
            else:
                self._user_refresh_after[adapter.device.id] = time.monotonic() + 300.0
            return users
        except Exception:
            logger.debug(
                "Device user download skipped device=%s",
                adapter.device.name,
                extra={"device_id": adapter.device.id},
            )
            self._user_refresh_after[adapter.device.id] = time.monotonic() + 300.0
            return []

    def _write_clock(self, adapter, device_now) -> dict:
        """Write PC wall clock when the terminal RTC is still year-2000/unset."""
        try:
            clock_fix = write_device_clock(
                get_time=adapter.get_device_time,
                set_time=adapter.sync_time,
                threshold_seconds=DEFAULT_TIME_DRIFT_THRESHOLD_SECONDS,
            )
        except Exception:
            logger.debug(
                "Device clock correction skipped device=%s", adapter.device.name
            )
            return {"read_device_time": device_now}
        clock_fix = dict(clock_fix or {})
        clock_fix["read_device_time"] = device_now or clock_fix.get("previous_time")
        if clock_fix.get("corrected"):
            logger.info(
                "Device clock %s device=%s message=%s",
                "corrected" if clock_fix.get("success") else "write failed",
                adapter.device.name,
                clock_fix.get("message") or "",
                extra={"device_id": adapter.device.id},
            )
            if clock_fix.get("success") and self._states is not None:
                try:
                    self._states.mark_post_time_sync(adapter.device.id)
                except Exception:
                    logger.debug(
                        "post-time-sync grace skipped device=%s",
                        adapter.device.name,
                    )
        return clock_fix

    def _collect_and_correct_clock(
        self, adapter, since=None, *, full: bool = True, refresh_users: bool = False
    ):
        device_now = None
        try:
            device_now = adapter.get_device_time()
        except Exception:
            device_now = None
        since = self._since_for_device(adapter.device, device_now=device_now)
        want_users = (not full) and self._needs_user_refresh(
            adapter.device, force=refresh_users
        )

        info = None
        try:
            info = adapter.device_info()
        except Exception:
            info = None

        log_count = int(info.attendance_count) if info else -1
        known = int(getattr(adapter.device, "current_logs", 0) or 0)
        has_cursor = bool(str(getattr(adapter.device, "last_sync_cursor", "") or "").strip())
        unset_clock = device_now is not None and getattr(device_now, "year", 9999) < 2010
        # Always download attendance on each poll. ZK/eSSL attendance_count can
        # stay flat when the circular buffer is full (new punches overwrite old),
        # so skipping on "unchanged count" silently drops punches. The since
        # cursor already keeps incremental polls cheap; duplicates are ignored.
        if not full and has_cursor and 0 <= log_count < known:
            # Device attendance buffer was cleared; pull the full set again.
            since = None

        logs = adapter.download_logs(since=since)
        if not full:
            clock_fix = {"read_device_time": device_now}
            if unset_clock:
                clock_fix.update(self._write_clock(adapter, device_now))
            users = self._download_users(adapter) if want_users else []
            return logs, info, users, clock_fix

        users = self._download_users(adapter)
        clock_fix = None
        try:
            clock_fix = write_device_clock(
                get_time=adapter.get_device_time,
                set_time=adapter.sync_time,
                threshold_seconds=DEFAULT_TIME_DRIFT_THRESHOLD_SECONDS,
            )
            if clock_fix.get("corrected"):
                logger.info(
                    "Device clock %s device=%s message=%s",
                    "corrected" if clock_fix.get("success") else "write failed",
                    adapter.device.name,
                    clock_fix.get("message") or "",
                    extra={"device_id": adapter.device.id},
                )
                if clock_fix.get("success") and self._states is not None:
                    try:
                        self._states.mark_post_time_sync(adapter.device.id)
                    except Exception:
                        logger.debug(
                            "post-time-sync grace skipped device=%s",
                            adapter.device.name,
                        )
        except Exception:
            logger.debug("Device clock correction skipped device=%s", adapter.device.name)
            clock_fix = None
        clock_fix = dict(clock_fix or {})
        clock_fix["read_device_time"] = device_now or clock_fix.get("previous_time")
        return logs, info, users, clock_fix
