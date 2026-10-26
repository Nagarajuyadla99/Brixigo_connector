"""Device discovery dialog — scan IP range and approve devices."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from models import DiscoveredDevice
from services.connector_engine import ConnectorEngine
from services.network_scanner import default_discovery_range
from ui.device_dialog import DeviceDialog


class _ScanWorker(QObject):
    finished = Signal(list)
    progress = Signal(int, int, str)
    failed = Signal(str)

    def __init__(self, engine: ConnectorEngine, ip_range: str, password: str):
        super().__init__()
        self.engine = engine
        self.ip_range = ip_range
        self.password = password

    def run(self) -> None:
        try:
            devices = self.engine.scanner.scan(
                self.ip_range,
                password=self.password,
                progress=lambda done, total, target: self.progress.emit(done, total, target),
            )
            self.finished.emit(devices)
        except Exception as exc:
            self.failed.emit(str(exc))


class DiscoveryDialog(QDialog):
    def __init__(self, engine: ConnectorEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Discover Devices")
        self.resize(980, 560)
        self._thread: QThread | None = None
        self._worker: _ScanWorker | None = None
        self._found = 0

        layout = QVBoxLayout(self)
        intro = QLabel("Scan the local network for compatible biometric terminals. Devices are never registered automatically.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.range_edit = QLineEdit(default_discovery_range())
        self.protocol = QComboBox()
        self.protocol.addItem("TCP (port 4370 / 4371)", "tcp")
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 8)
        self.timeout.setValue(1)
        self.timeout.setSuffix(" s")
        self.password_edit = QLineEdit("0")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Network range", self.range_edit)
        form.addRow("Protocol", self.protocol)
        form.addRow("Timeout", self.timeout)
        form.addRow("Comm password", self.password_edit)
        layout.addLayout(form)

        btns = QHBoxLayout()
        self.btn_scan = QPushButton("Start discovery")
        self.btn_scan.clicked.connect(self._start_scan)
        self.btn_add = QPushButton("Add Device")
        self.btn_add.clicked.connect(self._add_selected)
        self.btn_ignore = QPushButton("Ignore Device")
        self.btn_ignore.setObjectName("Secondary")
        self.btn_ignore.clicked.connect(self._ignore_selected)
        btns.addWidget(self.btn_scan)
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_ignore)
        btns.addStretch()
        layout.addLayout(btns)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel("Waiting to scan. Admin approval is required before a device is added.")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "IP",
                "Port",
                "Manufacturer",
                "Model",
                "Firmware",
                "MAC",
                "SDK",
                "Name",
                "Decision",
                "Latency",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        close_box.accepted.connect(self.accept)
        layout.addWidget(close_box)

        self._load_cached()

    def _load_cached(self) -> None:
        self._populate(self.engine.scanner.list_discovered())

    def _populate(self, devices: list[DiscoveredDevice]) -> None:
        self.table.setRowCount(len(devices))
        for row, d in enumerate(devices):
            values = [
                d.ip_address,
                str(d.port),
                d.manufacturer,
                d.model,
                d.firmware,
                d.mac_address,
                d.sdk_version,
                d.device_name,
                d.decision,
                f"{d.latency_ms:.0f} ms",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col == 8:
                    if d.decision == "pending":
                        item.setForeground(QColor("#b54708"))
                    elif d.decision == "added":
                        item.setForeground(QColor("#0b6e4f"))
                    elif d.decision == "ignored":
                        item.setForeground(QColor("#667085"))
                self.table.setItem(row, col, item)

    def _selected(self) -> tuple[str, int] | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        ip_item = self.table.item(row, 0)
        port_item = self.table.item(row, 1)
        if not ip_item or not port_item:
            return None
        return ip_item.text(), int(port_item.text())

    def _start_scan(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.progress.setValue(0)
        self._found = 0
        self.status.setText("Scanning network…")
        try:
            self.engine.scanner.connect_timeout = float(self.timeout.value())
        except Exception:
            pass
        self._thread = QThread(self)
        self._worker = _ScanWorker(
            self.engine, self.range_edit.text().strip(), self.password_edit.text()
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_progress(self, done: int, total: int, target: str) -> None:
        if total:
            self.progress.setValue(int(done * 100 / total))
        self.status.setText(f"Scanning network… {done} of {total} hosts checked · {target}")

    def _on_finished(self, devices: list) -> None:
        self.btn_scan.setEnabled(True)
        self.progress.setValue(100)
        self._found = len(devices)
        self.status.setText(f"Scan complete — {len(devices)} compatible device{'s' if len(devices) != 1 else ''} found")
        self._load_cached()

    def _on_failed(self, message: str) -> None:
        self.btn_scan.setEnabled(True)
        QMessageBox.critical(self, "Scan Failed", message)

    def _add_selected(self) -> None:
        selected = self._selected()
        if not selected:
            QMessageBox.information(self, "Select Device", "Select a discovered device first.")
            return
        ip, port = selected
        discovered = self.engine.container.discovery.get(ip, port)
        if not discovered:
            return
        if discovered.decision == "added":
            QMessageBox.information(self, "Already Added", "This device is already registered.")
            return
        # Prefill add dialog from discovery
        from models import Device

        prefill = Device(
            id="",
            name=discovered.device_name or f"{discovered.manufacturer}-{ip}",
            manufacturer=discovered.manufacturer or "zkteco",
            model=discovered.model,
            ip_address=discovered.ip_address,
            port=discovered.port,
            password=self.password_edit.text() or "0",
            branch="",
            serial_number=discovered.serial_number,
            mac_address=discovered.mac_address,
            firmware=discovered.firmware,
            sdk_version=discovered.sdk_version,
            communication_type=discovered.communication_type or "tcp",
        )
        dlg = DeviceDialog(self, prefill)
        if dlg.exec():
            try:
                values = dlg.values()
                self.engine.device_manager.add_device(**values)
                self.engine.scanner.mark_added(ip, port)
                self._load_cached()
                QMessageBox.information(self, "Added", "Device registered successfully.")
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def _ignore_selected(self) -> None:
        selected = self._selected()
        if not selected:
            return
        ip, port = selected
        self.engine.scanner.ignore_device(ip, port)
        self._load_cached()
