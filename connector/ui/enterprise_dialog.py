"""Phase 3 enterprise management dialog — employees, inventory, reports, access."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from services.connector_engine import ConnectorEngine


class EnterpriseDialog(QDialog):
    def __init__(self, engine: ConnectorEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Enterprise Device Management")
        self.resize(900, 620)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Phase 3 — employees, templates, backups, inventory, reports, access control")
        )

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # Employees
        emp_page = QWidget()
        emp_layout = QVBoxLayout(emp_page)
        emp_btns = QHBoxLayout()
        btn_sync_emp = QPushButton("Sync Employees Now")
        btn_sync_emp.clicked.connect(self._sync_employees)
        emp_btns.addWidget(btn_sync_emp)
        emp_btns.addStretch()
        emp_layout.addLayout(emp_btns)
        self.emp_table = QTableWidget(0, 6)
        self.emp_table.setHorizontalHeaderLabels(
            ["Code", "Name", "Branch", "Department", "Status", "Last Sync"]
        )
        self.emp_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        emp_layout.addWidget(self.emp_table)
        tabs.addTab(emp_page, "Employees")

        # Inventory
        inv_page = QWidget()
        inv_layout = QVBoxLayout(inv_page)
        inv_btns = QHBoxLayout()
        btn_push_inv = QPushButton("Refresh & Push Inventory")
        btn_push_inv.clicked.connect(self._push_inventory)
        inv_btns.addWidget(btn_push_inv)
        inv_btns.addStretch()
        inv_layout.addLayout(inv_btns)
        self.inv_table = QTableWidget(0, 8)
        self.inv_table.setHorizontalHeaderLabels(
            ["Name", "Serial", "Firmware", "MAC", "Branch", "Gate", "Users", "Health"]
        )
        self.inv_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        inv_layout.addWidget(self.inv_table)
        tabs.addTab(inv_page, "Inventory")

        # Reports
        rep_page = QWidget()
        rep_layout = QVBoxLayout(rep_page)
        rep_btns = QHBoxLayout()
        for label, rtype in (
            ("Attendance Sync", "attendance_sync"),
            ("Device Health", "device_health"),
            ("Audit", "audit"),
            ("Performance", "performance"),
            ("Firmware", "firmware"),
        ):
            b = QPushButton(label)
            b.setObjectName("Secondary")
            b.clicked.connect(lambda _=False, t=rtype: self._generate_report(t))
            rep_btns.addWidget(b)
        rep_btns.addStretch()
        rep_layout.addLayout(rep_btns)
        self.rep_table = QTableWidget(0, 4)
        self.rep_table.setHorizontalHeaderLabels(["Type", "Status", "Path", "Created"])
        self.rep_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rep_layout.addWidget(self.rep_table)
        tabs.addTab(rep_page, "Reports")

        # Access control
        ac_page = QWidget()
        ac_layout = QVBoxLayout(ac_page)
        ac_layout.addWidget(
            QLabel("Access-control architecture is ready for door controllers, levels, and schedules.")
        )
        self.ac_table = QTableWidget(0, 4)
        self.ac_table.setHorizontalHeaderLabels(["Type", "Name", "Branch", "Status/Doors"])
        self.ac_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        ac_layout.addWidget(self.ac_table)
        tabs.addTab(ac_page, "Access Control")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        employees = self.engine.container.employees.list_all()
        self.emp_table.setRowCount(len(employees))
        for row, e in enumerate(employees):
            vals = [
                e.employee_code,
                e.name,
                e.branch,
                e.department,
                e.status,
                (e.last_synced_at or "—")[-19:],
            ]
            for col, val in enumerate(vals):
                self.emp_table.setItem(row, col, QTableWidgetItem(val))

        devices = self.engine.device_manager.list_devices()
        self.inv_table.setRowCount(len(devices))
        for row, d in enumerate(devices):
            vals = [
                d.name,
                d.serial_number,
                d.firmware,
                d.mac_address,
                d.branch,
                d.gate,
                str(d.current_users),
                d.health_status,
            ]
            for col, val in enumerate(vals):
                self.inv_table.setItem(row, col, QTableWidgetItem(val))

        reports = self.engine.container.reports.recent(50)
        self.rep_table.setRowCount(len(reports))
        for row, r in enumerate(reports):
            vals = [r.report_type, r.status, r.result_path, (r.created_at or "")[-19:]]
            for col, val in enumerate(vals):
                self.rep_table.setItem(row, col, QTableWidgetItem(val))

        snap = self.engine.access_control.snapshot()
        rows = []
        for level in snap["access_levels"]:
            rows.append(("Level", level["name"], level.get("branch") or "", str(len(level.get("door_ids") or []))))
        for door in snap["doors"]:
            rows.append(("Door", door["name"], door.get("branch") or "", door.get("status") or ""))
        self.ac_table.setRowCount(len(rows))
        for row, vals in enumerate(rows):
            for col, val in enumerate(vals):
                self.ac_table.setItem(row, col, QTableWidgetItem(str(val)))

    def _sync_employees(self) -> None:
        try:
            result = self.engine.employee_sync.sync_all()
            QMessageBox.information(self, "Employee Sync", str(result))
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Sync Failed", str(exc))

    def _push_inventory(self) -> None:
        try:
            result = self.engine.inventory_service.push()
            QMessageBox.information(
                self, "Inventory", f"Pushed {result.get('pushed', 0)} device records"
            )
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Inventory Failed", str(exc))

    def _generate_report(self, report_type: str) -> None:
        try:
            req = self.engine.reporting.generate(report_type)
            QMessageBox.information(
                self, "Report", f"{report_type} → {req.status}\n{req.result_path}"
            )
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Report Failed", str(exc))
