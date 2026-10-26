"""Enterprise operations shell for the Brixigo Connector Agent."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, QObject, Slot
from PySide6.QtGui import QCloseEvent, QColor, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    DEFAULT_DASHBOARD_REFRESH_MS,
    DEFAULT_SYNC_WATCHDOG_MS,
)
from services.connector_engine import ConnectorEngine
from ui.attendance_views import AttendanceHub, PunchDetailsDialog
from ui.dashboard_page import DashboardPage
from ui.device_details_dialog import DeviceDetailsDialog
from ui.device_dialog import DeviceDialog
from ui.device_views import DeviceFleetPage
from ui.diagnostics_dialog import DiagnosticsDialog
from ui.discovery_dialog import DiscoveryDialog
from ui.enterprise_dialog import EnterpriseDialog
from ui.log_viewer_dialog import LogViewerDialog
from ui.operation_dialogs import NotificationDrawer, RestartProgressDialog, SyncProgressDialog
from ui.settings_dialog import SettingsDialog
from ui.styles import APP_STYLESHEET
from ui.table_utils import configure_data_table, format_datetime_display
from ui.widgets import LivePulse, NavRail

logger = logging.getLogger("brixigo.ui")

PAGE_TITLES = {
    "dashboard": ("Connector", "Biometric synchronization & device operations"),
    "connector": ("Connector", "Biometric synchronization & device operations"),
    "devices": ("Devices", "Biometric terminal fleet"),
    "attendance": ("Attendance", "Auto-fetches punches every 1 minute. Sync now is optional."),
    "alerts": ("Alerts", "Operational issues requiring attention"),
    "activity": ("Activity", "Recent connector events"),
    "diagnostics": ("Diagnostics", "Technical connection and protocol checks"),
    "enterprise": ("Enterprise", "Organisation and branch operations"),
    "settings": ("Settings", "Connector configuration"),
}


class _FnWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.finished.emit(self._fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    dashboard_hidden = Signal()
    dashboard_closed = Signal()
    _sync_finished = Signal(object)
    _sync_failed = Signal(str)
    _dash_ready = Signal(object)
    _dash_failed = Signal(str)

    def __init__(self, engine: ConnectorEngine, *, owns_engine: bool = True):
        super().__init__()
        logger.info("MainWindow.__init__ starting")
        self.engine = engine
        self.owns_engine = owns_engine
        self._force_close = False
        self._sync_busy = False
        self._test_busy = False
        self._clock_sync_busy = False
        self._paint_busy = False
        self._paint_queued = False
        self._paint_silent = True
        self._sync_progress = None
        self._current_nav = "connector"
        self._auto_refresh = True
        self.setWindowTitle("Brixigo Connector Agent")
        self.resize(1280, 840)
        self.setMinimumSize(960, 640)
        self.setStyleSheet(APP_STYLESHEET)

        root = QWidget()
        root.setObjectName("AppShell")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.nav = NavRail()
        self.nav.navigated.connect(self._on_nav)
        self.nav.select("connector")
        shell.addWidget(self.nav)

        main = QWidget()
        main_l = QVBoxLayout(main)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.setSpacing(0)

        top = QFrame()
        top.setObjectName("TopBar")
        top_l = QVBoxLayout(top)
        top_l.setContentsMargins(20, 12, 20, 12)
        top_l.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.page_title = QLabel("Connector")
        self.page_title.setObjectName("PageTitle")
        self.page_title.setMinimumWidth(160)
        self.page_title.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        self.page_status = QLabel("Biometric synchronization & device operations")
        self.page_status.setObjectName("PageSubtitle")
        self.page_status.setWordWrap(True)
        self.page_status.setMinimumWidth(200)
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_status)
        row1.addLayout(titles, 1)

        self.env_badge = QLabel("PRODUCTION")
        self.env_badge.setObjectName("EnvBadge")
        self.env_badge.setVisible(False)
        self.btn_notify = QPushButton("Alerts")
        self.btn_notify.setObjectName("IconButton")
        self.btn_notify.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_notify.setAccessibleName("Notifications")
        self.btn_notify.clicked.connect(self._open_notifications)
        self.btn_help = QPushButton("Help")
        self.btn_help.setObjectName("IconButton")
        self.btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_help.clicked.connect(self._open_help)
        self.avatar = QLabel("AD")
        self.avatar.setObjectName("Avatar")
        self.avatar.setFixedSize(28, 28)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setToolTip("Administrator")
        self.btn_more = QPushButton("More")
        self.btn_more.setObjectName("IconButton")
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.clicked.connect(self._open_more)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        for w in (self.env_badge, self.btn_notify, self.btn_help, self.avatar, self.btn_more):
            tools.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        row1.addLayout(tools, 0)

        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.setObjectName("HeaderPrimary")
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync.setToolTip(
            "Optional immediate pull. The engine already fetches punches every "
            "1 minute while it is running."
        )
        self.btn_sync.clicked.connect(self._sync_now)
        self.btn_reconnect = QPushButton("Restart connections")
        self.btn_reconnect.setObjectName("Secondary")
        self.btn_reconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reconnect.setToolTip(
            "Check cloud + every device, clear offline backoff, and restart "
            "all connector connection workers."
        )
        self.btn_reconnect.clicked.connect(lambda: self._restart_all_connections(silent=False))
        self.btn_discover = QPushButton("Discover devices")
        self.btn_discover.setObjectName("Secondary")
        self.btn_discover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_discover.clicked.connect(self._open_discovery)
        live_host = QWidget()
        live_l = QHBoxLayout(live_host)
        live_l.setContentsMargins(0, 0, 0, 0)
        live_l.setSpacing(6)
        self.live_pulse = LivePulse()
        self.live_badge = QLabel("LIVE")
        self.live_badge.setObjectName("LiveBadge")
        live_l.addWidget(self.live_pulse)
        live_l.addWidget(self.live_badge)
        self.btn_refresh = QPushButton("Auto-refresh on")
        self.btn_refresh.setObjectName("LiveToggle")
        self.btn_refresh.setCheckable(True)
        self.btn_refresh.setChecked(True)
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._toggle_refresh)
        self.updated_label = QLabel("Last updated: —")
        self.updated_label.setObjectName("Muted")
        self.updated_label.setMinimumWidth(280)
        self.updated_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        for w in (self.btn_sync, self.btn_reconnect, self.btn_discover, live_host, self.btn_refresh):
            row2.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        row2.addStretch(1)
        row2.addWidget(self.updated_label, 0, Qt.AlignmentFlag.AlignVCenter)

        top_l.addLayout(row1)
        top_l.addLayout(row2)
        main_l.addWidget(top)

        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(20, 16, 20, 16)
        self.stack = QStackedWidget()
        body_l.addWidget(self.stack, 1)
        main_l.addWidget(body, 1)
        shell.addWidget(main, 1)

        self.dashboard_page = DashboardPage()
        self.dashboard_page.view_alerts.connect(lambda: self._goto("alerts"))
        self.dashboard_page.view_devices.connect(lambda: self._goto("devices"))
        self.dashboard_page.view_attendance.connect(lambda: self._goto("attendance"))
        self.dashboard_page.view_queue.connect(self._goto_queue)
        self.dashboard_page.view_diagnostics.connect(lambda: self._goto("diagnostics"))
        self.dashboard_page.view_activity.connect(lambda: self._goto("activity"))
        self.dashboard_page.sync_requested.connect(self._sync_now)
        self.dashboard_page.discover_requested.connect(self._open_discovery)
        self.dashboard_page.sync_time_requested.connect(self._sync_device_time)
        self.dashboard_page.device_clicked.connect(self._device_details)
        self.dashboard_page.device_sync_requested.connect(lambda did: self._start_sync([did]))
        self.dashboard_page.retry_requested.connect(self.refresh)
        self.stack.addWidget(self.dashboard_page)

        self.devices_page = DeviceFleetPage()
        self.devices_page.add_requested.connect(self._add_device)
        self.devices_page.details_requested.connect(self._device_details)
        self.devices_page.sync_requested.connect(lambda did: self._start_sync([did]))
        self.devices_page.edit_requested.connect(self._edit_device)
        self.devices_page.delete_requested.connect(self._delete_device)
        self.devices_page.enable_requested.connect(self._set_enabled)
        self.devices_page.test_requested.connect(self._test_device)
        self.devices_page.filter_changed.connect(self.refresh)
        self.stack.addWidget(self.devices_page)

        self.attendance_hub = AttendanceHub()
        self.attendance_hub.live_page.details_requested.connect(self._open_live_punch)
        self.attendance_hub.history_page.refresh_requested.connect(self._load_history)
        self.attendance_hub.history_page.sync_requested.connect(self._sync_now)
        self.attendance_hub.daily_page.refresh_requested.connect(self._load_daily)
        self.attendance_hub.monthly_page.refresh_requested.connect(self._load_monthly)
        self.attendance_hub.queue_page.refresh_requested.connect(self._load_queue)
        self.attendance_hub.tabs.currentChanged.connect(self._on_attendance_tab)
        self.stack.addWidget(self.attendance_hub)

        alerts_page = QWidget()
        al = QVBoxLayout(alerts_page)
        al.setContentsMargins(0, 0, 0, 0)
        al_btns = QHBoxLayout()
        self.btn_ack = QPushButton("Acknowledge")
        self.btn_ack.setObjectName("Secondary")
        self.btn_ack.clicked.connect(self._ack_alert)
        al_btns.addWidget(self.btn_ack)
        al_btns.addStretch()
        al.addLayout(al_btns)
        self.alert_table = QTableWidget(0, 7)
        self.alert_table.setHorizontalHeaderLabels(
            ["Time", "Severity", "Type", "Title", "Message", "Status", "ID"]
        )
        configure_data_table(self.alert_table, min_height=280)
        self.alert_table.setColumnHidden(6, True)
        al.addWidget(self.alert_table, 1)
        self.stack.addWidget(alerts_page)

        activity_page = QWidget()
        act_l = QVBoxLayout(activity_page)
        act_l.setContentsMargins(0, 0, 0, 0)
        self.activity_table = QTableWidget(0, 5)
        self.activity_table.setHorizontalHeaderLabels(["Time", "Action", "Success", "Records", "Message"])
        configure_data_table(self.activity_table, min_height=280)
        act_l.addWidget(self.activity_table, 1)
        self.stack.addWidget(activity_page)

        diag_page = QWidget()
        diag_l = QVBoxLayout(diag_page)
        diag_l.setContentsMargins(0, 0, 0, 0)
        diag_btns = QHBoxLayout()
        self.btn_full_diag = QPushButton("Run diagnostics")
        self.btn_full_diag.clicked.connect(self._open_diagnostics)
        self.btn_logs = QPushButton("Log viewer")
        self.btn_logs.setObjectName("Secondary")
        self.btn_logs.clicked.connect(self._open_logs_viewer)
        diag_btns.addWidget(self.btn_full_diag)
        diag_btns.addWidget(self.btn_logs)
        diag_btns.addStretch()
        diag_l.addLayout(diag_btns)
        hint = QLabel("Technical diagnostics are separated from daily operations.")
        hint.setObjectName("Muted")
        diag_l.addWidget(hint)
        self.diag_panel = QPlainTextEdit()
        self.diag_panel.setObjectName("DiagnosticsPanel")
        self.diag_panel.setReadOnly(True)
        diag_l.addWidget(self.diag_panel, 1)
        self.stack.addWidget(diag_page)

        self._pages = {
            "dashboard": self.dashboard_page,
            "connector": self.dashboard_page,
            "devices": self.devices_page,
            "attendance": self.attendance_hub,
            "alerts": alerts_page,
            "activity": activity_page,
            "diagnostics": diag_page,
        }
        self._notify_drawer = NotificationDrawer(self)
        self._notify_drawer.view_all.connect(lambda: self._goto("alerts"))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._safe_refresh)
        self._timer.start(DEFAULT_DASHBOARD_REFRESH_MS)
        self._sync_watchdog = QTimer(self)
        self._sync_watchdog.setSingleShot(True)
        self._sync_watchdog.timeout.connect(self._on_sync_watchdog)
        self._sync_finished.connect(self._on_sync_done, Qt.ConnectionType.QueuedConnection)
        self._sync_failed.connect(self._on_sync_fail, Qt.ConnectionType.QueuedConnection)
        self._dash_ready.connect(self._on_dash_ready, Qt.ConnectionType.QueuedConnection)
        self._dash_failed.connect(self._on_dash_failed, Qt.ConnectionType.QueuedConnection)
        try:
            self._request_paint(silent=True)
        except Exception as exc:
            logger.exception("Initial dashboard refresh failed")
            self.dashboard_page.show_error(str(exc))
            QMessageBox.warning(
                self,
                "Dashboard warning",
                "The dashboard opened, but live status could not be loaded.\n\n"
                f"{exc}\n\nThe window will keep retrying.",
            )
        logger.info("MainWindow.__init__ complete")

    def _goto(self, key: str) -> None:
        self.nav.select(key)
        self._on_nav(key)

    def _goto_queue(self) -> None:
        self._goto("attendance")
        self.attendance_hub.show_tab("queue")
        self._load_queue()

    def _on_nav(self, key: str | None = None) -> None:
        key = key or self.nav.current_key()
        self._current_nav = key
        title, subtitle = PAGE_TITLES.get(key, ("Connector", ""))
        self.page_title.setText(title)
        self.page_title.setToolTip(title)
        if key == "enterprise":
            self._open_enterprise()
            self.nav.select("connector")
            self._on_nav("connector")
            return
        if key == "settings":
            self._open_settings()
            self.nav.select("connector")
            self._on_nav("connector")
            return
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)
        if key == "devices":
            self.devices_page.set_mode("all")
        if key == "attendance":
            self._on_attendance_tab(self.attendance_hub.tabs.currentIndex())
        else:
            self._request_paint(silent=True)
        if key != "dashboard" and key != "connector":
            self.page_title.setText(title)
            self.page_title.setToolTip(title)
            self.page_status.setText(subtitle)
            self.page_status.setToolTip(subtitle)

    def _on_attendance_tab(self, index: int) -> None:
        self._request_paint(silent=True)

    def _toggle_refresh(self) -> None:
        self._auto_refresh = self.btn_refresh.isChecked()
        self.btn_refresh.setText("Auto-refresh on" if self._auto_refresh else "Auto-refresh off")
        if self._auto_refresh:
            self.live_badge.setText("LIVE")
            self.live_badge.setObjectName("LiveBadge")
            self.live_pulse.set_live(True)
            self._timer.start(DEFAULT_DASHBOARD_REFRESH_MS)
        else:
            self.live_badge.setText("PAUSED")
            self.live_badge.setObjectName("LiveBadgeOff")
            self.live_pulse.set_live(False)
            self._timer.stop()
        self.live_badge.style().unpolish(self.live_badge)
        self.live_badge.style().polish(self.live_badge)

    def _open_notifications(self) -> None:
        self._notify_drawer.set_alerts((self.engine.dashboard() or {}).get("alerts") or [])
        self._notify_drawer.exec()

    def _open_more(self) -> None:
        menu = QMenu(self)
        menu.addAction("Discover devices", self._open_discovery)
        menu.addAction("Run diagnostics", self._open_diagnostics)
        menu.addAction("Log viewer", self._open_logs_viewer)
        menu.addSeparator()
        admin = menu.addMenu("Administrative actions")
        restart = admin.addAction("Restart connector engine…")
        restart.triggered.connect(self._restart)
        try:
            admin_ok = self.engine.is_admin()
        except Exception:
            admin_ok = True
        restart.setEnabled(admin_ok)
        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    def _open_help(self) -> None:
        QMessageBox.information(
            self,
            "Connector operations",
            "This is the command center for biometric infrastructure.\n\n"
            "• Connector health shows whether the engine is running\n"
            "• Punches are fetched from devices automatically every 1 minute\n"
            "  (Sync now is only for an immediate extra pull)\n"
            "• The connectivity flow shows devices → connector → cloud\n"
            "• Clock mismatch means a terminal date is wrong — sync device time\n"
            "• Raw vendor payloads are in punch View details\n"
            "• Restart engine is under More → Administrative actions\n\n"
            "Support: see the Admin Guide in the connector docs folder.",
        )

    def _safe_refresh(self) -> None:
        if not self._auto_refresh:
            return
        self._request_paint(silent=True)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        logging.getLogger("brixigo.startup").info(
            "MainWindow.showEvent visible=%s geometry=%s", self.isVisible(), self.geometry()
        )

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_close:
            self.dashboard_closed.emit()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.dashboard_hidden.emit()

    def force_close(self) -> None:
        self._force_close = True
        self.close()

    def save_window_geometry(self) -> bytes:
        return bytes(self.saveGeometry())

    def restore_window_geometry(self, data: bytes) -> bool:
        return bool(self.restoreGeometry(data))

    def refresh(self) -> None:
        self._request_paint(silent=False)

    def _request_paint(self, *, silent: bool = True) -> None:
        if self._paint_busy:
            self._paint_queued = True
            self._paint_silent = bool(self._paint_silent and silent)
            return
        self._paint_busy = True
        self._paint_silent = silent
        snapshot = self._ui_snapshot()
        worker = _FnWorker(lambda snap=snapshot: self._collect_ui_payload(snap))
        worker.setParent(self)
        self._paint_worker = worker
        worker.finished.connect(self._dash_ready, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._dash_failed, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=worker.run, name="brixigo-ui-paint", daemon=True).start()

    def _ui_snapshot(self) -> dict:
        nav = self._current_nav
        tab = self.attendance_hub.tabs.currentIndex() if nav == "attendance" else None
        snapshot: dict = {"nav": nav, "tab": tab}
        if tab == AttendanceHub.TAB_HISTORY:
            history = self.attendance_hub.history_page
            history.prepare_dates()
            snapshot["history_filters"] = history.filters()
        elif tab == AttendanceHub.TAB_DAILY:
            snapshot["day"] = self.attendance_hub.daily_page.prepare_day()
        elif tab == AttendanceHub.TAB_MONTHLY:
            snapshot["period"] = self.attendance_hub.monthly_page.selected_period()
        elif tab == AttendanceHub.TAB_QUEUE:
            snapshot["queue_status"] = self.attendance_hub.queue_page.selected_status()
        return snapshot

    def _collect_ui_payload(self, snapshot: dict) -> dict:
        dash = self.engine.dashboard()
        devices = self.engine.device_manager.list_devices()
        attendance = None
        if snapshot.get("nav") == "attendance":
            attendance = self._collect_attendance(snapshot)
        return {"dash": dash, "devices": devices, "attendance": attendance, "snapshot": snapshot}

    def _collect_attendance(self, snapshot: dict) -> dict:
        tab = snapshot.get("tab")
        if tab == AttendanceHub.TAB_HISTORY:
            return {
                "kind": "history",
                "result": self.engine.list_attendance(**(snapshot.get("history_filters") or {})),
            }
        if tab == AttendanceHub.TAB_DAILY:
            return {"kind": "daily", "rows": self.engine.daily_attendance(snapshot.get("day") or "")}
        if tab == AttendanceHub.TAB_MONTHLY:
            year, month = snapshot.get("period") or (0, 0)
            return {"kind": "monthly", "report": self.engine.monthly_attendance(int(year), int(month))}
        if tab == AttendanceHub.TAB_QUEUE:
            return {
                "kind": "queue",
                "rows": self.engine.list_upload_queue(status=snapshot.get("queue_status") or ""),
            }
        live = self.engine.live_punches()
        return {"kind": "live", "live": live}

    @Slot(object)
    def _on_dash_ready(self, payload: object) -> None:
        self._paint_busy = False
        data = payload if isinstance(payload, dict) else {}
        try:
            self._apply_collected(data)
        except Exception:
            logger.exception("Applying dashboard payload failed")
        if self._paint_queued:
            self._paint_queued = False
            self._request_paint(silent=self._paint_silent)

    @Slot(str)
    def _on_dash_failed(self, message: str) -> None:
        self._paint_busy = False
        logger.error("Dashboard refresh failed: %s", message)
        if not self._paint_silent:
            self.dashboard_page.show_error(message)
            QMessageBox.warning(self, "Dashboard warning", message)
        if self._paint_queued:
            self._paint_queued = False
            self._request_paint(silent=True)

    def _apply_collected(self, payload: dict) -> None:
        dash = payload.get("dash") or self.engine.dashboard()
        devices = payload.get("devices") if payload.get("devices") is not None else self.engine.device_manager.list_devices()
        now = datetime.now().strftime("%H:%M:%S")
        self.updated_label.setText(f"Last updated: {now}")
        tcp_open = int(dash.get("tcp_open_devices") or 0)
        connected = int(dash.get("connected_devices") or 0)
        connecting = int(dash.get("connecting_devices") or 0)
        offline = int(dash.get("offline_devices") or 0)
        net = str(dash.get("device_network_status") or "").lower()
        tcp_ok = tcp_open > 0 or connected > 0 or net in {"connected", "online"}
        # Live retry / soft probing must never show TCP DOWN — that badge was
        # the main operator disturbance while heartbeat still owned recovery.
        tcp_retrying = (not tcp_ok) and (
            connecting > 0 or net in {"probing", "unknown", "connecting"}
        )
        tcp_confirmed_down = (not tcp_ok) and (not tcp_retrying) and offline > 0
        if self._auto_refresh:
            if tcp_ok:
                self.live_badge.setText("TCP OK")
                self.live_badge.setObjectName("LiveBadge")
                self.live_pulse.set_live(True)
            elif tcp_retrying:
                self.live_badge.setText("TCP …")
                self.live_badge.setObjectName("LiveBadge")
                self.live_pulse.set_live(True)
            elif tcp_confirmed_down:
                self.live_badge.setText("TCP DOWN")
                self.live_badge.setObjectName("LiveBadgeOff")
                self.live_pulse.set_live(False)
            else:
                self.live_badge.setText("TCP …")
                self.live_badge.setObjectName("LiveBadge")
                self.live_pulse.set_live(True)
            self.live_badge.style().unpolish(self.live_badge)
            self.live_badge.style().polish(self.live_badge)
        snapshots = dash.get("device_snapshots") or []
        primary = snapshots[0] if snapshots else {}
        ip = str(primary.get("ip_address") or "")
        port = int(primary.get("port") or 4370)
        if ip:
            if tcp_ok:
                tcp_line = f"TCP {ip}:{port} connected"
            elif tcp_retrying or not tcp_confirmed_down:
                tcp_line = f"TCP {ip}:{port} reconnecting"
            else:
                tcp_line = f"TCP {ip}:{port} unreachable"
            self.updated_label.setText(f"Last updated: {now}  ·  {tcp_line}")
        headline = dash.get("status_headline") or "Connector running"
        if self._current_nav in {"dashboard", "connector"}:
            self.page_status.setText(headline)
            self.page_status.setToolTip(headline)
        self.dashboard_page.apply(dash)
        self.nav.set_runtime(running=str(dash.get("connector_status") or "") == "running", role=str(dash.get("role") or "admin"))
        open_alerts = sum(1 for a in (dash.get("alerts") or []) if not a.get("acknowledged"))
        self.btn_notify.setText(f"Alerts{f' ({open_alerts})' if open_alerts else ''}")
        self._notify_drawer.set_alerts(dash.get("alerts") or [])
        cloud_url = str(dash.get("cloud_base_url") or "")
        prod = "brixigo.com" in cloud_url.lower() and "localhost" not in cloud_url.lower()
        self.env_badge.setVisible(prod)
        role = str(dash.get("role") or "admin")
        self.avatar.setText((role[:2] or "AD").upper())
        self.avatar.setToolTip(role.title())
        self.devices_page.set_devices(devices, dash=dash, mode="all")
        punches = dash.get("punch_history") or []
        today_n = int(dash.get("today_punches") or 0)
        self.attendance_hub.live_page.set_punches(punches, today=today_n)
        self.attendance_hub.history_page.set_lookups(devices, dash.get("branches") or [])
        attendance = payload.get("attendance")
        if payload.get("snapshot", {}).get("nav") == "attendance" and attendance:
            self._apply_attendance(attendance)
        alerts = dash.get("alerts") or []
        self.alert_table.setRowCount(len(alerts))
        for row, alert in enumerate(alerts):
            vals = [
                format_datetime_display(alert.get("created_at") or ""),
                str(alert.get("severity") or ""),
                str(alert.get("alert_type") or ""),
                str(alert.get("title") or ""),
                str(alert.get("message") or ""),
                "Acknowledged" if alert.get("acknowledged") else "Open",
                str(alert.get("id") or ""),
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                if col == 1 and str(val).lower() == "critical":
                    item.setForeground(QColor("#b42318"))
                self.alert_table.setItem(row, col, item)
        activity = dash.get("recent_activity") or []
        self.activity_table.setRowCount(len(activity))
        for row, rec in enumerate(activity):
            vals = [
                format_datetime_display(rec.get("created_at") or ""),
                str(rec.get("action") or ""),
                "Yes" if rec.get("success") else "No",
                str(rec.get("records_count") or 0),
                str(rec.get("message") or ""),
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                self.activity_table.setItem(row, col, item)
        lines = dash.get("diagnostics_lines") or []
        self.diag_panel.setPlainText(
            "\n".join(lines)
            if lines
            else "No diagnostics yet. Run the full suite for DNS, TCP, TLS, and API checks."
        )

    def _apply_attendance(self, attendance: dict) -> None:
        kind = attendance.get("kind")
        if kind == "history":
            self.attendance_hub.history_page.set_result(attendance.get("result") or {})
        elif kind == "daily":
            self.attendance_hub.daily_page.set_rows(attendance.get("rows") or [])
        elif kind == "monthly":
            self.attendance_hub.monthly_page.set_report(attendance.get("report") or {})
        elif kind == "queue":
            self.attendance_hub.queue_page.set_rows(attendance.get("rows") or [])
        elif kind == "live":
            live = attendance.get("live") or {}
            self.attendance_hub.live_page.set_punches(
                live.get("punches") or [], today=int(live.get("today") or 0)
            )

    def _load_history(self) -> None:
        self._request_paint(silent=False)

    def _load_daily(self) -> None:
        self._request_paint(silent=False)

    def _load_monthly(self) -> None:
        self._request_paint(silent=False)

    def _load_queue(self) -> None:
        self._request_paint(silent=False)

    def _refresh_attendance_view(self, tab: int | None = None, *, silent: bool = True) -> None:
        self._request_paint(silent=silent)

    def _open_live_punch(self) -> None:
        punch = self.attendance_hub.live_page.selected_punch()
        if not punch:
            QMessageBox.information(self, "Select a punch", "Select a row to view employee and technical details.")
            return
        PunchDetailsDialog(punch, self).exec()

    def _sync_device_time(self, device_id: str) -> None:
        if not self.engine.is_admin():
            QMessageBox.warning(self, "Not permitted", "This action requires an administrator role.")
            return
        if self._clock_sync_busy:
            return
        if not device_id:
            QMessageBox.information(self, "No device", "Select a terminal before syncing its clock.")
            return
        self._clock_sync_busy = True
        self.dashboard_page.clock.btn_action.setEnabled(False)
        self.dashboard_page.clock.btn_action.setText("Syncing…")
        worker = _FnWorker(lambda: self.engine.sync_device_time(device_id))
        worker.setParent(self)

        def _done(result: object) -> None:
            self._clock_sync_busy = False
            self.dashboard_page.clock.btn_action.setEnabled(True)
            self.dashboard_page.clock.btn_action.setText("Sync device time")
            payload = result if isinstance(result, dict) else {}
            self.refresh()
            if payload.get("success"):
                QMessageBox.information(
                    self,
                    "Device time synced",
                    str(payload.get("message") or "Terminal clock now matches this PC."),
                )
            else:
                QMessageBox.warning(
                    self,
                    "Time sync failed",
                    str(payload.get("message") or "Unable to sync device time."),
                )

        def _fail(message: str) -> None:
            self._clock_sync_busy = False
            self.dashboard_page.clock.btn_action.setEnabled(True)
            self.dashboard_page.clock.btn_action.setText("Sync device time")
            QMessageBox.critical(self, "Time sync failed", message)
            self.refresh()

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        threading.Thread(target=worker.run, name="brixigo-ui-clock-sync", daemon=True).start()

    def _confirm_admin(self, title: str, body: str) -> bool:
        if not self.engine.is_admin():
            QMessageBox.warning(self, "Not permitted", "This action requires an administrator role.")
            return False
        return (
            QMessageBox.question(self, title, body)
            == QMessageBox.StandardButton.Yes
        )

    def _add_device(self) -> None:
        dlg = DeviceDialog(self)
        if dlg.exec():
            try:
                self.engine.device_manager.add_device(**dlg.values())
                self.refresh()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def _edit_device(self, device_id: str | None = None) -> None:
        device_id = device_id or self.devices_page.selected_id()
        if not device_id:
            QMessageBox.information(self, "Select device", "Please select a device first.")
            return
        device = self.engine.device_manager.get(device_id)
        if not device:
            return
        dlg = DeviceDialog(self, device)
        if dlg.exec():
            try:
                self.engine.device_manager.update_device(device_id, **dlg.values())
                self.refresh()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def _device_details(self, device_id: str | None = None) -> None:
        device_id = device_id or self.devices_page.selected_id()
        if not device_id:
            QMessageBox.information(self, "Select device", "Please select a device first.")
            return
        device = self.engine.device_manager.get(device_id)
        if not device:
            return
        DeviceDetailsDialog(self.engine, device, self).exec()

    def _delete_device(self, device_id: str | None = None) -> None:
        device_id = device_id or self.devices_page.selected_id()
        if not device_id:
            return
        if not self._confirm_admin(
            "Delete device",
            "Remove this device from the connector?\n\nStored punches are kept. This cannot be undone from this screen.",
        ):
            return
        self.engine.device_manager.delete_device(device_id)
        self.refresh()

    def _set_enabled(self, device_id: str, enabled: bool) -> None:
        if enabled:
            self.engine.device_manager.enable_device(device_id)
        else:
            self.engine.device_manager.disable_device(device_id)
        self.refresh()

    def _test_device(self, device_id: str | None = None) -> None:
        device_id = device_id or self.devices_page.selected_id()
        if not device_id:
            QMessageBox.information(self, "Select device", "Please select a device first.")
            return
        if self._test_busy:
            QMessageBox.information(self, "Busy", "A connection test is already running.")
            return
        self._test_busy = True
        worker = _FnWorker(lambda: self.engine.device_manager.reconnect(device_id))
        worker.setParent(self)
        self._test_worker = worker

        def _done(result: object) -> None:
            self._test_busy = False
            data = result if isinstance(result, dict) else {}
            msg = str(data.get("message") or "")
            if data.get("protocol_ok") or data.get("online"):
                QMessageBox.information(self, "Connection OK", f"Device online.\n{msg}")
            elif data.get("network_ok"):
                QMessageBox.warning(
                    self,
                    "Unable to complete device handshake",
                    f"The terminal is reachable, but the biometric protocol did not complete.\n\n{msg}\n\n"
                    "Possible causes: device busy, wrong communication key, or firmware timeout.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Unable to connect",
                    f"{msg or 'The device did not accept a TCP connection.'}\n\n"
                    "Possible causes: device offline, port closed, or local network unavailable.",
                )
            self.refresh()

        def _fail(message: str) -> None:
            self._test_busy = False
            QMessageBox.warning(self, "Connection failed", message)
            self.refresh()

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        threading.Thread(target=worker.run, name="brixigo-ui-test", daemon=True).start()

    def _restart_all_connections(self, *, silent: bool = False) -> None:
        """Check cloud + devices and restart all connection workers."""
        if getattr(self, "_reconnect_busy", False):
            if not silent:
                QMessageBox.information(self, "Busy", "A connection restart is already running.")
            return
        if not silent:
            pending = 0
            try:
                pending = int((self.engine.dashboard() or {}).get("queue_count") or 0)
            except Exception:
                pending = 0
            answer = QMessageBox.question(
                self,
                "Restart all connections",
                "Restart cloud and device connections?\n\n"
                "This re-checks every terminal and the Brixigo Cloud path.\n"
                f"Current queue: {pending} pending (not deleted).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._reconnect_busy = True
        self.btn_reconnect.setEnabled(False)
        self.btn_reconnect.setText("Restarting…")
        worker = _FnWorker(self.engine.check_and_restart_connections)
        worker.setParent(self)
        self._reconnect_worker = worker

        def _done(report: object) -> None:
            self._reconnect_busy = False
            self.btn_reconnect.setEnabled(True)
            self.btn_reconnect.setText("Restart connections")
            payload = report if isinstance(report, dict) else {}
            msg = str(payload.get("message") or "Connection restart finished.")
            if not silent:
                if payload.get("ok"):
                    QMessageBox.information(self, "Connections restarted", msg)
                else:
                    QMessageBox.warning(self, "Connection restart", msg)
            self.refresh()

        def _fail(message: str) -> None:
            self._reconnect_busy = False
            self.btn_reconnect.setEnabled(True)
            self.btn_reconnect.setText("Restart connections")
            logger.error("Connection restart failed: %s", message)
            if not silent:
                QMessageBox.warning(self, "Connection restart failed", message)
            self.refresh()

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        threading.Thread(target=worker.run, name="brixigo-ui-reconnect", daemon=True).start()

    def _sync_now(self) -> None:
        self._start_sync(None)

    def _set_sync_buttons(self, *, busy: bool) -> None:
        text = "Syncing…" if busy else "Sync now"
        for btn in (
            self.dashboard_page.hero.btn_sync,
            self.dashboard_page.actions.btn_sync,
            self.btn_sync,
            self.attendance_hub.history_page.btn_sync,
        ):
            btn.setEnabled(not busy)
            btn.setText(text)

    def _start_sync(self, device_ids: list[str] | None) -> None:
        if self._sync_busy:
            QMessageBox.information(self, "Busy", "A sync is already running.")
            return
        self._sync_busy = True
        self._set_sync_buttons(busy=True)
        progress = SyncProgressDialog(self)
        progress.setModal(False)
        self._sync_progress = progress
        progress.show()
        self._sync_watchdog.start(DEFAULT_SYNC_WATCHDOG_MS)
        worker = _FnWorker(lambda ids=list(device_ids or []): self.engine.sync_now(ids or None))
        worker.setParent(self)
        self._sync_worker = worker
        worker.finished.connect(self._sync_finished, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._sync_failed, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=worker.run, name="brixigo-ui-sync", daemon=True).start()

    @Slot(object)
    def _on_sync_done(self, results: object) -> None:
        self._sync_watchdog.stop()
        self._sync_busy = False
        self._set_sync_buttons(busy=False)
        rows = results if isinstance(results, list) else []
        progress = self._sync_progress
        if progress is not None:
            try:
                progress.mark_complete(rows)
            except RuntimeError:
                pass
        self._request_paint(silent=True)

    @Slot(str)
    def _on_sync_fail(self, message: str) -> None:
        self._sync_watchdog.stop()
        self._sync_busy = False
        self._set_sync_buttons(busy=False)
        logger.error("Sync Now failed: %s", message)
        progress = self._sync_progress
        if progress is not None:
            try:
                progress.mark_failed(message)
            except RuntimeError:
                pass

    def _on_sync_watchdog(self) -> None:
        if not self._sync_busy:
            return
        self._sync_busy = False
        self._set_sync_buttons(busy=False)
        progress = self._sync_progress
        if progress is not None:
            try:
                progress.body.setText(
                    "Still collecting from the terminal. Auto-sync continues every "
                    "1 minute and this page will update when punches arrive."
                )
                progress.btn.setText("Hide")
            except RuntimeError:
                pass
        self._request_paint(silent=True)

    def _open_discovery(self) -> None:
        DiscoveryDialog(self.engine, self).exec()
        self.refresh()

    def _open_enterprise(self) -> None:
        EnterpriseDialog(self.engine, self).exec()
        self.refresh()

    def _open_diagnostics(self) -> None:
        DiagnosticsDialog(self.engine, self).exec()

    def _open_logs_viewer(self) -> None:
        LogViewerDialog(self.engine, self).exec()

    def _ack_alert(self) -> None:
        rows = self.alert_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.alert_table.item(rows[0].row(), 6)
        if not item:
            return
        self.engine.alerts.acknowledge(item.text())
        self.refresh()

    def _open_settings(self) -> None:
        if not self.engine.is_admin():
            QMessageBox.warning(self, "Not permitted", "Settings can be changed only by an administrator.")
            return
        current = self.engine.container.settings.as_model()
        dlg = SettingsDialog(self, current)
        if dlg.exec():
            self.engine.container.settings.update(**dlg.values())
            self.engine.reload_settings()
            QMessageBox.information(self, "Saved", "Settings saved and applied.")
            self.refresh()

    def _restart(self) -> None:
        pending = 0
        try:
            pending = int((self.engine.dashboard() or {}).get("queue_count") or 0)
        except Exception:
            pending = 0
        if not self._confirm_admin(
            "Restart connector engine",
            "Restart Connector Engine?\n\n"
            "The connector may temporarily stop device synchronization.\n"
            f"Current queue: {pending} pending.\n\n"
            "Queued punches are not deleted.",
        ):
            return
        progress = RestartProgressDialog(self)

        def _work():
            if not self.owns_engine:
                if sys.platform.startswith("win"):
                    subprocess.run(["sc", "stop", "BrixigoConnector"], capture_output=True, text=True, check=False)
                    subprocess.run(["sc", "start", "BrixigoConnector"], capture_output=True, text=True, check=False)
                return "service"
            self.engine.restart()
            return "engine"

        worker = _FnWorker(_work)
        worker.setParent(self)
        self._restart_worker = worker

        def _done(kind: object) -> None:
            if kind == "service":
                progress.mark_complete(
                    "The Windows service owns device connectivity. A service restart was requested."
                )
            else:
                progress.mark_complete("Connector engine restarted. Queued punches were preserved.")
            self.refresh()

        def _fail(message: str) -> None:
            logger.exception("Engine restart failed: %s", message)
            progress.mark_failed(message)

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        threading.Thread(target=worker.run, name="brixigo-ui-restart", daemon=True).start()
        progress.exec()

    def open_logs(self) -> None:
        path = self.engine.container.paths.logs
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
