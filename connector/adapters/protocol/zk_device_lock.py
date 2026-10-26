"""Process-wide per-device socket lock.

ZK / eSSL terminals typically accept a single SDK session on TCP 4370.
Heartbeat, poll, health, diagnostics, and UI tests must never open
concurrent sockets to the same host:port.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("brixigo.connection")


class DevicePortLockRegistry:
    """One non-reentrant lock per (ip, port). Nested opens on the same thread wait."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, int], threading.Lock] = {}
        self._holders: dict[tuple[str, int], str] = {}
        self._holder_ident: dict[tuple[str, int], int] = {}
        # connecting = SYN in flight (NOT proof the device is up)
        # established = TCP handshake completed
        self._states: dict[tuple[str, int], str] = {}
        # Protocol sessions (zk-transport) register here so tcp-probe yields.
        self._priority_waiters: dict[tuple[str, int], int] = {}

    def _lock_for(self, ip: str, port: int) -> threading.Lock:
        key = (str(ip).strip(), int(port))
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def begin_priority_wait(self, ip: str, port: int) -> None:
        key = (str(ip).strip(), int(port))
        with self._guard:
            self._priority_waiters[key] = int(self._priority_waiters.get(key, 0)) + 1

    def end_priority_wait(self, ip: str, port: int) -> None:
        key = (str(ip).strip(), int(port))
        with self._guard:
            left = int(self._priority_waiters.get(key, 0)) - 1
            if left <= 0:
                self._priority_waiters.pop(key, None)
            else:
                self._priority_waiters[key] = left

    def has_priority_waiter(self, ip: str, port: int) -> bool:
        key = (str(ip).strip(), int(port))
        with self._guard:
            return int(self._priority_waiters.get(key, 0)) > 0

    def try_acquire(self, ip: str, port: int, *, owner: str = "", timeout: float = 0.0) -> bool:
        key = (str(ip).strip(), int(port))
        # Yield to real protocol connects — probes must not steal the port.
        owner_s = str(owner or "")
        if owner_s.startswith("tcp-probe:") and self.has_priority_waiter(ip, port):
            return False
        lock = self._lock_for(ip, port)
        got = (
            lock.acquire(blocking=True, timeout=timeout)
            if timeout > 0
            else lock.acquire(blocking=False)
        )
        if not got:
            return False
        with self._guard:
            self._holders[key] = owner_s or threading.current_thread().name
            self._holder_ident[key] = threading.get_ident()
            self._states[key] = "connecting"
        return True

    def acquire(self, ip: str, port: int, *, owner: str = "", timeout: float = 8.0) -> None:
        key = (str(ip).strip(), int(port))
        lock = self._lock_for(ip, port)
        with self._guard:
            holder = self._holders.get(key, "")
        if holder:
            logger.info(
                "DEVICE_LOCK waiting ip=%s port=%s owner=%s held_by=%s timeout=%s",
                ip,
                port,
                owner or "-",
                holder,
                timeout,
            )
        got = lock.acquire(blocking=True, timeout=max(0.1, float(timeout)))
        if not got:
            raise TimeoutError(
                f"TCP connect failed: timed out waiting for {ip}:{port}"
            )
        with self._guard:
            self._holders[key] = owner or threading.current_thread().name
            self._holder_ident[key] = threading.get_ident()
            self._states[key] = "connecting"
        logger.info(
            "DEVICE_LOCK acquired ip=%s port=%s owner=%s",
            ip,
            port,
            self._holders[key],
        )

    def mark_established(self, ip: str, port: int) -> None:
        key = (str(ip).strip(), int(port))
        with self._guard:
            if key in self._holders:
                self._states[key] = "established"

    def session_established(self, ip: str, port: int) -> bool:
        key = (str(ip).strip(), int(port))
        with self._guard:
            return self._states.get(key) == "established"

    def release(self, ip: str, port: int) -> None:
        key = (str(ip).strip(), int(port))
        with self._guard:
            holder = self._holders.pop(key, "")
            self._holder_ident.pop(key, None)
            self._states.pop(key, None)
            lock = self._locks.get(key)
        if lock is not None:
            try:
                lock.release()
            except RuntimeError:
                logger.warning(
                    "DEVICE_LOCK release without owner ip=%s port=%s", ip, port
                )
                return
        logger.info(
            "DEVICE_LOCK released ip=%s port=%s was_owner=%s",
            ip,
            port,
            holder or "-",
        )

    def holder(self, ip: str, port: int) -> str:
        key = (str(ip).strip(), int(port))
        with self._guard:
            return self._holders.get(key, "")

    def owned_by_current_thread(self, ip: str, port: int) -> bool:
        key = (str(ip).strip(), int(port))
        with self._guard:
            return self._holder_ident.get(key) == threading.get_ident()

    @contextmanager
    def hold(self, ip: str, port: int, *, owner: str = "") -> Iterator[None]:
        self.acquire(ip, port, owner=owner)
        try:
            yield
        finally:
            self.release(ip, port)


DEVICE_PORT_LOCKS = DevicePortLockRegistry()
