"""Add / Edit device dialog with multi-branch hierarchy fields."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.constants import COMMUNICATION_TYPES, DEFAULT_POLL_INTERVAL_SECONDS, SUPPORTED_MANUFACTURERS
from models import Device


class DeviceDialog(QDialog):
    def __init__(self, parent=None, device: Device | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit Device" if device else "Add Device")
        self.setMinimumWidth(520)
        self.resize(540, 640)
        self._device = device

        form = QFormLayout()
        self.name = QLineEdit(device.name if device else "")
        self.manufacturer = QComboBox()
        for m in SUPPORTED_MANUFACTURERS:
            self.manufacturer.addItem(m.upper() if m != "essl" else "eSSL", m)
        if device:
            idx = self.manufacturer.findData(device.manufacturer.lower())
            if idx >= 0:
                self.manufacturer.setCurrentIndex(idx)
        self.model = QLineEdit(device.model if device else "")
        self.ip = QLineEdit(device.ip_address if device else "")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(device.port if device else 4370)
        self.password = QLineEdit(device.password if device and device.password else "0")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("0")
        self.show_password = QCheckBox("Show")
        self.show_password.toggled.connect(
            lambda on: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        pwd_row = QWidget()
        pwd_layout = QHBoxLayout(pwd_row)
        pwd_layout.setContentsMargins(0, 0, 0, 0)
        pwd_layout.addWidget(self.password, 1)
        pwd_layout.addWidget(self.show_password)
        self.password_hint = QLabel(
            "Device comm key — usually 0 on eSSL/ZK. This is not the Brixigo Cloud API key."
        )
        self.password_hint.setWordWrap(True)
        self.password_hint.setObjectName("Subtitle")

        self.organization = QLineEdit(device.organization if device else "")
        self.branch = QLineEdit(device.branch if device else "")
        self.department = QLineEdit(device.department if device else "")
        self.building = QLineEdit(device.building if device else "")
        self.floor = QLineEdit(device.floor if device else "")
        self.gate = QLineEdit(device.gate if device else "")

        self.timezone = QLineEdit(device.timezone if device else "Asia/Kolkata")
        self.comm = QComboBox()
        for c in COMMUNICATION_TYPES:
            self.comm.addItem(c.upper(), c)
        if device:
            idx = self.comm.findData(device.communication_type)
            if idx >= 0:
                self.comm.setCurrentIndex(idx)
        self.poll = QSpinBox()
        self.poll.setRange(15, 3600)
        self.poll.setSuffix(" sec")
        self.poll.setValue(device.polling_interval if device else DEFAULT_POLL_INTERVAL_SECONDS)
        self.poll.setToolTip(
            "How often the connector pulls punches from this device. "
            "Leave at 60 seconds (1 minute) so attendance appears automatically without Sync now."
        )
        self.poll_hint = QLabel(
            "Attendance is fetched automatically at this interval while the engine is running. "
            "Sync now is only needed for an immediate extra pull."
        )
        self.poll_hint.setWordWrap(True)
        self.poll_hint.setObjectName("Subtitle")
        self.terminal = QLineEdit(device.terminal_code if device else "")
        self.terminal.setPlaceholderText("Cloud terminal code (auto if empty)")
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(True if not device else device.enabled)

        form.addRow("Device Name*", self.name)
        form.addRow("Manufacturer*", self.manufacturer)
        form.addRow("Model", self.model)
        form.addRow("IP Address*", self.ip)
        form.addRow("Port*", self.port)
        form.addRow("Password / Comm Key", pwd_row)
        form.addRow("", self.password_hint)
        form.addRow("Organization", self.organization)
        form.addRow("Branch", self.branch)
        form.addRow("Department", self.department)
        form.addRow("Building", self.building)
        form.addRow("Floor", self.floor)
        form.addRow("Gate", self.gate)
        form.addRow("Timezone", self.timezone)
        form.addRow("Communication", self.comm)
        form.addRow("Polling Interval", self.poll)
        form.addRow("", self.poll_hint)
        form.addRow("Terminal Code", self.terminal)
        form.addRow("", self.enabled)

        form_host = QWidget()
        form_host.setLayout(form)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(form_host)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)

        # Keep Save/Cancel on-screen even on short displays.
        screen = self.screen() or (self.parent().screen() if self.parent() else None)
        if screen is not None:
            avail = screen.availableGeometry()
            self.resize(min(560, avail.width() - 80), min(640, avail.height() - 120))

    def _accept(self) -> None:
        if not self.name.text().strip() or not self.ip.text().strip():
            QMessageBox.warning(self, "Validation", "Name and IP Address are required.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "manufacturer": self.manufacturer.currentData(),
            "model": self.model.text().strip(),
            "ip_address": self.ip.text().strip(),
            "port": int(self.port.value()),
            "password": self.password.text().strip() or "0",
            "organization": self.organization.text().strip(),
            "branch": self.branch.text().strip(),
            "department": self.department.text().strip(),
            "building": self.building.text().strip(),
            "floor": self.floor.text().strip(),
            "gate": self.gate.text().strip(),
            "timezone": self.timezone.text().strip() or "Asia/Kolkata",
            "communication_type": self.comm.currentData(),
            "polling_interval": int(self.poll.value()),
            "terminal_code": self.terminal.text().strip(),
            "enabled": self.enabled.isChecked(),
        }
