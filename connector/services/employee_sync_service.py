"""Two-way employee synchronization between Brixigo Cloud and biometric devices."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from adapters.registry import create_adapter
from api.cloud_client import CloudAPIError, CloudClient
from models import Device, DeviceUser, EmployeeStatus, SyncAction, new_id, utcnow
from models.phase3 import Employee
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import AuditRepository
from storage.repositories.phase3_repo import (
    EmployeeRepository,
    EmployeeSyncQueueRepository,
)
from storage.repositories.sync_repo import SyncLogRepository

logger = logging.getLogger("brixigo.sync")


class EmployeeSyncService:
    """
    Pull employee events from cloud and push create/update/delete/deactivate
    operations to assigned biometric devices in parallel.
    """

    def __init__(
        self,
        cloud: CloudClient,
        employees: EmployeeRepository,
        sync_queue: EmployeeSyncQueueRepository,
        devices: DeviceRepository,
        sync_logs: SyncLogRepository,
        audit: AuditRepository,
        *,
        list_devices: Callable[[], list[Device]],
        connect_timeout: float = 8.0,
        read_timeout: float = 30.0,
        max_workers: int = 16,
        interval_seconds: int = 120,
        on_sync_failure: Optional[Callable[[str, str], None]] = None,
        device_users=None,
    ) -> None:
        self._cloud = cloud
        self._employees = employees
        self._queue = sync_queue
        self._devices = devices
        self._sync_logs = sync_logs
        self._audit = audit
        self._list_devices = list_devices
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_workers = max(2, max_workers)
        self.interval_seconds = max(30, interval_seconds)
        self.on_sync_failure = on_sync_failure
        self._device_users = device_users
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
            target=self._loop, name="brixigo-employee-sync", daemon=True
        )
        self._thread.start()
        logger.info("Employee sync started interval=%ss", self.interval_seconds)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def pull_from_cloud(self) -> int:
        if not self._cloud.settings.connector_id:
            return 0
        try:
            payload = self._cloud.fetch_employee_sync_events()
        except CloudAPIError as exc:
            logger.warning("Employee sync pull failed: %s", exc)
            return 0
        events = payload if isinstance(payload, list) else payload.get("events", [])
        count = 0
        for event in events or []:
            if not isinstance(event, dict):
                continue
            action = str(event.get("action") or SyncAction.UPDATE.value).lower()
            emp = self._normalize_employee(event.get("employee") or event)
            saved = self._employees.upsert(emp)
            if action == SyncAction.DELETE.value:
                saved.status = EmployeeStatus.DELETED.value
                self._employees.upsert(saved)
            elif action == SyncAction.DEACTIVATE.value:
                saved.status = EmployeeStatus.INACTIVE.value
                self._employees.upsert(saved)
            elif action == SyncAction.REACTIVATE.value:
                saved.status = EmployeeStatus.ACTIVE.value
                self._employees.upsert(saved)
            targets = self._target_devices(saved)
            if not targets:
                self._queue.enqueue(
                    employee_id=saved.id, action=action, device_id="", payload={"employee_code": saved.employee_code}
                )
            for device in targets:
                self._queue.enqueue(
                    employee_id=saved.id,
                    action=action,
                    device_id=device.id,
                    payload={"employee_code": saved.employee_code},
                )
            count += 1
            try:
                self._cloud.acknowledge_employee_sync(event.get("id") or saved.id)
            except Exception:
                pass
        return count

    def process_queue(self) -> dict:
        pending = self._queue.list_pending()
        if not pending:
            return {"processed": 0, "succeeded": 0, "failed": 0}
        succeeded = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._process_item, item): item for item in pending}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    ok = fut.result()
                    if ok:
                        succeeded += 1
                        self._queue.mark(item["id"], status="succeeded")
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    self._queue.mark(item["id"], status="failed", error=str(exc))
                    if self.on_sync_failure:
                        self.on_sync_failure(str(exc), item.get("device_id") or "")
        return {"processed": len(pending), "succeeded": succeeded, "failed": failed}

    def sync_employee_to_devices(self, employee_id: str) -> dict:
        employee = self._employees.get(employee_id)
        if not employee:
            raise KeyError(f"Employee {employee_id} not found")
        results = []
        for device in self._target_devices(employee):
            results.append(self._apply_to_device(employee, device, SyncAction.UPDATE.value))
        return {"employee_id": employee_id, "results": results}

    def collect_directory(self) -> list[dict]:
        """Build the organisation employee directory from local HR + device users."""
        people: dict[str, dict] = {}
        devices = {d.id: d for d in self._list_devices()}
        try:
            for emp in self._employees.list_all():
                code = str(emp.employee_code or "").strip()
                name = str(emp.name or "").strip()
                if not code or not name or name.lower() == code.lower():
                    continue
                people[code] = {
                    "employee_code": code,
                    "name": name,
                    "organization": emp.organization or "",
                    "branch": emp.branch or "",
                    "department": emp.department or "",
                    "designation": emp.designation or "",
                    "card_number": emp.card_number or "",
                    "device_ids": list(emp.device_ids or []),
                }
        except Exception:
            logger.debug("Local employee directory skipped", exc_info=True)
        if self._device_users is not None and hasattr(self._device_users, "list_named"):
            try:
                for user in self._device_users.list_named():
                    code = str(user.user_id or "").strip()
                    name = str(user.name or "").strip()
                    if not code or not name or name.lower() == code.lower():
                        continue
                    device = devices.get(user.device_id)
                    row = people.get(code) or {"employee_code": code}
                    existing_name = str(row.get("name") or "").strip()
                    if not existing_name:
                        row["name"] = name
                    row["employee_code"] = code
                    row["device_id"] = user.device_id
                    row["card_number"] = user.card or row.get("card_number") or ""
                    if device:
                        row["terminal_code"] = device.terminal_code
                        row["device_name"] = device.name
                        row["branch"] = row.get("branch") or device.branch or ""
                        row["organization"] = row.get("organization") or device.organization or ""
                    people[code] = row
            except Exception:
                logger.debug("Device user directory skipped", exc_info=True)
        return list(people.values())

    def push_directory_to_cloud(self) -> int:
        if not self._cloud.settings.connector_id:
            return 0
        employees = self.collect_directory()
        if not employees:
            return 0
        try:
            self._cloud.push_employee_directory(employees)
            logger.info("Pushed %s employee names to cloud", len(employees))
            return len(employees)
        except CloudAPIError as exc:
            logger.warning("Employee directory push failed: %s", exc)
            return 0

    def sync_all(self) -> dict:
        pushed = self.push_directory_to_cloud()
        pulled = self.pull_from_cloud()
        processed = self.process_queue()
        return {"directory_pushed": pushed, "pulled": pulled, **processed}

    def _process_item(self, item: dict) -> bool:
        employee = self._employees.get(item["employee_id"])
        if not employee:
            self._queue.mark(item["id"], status="failed", error="employee missing")
            return False
        device_id = item.get("device_id") or ""
        if not device_id:
            # Fan-out to all matching devices
            ok_all = True
            for device in self._target_devices(employee):
                if not self._apply_to_device(employee, device, item["action"]):
                    ok_all = False
            return ok_all
        device = self._devices.get(device_id)
        if not device:
            self._queue.mark(item["id"], status="failed", error="device missing")
            return False
        return self._apply_to_device(employee, device, item["action"])

    def _apply_to_device(self, employee: Employee, device: Device, action: str) -> bool:
        adapter = create_adapter(
            device,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )
        try:
            with adapter:
                if action in (SyncAction.DELETE.value,):
                    adapter.delete_user(employee.employee_code)
                elif action == SyncAction.DEACTIVATE.value:
                    # Deactivate by removing from device while keeping local record
                    try:
                        adapter.delete_user(employee.employee_code)
                    except Exception:
                        # Some firmwares prefer privilege demotion
                        adapter.set_user(
                            DeviceUser(
                                user_id=employee.employee_code,
                                name=employee.name,
                                card=employee.card_number,
                                privilege=0,
                                password=employee.pin,
                                enabled=False,
                            )
                        )
                else:
                    adapter.set_user(
                        DeviceUser(
                            user_id=employee.employee_code,
                            name=employee.name,
                            card=employee.card_number,
                            privilege=employee.privilege,
                            password=employee.pin,
                            enabled=employee.status == EmployeeStatus.ACTIVE.value,
                        )
                    )
            self._employees.mark_synced(employee.id)
            self._devices.touch_field(device.id, "last_employee_sync_at")
            self._sync_logs.add(
                device_id=device.id,
                action=f"employee_sync:{action}",
                success=True,
                message=f"{employee.employee_code} {action}",
                records_count=1,
            )
            self._audit.add(
                action="employee_sync",
                actor="system",
                success=True,
                message=f"{action} {employee.employee_code} → {device.name}",
                resource_type="employee",
                resource_id=employee.id,
            )
            return True
        except Exception as exc:
            logger.error(
                "Employee sync failed code=%s device=%s: %s",
                employee.employee_code,
                device.name,
                exc,
            )
            self._sync_logs.add(
                device_id=device.id,
                action=f"employee_sync:{action}",
                success=False,
                message=str(exc),
            )
            if self.on_sync_failure:
                self.on_sync_failure(str(exc), device.id)
            return False

    def _target_devices(self, employee: Employee) -> list[Device]:
        devices = self._list_devices()
        if employee.device_ids:
            return [d for d in devices if d.id in employee.device_ids and d.enabled]
        matched = []
        for d in devices:
            if not d.enabled:
                continue
            if employee.organization and d.organization and employee.organization != d.organization:
                continue
            if employee.branch and d.branch and employee.branch != d.branch:
                continue
            if employee.department and d.department and employee.department != d.department:
                continue
            if employee.gate and d.gate and employee.gate != d.gate:
                continue
            if d.branch and employee.branch and d.branch == employee.branch:
                matched.append(d)
            elif not employee.branch and not employee.device_ids:
                # Broad sync only when explicitly unscoped is discouraged;
                # include devices in same organization if set.
                if employee.organization and d.organization == employee.organization:
                    matched.append(d)
        return matched

    def _normalize_employee(self, data: dict) -> Employee:
        code = str(data.get("employee_code") or data.get("code") or "").strip()
        if not code:
            raise ValueError("employee_code required")
        existing = self._employees.get_by_code(code)
        return Employee(
            id=(existing.id if existing else str(data.get("id") or new_id())),
            employee_code=code,
            name=str(data.get("name") or code),
            department=str(data.get("department") or ""),
            designation=str(data.get("designation") or ""),
            branch=str(data.get("branch") or ""),
            organization=str(data.get("organization") or ""),
            building=str(data.get("building") or ""),
            floor=str(data.get("floor") or ""),
            gate=str(data.get("gate") or ""),
            shift=str(data.get("shift") or ""),
            card_number=str(data.get("card_number") or data.get("card") or ""),
            pin=str(data.get("pin") or data.get("password") or ""),
            status=str(data.get("status") or EmployeeStatus.ACTIVE.value),
            device_ids=list(data.get("device_ids") or []),
            privilege=int(data.get("privilege") or 0),
            cloud_version=int(data.get("version") or data.get("cloud_version") or 0),
            extra=dict(data.get("extra") or {}),
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_all()
            except Exception as exc:
                logger.exception("Employee sync cycle failed: %s", exc)
            self._stop.wait(self.interval_seconds)
