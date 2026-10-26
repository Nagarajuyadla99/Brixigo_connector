"""Phase 3 management services — templates, users, time, backup, firmware, inventory."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from adapters.registry import create_adapter
from api.cloud_client import CloudAPIError, CloudClient
from models import Device, DeviceUser, new_id, utcnow
from models.phase3 import BiometricTemplate, DeviceBackup, DeviceUserRecord, FirmwareJob
from security.encryption import SecretBox
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import AuditRepository
from storage.repositories.phase3_repo import (
    BackupRepository,
    DeviceUserCacheRepository,
    FirmwareJobRepository,
    TemplateRepository,
    TimeSyncLogRepository,
)
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.service")


class TemplateService:
    """Biometric template upload/download/replace/delete/backup/restore."""

    def __init__(
        self,
        templates: TemplateRepository,
        secrets: SecretBox,
        devices: DeviceRepository,
        sync_logs: SyncLogRepository,
        audit: AuditRepository,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        on_failure: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._templates = templates
        self._secrets = secrets
        self._devices = devices
        self._sync_logs = sync_logs
        self._audit = audit
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.on_failure = on_failure

    def store_encrypted(
        self,
        *,
        employee_id: str,
        employee_code: str,
        template_type: str,
        raw: bytes,
        finger_index: int = 0,
        vendor: str = "",
        device_id: str = "",
        cloud_version: int = 0,
    ) -> BiometricTemplate:
        enc = self._secrets.encrypt_str(raw.hex())
        checksum = hashlib.sha256(raw).hexdigest()
        return self._templates.upsert(
            BiometricTemplate(
                id=new_id(),
                employee_id=employee_id,
                employee_code=employee_code,
                template_type=template_type,
                finger_index=finger_index,
                template_enc=enc,
                checksum=checksum,
                vendor=vendor,
                size=len(raw),
                device_id=device_id,
                cloud_version=cloud_version,
            )
        )

    def decrypt_template(self, template: BiometricTemplate) -> bytes:
        hex_body = self._secrets.decrypt_str(template.template_enc)
        return bytes.fromhex(hex_body)

    def download_from_device(self, device_id: str) -> list[BiometricTemplate]:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        saved: list[BiometricTemplate] = []
        with adapter:
            for item in adapter.download_templates():
                raw = item.get("template") or b""
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                saved.append(
                    self.store_encrypted(
                        employee_id="",
                        employee_code=str(item.get("uid") or ""),
                        template_type=str(item.get("template_type") or "fingerprint"),
                        raw=bytes(raw),
                        finger_index=int(item.get("finger_index") or 0),
                        vendor=device.manufacturer,
                        device_id=device.id,
                    )
                )
        self._sync_logs.add(
            device_id=device_id,
            action="template_download",
            success=True,
            message=f"Downloaded {len(saved)} templates",
            records_count=len(saved),
        )
        return saved

    def upload_to_device(self, device_id: str, template_id: str, *, uid: int) -> dict:
        device = self._devices.get(device_id)
        template = next(
            (t for t in self._templates.list_all() if t.id == template_id), None
        )
        if not device or not template:
            raise KeyError("device or template missing")
        raw = self.decrypt_template(template)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        try:
            with adapter:
                adapter.upload_template(
                    {
                        "uid": uid,
                        "finger_index": template.finger_index,
                        "template": raw,
                        "valid": 1,
                    }
                )
            self._audit.add(
                action="template_upload",
                actor="system",
                success=True,
                message=f"Uploaded template {template_id}",
                resource_type="template",
                resource_id=template_id,
            )
            return {"success": True, "template_id": template_id}
        except Exception as exc:
            if self.on_failure:
                self.on_failure(str(exc), device_id)
            raise

    def delete_from_device(self, device_id: str, *, uid: int, finger_index: int = 0) -> None:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        with adapter:
            adapter.delete_template(uid=uid, finger_index=finger_index)

    def replace_template(self, template_id: str, raw: bytes) -> BiometricTemplate:
        existing = next(
            (t for t in self._templates.list_all() if t.id == template_id), None
        )
        if not existing:
            raise KeyError(template_id)
        return self.store_encrypted(
            employee_id=existing.employee_id,
            employee_code=existing.employee_code,
            template_type=existing.template_type,
            raw=raw,
            finger_index=existing.finger_index,
            vendor=existing.vendor,
            device_id=existing.device_id,
            cloud_version=existing.cloud_version + 1,
        )


class DeviceUserService:
    """Download/upload/delete/search/bulk sync device users with conflict resolution."""

    def __init__(
        self,
        cache: DeviceUserCacheRepository,
        devices: DeviceRepository,
        sync_logs: SyncLogRepository,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        max_workers: int = 16,
    ) -> None:
        self._cache = cache
        self._devices = devices
        self._sync_logs = sync_logs
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_workers = max(2, max_workers)

    def download_users(self, device_id: str) -> list[DeviceUserRecord]:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        records: list[DeviceUserRecord] = []
        with adapter:
            for u in adapter.download_users():
                records.append(
                    self._cache.upsert(
                        DeviceUserRecord(
                            id=new_id(),
                            device_id=device_id,
                            user_id=u.user_id,
                            name=u.name,
                            card=u.card,
                            privilege=u.privilege,
                            password=u.password,
                            enabled=u.enabled,
                            last_synced_at=utcnow().isoformat(),
                            sync_status="synced",
                            raw=u.raw or {},
                        )
                    )
                )
        self._sync_logs.add(
            device_id=device_id,
            action="download_users",
            success=True,
            message=f"Downloaded {len(records)} users",
            records_count=len(records),
        )
        return records

    def upload_user(self, device_id: str, user: DeviceUser) -> None:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        with adapter:
            adapter.set_user(user)
        self._cache.upsert(
            DeviceUserRecord(
                id=new_id(),
                device_id=device_id,
                user_id=user.user_id,
                name=user.name,
                card=user.card,
                privilege=user.privilege,
                password=user.password,
                enabled=user.enabled,
                last_synced_at=utcnow().isoformat(),
                sync_status="uploaded",
            )
        )

    def delete_user(self, device_id: str, user_id: str) -> None:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        with adapter:
            adapter.delete_user(user_id)
        self._cache.delete(device_id, user_id)

    def search(self, device_id: str, query: str) -> list[DeviceUserRecord]:
        return self._cache.search(device_id, query)

    def bulk_sync(self, device_ids: list[str] | None = None) -> dict:
        targets = device_ids or [d.id for d in self._devices.list_all(enabled_only=True)]
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self.download_users, did): did for did in targets}
            for fut in as_completed(futures):
                did = futures[fut]
                try:
                    records = fut.result()
                    results.append({"device_id": did, "success": True, "count": len(records)})
                except Exception as exc:
                    results.append({"device_id": did, "success": False, "error": str(exc)})
        return {"results": results}

    def resolve_conflict(
        self, device_id: str, user_id: str, *, prefer: str = "cloud"
    ) -> dict:
        """Conflict resolution: prefer cloud (local employee) or device mirror."""
        cached = next(
            (u for u in self._cache.list_for_device(device_id) if u.user_id == user_id),
            None,
        )
        if not cached:
            return {"resolved": False, "message": "user not cached"}
        if prefer == "device":
            cached.sync_status = "device_wins"
            cached.conflict = ""
            self._cache.upsert(cached)
            return {"resolved": True, "prefer": "device"}
        # cloud wins — re-upload cached employee-linked identity
        self.upload_user(
            device_id,
            DeviceUser(
                user_id=cached.user_id,
                name=cached.name,
                card=cached.card,
                privilege=cached.privilege,
                password=cached.password,
                enabled=cached.enabled,
            ),
        )
        cached.sync_status = "cloud_wins"
        cached.conflict = ""
        self._cache.upsert(cached)
        return {"resolved": True, "prefer": "cloud"}


class TimeSyncService:
    """Compare connector/cloud/device clocks and auto-correct drift."""

    def __init__(
        self,
        cloud: CloudClient,
        devices: DeviceRepository,
        logs: TimeSyncLogRepository,
        sync_logs: SyncLogRepository,
        *,
        list_devices: Callable[[], list[Device]],
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        interval_seconds: int = 300,
        drift_threshold_seconds: int = 30,
        max_workers: int = 16,
        sessions=None,
        on_clock_mismatch=None,
        states=None,
    ) -> None:
        self._cloud = cloud
        self._devices = devices
        self._logs = logs
        self._sync_logs = sync_logs
        self._list_devices = list_devices
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.interval_seconds = max(60, interval_seconds)
        self.drift_threshold_seconds = max(1, drift_threshold_seconds)
        self.max_workers = max(2, max_workers)
        self._sessions = sessions
        self._on_clock_mismatch = on_clock_mismatch
        self._states = states
        self._sessions = sessions
        self._on_clock_mismatch = on_clock_mismatch
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
            target=self._loop, name="brixigo-time-sync", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def sync_all(self) -> list[dict]:
        cloud_time = self._fetch_cloud_time()
        results = []
        devices = self._list_devices()
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self.sync_device, d, cloud_time): d for d in devices
            }
            for fut in as_completed(futures):
                results.append(fut.result())
        return results

    def sync_device(self, device: Device, cloud_time: Optional[datetime] = None) -> dict:
        from services.punch_normalizer import (
            analyze_clock,
            connector_wall_clock,
            write_device_clock,
        )
        from services.connection_state import DeviceLinkStatus, DeviceNetworkStatus

        # Do not fight heartbeat for TCP 4370 while the terminal is offline.
        states = getattr(self, "_states", None)
        if states is not None:
            link = states.device(getattr(device, "id", "") or "")
            if link is not None and (
                link.network_status == DeviceNetworkStatus.OFFLINE
                or link.status == DeviceLinkStatus.DISCONNECTED
            ):
                return {
                    "device_id": getattr(device, "id", ""),
                    "success": False,
                    "corrected": False,
                    "message": "Device LAN offline — time sync deferred until reconnect",
                    "deferred": True,
                }

        connector_now = connector_wall_clock()
        cloud_time = cloud_time or connector_now

        def _run(adapter) -> dict:
            return write_device_clock(
                get_time=adapter.get_device_time,
                set_time=adapter.sync_time,
                when=connector_now,
                threshold_seconds=self.drift_threshold_seconds,
                write_if_unreadable=True,
            )

        try:
            from app.constants import FAST_TCP_PROBE_TIMEOUT
            from services.connection_state import (
                is_hard_lan_unreachable,
                is_inconclusive_tcp_probe,
                tcp_probe,
            )

            ip = str(getattr(device, "ip_address", "") or "").strip()
            port = int(getattr(device, "port", 0) or 4370)
            if ip:
                ok, _, msg = tcp_probe(ip, port, timeout=FAST_TCP_PROBE_TIMEOUT)
                # SYN timeout is not a reason to skip CMD_SET_TIME. The fast
                # probe is what showed "Time sync failed / TCP connect failed:
                # timed out" while the K90 was only slow to ARP.
                if (
                    not ok
                    and not is_inconclusive_tcp_probe(msg)
                    and is_hard_lan_unreachable(msg)
                ):
                    return {
                        "device_id": getattr(device, "id", ""),
                        "success": False,
                        "corrected": False,
                        "message": f"TCP connect failed: {msg}",
                    }
        except Exception:
            pass

        try:
            if self._sessions is not None:
                clock = self._sessions.execute(device, _run, retries=3, owner="time-sync")
            else:
                adapter = create_adapter(
                    device,
                    connect_timeout=self.connect_timeout,
                    read_timeout=self.read_timeout,
                )
                with adapter:
                    clock = _run(adapter)
            device_time = clock.get("device_time") or clock.get("previous_time")
            success = bool(clock.get("success"))
            corrected = bool(clock.get("corrected"))
            message = str(clock.get("message") or "")
            drift = float(clock.get("drift_seconds") or 0.0)
            previous = clock.get("previous_time")
            if (
                self._on_clock_mismatch
                and previous is not None
                and not (corrected and success)
                and (getattr(previous, "year", 9999) < 2010 or drift > 86400)
            ):
                try:
                    self._on_clock_mismatch(device, analyze_clock(previous, connector_now))
                except Exception:
                    logger.debug("Clock mismatch callback skipped device=%s", device.id)
            if success:
                self._devices.touch_field(device.id, "last_time_sync_at")
                # SET_TIME drops TCP briefly — suppress false TCP DOWN flaps.
                states = getattr(self, "_states", None)
                if states is not None:
                    try:
                        states.mark_post_time_sync(device.id)
                        states.record_device_heartbeat(
                            device.id,
                            network_ok=True,
                            protocol_ok=True,
                            latency_ms=0.0,
                            ip_address=getattr(device, "ip_address", "") or "",
                            port=int(getattr(device, "port", 0) or 4370),
                            name=getattr(device, "name", "") or "",
                            protocol_code="OK",
                            failure_stage="",
                        )
                    except Exception:
                        logger.debug(
                            "post-time-sync grace skipped device=%s", device.id
                        )
            self._logs.add(
                device_id=device.id,
                connector_time=connector_now.isoformat(sep=" "),
                cloud_time=cloud_time.isoformat(sep=" ")
                if hasattr(cloud_time, "isoformat")
                else str(cloud_time),
                device_time=device_time.isoformat(sep=" ") if device_time else "",
                drift_seconds=drift,
                corrected=corrected,
                success=success,
                message=message,
            )
            self._sync_logs.add(
                device_id=device.id,
                action="time_sync",
                success=success,
                message=message,
            )
            return {
                "device_id": device.id,
                "success": success,
                "corrected": corrected,
                "message": message,
            }
        except Exception as exc:
            self._logs.add(
                device_id=device.id,
                connector_time=connector_now.isoformat(sep=" "),
                cloud_time=cloud_time.isoformat(sep=" ")
                if hasattr(cloud_time, "isoformat")
                else str(cloud_time),
                device_time="",
                drift_seconds=0,
                corrected=False,
                success=False,
                message=str(exc),
            )
            return {"device_id": device.id, "success": False, "message": str(exc)}

    def _fetch_cloud_time(self) -> datetime:
        try:
            data = self._cloud.get_server_time()
            value = data.get("utc") or data.get("server_time") or data.get("now")
            if value:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
        except Exception:
            pass
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_all()
            except Exception as exc:
                logger.warning("Time sync cycle failed: %s", exc)
            self._stop.wait(self.interval_seconds)


class BackupRestoreService:
    """Encrypted device backup and restore (users, templates, attendance, config)."""

    def __init__(
        self,
        backups: BackupRepository,
        devices: DeviceRepository,
        templates: TemplateService,
        user_service: DeviceUserService,
        secrets: SecretBox,
        audit: AuditRepository,
        sync_logs: SyncLogRepository,
        backup_dir: Path,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        interval_seconds: int = 86400,
        list_devices: Optional[Callable[[], list[Device]]] = None,
        on_failure: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._backups = backups
        self._devices = devices
        self._templates = templates
        self._users = user_service
        self._secrets = secrets
        self._audit = audit
        self._sync_logs = sync_logs
        self.backup_dir = backup_dir
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.interval_seconds = max(3600, interval_seconds)
        self._list_devices = list_devices or (lambda: devices.list_all(enabled_only=True))
        self.on_failure = on_failure
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-backup", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def backup_device(self, device_id: str, *, backup_type: str = "full") -> DeviceBackup:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        payload: dict[str, Any] = {
            "device": device.to_public_dict(),
            "created_at": utcnow().isoformat(),
            "backup_type": backup_type,
        }
        try:
            users = self._users.download_users(device_id)
            payload["users"] = [
                {
                    "user_id": u.user_id,
                    "name": u.name,
                    "card": u.card,
                    "privilege": u.privilege,
                    "password": u.password,
                    "enabled": u.enabled,
                }
                for u in users
            ]
        except Exception as exc:
            payload["users_error"] = str(exc)
            users = []

        try:
            with adapter:
                payload["config"] = adapter.get_device_config()
                payload["info"] = adapter.device_info().__dict__
                try:
                    logs = adapter.download_logs()
                    payload["attendance"] = [
                        {
                            "user_id": l.user_id,
                            "punch_time": l.punch_time.isoformat(sep=" "),
                            "status": l.status,
                            "punch": l.punch,
                        }
                        for l in logs
                    ]
                except Exception as exc:
                    payload["attendance_error"] = str(exc)
                try:
                    tpls = adapter.download_templates()
                    payload["templates"] = [
                        {
                            "uid": t.get("uid"),
                            "finger_index": t.get("finger_index"),
                            "template_hex": (t.get("template") or b"").hex(),
                            "size": t.get("size"),
                        }
                        for t in tpls
                        if isinstance(t.get("template"), (bytes, bytearray))
                    ]
                except Exception as exc:
                    payload["templates_error"] = str(exc)
        except Exception as exc:
            if self.on_failure:
                self.on_failure(str(exc), device_id)
            raise

        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token = self._secrets.encrypt_str(raw.hex())
        checksum = hashlib.sha256(raw).hexdigest()
        filename = f"{device.terminal_code or device.id}_{utcnow().strftime('%Y%m%d_%H%M%S')}.bkp"
        path = self.backup_dir / filename
        path.write_text(token, encoding="utf-8")
        backup = self._backups.add(
            DeviceBackup(
                id=new_id(),
                device_id=device_id,
                backup_path=str(path),
                backup_type=backup_type,
                includes_users="users" in payload,
                includes_templates="templates" in payload,
                includes_attendance="attendance" in payload,
                includes_config="config" in payload,
                size_bytes=path.stat().st_size,
                checksum=checksum,
                encrypted=True,
                meta={"user_count": len(users), "filename": filename},
            )
        )
        self._devices.touch_field(device_id, "last_backup_at")
        self._audit.add(
            action="device_backup",
            actor="system",
            success=True,
            message=f"Backup created {filename}",
            resource_type="backup",
            resource_id=backup.id,
        )
        self._sync_logs.add(
            device_id=device_id,
            action="backup",
            success=True,
            message=filename,
        )
        return backup

    def restore_device(
        self,
        device_id: str,
        backup_id: str,
        *,
        include_users: bool = True,
        include_templates: bool = True,
        include_config: bool = False,
    ) -> dict:
        device = self._devices.get(device_id)
        backup = self._backups.get(backup_id)
        if not device or not backup:
            raise KeyError("device or backup missing")
        token = Path(backup.backup_path).read_text(encoding="utf-8")
        raw_hex = self._secrets.decrypt_str(token)
        payload = json.loads(bytes.fromhex(raw_hex).decode("utf-8"))
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        restored = {"users": 0, "templates": 0}
        try:
            with adapter:
                if include_users:
                    for u in payload.get("users") or []:
                        adapter.set_user(
                            DeviceUser(
                                user_id=str(u.get("user_id") or ""),
                                name=str(u.get("name") or ""),
                                card=str(u.get("card") or ""),
                                privilege=int(u.get("privilege") or 0),
                                password=str(u.get("password") or ""),
                                enabled=bool(u.get("enabled", True)),
                            )
                        )
                        restored["users"] += 1
                if include_templates:
                    for t in payload.get("templates") or []:
                        adapter.upload_template(
                            {
                                "uid": int(t.get("uid") or 0),
                                "finger_index": int(t.get("finger_index") or 0),
                                "template": bytes.fromhex(t.get("template_hex") or ""),
                                "valid": 1,
                            }
                        )
                        restored["templates"] += 1
            self._audit.add(
                action="device_restore",
                actor="system",
                success=True,
                message=f"Restored backup {backup_id} → {device.name}",
                resource_type="backup",
                resource_id=backup_id,
                details=restored,
            )
            return {"success": True, "restored": restored, "include_config": include_config}
        except Exception as exc:
            if self.on_failure:
                self.on_failure(str(exc), device_id)
            self._audit.add(
                action="device_restore",
                actor="system",
                success=False,
                message=str(exc),
                resource_type="backup",
                resource_id=backup_id,
            )
            raise

    def backup_all(self) -> list[DeviceBackup]:
        out = []
        for device in self._list_devices():
            try:
                out.append(self.backup_device(device.id))
            except Exception as exc:
                logger.warning("Backup failed device=%s: %s", device.id, exc)
                if self.on_failure:
                    self.on_failure(str(exc), device.id)
        return out

    def _loop(self) -> None:
        # Do not backup on engine start — a dead device would block the
        # session lock for connect_timeout and freeze the first UI paint.
        while not self._stop.is_set():
            self._stop.wait(self.interval_seconds)
            if self._stop.is_set():
                break
            try:
                self.backup_all()
            except Exception as exc:
                logger.warning("Scheduled backup failed: %s", exc)


class FirmwareService:
    def __init__(
        self,
        jobs: FirmwareJobRepository,
        devices: DeviceRepository,
        audit: AuditRepository,
        sync_logs: SyncLogRepository,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
    ) -> None:
        self._jobs = jobs
        self._devices = devices
        self._audit = audit
        self._sync_logs = sync_logs
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def detect(self, device_id: str) -> dict:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        with adapter:
            info = adapter.device_info()
        return {
            "firmware": info.firmware or device.firmware,
            "sdk_version": info.sdk_version or device.sdk_version,
            "model": info.model or device.model,
            "serial_number": info.serial_number or device.serial_number,
        }

    def upgrade(self, device_id: str, package_path: str, *, to_version: str = "") -> FirmwareJob:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(device_id)
        job = self._jobs.add(
            FirmwareJob(
                id=new_id(),
                device_id=device_id,
                action="upgrade",
                from_version=device.firmware,
                to_version=to_version,
                package_path=package_path,
                status="running",
            )
        )
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        try:
            with adapter:
                result = adapter.apply_firmware(package_path)
                if result.get("restart_required"):
                    try:
                        adapter.restart_device()
                    except Exception:
                        pass
            job.status = "succeeded"
            job.message = str(result.get("message") or "ok")
            job.completed_at = utcnow().isoformat()
            job.details = result
            self._jobs.update(job)
            self._audit.add(
                action="firmware_upgrade",
                actor="system",
                success=True,
                message=job.message,
                resource_type="firmware",
                resource_id=job.id,
                details=result,
            )
            return job
        except Exception as exc:
            job.status = "failed"
            job.message = str(exc)
            job.completed_at = utcnow().isoformat()
            self._jobs.update(job)
            self._audit.add(
                action="firmware_upgrade",
                actor="system",
                success=False,
                message=str(exc),
                resource_type="firmware",
                resource_id=job.id,
            )
            raise

    def rollback(self, device_id: str, package_path: str, *, to_version: str = "") -> FirmwareJob:
        job = self.upgrade(device_id, package_path, to_version=to_version)
        job.action = "rollback"
        self._jobs.update(job)
        return job


class InventoryService:
    def __init__(
        self,
        cloud: CloudClient,
        devices: DeviceRepository,
        *,
        list_devices: Callable[[], list[Device]],
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        interval_seconds: int = 600,
        max_workers: int = 16,
    ) -> None:
        self._cloud = cloud
        self._devices = devices
        self._list_devices = list_devices
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.interval_seconds = max(60, interval_seconds)
        self.max_workers = max(2, max_workers)
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
            target=self._loop, name="brixigo-inventory", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def collect(self) -> list[dict]:
        inventory = []
        devices = self._list_devices()
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._collect_one, d): d for d in devices}
            for fut in as_completed(futures):
                try:
                    inventory.append(fut.result())
                except Exception as exc:
                    d = futures[fut]
                    inventory.append({"device_id": d.id, "error": str(exc)})
        return inventory

    def push(self) -> dict:
        inventory = self.collect()
        if not self._cloud.settings.connector_id:
            return {"pushed": 0, "inventory": inventory}
        try:
            self._cloud.push_device_status(inventory)
            self._cloud.push_inventory(inventory)
            return {"pushed": len(inventory), "inventory": inventory}
        except CloudAPIError as exc:
            logger.warning("Inventory push failed: %s", exc)
            return {"pushed": 0, "error": str(exc), "inventory": inventory}

    def _collect_one(self, device: Device) -> dict:
        adapter = create_adapter(
            device, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout
        )
        info = {}
        health = {}
        try:
            with adapter:
                di = adapter.device_info()
                info = di.__dict__
                health = adapter.collect_health_metrics()
                self._devices.update_telemetry(
                    device.id,
                    firmware=di.firmware or None,
                    mac_address=di.mac_address or None,
                    sdk_version=di.sdk_version or None,
                    model=di.model or None,
                    serial_number=di.serial_number or None,
                    current_users=di.user_count,
                    current_logs=di.attendance_count,
                )
        except Exception as exc:
            health = {"online": False, "message": str(exc)}
        refreshed = self._devices.get(device.id) or device
        online = bool(health.get("online", refreshed.status == "online"))
        return {
            "device_id": refreshed.id,
            "name": refreshed.name,
            "manufacturer": refreshed.manufacturer,
            "model": refreshed.model,
            "serial_number": refreshed.serial_number,
            "mac_address": refreshed.mac_address,
            "firmware": refreshed.firmware,
            "sdk_version": refreshed.sdk_version,
            "ip_address": refreshed.ip_address,
            "port": refreshed.port,
            "terminal_code": refreshed.terminal_code,
            "device_code": refreshed.terminal_code,
            "status": "online" if online else "offline",
            "online": online,
            "enabled": refreshed.enabled,
            "communication_type": refreshed.communication_type,
            "organization": refreshed.organization,
            "branch": refreshed.branch,
            "department": refreshed.department,
            "building": refreshed.building,
            "floor": refreshed.floor,
            "gate": refreshed.gate,
            "warranty_until": refreshed.warranty_until,
            "installation_date": refreshed.installation_date,
            "last_maintenance_at": refreshed.last_maintenance_at,
            "memory_usage_percent": health.get("memory_usage_percent"),
            "storage_usage_percent": health.get("storage_usage_percent"),
            "cpu_temperature": health.get("cpu_temperature"),
            "battery_level": health.get("battery_level"),
            "info": info,
            "health": health,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.push()
            except Exception as exc:
                logger.warning("Inventory cycle failed: %s", exc)
            self._stop.wait(self.interval_seconds)
