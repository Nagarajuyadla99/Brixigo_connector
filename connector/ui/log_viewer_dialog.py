"""Categorized log viewer with search and export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
)

from services.connector_engine import ConnectorEngine

LOG_CATEGORIES = {
    "Connection Logs": "connection.log",
    "Attendance / Sync Logs": "sync.log",
    "Upload Logs": "upload.log",
    "Health Logs": "health.log",
    "Error Logs": "error.log",
    "Heartbeat Logs": "heartbeat.log",
    "Service Logs": "service.log",
    "All Connector Logs": "connector.log",
}


class LogViewerDialog(QDialog):
    def __init__(self, engine: ConnectorEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Log Viewer")
        self.resize(900, 620)
        self._current_text = ""

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.category = QComboBox()
        for label in LOG_CATEGORIES:
            self.category.addItem(label)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search logs…")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_export = QPushButton("Export")
        self.btn_export.setObjectName("Secondary")
        controls.addWidget(QLabel("Category"))
        controls.addWidget(self.category)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.btn_refresh)
        controls.addWidget(self.btn_export)
        layout.addLayout(controls)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        layout.addWidget(self.viewer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.category.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self._apply_filter)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_export.clicked.connect(self._export)
        self.refresh()

    def _log_path(self) -> Path:
        name = LOG_CATEGORIES[self.category.currentText()]
        return self.engine.container.paths.logs / name

    def refresh(self) -> None:
        path = self._log_path()
        if not path.exists():
            self._current_text = f"No log file yet: {path.name}"
        else:
            try:
                # Read last ~1MB for responsiveness
                data = path.read_bytes()
                if len(data) > 1_000_000:
                    data = data[-1_000_000:]
                self._current_text = data.decode("utf-8", errors="replace")
            except Exception as exc:
                self._current_text = f"Failed to read log: {exc}"
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.search.text().strip().lower()
        if not query:
            self.viewer.setPlainText(self._current_text)
            return
        lines = [line for line in self._current_text.splitlines() if query in line.lower()]
        self.viewer.setPlainText("\n".join(lines) if lines else "No matching log lines.")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs",
            str(self._log_path().with_suffix(".export.log")),
            "Log Files (*.log *.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.viewer.toPlainText(), encoding="utf-8")
            QMessageBox.information(self, "Exported", f"Logs exported to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
