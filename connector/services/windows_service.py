"""Windows Service host for Brixigo Connector Agent."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

# Ensure package root on path when frozen / service context
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logger = logging.getLogger("brixigo.service")


def run_headless() -> None:
    """Run connector engine until SIGTERM / SIGINT / KeyboardInterrupt."""
    from app.bootstrap import create_container
    from app.instance_lock import release_engine_lock, try_acquire_engine_lock
    from services.connector_engine import ConnectorEngine

    if not try_acquire_engine_lock():
        raise RuntimeError(
            "Another connector engine is already running. "
            "Stop the Windows service or other headless/tray engine first."
        )

    container = create_container()
    engine = ConnectorEngine(container)
    stop = {"flag": False}

    def _request_stop(signum=None, frame=None) -> None:
        logger.info("Shutdown signal received (%s)", signum)
        stop["flag"] = True

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _request_stop)
        except Exception:
            pass

    engine.start()
    logger.info("Headless connector running")
    try:
        while not stop["flag"]:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested via KeyboardInterrupt")
    finally:
        try:
            engine.stop()
        except Exception as exc:
            logger.error("Engine stop error: %s", exc)
        try:
            container.shutdown()
        except Exception as exc:
            logger.error("Container shutdown error: %s", exc)
        release_engine_lock()


def _service_main() -> None:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    from app.bootstrap import create_container
    from app.instance_lock import release_engine_lock, try_acquire_engine_lock
    from services.connector_engine import ConnectorEngine

    class BrixigoConnectorService(win32serviceutil.ServiceFramework):
        _svc_name_ = "BrixigoConnector"
        _svc_display_name_ = "Brixigo Connector Agent"
        _svc_description_ = (
            "Synchronizes biometric attendance devices with Brixigo Cloud Payroll & HRMS."
        )

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._container = None
            self._engine = None
            self._stopping = False
            self._owns_lock = False

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stopping = True
            win32event.SetEvent(self._stop_event)
            self._safe_shutdown()

        def _safe_shutdown(self) -> None:
            try:
                if self._engine:
                    self._engine.stop()
                    self._engine = None
                if self._container:
                    self._container.shutdown()
                    self._container = None
            except Exception as exc:
                logger.error("Service stop error: %s", exc)
            finally:
                if self._owns_lock:
                    release_engine_lock()
                    self._owns_lock = False

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            try:
                if not try_acquire_engine_lock():
                    msg = (
                        "Engine lock already held — refusing to start duplicate service engine"
                    )
                    logger.error(msg)
                    servicemanager.LogErrorMsg(msg)
                    return
                self._owns_lock = True
                self._container = create_container()
                self._engine = ConnectorEngine(self._container)
                self._engine.start()
                # Restart-on-crash loop inside service process
                while not self._stopping:
                    rc = win32event.WaitForSingleObject(self._stop_event, 5000)
                    if rc == win32event.WAIT_OBJECT_0:
                        break
                    if self._engine and not self._engine.running and not self._stopping:
                        logger.error("Engine stopped unexpectedly — restarting")
                        servicemanager.LogErrorMsg("Engine stopped unexpectedly — restarting")
                        try:
                            self._engine.start()
                        except Exception as exc:
                            logger.exception("Engine restart failed: %s", exc)
                            servicemanager.LogErrorMsg(f"Engine restart failed: {exc}")
            except Exception as exc:
                logger.exception("Service crashed: %s", exc)
                servicemanager.LogErrorMsg(str(exc))
            finally:
                if not self._stopping:
                    self._safe_shutdown()
                elif self._engine or self._container or self._owns_lock:
                    self._safe_shutdown()
                servicemanager.LogMsg(
                    servicemanager.EVENTLOG_INFORMATION_TYPE,
                    servicemanager.PYS_SERVICE_STOPPED,
                    (self._svc_name_, ""),
                )

    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(BrixigoConnectorService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(BrixigoConnectorService)


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        try:
            _service_main()
        except ImportError:
            logger.warning("pywin32 not available — running headless")
            run_headless()
    else:
        run_headless()
