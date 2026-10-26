"""Operator dialogs: sync progress, engine restart, and notification drawer."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from services.sync_report import format_sync_dialog
from ui.theme import prefers_reduced_motion, severity_rank, status_color
from ui.widgets import EmptyState, SectionHeader


class _StageList(QWidget):
    def __init__(self, stages: list[str], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._names = list(stages)
        self._labels: list[QLabel] = []
        for name in stages:
            row = QLabel(f"○  {name}")
            row.setObjectName("RowTitle")
            layout.addWidget(row)
            self._labels.append(row)
        self._index = -1

    def set_stage(self, index: int) -> None:
        self._index = index
        for i, (lab, name) in enumerate(zip(self._labels, self._names)):
            if i < index:
                lab.setText(f"✓  {name}")
                lab.setStyleSheet("color: #0F6E56;")
            elif i == index:
                lab.setText(f"●  {name}")
                lab.setStyleSheet("color: #1D4E89;")
            else:
                lab.setText(f"○  {name}")
                lab.setStyleSheet("color: #5C6B78;")

    def complete_all(self) -> None:
        for lab, name in zip(self._labels, self._names):
            lab.setText(f"✓  {name}")
            lab.setStyleSheet("color: #0F6E56;")

    def fail_all(self) -> None:
        for lab, name in zip(self._labels, self._names):
            lab.setText(f"✕  {name}")
            lab.setStyleSheet("color: #B42318;")


class SyncProgressDialog(QDialog):
    """Honest sync progress — stages stay in-progress until the engine returns."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync devices")
        self.setModal(True)
        self.resize(480, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        self.title = QLabel("Syncing attendance")
        self.title.setObjectName("PanelTitle")
        self.body = QLabel(
            "Pulling new punches from the biometric terminal. "
            "Cloud upload continues automatically in the background."
        )
        self.body.setWordWrap(True)
        self.body.setObjectName("Muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.stages = _StageList(["Collecting", "Saving"])
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("RowTitle")
        self.summary.hide()
        self.btn = QPushButton("Hide")
        self.btn.setObjectName("Secondary")
        self.btn.clicked.connect(self.accept)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addWidget(self.bar)
        layout.addWidget(self.stages)
        layout.addWidget(self.summary)
        layout.addStretch()
        layout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._advance_hint)
        self._hint = 0
        if not prefers_reduced_motion():
            self._tick.start(1400)
        self.stages.set_stage(0)

    def _advance_hint(self) -> None:
        # Stay on Collecting until the engine returns — do not pretend upload finished.
        if self._hint < 1:
            self._hint = 1
            self.stages.set_stage(self._hint)

    def mark_complete(self, results: list) -> None:
        self._tick.stop()
        title, body, ok = format_sync_dialog(results if isinstance(results, list) else [])
        self.title.setText(title)
        collected = inserted = uploaded = duplicates = failed = 0
        for row in results or []:
            collected += int(row.get("fetched") or 0)
            inserted += int(row.get("inserted") or 0)
            uploaded += int(row.get("uploaded") or 0)
            duplicates += int(row.get("duplicates") or 0)
            failed += int(row.get("failed") or row.get("upload_failed") or 0)
            if not row.get("success") and failed == 0:
                failed = 1
        self.summary.setText(
            f"{collected} collected   ·   {uploaded} uploaded   ·   "
            f"{duplicates} duplicates   ·   {failed} failed"
        )
        self.summary.show()
        self.body.setText(body)
        self.btn.setText("Close")
        self.bar.setRange(0, 1)
        if ok:
            self.bar.setValue(1)
            self.stages.complete_all()
            self.btn.setObjectName("")
        else:
            self.bar.setValue(0)
            self.stages.fail_all()
            self.btn.setObjectName("Secondary")

    def mark_failed(self, message: str) -> None:
        self._tick.stop()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.title.setText("Sync failed")
        self.body.setText(message or "The connector could not complete this sync.")
        self.stages.fail_all()
        self.btn.setText("Close")


class RestartProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Restart connector engine")
        self.setModal(True)
        self.resize(440, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        self.title = QLabel("Restarting engine")
        self.title.setObjectName("PanelTitle")
        self.body = QLabel("Device polling and cloud upload workers are restarting. Queued punches are kept.")
        self.body.setWordWrap(True)
        self.body.setObjectName("Muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.stages = _StageList(
            ["Stopping", "Restarting", "Reconnecting devices", "Cloud reconnecting"]
        )
        self.btn = QPushButton("Hide")
        self.btn.setObjectName("Secondary")
        self.btn.clicked.connect(self.accept)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addWidget(self.bar)
        layout.addWidget(self.stages)
        layout.addStretch()
        layout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._advance_hint)
        self._hint = 0
        if not prefers_reduced_motion():
            self._tick.start(700)
        self.stages.set_stage(0)

    def _advance_hint(self) -> None:
        self._hint = min(3, self._hint + 1)
        self.stages.set_stage(self._hint)

    def mark_complete(self, message: str = "") -> None:
        self._tick.stop()
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.stages.complete_all()
        self.title.setText("Connector operational")
        self.body.setText(message or "The connector engine is running again.")
        self.btn.setText("Close")

    def mark_failed(self, message: str) -> None:
        self._tick.stop()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.title.setText("Restart failed")
        self.body.setText(message)
        self.btn.setText("Close")


class NotificationDrawer(QDialog):
    """Header notification center — alerts only, not the activity stream."""

    view_all = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Notifications")
        self.setModal(False)
        self.resize(380, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        head = SectionHeader("NOTIFICATIONS", "Requires attention")
        layout.addWidget(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.host = QWidget()
        self.body = QVBoxLayout(self.host)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        scroll.setWidget(self.host)
        layout.addWidget(scroll, 1)
        self.empty = EmptyState("No unread operational alerts.")
        layout.addWidget(self.empty)
        self.empty.hide()
        row = QHBoxLayout()
        self.btn_all = QPushButton("Open alert center")
        self.btn_all.setObjectName("Secondary")
        self.btn_all.clicked.connect(self.view_all.emit)
        row.addWidget(self.btn_all)
        row.addStretch()
        layout.addLayout(row)
        self._alerts: list[dict] = []

    def set_alerts(self, alerts: list[dict]) -> None:
        open_alerts = [a for a in (alerts or []) if not a.get("acknowledged")]
        open_alerts.sort(key=lambda a: severity_rank(str(a.get("severity") or "")))
        self._alerts = open_alerts
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not open_alerts:
            self.empty.show()
            return
        self.empty.hide()
        for alert in open_alerts[:12]:
            row = QFrame()
            row.setObjectName("AlertRow")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(4, 8, 4, 8)
            bar = QFrame()
            bar.setFixedWidth(3)
            sev = str(alert.get("severity") or "info")
            bar.setStyleSheet(f"background: {status_color(sev)}; border-radius: 1px;")
            col = QVBoxLayout()
            col.setSpacing(1)
            s = QLabel(sev.upper())
            s.setObjectName("SectionKicker")
            s.setStyleSheet(f"color: {status_color(sev)};")
            t = QLabel(str(alert.get("title") or alert.get("alert_type") or "Alert"))
            t.setObjectName("RowTitle")
            t.setWordWrap(True)
            m = QLabel(str(alert.get("message") or "")[:140])
            m.setObjectName("RowMeta")
            m.setWordWrap(True)
            col.addWidget(s)
            col.addWidget(t)
            col.addWidget(m)
            hl.addWidget(bar)
            hl.addLayout(col, 1)
            self.body.addWidget(row)
        self.body.addStretch()
