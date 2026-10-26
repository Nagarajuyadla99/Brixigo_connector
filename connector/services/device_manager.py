"""Device management service — CRUD + connection tests + hierarchy."""

from __future__ import annotations

import logging
from typing import Optional

from adapters.registry import create_adapter
from models import Device, DeviceStatus, DiscoveredDevice, new_id, utcnow
from services.connection_state import tcp_probe
from services.device_session_manager import DeviceSessionManager
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import AuditRepository, DiscoveryRepository
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.connection")


class DeviceManager:
    def __init__(
        self,
        devices: DeviceRepository,
        sync_logs: SyncLogRepository,
        *,
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        audit: AuditRepository | None = None,
        discovery: DiscoveryRepository | None = None,
        sessions: DeviceSessionManager | None = None,
        states=None,
        cloud=None,
    ) -> None:
        self._devices = devices
        self._sync_logs = sync_logs
        self._audit = audit
        self._discovery = discovery
        self._sessions = sessions
        self._states = states
        self._cloud = cloud
        self._scanner = None
        self._relocate_at: dict[str, float] = {}
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def bind_scanner(self, scanner) -> None:
        self._scanner = scanner

    def _sync_device_to_cloud(self, device: Device) -> None:
        if self._cloud is None:
            return
        if not getattr(self._cloud.settings, "connector_id", None):
            return
        try:
            payload = device.to_public_dict()
            payload["device_id"] = device.id
            payload.pop("password", None)
            self._cloud.register_device(payload)
        except Exception as exc:
            logger.warning("Cloud device sync failed for %s: %s", device.id, exc)

    def sync_all_devices_to_cloud(self) -> int:
        """Push the full local device inventory to cloud (startup / reconnect)."""
        if self._cloud is None or not getattr(self._cloud.settings, "connector_id", None):
            return 0
        count = 0
        for device in self._devices.list_all(enabled_only=False):
            try:
                self._sync_device_to_cloud(device)
                count += 1
            except Exception:
                continue
        return count

    def relocate_if_unreachable(self, device: Device, force: bool = False):
        """If the stored IP is dead, scan this PC's LAN and update a matching terminal."""
        import time

        from app.constants import FAST_TCP_PROBE_TIMEOUT, LAN_RELOCATE_COOLDOWN_SECONDS
        from services.network_scanner import (
            LanRelocateResult,
            host_on_local_subnets,
            local_ipv4_addresses,
            local_ipv4_nics,
            local_subnet_specs,
        )

        nics = local_ipv4_nics()
        local_ips = tuple(local_ipv4_addresses())
        specs = tuple(local_subnet_specs())
        same = host_on_local_subnets(device.ip_address, nics)
        now = time.monotonic()
        last = self._relocate_at.get(device.id, 0.0)
        if not force and (now - last) < LAN_RELOCATE_COOLDOWN_SECONDS:
            return LanRelocateResult(
                local_ips=local_ips,
                scan_ranges=specs,
                same_subnet=same,
                skipped_cooldown=True,
            )
        self._relocate_at[device.id] = now
        if self._scanner is None:
            return LanRelocateResult(
                local_ips=local_ips, scan_ranges=specs, same_subnet=same
            )
        ok, _, _ = tcp_probe(
            device.ip_address,
            int(device.port or 4370),
            timeout=FAST_TCP_PROBE_TIMEOUT,
        )
        if ok:
            return LanRelocateResult(
                local_ips=local_ips, scan_ranges=specs, same_subnet=same
            )
        found, used_specs, open_count = self._scanner.find_relocation_candidate(device)
        result = LanRelocateResult(
            scanned=True,
            scan_ranges=tuple(used_specs or specs),
            local_ips=local_ips,
            open_host_count=int(open_count or 0),
            same_subnet=same,
        )
        if found is None:
            return result
        conflict = self._devices.get_by_ip_port(found.ip_address, found.port)
        if conflict is not None and conflict.id != device.id:
            logger.warning(
                "LAN relocate skipped device=%s target=%s:%s owned by %s",
                device.name,
                found.ip_address,
                found.port,
                conflict.name,
            )
            return result
        old = f"{device.ip_address}:{device.port}"
        updated = self.update_device(
            device.id,
            ip_address=found.ip_address,
            port=int(found.port),
            serial_number=found.serial_number or device.serial_number,
            mac_address=found.mac_address or device.mac_address,
            firmware=found.firmware or device.firmware,
            model=found.model or device.model,
        )
        logger.info(
            "Relocated device=%s %s -> %s:%s serial=%s",
            device.name,
            old,
            found.ip_address,
            found.port,
            found.serial_number or device.serial_number,
        )
        result.relocated = updated
        return result

    def list_devices(self, enabled_only: bool = False) -> list[Device]:
        return self._devices.list_all(enabled_only=enabled_only)

    def get(self, device_id: str) -> Optional[Device]:
        return self._devices.get(device_id)

    def add_device(self, **kwargs) -> Device:
        device = Device(
            id=kwargs.get("id") or new_id(),
            name=str(kwargs["name"]).strip(),
            manufacturer=str(kwargs["manufacturer"]).strip().lower(),
            model=str(kwargs.get("model") or "").strip(),
            ip_address=str(kwargs["ip_address"]).strip(),
            port=int(kwargs.get("port") or 4370),
            password=str(kwargs.get("password") or "").strip() or "0",
            branch=str(kwargs.get("branch") or "").strip(),
            timezone=str(kwargs.get("timezone") or "Asia/Kolkata"),
            communication_type=str(kwargs.get("communication_type") or "tcp"),
            polling_interval=int(kwargs.get("polling_interval") or 60),
            status=DeviceStatus.UNKNOWN.value,
            terminal_code=str(kwargs.get("terminal_code") or "").strip(),
            serial_number=str(kwargs.get("serial_number") or "").strip(),
            enabled=bool(kwargs.get("enabled", True)),
            extra=dict(kwargs.get("extra") or {}),
            organization=str(kwargs.get("organization") or "").strip(),
            department=str(kwargs.get("department") or "").strip(),
            building=str(kwargs.get("building") or "").strip(),
            floor=str(kwargs.get("floor") or "").strip(),
            gate=str(kwargs.get("gate") or "").strip(),
            mac_address=str(kwargs.get("mac_address") or "").strip(),
            firmware=str(kwargs.get("firmware") or "").strip(),
            sdk_version=str(kwargs.get("sdk_version") or "").strip(),
        )
        if not device.name or not device.ip_address:
            raise ValueError("Device name and IP address are required")
        if device.enabled:
            conflict = self._devices.get_by_ip_port(device.ip_address, device.port)
            if conflict is not None and conflict.enabled:
                raise ValueError(
                    f"'{conflict.name}' is already registered at "
                    f"{device.ip_address}:{device.port}. "
                    "eSSL/ZK terminals allow only one SDK session on that port. "
                    "Disable or delete the existing device first."
                )
        saved = self._devices.create(device)
        self._sync_logs.add(
            device_id=saved.id,
            action="device_create",
            success=True,
            message=f"Device '{saved.name}' created",
        )
        if self._audit:
            self._audit.add(
                action="device_create",
                actor="admin",
                success=True,
                message=f"Created device {saved.name}",
                resource_type="device",
                resource_id=saved.id,
            )
        if self._discovery:
            self._discovery.mark_added(saved.ip_address, saved.port)
        self._sync_device_to_cloud(saved)
        return saved

    def add_from_discovery(
        self,
        discovered: DiscoveredDevice,
        *,
        password: str = "0",
        organization: str = "",
        branch: str = "",
        department: str = "",
        building: str = "",
        floor: str = "",
        gate: str = "",
        terminal_code: str = "",
        enabled: bool = True,
    ) -> Device:
        """Admin-approved registration of a discovered device."""
        existing = self._devices.get_by_ip_port(discovered.ip_address, discovered.port)
        if existing:
            raise ValueError(
                f"Device already registered at {discovered.ip_address}:{discovered.port}"
            )
        return self.add_device(
            name=discovered.device_name or f"{discovered.manufacturer}-{discovered.ip_address}",
            manufacturer=discovered.manufacturer or "zkteco",
            model=discovered.model,
            ip_address=discovered.ip_address,
            port=discovered.port,
            password=password,
            organization=organization,
            branch=branch,
            department=department,
            building=building,
            floor=floor,
            gate=gate,
            communication_type=discovered.communication_type or "tcp",
            terminal_code=terminal_code,
            serial_number=discovered.serial_number,
            mac_address=discovered.mac_address,
            firmware=discovered.firmware,
            sdk_version=discovered.sdk_version,
            enabled=enabled,
        )

    def update_device(self, device_id: str, **kwargs) -> Device:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(f"Device {device_id} not found")
        for field in (
            "name",
            "manufacturer",
            "model",
            "ip_address",
            "port",
            "password",
            "branch",
            "timezone",
            "communication_type",
            "polling_interval",
            "terminal_code",
            "serial_number",
            "enabled",
            "extra",
            "organization",
            "department",
            "building",
            "floor",
            "gate",
            "mac_address",
            "firmware",
            "sdk_version",
        ):
            if field in kwargs and kwargs[field] is not None:
                value = kwargs[field]
                if field == "password":
                    value = str(value).strip() or "0"
                setattr(device, field, value)
        if "manufacturer" in kwargs and kwargs["manufacturer"]:
            device.manufacturer = str(kwargs["manufacturer"]).strip().lower()
        device.updated_at = utcnow().isoformat()
        saved = self._devices.update(device)
        self._sync_logs.add(
            device_id=saved.id,
            action="device_update",
            success=True,
            message=f"Device '{saved.name}' updated",
        )
        if self._audit:
            self._audit.add(
                action="device_update",
                actor="admin",
                success=True,
                message=f"Updated device {saved.name}",
                resource_type="device",
                resource_id=saved.id,
            )
        self._sync_device_to_cloud(saved)
        return saved

    def delete_device(self, device_id: str) -> bool:
        device = self._devices.get(device_id)
        ok = self._devices.delete(device_id)
        if ok and device:
            self._sync_logs.add(
                device_id=device_id,
                action="device_delete",
                success=True,
                message=f"Device '{device.name}' deleted",
            )
            if self._audit:
                self._audit.add(
                    action="device_delete",
                    actor="admin",
                    success=True,
                    message=f"Deleted device {device.name}",
                    resource_type="device",
                    resource_id=device_id,
                )
        return ok

    def enable_device(self, device_id: str) -> None:
        self._devices.set_enabled(device_id, True)
        self._sync_logs.add(
            device_id=device_id, action="device_enable", success=True, message="Device enabled"
        )

    def disable_device(self, device_id: str) -> None:
        self._devices.set_enabled(device_id, False)
        self._sync_logs.add(
            device_id=device_id, action="device_disable", success=True, message="Device disabled"
        )

    def reconnect(self, device_id: str) -> dict:
        """Force a fresh connection test / status refresh."""
        return self.test_connection(device_id)

    def test_connection(self, device_id: str) -> dict:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(f"Device {device_id} not found")
        if self._sessions is not None:
            diag = self._sessions.test_protocol(device)
            message = self._sessions.operator_message(device, diag)
            if self._states is not None:
                from services.connection_state import persist_device_status, persist_health_status

                link = self._states.record_device_heartbeat(
                    device.id,
                    network_ok=diag.tcp_ok,
                    protocol_ok=diag.protocol_ok,
                    latency_ms=diag.latency_ms or diag.tcp_latency_ms,
                    error="" if diag.protocol_ok else (diag.protocol_message or diag.tcp_message),
                    ip_address=device.ip_address,
                    port=int(device.port or 4370),
                    name=device.name,
                    protocol_code=diag.protocol_code,
                    failure_stage=diag.failure_stage,
                    auth_status=diag.auth_status,
                    comm_key_is_zero=diag.comm_key_is_zero,
                    auth_error=(diag.protocol_code or "").endswith("AUTH_ERROR"),
                )
                persisted = persist_device_status(link)
                health = persist_health_status(link)
                self._devices.update_status(
                    device.id,
                    status=persisted,
                    last_error="" if diag.protocol_ok else (link.last_error or ""),
                    serial_number=(diag.info or {}).get("serial_number") or None,
                )
                self._devices.update_telemetry(
                    device.id,
                    health_status=health,
                    last_health_at=link.last_heartbeat_at,
                    firmware=(diag.info or {}).get("firmware") or None,
                    serial_number=(diag.info or {}).get("serial_number") or None,
                )
            self._sync_logs.add(
                device_id=device.id,
                action="test_connection",
                success=diag.protocol_ok,
                message=diag.protocol_message or message,
                details={
                    "latency_ms": diag.latency_ms,
                    "tcp_ok": diag.tcp_ok,
                    "protocol_ok": diag.protocol_ok,
                    "protocol_code": diag.protocol_code,
                    "failure_stage": diag.failure_stage,
                },
            )
            return {
                "online": diag.protocol_ok,
                "network_ok": diag.tcp_ok,
                "protocol_ok": diag.protocol_ok,
                "message": message,
                "latency_ms": diag.latency_ms or diag.tcp_latency_ms,
                "protocol_code": diag.protocol_code,
                "failure_stage": diag.failure_stage,
                "auth_status": diag.auth_status,
                "info": diag.info or None,
                "diagnostic": diag.to_public_dict(),
            }

        adapter = create_adapter(
            device,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )
        try:
            health = adapter.health()
            info = None
            if health.online:
                try:
                    with adapter:
                        info = adapter.device_info()
                        self._devices.update_telemetry(
                            device.id,
                            firmware=info.firmware or None,
                            mac_address=info.mac_address or None,
                            sdk_version=info.sdk_version or None,
                            model=info.model or None,
                            serial_number=info.serial_number or None,
                            current_users=info.user_count,
                            current_logs=info.attendance_count,
                            health_status="healthy",
                            last_health_at=utcnow().isoformat(),
                        )
                        self._devices.update_status(
                            device.id,
                            status=DeviceStatus.ONLINE.value,
                            serial_number=info.serial_number or None,
                        )
                except Exception as exc:
                    logger.warning("device_info after health failed: %s", exc)
                    self._devices.update_status(device.id, status=DeviceStatus.ONLINE.value)
            else:
                tcp_ok, _, tcp_msg = tcp_probe(device.ip_address, int(device.port or 4370))
                if tcp_ok:
                    from services.connection_state import communication_error_message

                    self._devices.update_status(
                        device.id,
                        status=DeviceStatus.DEGRADED.value,
                        last_error=health.message,
                    )
                    self._devices.update_telemetry(
                        device.id,
                        health_status="warning",
                        last_health_at=utcnow().isoformat(),
                    )
                    health.online = False
                    health.message = communication_error_message(
                        device.ip_address,
                        int(device.port or 4370),
                        error=health.message,
                    )
                else:
                    self._devices.update_status(
                        device.id,
                        status=DeviceStatus.OFFLINE.value,
                        last_error=health.message,
                    )
                    self._devices.update_telemetry(
                        device.id,
                        health_status="unhealthy",
                        last_health_at=utcnow().isoformat(),
                    )
            self._sync_logs.add(
                device_id=device.id,
                action="test_connection",
                success=health.online,
                message=health.message,
                details={"latency_ms": health.latency_ms},
            )
            return {
                "online": health.online,
                "message": health.message,
                "latency_ms": health.latency_ms,
                "info": info.__dict__ if info else None,
            }
        except Exception as exc:
            self._devices.update_status(
                device.id, status=DeviceStatus.ERROR.value, last_error=str(exc)
            )
            self._sync_logs.add(
                device_id=device.id,
                action="test_connection",
                success=False,
                message=str(exc),
            )
            return {"online": False, "message": str(exc), "latency_ms": 0, "info": None}

    def device_details(self, device_id: str) -> dict:
        device = self._devices.get(device_id)
        if not device:
            raise KeyError(f"Device {device_id} not found")
        return {
            "device": device.to_public_dict(),
            "location": device.location_label(),
            "health_status": device.health_status,
            "last_sync": device.last_sync_at,
            "last_attendance": device.last_attendance_at,
            "current_users": device.current_users,
            "current_logs": device.current_logs,
            "firmware": device.firmware,
            "mac_address": device.mac_address,
            "sdk_version": device.sdk_version,
        }

    def status_summary(self) -> dict[str, int]:
        counts = self._devices.counts_by_status()
        connected = counts.get(DeviceStatus.ONLINE.value, 0) + counts.get(
            DeviceStatus.DEGRADED.value, 0
        )
        offline = counts.get(DeviceStatus.OFFLINE.value, 0)
        return {
            "total": sum(counts.values()),
            "connected": connected,
            "offline": offline,
            "unknown": counts.get(DeviceStatus.UNKNOWN.value, 0),
            "by_status": counts,
        }
