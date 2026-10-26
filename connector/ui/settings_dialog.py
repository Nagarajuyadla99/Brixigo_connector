"""Settings dialog — cloud credentials and connector options."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from config.validation import validate_settings_dict
from models import ConnectorSettings


class SettingsDialog(QDialog):
    def __init__(self, parent=None, settings: ConnectorSettings | None = None):
        super().__init__(parent)
        self.setWindowTitle("Connector Settings")
        self.setMinimumWidth(520)
        s = settings or ConnectorSettings()

        form = QFormLayout()
        self.connector_id = QLineEdit(s.connector_id)
        self.connector_id.setPlaceholderText("UUID from Cloud Register — not the display name")
        self.org_id = QLineEdit(s.organisation_id)
        self.api_key = QLineEdit(s.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.jwt = QLineEdit(s.jwt_token)
        self.jwt.setEchoMode(QLineEdit.EchoMode.Password)
        self.cloud_url = QLineEdit(s.cloud_base_url)
        self.heartbeat = QSpinBox()
        self.heartbeat.setRange(15, 600)
        self.heartbeat.setValue(s.heartbeat_interval)
        self.batch = QSpinBox()
        self.batch.setRange(10, 2000)
        self.batch.setValue(s.upload_batch_size)
        self.workers = QSpinBox()
        self.workers.setRange(2, 64)
        self.workers.setValue(s.max_worker_threads)
        self.retry = QSpinBox()
        self.retry.setRange(1, 10)
        self.retry.setValue(s.retry_count)
        self.health_interval = QSpinBox()
        self.health_interval.setRange(30, 600)
        self.health_interval.setValue(s.health_interval)
        self.command_interval = QSpinBox()
        self.command_interval.setRange(10, 600)
        self.command_interval.setValue(s.command_poll_interval)
        self.queue_threshold = QSpinBox()
        self.queue_threshold.setRange(100, 500000)
        self.queue_threshold.setValue(s.queue_alert_threshold)
        self.logging_level = QComboBox()
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.logging_level.addItem(level)
        idx = self.logging_level.findText((s.logging_level or "INFO").upper())
        if idx >= 0:
            self.logging_level.setCurrentIndex(idx)
        self.command_secret = QLineEdit(s.command_signing_secret)
        self.command_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.command_secret.setPlaceholderText("HMAC secret for remote commands")
        self.ca_bundle = QLineEdit(s.ca_bundle_path)
        self.ca_bundle.setPlaceholderText("Optional custom CA bundle path")
        self.verify_ssl = QCheckBox("Validate TLS certificates (HTTPS)")
        self.verify_ssl.setChecked(s.verify_ssl)
        self.auto_update = QCheckBox("Automatically check for updates")
        self.auto_update.setChecked(s.auto_update)
        self.proxy = QLineEdit(s.proxy_url)
        self.proxy.setPlaceholderText("http://proxy:port (optional)")

        hint = QLabel(
            "Organisation ID and API Key come from Brixigo Cloud → Connectors → Register. "
            "Do not enter the device comm key (0) here."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Subtitle")

        form.addRow("Connector ID", self.connector_id)
        form.addRow("Organisation ID*", self.org_id)
        form.addRow("API Key / Device Token*", self.api_key)
        form.addRow("", hint)
        form.addRow("JWT Token (optional)", self.jwt)
        form.addRow("Cloud Base URL", self.cloud_url)
        form.addRow("Heartbeat Interval (sec)", self.heartbeat)
        form.addRow("Health Interval (sec)", self.health_interval)
        form.addRow("Command Poll Interval (sec)", self.command_interval)
        form.addRow("Upload Batch Size", self.batch)
        form.addRow("Upload Retry Attempts", self.retry)
        form.addRow("Max Worker Threads", self.workers)
        form.addRow("Queue Alert Threshold", self.queue_threshold)
        form.addRow("Logging Level", self.logging_level)
        form.addRow("Command Signing Secret", self.command_secret)
        form.addRow("CA Bundle Path", self.ca_bundle)
        form.addRow("Proxy URL", self.proxy)
        form.addRow("", self.verify_ssl)
        form.addRow("", self.auto_update)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        values = self.values()
        result = validate_settings_dict(values, require_cloud=True)
        if not result.ok:
            QMessageBox.warning(
                self,
                "Invalid Settings",
                "Please fix the following:\n\n• " + "\n• ".join(result.errors),
            )
            return
        if result.warnings:
            QMessageBox.information(
                self,
                "Settings Warnings",
                "Saved with warnings:\n\n• " + "\n• ".join(result.warnings),
            )
        self.accept()

    def values(self) -> dict:
        return {
            "connector_id": self.connector_id.text().strip(),
            "organisation_id": self.org_id.text().strip(),
            "api_key": self.api_key.text().strip(),
            "jwt_token": self.jwt.text().strip(),
            "cloud_base_url": self.cloud_url.text().strip().rstrip("/"),
            "heartbeat_interval": int(self.heartbeat.value()),
            "health_interval": int(self.health_interval.value()),
            "command_poll_interval": int(self.command_interval.value()),
            "upload_batch_size": int(self.batch.value()),
            "retry_count": int(self.retry.value()),
            "max_worker_threads": int(self.workers.value()),
            "queue_alert_threshold": int(self.queue_threshold.value()),
            "logging_level": self.logging_level.currentText(),
            "command_signing_secret": self.command_secret.text().strip(),
            "ca_bundle_path": self.ca_bundle.text().strip(),
            "verify_ssl": self.verify_ssl.isChecked(),
            "auto_update": self.auto_update.isChecked(),
            "proxy_url": self.proxy.text().strip(),
        }
