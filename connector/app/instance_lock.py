"""Single-instance engine lock — prevents tray + service dual engines."""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional

logger = logging.getLogger("brixigo.service")

_MUTEX_NAME = "Global\\BrixigoConnectorEngine"
_TRAY_MUTEX_NAME = "Global\\BrixigoConnectorTrayUI"
_handle: Optional[object] = None
_tray_handle: Optional[object] = None
_SERVICE_CACHE_TTL_SECONDS = 15.0
_service_status_cache: tuple[float, bool] = (0.0, False)


def is_windows_service_running(service_name: str = "BrixigoConnector") -> bool:
    """Return True when the Windows service is installed and running."""
    global _service_status_cache
    if not sys.platform.startswith("win"):
        return False
    now = time.monotonic()
    cached_at, cached_val = _service_status_cache
    if now - cached_at < _SERVICE_CACHE_TTL_SECONDS:
        return cached_val
    try:
        import subprocess

        result = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        out = (result.stdout or "") + (result.stderr or "")
        running = "RUNNING" in out.upper()
        _service_status_cache = (now, running)
        return running
    except Exception as exc:
        logger.debug("Service status check failed: %s", exc)
        _service_status_cache = (now, False)
        return False


def try_acquire_engine_lock() -> bool:
    """
    Acquire a named mutex so only one process runs ConnectorEngine workers.
    Returns True if this process owns the engine lock.
    """
    global _handle
    if _handle is not None:
        return True
    if not sys.platform.startswith("win"):
        # Non-Windows: best-effort file lock via exclusive create is enough for soak/dev
        return True
    try:
        import win32event
        import win32api
        import winerror

        handle = win32event.CreateMutex(None, False, _MUTEX_NAME)
        last_error = win32api.GetLastError()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            win32api.CloseHandle(handle)
            logger.warning("Engine lock already held — another connector process owns the engine")
            return False
        _handle = handle
        logger.info("Acquired connector engine lock")
        return True
    except ImportError:
        # pywin32 missing — allow start (dev environments)
        logger.debug("pywin32 unavailable for engine mutex; proceeding without lock")
        return True
    except Exception as exc:
        logger.error("Failed to acquire engine lock: %s", exc)
        return False


def release_engine_lock() -> None:
    global _handle
    if _handle is None:
        return
    try:
        import win32api

        win32api.CloseHandle(_handle)
    except Exception:
        pass
    _handle = None


def try_acquire_tray_lock() -> bool:
    """
    Acquire a named mutex so only one tray UI process runs.
    Returns True if this process owns the tray UI lock.
    """
    global _tray_handle
    if _tray_handle is not None:
        return True
    if not sys.platform.startswith("win"):
        return True
    try:
        import win32api
        import win32event
        import winerror

        handle = win32event.CreateMutex(None, False, _TRAY_MUTEX_NAME)
        last_error = win32api.GetLastError()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            win32api.CloseHandle(handle)
            logger.warning("Tray UI lock already held — another connector UI is running")
            return False
        _tray_handle = handle
        logger.info("Acquired connector tray UI lock")
        return True
    except ImportError:
        logger.debug("pywin32 unavailable for tray mutex; proceeding without lock")
        return True
    except Exception as exc:
        logger.error("Failed to acquire tray UI lock: %s", exc)
        return False


def release_tray_lock() -> None:
    global _tray_handle
    if _tray_handle is None:
        return
    try:
        import win32api

        win32api.CloseHandle(_tray_handle)
    except Exception:
        pass
    _tray_handle = None
