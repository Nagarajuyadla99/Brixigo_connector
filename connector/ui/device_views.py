"""Device fleet operations view."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from services.connection_state import operator_device_badge
from ui.table_utils import format_datetime_display
from ui.widgets import EmptyState, StatusBadge


class DeviceCard(QFrame):
    details_clicked = Signal(str)
    sync_clicked = Signal(str)
    edit_clicked = Signal(str)

    def __init__(self, device, *, link: dict, today: int, pending: int, latency_ms: float | None, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        self.device_id = device.id
        layout = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel(device.name or "Unnamed device")
        title.setObjectName("PanelTitle")
        status = operator_device_badge(link, fallback=str(device.status or "unknown"))
        if not device.enabled:
            status = "disabled"
        badge = StatusBadge(status, status)
        head.addWidget(title, 1)
        head.addWidget(badge)
        layout.addLayout(head)
        meta = [
            ("IP address", f"{device.ip_address}:{device.port}"),
            ("Serial", device.serial_number or "Not reported"),
            ("Firmware", device.firmware or "Not reported"),
            ("Model", device.model or "Not reported"),
            ("Branch", device.branch or "—"),
            ("Last sync", format_datetime_display(device.last_sync_at or "—") or "—"),
            ("Upload latency", f"{latency_ms:.0f} ms" if latency_ms else "—"),
            ("Today's punches", str(today)),
            ("Pending uploads", str(pending)),
        ]
        if link:
            meta.insert(5, ("Network", str(link.get("display_network") or link.get("network_status") or "—")))
        for label, value in meta:
            row = QHBoxLayout()
            cap = QLabel(label)
            cap.setObjectName("KpiHint")
            val = QLabel(str(value))
            row.addWidget(cap, 1)
            row.addWidget(val, 2)
            layout.addLayout(row)
        actions = QHBoxLayout()
        sync = QPushButton("Sync now")
        details = QPushButton("View details")
        details.setObjectName("Secondary")
        edit = QPushButton("Edit")
        edit.setObjectName("Secondary")
        sync.clicked.connect(lambda: self.sync_clicked.emit(self.device_id))
        details.clicked.connect(lambda: self.details_clicked.emit(self.device_id))
        edit.clicked.connect(lambda: self.edit_clicked.emit(self.device_id))
        actions.addWidget(sync)
        actions.addWidget(details)
        actions.addWidget(edit)
        layout.addLayout(actions)


class DeviceFleetPage(QWidget):
    add_requested = Signal()
    details_requested = Signal(str)
    sync_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    enable_requested = Signal(str, bool)
    test_requested = Signal(str)
    filter_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, IP, serial, or branch")
        self.status = QComboBox()
        self.status.addItem("All devices", "all")
        self.status.addItem("Online", "online")
        self.status.addItem("Offline", "offline")
        self.branch = QComboBox()
        self.branch.addItem("All branches", "")
        self.model = QComboBox()
        self.model.addItem("All models", "")
        self.chk_disabled_label = QLabel("")
        self.btn_add = QPushButton("Add device")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setObjectName("Danger")
        self.btn_enable = QPushButton("Enable")
        self.btn_enable.setObjectName("Secondary")
        self.btn_disable = QPushButton("Disable")
        self.btn_disable.setObjectName("Secondary")
        self.btn_test = QPushButton("Test connection")
        self.btn_test.setObjectName("Secondary")
        for w in (
            self.search, self.status, self.branch, self.model,
            self.btn_add, self.btn_test, self.btn_enable, self.btn_disable, self.btn_delete,
        ):
            toolbar.addWidget(w)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.hint = QLabel("Device fleet. Serial number is the durable identity; IP can change.")
        self.hint.setObjectName("Muted")
        layout.addWidget(self.hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.scroll.setWidget(self.host)
        layout.addWidget(self.scroll, 1)
        self.empty = EmptyState("No biometric devices discovered yet.")
        self.empty.hide()
        layout.addWidget(self.empty)
        self._cards: list[DeviceCard] = []
        self._selected_id: str | None = None
        self.search.textChanged.connect(lambda *_: self.filter_changed.emit())
        self.status.currentIndexChanged.connect(lambda *_: self.filter_changed.emit())
        self.branch.currentIndexChanged.connect(lambda *_: self.filter_changed.emit())
        self.model.currentIndexChanged.connect(lambda *_: self.filter_changed.emit())
        self.btn_add.clicked.connect(self.add_requested.emit)
        self.btn_delete.clicked.connect(lambda: self._selected_id and self.delete_requested.emit(self._selected_id))
        self.btn_enable.clicked.connect(lambda: self._selected_id and self.enable_requested.emit(self._selected_id, True))
        self.btn_disable.clicked.connect(lambda: self._selected_id and self.enable_requested.emit(self._selected_id, False))
        self.btn_test.clicked.connect(lambda: self._selected_id and self.test_requested.emit(self._selected_id))

    def set_mode(self, mode: str) -> None:
        idx = self.status.findData(mode)
        if idx >= 0:
            self.status.setCurrentIndex(idx)

    def selected_id(self) -> str | None:
        return self._selected_id

    def set_devices(self, devices, *, dash: dict, mode: str = "all") -> None:
        branches = sorted({d.branch for d in devices if d.branch})
        models = sorted({d.model for d in devices if d.model})
        self._fill_combo(self.branch, "All branches", branches)
        self._fill_combo(self.model, "All models", models)
        query = self.search.text().strip().lower()
        status_filter = self.status.currentData() or mode
        branch_filter = self.branch.currentData() or ""
        model_filter = self.model.currentData() or ""
        links = {
            d.get("device_id"): d
            for d in ((dash.get("connection") or {}).get("devices") or [])
            if d.get("device_id")
        }
        today_counts = dash.get("device_today_counts") or {}
        pending_counts = dash.get("device_pending_counts") or {}
        avg = dash.get("average_upload_ms")
        visible = []
        for device in devices:
            hay = " ".join(
                str(x or "") for x in (device.name, device.ip_address, device.serial_number, device.branch, device.model)
            ).lower()
            if query and query not in hay:
                continue
            if branch_filter and device.branch != branch_filter:
                continue
            if model_filter and device.model != model_filter:
                continue
            online = str(
                operator_device_badge(links.get(device.id), fallback=str(device.status or "unknown"))
                if device.enabled
                else "disabled"
            ).lower() in {"online", "degraded", "connecting"}
            if status_filter == "online" and not online:
                continue
            if status_filter == "offline" and online:
                continue
            visible.append(device)
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._cards = []
        if not visible:
            self.scroll.hide()
            self.empty.show()
            self.empty.setText(
                "No biometric devices match this filter."
                if devices
                else "No biometric devices discovered yet."
            )
            return
        self.empty.hide()
        self.scroll.show()
        for idx, device in enumerate(visible):
            card = DeviceCard(
                device,
                link=links.get(device.id) or {},
                today=int(today_counts.get(device.id) or 0),
                pending=int(pending_counts.get(device.id) or 0),
                latency_ms=avg,
            )
            card.details_clicked.connect(self.details_requested.emit)
            card.sync_clicked.connect(self.sync_requested.emit)
            card.edit_clicked.connect(self.edit_requested.emit)
            card.mousePressEvent = lambda event, did=device.id, orig=card.mousePressEvent: self._select(did, orig, event)
            self.grid.addWidget(card, idx // 2, idx % 2)
            self._cards.append(card)
        if visible and not self._selected_id:
            self._selected_id = visible[0].id
        self.hint.setText(f"{len(visible)} device{'s' if len(visible) != 1 else ''} in this view.")

    def _select(self, device_id: str, orig, event) -> None:
        self._selected_id = device_id
        orig(event)

    @staticmethod
    def _fill_combo(combo: QComboBox, all_label: str, values: list[str]) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "")
        for value in values:
            combo.addItem(value, value)
        idx = combo.findData(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)
