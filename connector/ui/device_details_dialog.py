"""Device details page — live link, inventory, punch history, connection history."""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import Device
from services.connection_state import live_display_fields
from services.connector_engine import ConnectorEngine
from ui.table_utils import (
    apply_column_widths,
    configure_data_table,
    format_datetime_display,
    format_punch_time_display,
)
from ui.widgets import ClockMismatchPanel


class DeviceDetailsDialog(QDialog):
    def __init__(self, engine: ConnectorEngine, device: Device, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.device_id = device.id
        self._connect_busy = False
        self._punches: list[dict] = []
        self.setWindowTitle(f"Device Details — {device.name}")
        self.resize(980, 860)

        layout = QVBoxLayout(self)
        self.banner = QLabel()
        self.banner.setObjectName("StatusBanner")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)

        self.clock = ClockMismatchPanel()
        self.clock.sync_clicked.connect(self._sync_time)
        self.clock.details_clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        layout.addWidget(self.clock)
        actions = QHBoxLayout()
        self.btn_connect = QPushButton("Connect now")
        self.btn_connect.clicked.connect(self._connect_now)
        self.btn_refresh = QPushButton("Refresh history")
        self.btn_refresh.setObjectName("Secondary")
        self.btn_refresh.clicked.connect(self._reload)
        self.btn_sync_time = QPushButton("Sync device time")
        self.btn_sync_time.setObjectName("Secondary")
        self.btn_sync_time.clicked.connect(self._sync_time)
        actions.addWidget(self.btn_connect)
        actions.addWidget(self.btn_refresh)
        actions.addWidget(self.btn_sync_time)
        actions.addStretch()
        layout.addLayout(actions)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        info_page = QWidget()
        info_outer = QVBoxLayout(info_page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        info_inner = QWidget()
        self.info_form = QFormLayout(info_inner)
        scroll.setWidget(info_inner)
        info_outer.addWidget(scroll)
        self.tabs.addTab(info_page, "Live device")

        punches_page = QWidget()
        p_layout = QVBoxLayout(punches_page)
        self.punch_summary = QLabel("Loading punch history…")
        p_layout.addWidget(self.punch_summary)
        self.punch_table = QTableWidget(0, 6)
        self.punch_table.setHorizontalHeaderLabels(
            ["Punch Time", "Employee", "Event", "Method", "Upload Status", "Error"]
        )
        self.punch_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        configure_data_table(self.punch_table, min_height=220, wrap=False, row_height=36)
        apply_column_widths(
            self.punch_table,
            {0: 210, 1: 180, 2: 80, 3: 150, 4: 120, 5: 240},
        )
        p_layout.addWidget(self.punch_table, 1)
        punch_actions = QHBoxLayout()
        self.btn_punch_details = QPushButton("View details")
        self.btn_punch_details.setObjectName("Secondary")
        self.btn_punch_details.clicked.connect(self._open_punch_details)
        punch_actions.addWidget(self.btn_punch_details)
        punch_actions.addStretch()
        p_layout.addLayout(punch_actions)
        self.tabs.addTab(punches_page, "Punch history")

        hist_page = QWidget()
        h_layout = QVBoxLayout(hist_page)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            ["Time", "Action", "Success", "Records", "Message"]
        )
        configure_data_table(self.history_table, min_height=160, wrap=False, row_height=36)
        apply_column_widths(self.history_table, {0: 170, 1: 90, 2: 80, 3: 80, 4: 360})
        self.health_table = QTableWidget(0, 5)
        self.health_table.setHorizontalHeaderLabels(
            ["Collected", "Online", "Ping", "Users / Logs", "Message"]
        )
        configure_data_table(self.health_table, min_height=140)
        apply_column_widths(self.health_table, {0: 170, 1: 80, 2: 90, 3: 120, 4: 320})
        hist_split = QSplitter(Qt.Orientation.Vertical)
        hist_split.setChildrenCollapsible(False)
        hist_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        hist_top = QWidget()
        hist_top_l = QVBoxLayout(hist_top)
        hist_top_l.setContentsMargins(0, 0, 0, 0)
        hist_top_l.addWidget(QLabel("Connection / sync history"))
        hist_top_l.addWidget(self.history_table, 1)
        hist_bottom = QWidget()
        hist_bottom_l = QVBoxLayout(hist_bottom)
        hist_bottom_l.setContentsMargins(0, 0, 0, 0)
        hist_bottom_l.addWidget(QLabel("Health snapshots"))
        hist_bottom_l.addWidget(self.health_table, 1)
        hist_split.addWidget(hist_top)
        hist_split.addWidget(hist_bottom)
        hist_split.setStretchFactor(0, 3)
        hist_split.setStretchFactor(1, 2)
        h_layout.addWidget(hist_split, 1)
        self.tabs.addTab(hist_page, "Device history")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._reload()

    def _device(self) -> Device | None:
        return self.engine.device_manager.get(self.device_id)

    def _reload(self) -> None:
        device = self._device()
        if not device:
            self.banner.setText("Device was removed.")
            return
        health = self.engine.container.health_repo.latest_for_device(device.id)
        link = self.engine.states.device(device.id) if getattr(self.engine, "states", None) else None
        display = live_display_fields(link)
        live = bool(display.get("live_retry"))
        status = display.get("display_status") or device.status
        network = display.get("display_network") or "—"
        protocol = display.get("display_protocol") or "—"

        if live:
            self.banner.setText(
                f"Live mission — connecting to {device.ip_address}:{device.port}. "
                "Heartbeat is retrying now. Stored punch and connection history is below."
            )
            self.banner.setStyleSheet(
                "QLabel#StatusBanner { color: #9a6700; font-weight: 600; }"
            )
        elif status == "connected" and protocol == "connected" and network == "connected":
            self.banner.setText(
                f"Live mission — online at {device.ip_address}:{device.port}."
            )
            self.banner.setStyleSheet(
                "QLabel#StatusBanner { color: #0b6e4f; font-weight: 600; }"
            )
        else:
            self.banner.setText(
                f"Device link {status} · network {network} · protocol {protocol}."
            )
            self.banner.setStyleSheet(
                "QLabel#StatusBanner { color: #b42318; font-weight: 600; }"
            )

        while self.info_form.rowCount():
            self.info_form.removeRow(0)

        rows = [
            ("Mission", "Live retry in progress" if live else ("Online" if status == "connected" else status.replace("_", " "))),
            ("Name", device.name),
            ("Manufacturer", device.manufacturer),
            ("Model", device.model or (health.details.get("model") if health else "") or "—"),
            ("Firmware", device.firmware or (health.firmware_version if health else "") or "—"),
            ("Serial Number", device.serial_number or "—"),
            ("IP Address", device.ip_address),
            ("MAC Address", device.mac_address or "—"),
            ("Port", str(device.port)),
            ("Communication", device.communication_type),
            ("Terminal Code", device.terminal_code or "—"),
            ("Status", status),
            ("Health Status", device.health_status),
            ("Device Link", status if live else ((link.status.value if link else "—"))),
            ("Device Network", network),
            ("Device Protocol", protocol),
            ("Protocol Code", (link.protocol_code if link else "—") or "—"),
            ("Failure Stage", (link.failure_stage if link else "—") or "—"),
            (
                "Last Device Heartbeat",
                (link.last_heartbeat_at if link else device.last_health_at) or "—",
            ),
            (
                "Last TCP Heartbeat",
                (link.last_tcp_heartbeat_at if link else "—") or "—",
            ),
            (
                "Last Protocol Session",
                (link.last_protocol_success_at if link else "—") or "—",
            ),
            ("Enabled", "Yes" if device.enabled else "No"),
            ("Last Sync", format_datetime_display(device.last_sync_at or "") or "—"),
            (
                "Last Attendance",
                format_punch_time_display(device.last_attendance_at or "")
                or format_datetime_display(device.last_attendance_at or "")
                or "—",
            ),
            ("Current Users", str(device.current_users)),
            ("Current Logs", str(device.current_logs)),
            ("SDK Version", device.sdk_version or "—"),
            ("Last Health", format_datetime_display(device.last_health_at or "") or "—"),
            ("Last Error", device.last_error or "—"),
        ]
        if health:
            rows.extend(
                [
                    ("Last known ping", f"{health.ping_ms:.0f} ms"),
                    ("Memory Usage", f"{health.memory_usage_percent:.1f}%"),
                    ("Storage Usage", f"{health.storage_usage_percent:.1f}%"),
                    ("User Capacity", str(health.user_capacity)),
                    ("Log Capacity", str(health.log_capacity)),
                    (
                        "Device Time",
                        format_datetime_display(health.device_time or "")
                        or health.device_time
                        or "—",
                    ),
                    (
                        "Battery",
                        "—" if health.battery_level is None else f"{health.battery_level}%",
                    ),
                    (
                        "CPU Temp",
                        "—"
                        if health.cpu_temperature is None
                        else f"{health.cpu_temperature}°C",
                    ),
                    ("Restart Count", str(health.restart_count)),
                ]
            )

        for label, value in rows:
            self.info_form.addRow(label, QLabel(str(value)))

        self._punches: list[dict] = []
        punches = self.engine.container.attendance.list_recent_for_device(device.id, 200)
        self._punches = punches
        summary = self.engine.container.attendance.clock_mismatch_summary()
        device_samples = [
            sample
            for sample in (summary.get("samples") or [])
            if str(sample.get("device_id") or "") == device.id
        ]
        if device_samples:
            self.clock.set_mismatch(
                {
                    "critical": True,
                    "count": int(summary.get("count") or len(device_samples)),
                    "samples": device_samples,
                }
            )
        else:
            self.clock.set_mismatch({})
        self.punch_table.setRowCount(len(punches))
        if punches:
            self.punch_summary.setText(
                f"{len(punches)} stored punch{'es' if len(punches) != 1 else ''} "
                "for this device (includes history even if the terminal clock is wrong)."
            )
        else:
            self.punch_summary.setText(
                "No punches stored for this device yet. When TCP 4370 answers, "
                "Connect now / Sync from device will download attendance."
            )
        for row, p in enumerate(punches):
            vals = [
                format_punch_time_display(
                    p.get("normalized_timestamp") or p.get("punch_time") or "",
                    p.get("created_at"),
                ),
                str(p.get("employee_display_name") or p.get("device_employee_code") or ""),
                str(p.get("event_display") or p.get("event_label") or p.get("event_type") or ""),
                str(p.get("verification_method_label") or "Not reported by device"),
                str(p.get("upload_status_label") or p.get("status") or ""),
                str(p.get("last_error") or ""),
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setToolTip(p.get("employee_name_hint") if col == 1 else val)
                if col == 4:
                    st = str(p.get("status") or "").lower()
                    if st == "uploaded":
                        item.setForeground(QColor("#0b6e4f"))
                    elif st in ("failed", "dead"):
                        item.setForeground(QColor("#b42318"))
                    elif st in ("pending", "uploading"):
                        item.setForeground(QColor("#9a6700"))
                self.punch_table.setItem(row, col, item)

        history = self.engine.container.sync_logs.recent_for_device(device.id, 80)
        self.history_table.setRowCount(len(history))
        for row, a in enumerate(history):
            vals = [
                format_datetime_display(a.created_at or ""),
                str(a.action or ""),
                "Yes" if a.success else "No",
                str(a.records_count or 0),
                str(a.message or ""),
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                if col == 2 and val == "No":
                    item.setForeground(QColor("#b42318"))
                self.history_table.setItem(row, col, item)

        snaps = self.engine.container.health_repo.recent_for_device(device.id, 30)
        self.health_table.setRowCount(len(snaps))
        for row, snap in enumerate(snaps):
            vals = [
                format_datetime_display(snap.collected_at or ""),
                "Yes" if snap.online else "No",
                f"{snap.ping_ms:.0f} ms" if snap.ping_ms else "—",
                f"{snap.current_users} / {snap.current_logs}",
                str(snap.message or ""),
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                if col == 1 and val == "No":
                    item.setForeground(QColor("#b42318"))
                self.health_table.setItem(row, col, item)

    def _open_punch_details(self) -> None:
        from ui.attendance_views import PunchDetailsDialog

        rows = self.punch_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Select a punch", "Select a punch to view employee and raw device data.")
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._punches):
            PunchDetailsDialog(self._punches[idx], self).exec()

    def _sync_time(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Sync device time",
                "Write the connector clock to this biometric terminal?\n\n"
                "Existing punches keep their original device timestamp.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            result = self.engine.sync_device_time(self.device_id)
            if result.get("success"):
                QMessageBox.information(self, "Device time synced", str(result.get("message") or "Done."))
            else:
                QMessageBox.warning(self, "Time sync failed", str(result.get("message") or "Unable to sync."))
        except Exception as exc:
            QMessageBox.critical(self, "Time sync failed", str(exc))
        self._reload()

    def _connect_now(self) -> None:
        if self._connect_busy:
            return
        self._connect_busy = True
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText("Connecting…")
        device_id = self.device_id

        def work() -> None:
            try:
                result = self.engine.device_manager.reconnect(device_id)
            except Exception as exc:
                result = {"ok": False, "message": str(exc)}
            QTimer.singleShot(0, lambda r=result: self._on_connect_done(r))

        threading.Thread(target=work, daemon=True, name="device-details-connect").start()

    def _on_connect_done(self, result: object) -> None:
        self._connect_busy = False
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("Connect now")
        payload = result if isinstance(result, dict) else {"ok": False, "message": str(result)}
        ok = bool(payload.get("ok") or payload.get("protocol_ok") or payload.get("online"))
        message = str(payload.get("message") or payload.get("error") or "")
        self._reload()
        parent = self.parent()
        refresh = getattr(parent, "refresh", None) if parent is not None else None
        if callable(refresh):
            refresh()
        if ok:
            QMessageBox.information(self, "Connected", message or "Device protocol session succeeded.")
            self.tabs.setCurrentIndex(1)
        else:
            QMessageBox.warning(
                self,
                "Still connecting",
                message
                or (
                    "The K90 did not answer TCP 4370 on this PC's LAN. "
                    "History below is the last stored data. Keep the terminal powered "
                    "on the same 192.168.100.x network and retry."
                ),
            )
            self.tabs.setCurrentIndex(2)
