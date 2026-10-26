#!/usr/bin/env python3
"""Brixigo Connector Agent — primary entrypoint (system tray UI)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback

# Package root on sys.path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _fatal_dialog(title: str, message: str) -> None:
    """Best-effort user-visible error for windowed builds (console=False)."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(True)
        QMessageBox.critical(None, title, message)
    except Exception:
        print(f"{title}: {message}", file=sys.stderr)


def _run_soak(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="brixigo-connector soak")
    parser.add_argument("--cycles", type=int, default=1, help="Poll/upload cycles")
    parser.add_argument("--sleep", type=float, default=0, help="Seconds between cycles")
    parser.add_argument("--device", action="append", default=[], help="Device id or terminal code")
    parser.add_argument("--no-upload", action="store_true", help="Poll only, skip cloud upload")
    args = parser.parse_args(argv)

    from app.bootstrap import create_container
    from app.instance_lock import release_engine_lock, try_acquire_engine_lock
    from services.soak_service import SoakService

    if not try_acquire_engine_lock():
        print("ERROR: Another connector engine is already running.", file=sys.stderr)
        return 3

    container = create_container()
    try:
        soak = SoakService(container)
        report = soak.run(
            cycles=max(1, args.cycles),
            sleep_seconds=max(0.0, args.sleep),
            device_ids=args.device or None,
            upload=not args.no_upload,
        )
        soak.print_report(report)
        return 0 if report.get("summary", {}).get("ok") else 2
    finally:
        container.shutdown()
        release_engine_lock()


def main() -> int:
    # Required for frozen Windows builds if any library uses multiprocessing helpers.
    try:
        import multiprocessing

        multiprocessing.freeze_support()
    except Exception:
        pass

    mode = (sys.argv[1] if len(sys.argv) > 1 else "tray").lower()

    if mode in ("soak", "--soak"):
        return _run_soak(sys.argv[2:])

    if mode in ("service", "--service"):
        from services.windows_service import run_headless, _service_main

        if sys.platform.startswith("win"):
            try:
                _service_main()
                return 0
            except ImportError:
                run_headless()
                return 0
        run_headless()
        return 0

    if mode in ("headless", "--headless"):
        from services.windows_service import run_headless

        run_headless()
        return 0

    # Default: interactive tray + dashboard UI. Engine runs here only when the
    # Windows service is not already owning the workers (prevents dual sync).
    from app.bootstrap import create_container
    from app.instance_lock import (
        is_windows_service_running,
        release_engine_lock,
        release_tray_lock,
        try_acquire_engine_lock,
        try_acquire_tray_lock,
    )
    from services.connector_engine import ConnectorEngine
    from ui.tray import TrayApp, notify_existing_tray_instance

    logger = logging.getLogger("brixigo")
    startup_log = logging.getLogger("brixigo.startup")
    owns_engine = False
    container = None
    engine = None

    try:
        # Cursor / test shells often set QT_QPA_PLATFORM=offscreen. That makes Qt
        # report a visible window while Windows never creates a real HWND.
        plat = (os.environ.get("QT_QPA_PLATFORM") or "").strip().lower()
        if plat in {"offscreen", "minimal", "null", "vnc"}:
            os.environ.pop("QT_QPA_PLATFORM", None)
            startup_log.warning("Cleared QT_QPA_PLATFORM=%s before UI start", plat)

        # Second launch: bring existing MainWindow to front — never start a duplicate.
        if notify_existing_tray_instance():
            logger.info("Existing tray UI notified — exiting duplicate process")
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.quit()
            return 0

        if not try_acquire_tray_lock():
            # Race: UI started between notify and mutex — ask it to show, then exit.
            if notify_existing_tray_instance():
                return 0
            _fatal_dialog(
                "Brixigo Connector",
                "Brixigo Connector is already running.\n\n"
                "Check the system tray (including the ^ overflow on Windows 11),\n"
                "or run Launch Connector UI.bat to bring Settings to the front.",
            )
            return 0

        container = create_container()
        startup_log.info("Application started")
        startup_log.info("Loading config")
        engine = ConnectorEngine(container)

        if is_windows_service_running():
            logger.info(
                "Windows service BrixigoConnector is running — tray starts in UI-only mode"
            )
            startup_log.info("Windows service running — UI-only mode")
        elif try_acquire_engine_lock():
            engine.start()
            owns_engine = True
            startup_log.info("Connector engine started in tray process")
        else:
            logger.warning(
                "Another connector engine holds the lock — tray starts in UI-only mode"
            )
            startup_log.warning("Engine lock held elsewhere — UI-only mode")

        app = TrayApp(engine, owns_engine=owns_engine)
        return app.run()
    except Exception as exc:
        tb = traceback.format_exc()
        logging.getLogger("brixigo.crash").critical(
            "Tray startup failed: %s", exc, exc_info=True
        )
        try:
            startup_log.critical("Tray startup failed: %s\n%s", exc, tb)
        except Exception:
            pass
        # Best-effort write when logging may not be configured yet.
        try:
            from app.bootstrap import resolve_root_dir

            log_path = resolve_root_dir() / "logs" / "startup.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\nFATAL startup failure:\n{tb}\n")
        except Exception:
            pass
        _fatal_dialog(
            "Brixigo Connector — Startup Failed",
            f"The connector could not start.\n\n{exc}\n\n"
            f"See logs/startup.log for details.\n\n{tb[-1200:]}",
        )
        return 1
    finally:
        if owns_engine and engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
            release_engine_lock()
        if container is not None:
            try:
                container.shutdown()
            except Exception:
                pass
        release_tray_lock()


if __name__ == "__main__":
    raise SystemExit(main())
