"""System tray application for Brixigo Connector Agent."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import traceback

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from services.connector_engine import ConnectorEngine
from services.sync_report import format_sync_dialog
from ui.main_window import MainWindow, _FnWorker

logger = logging.getLogger("brixigo.ui")
startup_log = logging.getLogger("brixigo.startup")

_TRAY_IPC_NAME = "BrixigoConnectorTrayUI"
_SETTINGS_ORG = "Brixigo"
_SETTINGS_APP = "Connector"


def _make_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor("#0f2744"))
    painter = QPainter(pix)
    painter.setPen(QColor("#3dd68c"))
    painter.setBrush(QColor("#3dd68c"))
    painter.drawEllipse(12, 12, 40, 40)
    painter.setPen(QColor("#0f2744"))
    painter.drawText(pix.rect(), int(Qt.AlignmentFlag.AlignCenter), "B")
    painter.end()
    icon = QIcon(pix)
    if icon.isNull():
        logger.error("Generated tray icon is null")
    return icon


def _ui_settings() -> QSettings:
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def _wants_minimized_start() -> bool:
    """Autostart / tray-only launches skip the dashboard window."""
    args = {a.lower() for a in sys.argv[1:]}
    return bool(args & {"--minimized", "--tray-only", "minimized", "tray-only"})


def notify_existing_tray_instance() -> bool:
    """If another tray UI is running, ask it to show the dashboard and return True.

    A successful notify means this process must exit (single-instance). The existing
    process owns the UI and will bring MainWindow to the front — we do not spawn
    another BrixigoConnector.exe.
    """
    plat = (os.environ.get("QT_QPA_PLATFORM") or "").strip().lower()
    if plat in {"offscreen", "minimal", "null", "vnc"}:
        os.environ.pop("QT_QPA_PLATFORM", None)

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    socket = QLocalSocket()
    socket.connectToServer(_TRAY_IPC_NAME)
    connected = socket.waitForConnected(1500)
    if not connected:
        if created_app:
            app.quit()
        return False
    socket.write(b"show\n")
    if not socket.waitForBytesWritten(1000):
        logger.warning("IPC show request write timed out")
        socket.disconnectFromServer()
        if created_app:
            app.quit()
        return False
    socket.flush()
    # Give the live instance a moment to process show before we exit.
    socket.waitForDisconnected(400)
    socket.disconnectFromServer()
    logger.info("Notified existing tray instance to show dashboard")
    startup_log.info(
        "Existing instance notified — bring MainWindow to front; exiting duplicate"
    )
    if created_app:
        app.quit()
    return True


class TrayApp:
    def __init__(self, engine: ConnectorEngine, *, owns_engine: bool = True):
        self.engine = engine
        self.owns_engine = owns_engine
        self.window: MainWindow | None = None
        self._icon = _make_icon()

        try:
            self._build_ui()
        except Exception:
            startup_log.exception("TrayApp initialization failed")
            raise

    def _build_ui(self) -> None:
        """
        Startup sequence (interactive):
          QApplication → MainWindow.show() → Tray icon → IPC → (exec in run())

        Autostart (--minimized):
          QApplication → Tray icon only → IPC → (exec in run())
        """
        startup_log.info("Application started")
        logger.info("Creating QApplication")
        plat = (os.environ.get("QT_QPA_PLATFORM") or "").strip().lower()
        if plat in {"offscreen", "minimal", "null", "vnc"}:
            os.environ.pop("QT_QPA_PLATFORM", None)
            startup_log.warning("Cleared QT_QPA_PLATFORM=%s so the dashboard can appear", plat)
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("Brixigo Connector")
        self.app.setOrganizationName(_SETTINGS_ORG)
        startup_log.info("Qt platform=%s", self.app.platformName())

        startup_log.info("Loading config / preparing UI")
        minimized = _wants_minimized_start()
        tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if not tray_available:
            logger.error("System tray is not available on this desktop session")

        # Interactive launches always open the settings dashboard.
        # Prior dashboard_visible / first_run flags must NOT suppress the window.
        # Only --minimized / --tray-only (Windows Run autostart) stay tray-only.
        # Open MainWindow BEFORE any modal dialogs so Settings is never blocked.
        if not minimized or not tray_available:
            startup_log.info("Creating MainWindow")
            self.open_dashboard()
            startup_log.info("Showing MainWindow")
        else:
            startup_log.info(
                "Minimized start requested — MainWindow deferred until tray activation"
            )
            _ui_settings().setValue("ui/first_run_done", True)

        startup_log.info("Creating Tray")
        self._create_tray(tray_available=tray_available)
        self._start_ipc_server()
        # Never show modal dialogs before app.exec() — they block startup and can
        # leave the UI invisible if the process is not on the interactive desktop.
        if not tray_available:
            startup_log.warning(
                "System tray unavailable — Settings window is the primary UI"
            )
            QTimer.singleShot(
                500,
                lambda: QMessageBox.warning(
                    self.window,
                    "Brixigo Connector",
                    "The Windows system tray is not available in this session.\n\n"
                    "Use the Settings window to manage the connector.",
                ),
            )
        startup_log.info("UI bootstrap complete — ready for event loop")

    def _create_tray(self, *, tray_available: bool) -> None:
        logger.info("Creating QSystemTrayIcon (icon_null=%s)", self._icon.isNull())
        self.tray = QSystemTrayIcon(self._icon, self.app)
        mode = "UI (service manages sync)" if not self.owns_engine else "Connector agent"
        self.tray.setToolTip(f"Brixigo Connector — {mode}")
        self.tray.setIcon(self._icon)

        # Keep menu/actions on self so they are not garbage-collected.
        self.menu = QMenu()
        act_open = QAction("Open Dashboard", self.menu)
        act_open.triggered.connect(self.open_dashboard)
        act_sync = QAction("Sync Now", self.menu)
        act_sync.triggered.connect(self.sync_now)
        act_reconnect = QAction("Restart All Connections", self.menu)
        act_reconnect.triggered.connect(self._reconnect_all)
        act_restart = QAction("Restart Service", self.menu)
        act_restart.triggered.connect(self.restart_service)
        act_logs = QAction("View Logs", self.menu)
        act_logs.triggered.connect(self.view_logs)
        act_settings = QAction("Settings", self.menu)
        act_settings.triggered.connect(self.open_settings)
        act_exit = QAction("Exit", self.menu)
        act_exit.triggered.connect(self.exit_app)
        self._actions = (
            act_open,
            act_sync,
            act_reconnect,
            act_restart,
            act_logs,
            act_settings,
            act_exit,
        )

        for a in (act_open, act_sync, act_reconnect, act_restart, act_logs, act_settings):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(act_exit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()
        logger.info("Tray show() called — visible=%s", self.tray.isVisible())

        if tray_available and not self.tray.isVisible():
            logger.error("Tray icon reported not visible after show()")
            QMessageBox.warning(
                None,
                "Brixigo Connector",
                "The system tray icon could not be shown.\n\n"
                "On Windows 11, check the notification area overflow (^).\n"
                "Use the desktop shortcut or Launch Connector UI.bat to open Settings.",
            )

        msg = (
            "Tray UI connected. Sync is managed by the Windows service."
            if not self.owns_engine
            else "Connector agent is running in the system tray."
        )
        if QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(
                "Brixigo Connector",
                msg,
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _start_ipc_server(self) -> None:
        self._ipc_server = QLocalServer(self.app)
        QLocalServer.removeServer(_TRAY_IPC_NAME)
        if self._ipc_server.listen(_TRAY_IPC_NAME):
            self._ipc_server.newConnection.connect(self._on_ipc_connection)
            logger.info("Tray IPC server listening as %s", _TRAY_IPC_NAME)
        else:
            logger.warning(
                "Tray IPC server failed to listen: %s", self._ipc_server.errorString()
            )

    def _on_ipc_connection(self) -> None:
        while self._ipc_server.hasPendingConnections():
            sock = self._ipc_server.nextPendingConnection()
            if sock is None:
                continue
            if sock.bytesAvailable() or sock.waitForReadyRead(800):
                self._on_ipc_ready(sock)
            else:
                sock.disconnectFromServer()
                sock.deleteLater()

    def _on_ipc_ready(self, sock: QLocalSocket) -> None:
        try:
            data = bytes(sock.readAll()).decode("utf-8", errors="ignore").strip().lower()
            logger.info("Tray IPC received: %r", data)
            if data.startswith("reconnect") or data.startswith("restart-connections") or data.startswith("check-connections"):
                startup_log.info("IPC reconnect — check + restart all connections")
                self.open_dashboard()
                try:
                    if self.window is not None:
                        self.window._restart_all_connections(silent=True)
                    else:
                        self._reconnect_all()
                except Exception:
                    logger.exception("IPC reconnect failed")
                    self._reconnect_all()
            elif data.startswith("sync") or data.startswith("refresh"):
                startup_log.info("IPC %s — sync + restore MainWindow", data.split()[0])
                self.open_dashboard()
                try:
                    if self.window is not None:
                        self.window._sync_now()
                    else:
                        self.sync_now()
                except Exception:
                    logger.exception("IPC sync failed")
                    self.sync_now()
            elif data.startswith("show"):
                startup_log.info("IPC show — restoring MainWindow")
                self.open_dashboard()
        finally:
            sock.disconnectFromServer()

    def _reconnect_all(self) -> None:
        if getattr(self, "_reconnect_busy", False):
            return
        self._reconnect_busy = True
        worker = _FnWorker(self.engine.check_and_restart_connections)
        worker.setParent(self.app)
        self._reconnect_worker = worker

        def _done(report: object) -> None:
            self._reconnect_busy = False
            payload = report if isinstance(report, dict) else {}
            ok = bool(payload.get("ok"))
            msg = str(payload.get("message") or "Connection restart finished.")
            self.tray.showMessage(
                "Connections restarted" if ok else "Connection restart",
                msg[:250],
                QSystemTrayIcon.MessageIcon.Information
                if ok
                else QSystemTrayIcon.MessageIcon.Warning,
                6000,
            )
            if self.window is not None:
                try:
                    self.window.refresh()
                except Exception:
                    pass

        def _fail(message: str) -> None:
            self._reconnect_busy = False
            logger.error("Tray reconnect failed: %s", message)
            self.tray.showMessage(
                "Reconnect Failed", message, QSystemTrayIcon.MessageIcon.Critical, 5000
            )

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        threading.Thread(target=worker.run, name="brixigo-tray-reconnect", daemon=True).start()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Windows often delivers Trigger (single-click), not DoubleClick.
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            logger.info("Tray activated reason=%s — opening dashboard", reason)
            self.open_dashboard()

    def open_dashboard(self) -> None:
        try:
            if self.window is None:
                logger.info("Creating MainWindow")
                startup_log.info("Creating MainWindow")
                self.window = MainWindow(self.engine, owns_engine=self.owns_engine)
                self.window.setWindowIcon(self._icon)
                self.window.dashboard_hidden.connect(self._on_dashboard_hidden)
                self.window.dashboard_closed.connect(self._on_dashboard_closed)
                startup_log.info("MainWindow created (centered on primary screen)")
            else:
                logger.info("Re-showing existing MainWindow")
                startup_log.info("Restoring MainWindow")

            self._place_on_primary_screen(self.window)

            startup_log.info("MainWindow.show() called")
            self.window.setWindowState(
                (self.window.windowState() & ~Qt.WindowState.WindowMinimized)
                | Qt.WindowState.WindowActive
            )
            self.window.setVisible(True)
            self.window.show()
            startup_log.info("MainWindow.showNormal() called")
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            self._place_on_primary_screen(self.window)
            QTimer.singleShot(0, lambda: self._force_win32_foreground(self.window, 0) if self.window else None)
            QTimer.singleShot(300, lambda: self._force_win32_foreground(self.window, 0) if self.window else None)
            if self.window is not None:
                self.window._goto("dashboard")

            _ui_settings().setValue("ui/first_run_done", True)
            _ui_settings().setValue("ui/dashboard_visible", True)
            self._save_geometry()

            visible = self.window.isVisible()
            minimized = self.window.isMinimized()
            geo = self.window.geometry()
            logger.info(
                "Dashboard visible=%s minimized=%s geometry=%s",
                visible,
                minimized,
                geo,
            )
            startup_log.info(
                "MainWindow.isVisible()=%s minimized=%s geometry=%s",
                visible,
                minimized,
                geo,
            )
            if not visible:
                startup_log.error("MainWindow.show() ran but isVisible() is False")
        except Exception as exc:
            tb = traceback.format_exc()
            logger.exception("Failed to open dashboard: %s", exc)
            startup_log.exception("Failed to open MainWindow: %s", exc)
            QMessageBox.critical(
                None,
                "Dashboard Error",
                f"Failed to open the connector dashboard.\n\n{exc}\n\n"
                f"See logs/startup.log for details.\n\n{tb[-1500:]}",
            )

    def _native_hwnd(self, window: MainWindow) -> int:
        window.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        handle = window.windowHandle()
        if handle is None:
            window.winId()
            handle = window.windowHandle()
        if handle is None:
            return 0
        try:
            return int(handle.winId())
        except (TypeError, ValueError):
            return 0

    def _hwnd_by_title(self, title: str) -> int:
        if not sys.platform.startswith("win"):
            return 0
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found: list[int] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _enum(hwnd, _lparam):
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value == title:
                    found.append(int(hwnd))
                    return False
                return True

            user32.EnumWindows(_enum, 0)
            return found[0] if found else 0
        except Exception:
            return 0

    def _force_win32_foreground(self, window: MainWindow, attempt: int = 0) -> None:
        """Best-effort Win32 activate — Qt activateWindow alone is often ignored on Windows."""
        if window is None or not sys.platform.startswith("win"):
            return
        try:
            import ctypes

            if window.windowHandle() is not None:
                window.windowHandle().requestActivate()
            hwnd = self._native_hwnd(window)
            if hwnd in (0, 1):
                hwnd = self._hwnd_by_title(window.windowTitle() or "Brixigo Connector Agent")
            if hwnd in (0, 1):
                if attempt < 12:
                    QTimer.singleShot(150, lambda: self._force_win32_foreground(window, attempt + 1))
                else:
                    startup_log.warning("Win32 hwnd not ready yet (hwnd=%s)", hwnd)
                return
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            SW_RESTORE = 9
            SW_SHOW = 5
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_SHOW)
            fg = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg, None)
            our_tid = kernel32.GetCurrentThreadId()
            if fg_tid and fg_tid != our_tid:
                user32.AttachThreadInput(fg_tid, our_tid, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            if fg_tid and fg_tid != our_tid:
                user32.AttachThreadInput(fg_tid, our_tid, False)
            startup_log.info("Win32 SetForegroundWindow hwnd=%s", hwnd)
        except Exception as exc:
            startup_log.warning("Win32 foreground activate failed: %s", exc)

    def _restore_or_center(self, window: MainWindow) -> None:
        self._place_on_primary_screen(window)

    def _geometry_on_screen(self, window: MainWindow) -> bool:
        frame = window.frameGeometry()
        center = frame.center()
        for screen in self.app.screens():
            if screen.availableGeometry().contains(center):
                return True
        return False

    def _place_on_primary_screen(self, window: MainWindow) -> None:
        """Force the dashboard onto the primary monitor at a usable size."""
        if window is None:
            return
        screen = self.app.screenAt(window.pos()) or self.app.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(1440, max(1100, available.width() - 64))
        height = min(920, max(720, available.height() - 80))
        window.setMinimumSize(960, 640)
        window.resize(width, height)
        window.move(
            available.x() + max(0, (available.width() - width) // 2),
            available.y() + max(0, (available.height() - height) // 2),
        )
        logger.info("Placed dashboard on primary screen %s", window.geometry())

    def _center_window(self, window: MainWindow) -> None:
        self._place_on_primary_screen(window)

    def _save_geometry(self) -> None:
        if self.window is None:
            return
        try:
            _ui_settings().setValue("ui/geometry", self.window.save_window_geometry())
        except Exception as exc:
            logger.debug("saveGeometry failed: %s", exc)

    def _on_dashboard_hidden(self) -> None:
        logger.info("Dashboard hidden to tray")
        self._save_geometry()
        _ui_settings().setValue("ui/dashboard_visible", False)

    def _on_dashboard_closed(self) -> None:
        logger.info("Dashboard closed")
        self._save_geometry()
        _ui_settings().setValue("ui/dashboard_visible", False)
        self.window = None

    def sync_now(self) -> None:
        if not self.owns_engine:
            self.tray.showMessage(
                "Service Mode",
                "Sync is owned by the Windows service. Use Restart Service or wait for the next poll cycle.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            return
        if getattr(self, "_sync_busy", False):
            self.tray.showMessage(
                "Busy",
                "A sync is already running.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            return
        self._sync_busy = True
        worker = _FnWorker(self.engine.sync_now)
        worker.setParent(self.app)
        self._sync_worker = worker

        def _done(results: object) -> None:
            self._sync_busy = False
            title, body, ok = format_sync_dialog(results if isinstance(results, list) else [])
            icon = (
                QSystemTrayIcon.MessageIcon.Information
                if ok
                else QSystemTrayIcon.MessageIcon.Warning
            )
            self.tray.showMessage(title, body[:250], icon, 6000)

        def _fail(message: str) -> None:
            self._sync_busy = False
            logger.error("Tray sync failed: %s", message)
            self.tray.showMessage(
                "Sync Failed", message, QSystemTrayIcon.MessageIcon.Critical, 5000
            )

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        threading.Thread(target=worker.run, name="brixigo-tray-sync", daemon=True).start()

    def restart_service(self) -> None:
        try:
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["sc", "stop", "BrixigoConnector"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                subprocess.run(
                    ["sc", "start", "BrixigoConnector"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if self.owns_engine:
                self.engine.restart()
            self.tray.showMessage(
                "Restarted",
                "Connector service / engine restart requested.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        except Exception as exc:
            logger.exception("Restart failed: %s", exc)
            QMessageBox.warning(None, "Restart Failed", str(exc))

    def view_logs(self) -> None:
        path = self.engine.container.paths.logs
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def open_settings(self) -> None:
        self.open_dashboard()
        if self.window:
            try:
                self.window._open_settings()
            except Exception as exc:
                logger.exception("Settings failed: %s", exc)
                startup_log.exception("Settings failed: %s", exc)
                QMessageBox.critical(None, "Settings Error", str(exc))

    def exit_app(self) -> None:
        logger.info("Exit requested from tray")
        startup_log.info("Exit requested from tray")
        self._save_geometry()
        # Never stop the Windows service engine from tray Exit when UI-only.
        if self.owns_engine:
            self.engine.stop()
        if self.window is not None:
            self.window.force_close()
            self.window = None
        self.tray.hide()
        if self._ipc_server.isListening():
            self._ipc_server.close()
            QLocalServer.removeServer(_TRAY_IPC_NAME)
        self.app.quit()

    def run(self) -> int:
        # Engine start is owned by main.py; do not auto-start here (avoids dual engines).
        # Present the dashboard once the event loop is live — Windows often ignores
        # activateWindow() calls made before QApplication.exec().
        if not _wants_minimized_start() or not QSystemTrayIcon.isSystemTrayAvailable():
            QTimer.singleShot(0, self.open_dashboard)
            QTimer.singleShot(250, self.open_dashboard)
        logger.info("Entering Qt event loop (QApplication.exec)")
        startup_log.info("Starting event loop")
        code = self.app.exec()
        logger.info("Qt event loop exited code=%s", code)
        startup_log.info("Event loop exited code=%s", code)
        return code
