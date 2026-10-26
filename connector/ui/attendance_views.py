"""Attendance operations views: live, history, daily, monthly, upload queue."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.table_utils import (
    apply_column_widths,
    configure_data_table,
    fit_visible_columns,
    format_punch_time_display,
)
from ui.widgets import EmptyState, StatusPill, status_color


class _NoWheelDateEdit(QDateEdit):
    """Calendar date picker that does not change when the mouse wheel scrolls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd MMM yyyy")
        # Block the ZK unset-clock epoch (year 2000) from the calendar UI.
        self.setMinimumDate(QDate(2015, 1, 1))
        self.setMaximumDate(QDate.currentDate())

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


def _today_qdate() -> QDate:
    return QDate.currentDate()


BUSINESS_COLUMNS = [
    "Time",
    "Employee",
    "Device",
    "Branch",
    "Event",
    "Method",
    "Upload status",
]


def _item(text: str, *, status: str | None = None, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text or "—"))
    item.setToolTip(tooltip or str(text or ""))
    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
    if status:
        item.setForeground(QColor(status_color(status)))
    return item


class PunchDetailsDialog(QDialog):
    def __init__(self, punch: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Punch details")
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("EVENT"))
        form = QFormLayout()
        form.addRow("Employee", QLabel(str(punch.get("employee_display_name") or punch.get("device_employee_code") or "—")))
        form.addRow("Name source", QLabel(str(punch.get("employee_name_hint") or "—")))
        form.addRow("Device", QLabel(str(punch.get("device_name") or "—")))
        form.addRow("Branch", QLabel(str(punch.get("branch") or "—")))
        import json

        payload = punch.get("raw_payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"raw": payload}
        if not isinstance(payload, dict):
            payload = {}
        event_text = punch.get("event_display") or punch.get("event_label") or punch.get("event_type") or "—"
        reason_lbl = QLabel(str(punch.get("classification_reason") or "—"))
        reason_lbl.setWordWrap(True)
        device_status = punch.get("device_status")
        if device_status is None:
            device_status = payload.get("device_status", payload.get("status"))
        device_punch = punch.get("device_punch")
        if device_punch is None:
            device_punch = payload.get("device_punch", payload.get("punch"))
        direction = punch.get("device_direction")
        if direction in (None, "", "none"):
            direction = payload.get("device_direction")
        form.addRow("Event", QLabel(str(event_text)))
        form.addRow("Classification", QLabel(str(punch.get("classification_label") or "—")))
        form.addRow("Classification reason", reason_lbl)
        form.addRow("Device status", QLabel("—" if device_status is None else str(device_status)))
        form.addRow("Device punch", QLabel("—" if device_punch is None else str(device_punch)))
        form.addRow(
            "Device direction",
            QLabel(str(direction) if direction else "Not reported by device"),
        )
        form.addRow("Method", QLabel(str(punch.get("verification_method_label") or "Not reported")))
        form.addRow("Upload", QLabel(str(punch.get("upload_status_label") or punch.get("status") or "—")))
        layout.addLayout(form)
        layout.addWidget(QLabel("NORMALIZED DATA"))
        clock = punch.get("clock") or {}
        collected = punch.get("created_at") or punch.get("server_received_at")
        norm = QFormLayout()
        # Never show year-2000 device stamps as the attendance time.
        device_disp = format_punch_time_display(
            clock.get("device_timestamp") or punch.get("device_punch_time") or "",
            collected,
        )
        received_disp = format_punch_time_display(
            clock.get("server_received_at") or punch.get("created_at") or "",
        )
        normalized_disp = format_punch_time_display(
            clock.get("normalized_timestamp") or punch.get("punch_time") or "",
            collected,
        )
        norm.addRow("Device timestamp", QLabel(device_disp or "—"))
        norm.addRow("Received at", QLabel(received_disp or "—"))
        norm.addRow("Normalized time", QLabel(normalized_disp or "—"))
        if clock.get("mismatch"):
            stored = str(punch.get("punch_time") or punch.get("normalized_timestamp") or "")
            stored_year = int(stored[:4]) if stored[:4].isdigit() else 0
            if stored_year < 2010:
                norm.addRow("Clock", QLabel(str(clock.get("difference_label") or "Mismatch")))
        layout.addLayout(norm)
        layout.addWidget(QLabel("RAW VENDOR DATA"))
        raw = QPlainTextEdit()
        raw.setReadOnly(True)
        raw.setObjectName("DiagnosticsPanel")
        raw.setPlainText(json.dumps(payload, indent=2, default=str) if payload else "—")
        layout.addWidget(raw, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AttendanceTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, len(BUSINESS_COLUMNS), parent)
        self.setHorizontalHeaderLabels(BUSINESS_COLUMNS)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        configure_data_table(self, min_height=280, wrap=False, row_height=34)
        apply_column_widths(self, {0: 190, 1: 200, 2: 140, 3: 100, 4: 120, 5: 150, 6: 120})
        self._rows: list[dict] = []

    def set_punches(self, punches: list[dict]) -> None:
        self._rows = punches or []
        self.clearContents()
        self.setRowCount(len(self._rows))
        for row, punch in enumerate(self._rows):
            values = [
                format_punch_time_display(
                    punch.get("normalized_timestamp") or punch.get("punch_time"),
                    punch.get("created_at") or punch.get("server_received_at"),
                ),
                punch.get("employee_display_name")
                or (
                    f"Device user {punch.get('device_employee_code')}"
                    if punch.get("device_employee_code")
                    else "Unnamed employee"
                ),
                punch.get("device_name") or "—",
                punch.get("branch") or "—",
                punch.get("event_display") or punch.get("event_label") or punch.get("event_type") or "—",
                punch.get("verification_method_label") or "Not reported",
                punch.get("upload_status_label") or punch.get("status") or "—",
            ]
            statuses = [None, None, None, None, None, None, punch.get("status")]
            tooltips = [
                "",
                punch.get("employee_name_hint") or "",
                "",
                "",
                punch.get("classification_reason") or "",
                punch.get("verification_method_label") or "Not reported",
                punch.get("last_error") or "",
            ]
            for col, val in enumerate(values):
                if col == 6:
                    pill = StatusPill(str(val), str(punch.get("status") or "info"))
                    self.setCellWidget(row, col, pill)
                else:
                    self.setItem(row, col, _item(val, status=statuses[col], tooltip=tooltips[col]))
        fit_visible_columns(self, {0: 190, 1: 180, 2: 130})

    def selected_punch(self) -> dict | None:
        rows = self.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self._rows):
            return self._rows[idx]
        return None


class HistoryPage(QWidget):
    refresh_requested = Signal()
    sync_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preparing_dates = False
        self._date_from_locked = False
        self._date_to_locked = False
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search employee, device, or code")
        self.device = QComboBox()
        self.device.addItem("All devices", "")
        self.branch = QComboBox()
        self.branch.addItem("All branches", "")
        self.event = QComboBox()
        self.event.addItem("All events", "")
        self.event.addItem("IN", "IN")
        self.event.addItem("OUT", "OUT")
        self.event.addItem("Unclassified", "UNKNOWN")
        self.status = QComboBox()
        self.status.addItems(["All statuses", "pending", "uploaded", "failed", "dead"])
        self.date_from = _NoWheelDateEdit()
        today = _today_qdate()
        self.date_from.setDate(QDate(today.year(), today.month(), 1))
        self.date_to = _NoWheelDateEdit()
        self.date_to.setDate(today)
        self.btn_month = QPushButton("This month")
        self.btn_month.setObjectName("Secondary")
        self.btn_month.setToolTip("Reset the date range to the 1st of this month through today.")
        self.btn_today = QPushButton("Today")
        self.btn_today.setObjectName("Secondary")
        self.btn_today.setToolTip("Show only today's punches.")
        self.btn_apply = QPushButton("Apply filters")
        self.btn_apply.setObjectName("Secondary")
        self.btn_details = QPushButton("View details")
        self.btn_details.setObjectName("Secondary")
        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.setToolTip(
            "Optional immediate pull. Punches are already fetched automatically every 1 minute."
        )
        for w in (
            self.search,
            self.device,
            self.branch,
            self.event,
            self.status,
            self.date_from,
            self.date_to,
            self.btn_month,
            self.btn_today,
            self.btn_apply,
            self.btn_details,
            self.btn_sync,
        ):
            filters.addWidget(w)
        layout.addLayout(filters)
        self.summary = QLabel("Loading attendance…")
        self.summary.setObjectName("Muted")
        layout.addWidget(self.summary)
        self.table = AttendanceTable()
        layout.addWidget(self.table, 1)
        self.empty = EmptyState("No attendance punches found for this period.")
        self.empty.hide()
        layout.addWidget(self.empty)
        pager = QHBoxLayout()
        self.btn_prev = QPushButton("Previous")
        self.btn_prev.setObjectName("Secondary")
        self.btn_next = QPushButton("Next")
        self.btn_next.setObjectName("Secondary")
        self.page_label = QLabel("Page 1")
        pager.addWidget(self.btn_prev)
        pager.addWidget(self.btn_next)
        pager.addWidget(self.page_label)
        pager.addStretch()
        layout.addLayout(pager)
        self.offset = 0
        self.limit = 100
        self.btn_apply.clicked.connect(self._reset_and_refresh)
        self.btn_month.clicked.connect(self._go_current_month)
        self.btn_today.clicked.connect(self._go_today)
        self.btn_sync.clicked.connect(self.sync_requested.emit)
        self.btn_details.clicked.connect(self._open_details)
        self.table.doubleClicked.connect(self._open_details)
        self.btn_prev.clicked.connect(self._prev)
        self.btn_next.clicked.connect(self._next)
        self.search.returnPressed.connect(self._reset_and_refresh)
        self.search.textChanged.connect(self._on_search_changed)
        for combo in (self.device, self.branch, self.event, self.status):
            combo.currentIndexChanged.connect(self._on_filter_changed)
        self.date_from.dateChanged.connect(self._on_date_from_changed)
        self.date_to.dateChanged.connect(self._on_date_to_changed)
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(400)
        self._search_debounce.timeout.connect(self._reset_and_refresh)

    def prepare_dates(self) -> None:
        """Keep History on real calendar days — date pickers do not roll forward by themselves."""
        self._preparing_dates = True
        try:
            today = _today_qdate()
            self.date_from.setMaximumDate(today)
            self.date_to.setMaximumDate(today)
            if not self._date_to_locked:
                self.date_to.setDate(today)
            elif self.date_to.date() > today:
                self.date_to.setDate(today)
            if not self._date_from_locked:
                self.date_from.setDate(QDate(today.year(), today.month(), 1))
            elif self.date_from.date() > today:
                self.date_from.setDate(QDate(today.year(), today.month(), 1))
            self._normalize_date_range()
        finally:
            self._preparing_dates = False

    def _normalize_date_range(self) -> None:
        start = self.date_from.date()
        end = self.date_to.date()
        if start <= end:
            return
        if self._date_from_locked and not self._date_to_locked:
            self.date_to.setDate(start)
        elif self._date_to_locked and not self._date_from_locked:
            self.date_from.setDate(end)
        else:
            self.date_from.setDate(end)

    def _go_current_month(self) -> None:
        self._date_from_locked = False
        self._date_to_locked = False
        self._reset_and_refresh()

    def _go_today(self) -> None:
        today = _today_qdate()
        self._date_from_locked = True
        self._date_to_locked = True
        self._preparing_dates = True
        try:
            self.date_from.setDate(today)
            self.date_to.setDate(today)
        finally:
            self._preparing_dates = False
        self._reset_and_refresh()

    def _on_date_from_changed(self, *_args) -> None:
        if self._preparing_dates:
            return
        self._date_from_locked = True
        self._normalize_date_range()
        self._reset_and_refresh()

    def _on_date_to_changed(self, *_args) -> None:
        if self._preparing_dates:
            return
        self._date_to_locked = True
        self._normalize_date_range()
        self._reset_and_refresh()

    def _on_filter_changed(self, *_args) -> None:
        self._reset_and_refresh()

    def _on_search_changed(self, _text: str) -> None:
        self._search_debounce.start()

    def filters(self) -> dict:
        status = self.status.currentText()
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd")
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {
            "search": self.search.text().strip(),
            "device_id": self.device.currentData() or "",
            "branch": self.branch.currentData() or "",
            "event_type": self.event.currentData() or "",
            "status": "" if status.startswith("All") else status,
            "date_from": date_from,
            "date_to": date_to,
            "limit": self.limit,
            "offset": self.offset,
        }

    def set_lookups(self, devices: list, branches: list[str]) -> None:
        current_device = self.device.currentData()
        current_branch = self.branch.currentData()
        self.device.blockSignals(True)
        self.branch.blockSignals(True)
        self.device.clear()
        self.device.addItem("All devices", "")
        for d in devices:
            self.device.addItem(f"{d.name} ({d.ip_address})", d.id)
        self.branch.clear()
        self.branch.addItem("All branches", "")
        for b in branches:
            self.branch.addItem(b, b)
        idx = self.device.findData(current_device)
        if idx >= 0:
            self.device.setCurrentIndex(idx)
        idx = self.branch.findData(current_branch)
        if idx >= 0:
            self.branch.setCurrentIndex(idx)
        self.device.blockSignals(False)
        self.branch.blockSignals(False)

    def set_result(self, result: dict) -> None:
        items = result.get("items") or []
        total = int(result.get("total") or 0)
        self.table.set_punches(items)
        self.table.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.summary.setText(f"{total} punch{'es' if total != 1 else ''} match the current filters.")
        page = (self.offset // self.limit) + 1
        self.page_label.setText(f"Page {page} · showing {len(items)} of {total}")
        self.btn_prev.setEnabled(self.offset > 0)
        self.btn_next.setEnabled(self.offset + self.limit < total)

    def _reset_and_refresh(self) -> None:
        self.offset = 0
        self.refresh_requested.emit()

    def _prev(self) -> None:
        self.offset = max(0, self.offset - self.limit)
        self.refresh_requested.emit()

    def _next(self) -> None:
        self.offset += self.limit
        self.refresh_requested.emit()

    def _open_details(self) -> None:
        punch = self.table.selected_punch()
        if not punch:
            QMessageBox.information(self, "Select a punch", "Select an attendance row to view details.")
            return
        PunchDetailsDialog(punch, self).exec()


class DailyPage(QWidget):
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Daily attendance"))
        self.day = _NoWheelDateEdit()
        self.day.setDate(_today_qdate())
        self.btn = QPushButton("Load day")
        self.btn.setObjectName("Secondary")
        self.btn_today = QPushButton("Today")
        self.btn_today.setObjectName("Secondary")
        header.addWidget(self.day)
        header.addWidget(self.btn)
        header.addWidget(self.btn_today)
        header.addStretch()
        layout.addLayout(header)
        self.summary = QLabel(
            "Daily attendance for today. Updates automatically while the engine is running."
        )
        self.summary.setObjectName("Muted")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Employee", "Code", "Name source", "First IN", "Last OUT", "Punches", "Device", "Branch"]
        )
        configure_data_table(self.table, min_height=320, wrap=False, row_height=34)
        layout.addWidget(self.table, 1)
        self.empty = EmptyState("No attendance punches found for this day.")
        self.empty.hide()
        layout.addWidget(self.empty)
        self.btn.clicked.connect(self.refresh_requested.emit)
        self.btn_today.clicked.connect(self._go_today)
        self.day.dateChanged.connect(lambda *_: self.refresh_requested.emit())

    def _go_today(self) -> None:
        self.prepare_day(force_today=True)
        self.refresh_requested.emit()

    def prepare_day(self, *, force_today: bool = False) -> str:
        """Keep Daily on a real calendar day — never a future date like 2028."""
        today = _today_qdate()
        self.day.setMaximumDate(today)
        current = self.day.date()
        if force_today or not current.isValid() or current > today:
            self.day.blockSignals(True)
            self.day.setDate(today)
            self.day.blockSignals(False)
        return self.selected_day()

    def selected_day(self) -> str:
        return self.day.date().toString("yyyy-MM-dd")

    def set_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            first_in = format_punch_time_display(
                row.get("first_in") or row.get("first_punch") or ""
            )
            last_out = format_punch_time_display(
                row.get("last_out") or row.get("last_punch") or ""
            )
            vals = [
                row.get("employee_display_name") or "—",
                row.get("employee_code") or "—",
                row.get("employee_name_hint") or "—",
                first_in or "—",
                last_out or "—",
                str(row.get("punch_count") or 0),
                ", ".join(row.get("devices") or []) or "—",
                row.get("branch") or "—",
            ]
            for col, val in enumerate(vals):
                self.table.setItem(i, col, _item(val))
        fit_visible_columns(
            self.table,
            min_widths={0: 120, 1: 56, 2: 110, 3: 168, 4: 168, 5: 64, 6: 110, 7: 90},
        )
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        present = sum(1 for r in rows if r.get("present"))
        self.summary.setText(
            f"{present} employee{'s' if present != 1 else ''} present on {self.selected_day()}."
            if rows
            else (
                f"No valid-dated attendance for {self.selected_day()}. "
                "Old punches from an unset device clock stay in History until new punches arrive."
            )
        )


class MonthlyPage(QWidget):
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Monthly attendance"))
        self.year = QSpinBox()
        # Never offer year-2000 (ZK unset-clock epoch) in the month picker.
        current_year = date.today().year
        self.year.setRange(max(2015, current_year - 10), max(current_year, 2100))
        self.year.setValue(current_year)
        self.month = QSpinBox()
        self.month.setRange(1, 12)
        self.month.setValue(date.today().month)
        self.btn = QPushButton("Load month")
        self.btn.setObjectName("Secondary")
        header.addWidget(QLabel("Year"))
        header.addWidget(self.year)
        header.addWidget(QLabel("Month"))
        header.addWidget(self.month)
        header.addWidget(self.btn)
        header.addStretch()
        layout.addLayout(header)
        self.summary = QLabel("Monthly attendance for the selected period. Updates automatically every 1 minute.")
        self.summary.setObjectName("Muted")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 0)
        configure_data_table(self.table, min_height=320, wrap=False, row_height=32, stretch_last=False)
        layout.addWidget(self.table, 1)
        self.empty = EmptyState("No monthly attendance records for this period.")
        self.empty.hide()
        layout.addWidget(self.empty)
        self.btn.clicked.connect(self.refresh_requested.emit)

    def selected_period(self) -> tuple[int, int]:
        return int(self.year.value()), int(self.month.value())

    def set_report(self, report: dict) -> None:
        days = report.get("days") or []
        rows = report.get("rows") or []
        headers = ["Employee", "Present", "%"] + [d[-2:] for d in days]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for i, emp in enumerate(rows):
            self.table.setItem(i, 0, _item(emp.get("employee_display_name") or emp.get("employee_code")))
            self.table.setItem(i, 1, _item(str(emp.get("present_days") or 0)))
            self.table.setItem(i, 2, _item(f"{emp.get('attendance_percent') or 0:.0f}%"))
            day_map = emp.get("days") or {}
            for col, key in enumerate(days, start=3):
                cell = day_map.get(key) or {}
                mark = "P" if cell.get("present") else "—"
                item = _item(mark, status="ok" if cell.get("present") else "unknown")
                if cell.get("present"):
                    item.setToolTip(f"{cell.get('punches')} punch(es)")
                self.table.setItem(i, col, item)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.summary.setText(
            f"{len(rows)} employee{'s' if len(rows) != 1 else ''} with punches in "
            f"{report.get('year')}-{int(report.get('month') or 0):02d}."
            " Updates automatically every 1 minute."
        )
        year = int(report.get("year") or 0)
        month = int(report.get("month") or 0)
        today = date.today()
        if rows and year == today.year and month == today.month:
            col = 2 + today.day
            if 0 <= col < self.table.columnCount():
                self.table.scrollTo(self.table.model().index(0, col))


class QueuePage(QWidget):
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status = QComboBox()
        self.status.addItem("Active queue (pending / failed)", "")
        self.status.addItem("Pending", "pending")
        self.status.addItem("Failed", "failed")
        self.status.addItem("Permanently failed", "dead")
        self.btn = QPushButton("Refresh")
        self.btn.setObjectName("Secondary")
        header.addWidget(self.status)
        header.addWidget(self.btn)
        header.addStretch()
        layout.addLayout(header)
        self.summary = QLabel("Upload queue")
        self.summary.setObjectName("Muted")
        layout.addWidget(self.summary)
        self.table = AttendanceTable()
        layout.addWidget(self.table, 1)
        self.empty = EmptyState("The upload queue is empty.")
        self.empty.hide()
        layout.addWidget(self.empty)
        self.btn.clicked.connect(self.refresh_requested.emit)
        self.status.currentIndexChanged.connect(lambda *_: self.refresh_requested.emit())

    def selected_status(self) -> str:
        return self.status.currentData() or ""

    def set_rows(self, rows: list[dict]) -> None:
        self.table.set_punches(rows)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.summary.setText(f"{len(rows)} record{'s' if len(rows) != 1 else ''} in this queue view.")


class LivePage(QWidget):
    details_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        head = QHBoxLayout()
        self.summary = QLabel("Live punches")
        self.summary.setObjectName("Muted")
        head.addWidget(self.summary, 1)
        btn = QPushButton("View details")
        btn.setObjectName("Secondary")
        btn.clicked.connect(self.details_requested.emit)
        head.addWidget(btn)
        layout.addLayout(head)
        self.table = AttendanceTable()
        self.table.doubleClicked.connect(self.details_requested.emit)
        layout.addWidget(self.table, 1)
        self.empty = EmptyState(
            "Waiting for today's punches. Devices are polled every 1 minute."
        )
        self.empty.hide()
        layout.addWidget(self.empty)

    def set_punches(self, punches: list[dict], *, today: int = 0) -> None:
        self.table.set_punches(punches)
        self.table.setVisible(bool(punches))
        self.empty.setVisible(not punches)
        self.summary.setText(
            f"{len(punches)} punch{'es' if len(punches) != 1 else ''} today, newest first"
            f" · {today} total today · auto-refresh on."
        )

    def selected_punch(self) -> dict | None:
        return self.table.selected_punch()


class AttendanceHub(QWidget):
    """Live, history, daily, monthly, and upload queue in one operations view."""

    TAB_LIVE = 0
    TAB_HISTORY = 1
    TAB_DAILY = 2
    TAB_MONTHLY = 3
    TAB_QUEUE = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.live_page = LivePage()
        self.history_page = HistoryPage()
        self.daily_page = DailyPage()
        self.monthly_page = MonthlyPage()
        self.queue_page = QueuePage()
        self.tabs.addTab(self.live_page, "Live punches")
        self.tabs.addTab(self.history_page, "History")
        self.tabs.addTab(self.daily_page, "Daily")
        self.tabs.addTab(self.monthly_page, "Monthly")
        self.tabs.addTab(self.queue_page, "Upload queue")
        layout.addWidget(self.tabs)

    def show_tab(self, name: str) -> None:
        mapping = {
            "live": self.TAB_LIVE,
            "history": self.TAB_HISTORY,
            "daily": self.TAB_DAILY,
            "monthly": self.TAB_MONTHLY,
            "queue": self.TAB_QUEUE,
        }
        self.tabs.setCurrentIndex(mapping.get(name, self.TAB_LIVE))

