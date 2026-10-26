"""Brixigo Biometric Connector Command Center."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.attendance_views import AttendanceTable, PunchDetailsDialog
from ui.widgets import (
    ActivityTimeline,
    AlertsPanel,
    AttendancePipeline,
    ClockMismatchPanel,
    ConnectivityFlow,
    ConnectorHealthHero,
    DeviceOverview,
    DeviceStatusChart,
    EmptyState,
    ErrorSurface,
    HealthBreakdown,
    MetricsOverview,
    OperationalBanner,
    QueueStatusPanel,
    QuickActions,
    SectionHeader,
    SimpleBarChart,
    SkeletonBlock,
    SystemStatusStrip,
    UploadPerformancePanel,
)


class ConnectorPage(QWidget):
    view_alerts = Signal()
    view_devices = Signal()
    view_attendance = Signal()
    view_queue = Signal()
    view_diagnostics = Signal()
    view_activity = Signal()
    sync_requested = Signal()
    discover_requested = Signal()
    sync_time_requested = Signal(str)
    punch_details_requested = Signal(dict)
    device_clicked = Signal(str)
    device_sync_requested = Signal(str)
    retry_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.error = ErrorSurface()
        self.error.retry.connect(self.retry_requested.emit)
        self.error.diagnostics.connect(self.view_diagnostics.emit)
        root.addWidget(self.error)

        self.skeleton = QWidget()
        sk = QVBoxLayout(self.skeleton)
        sk.setContentsMargins(4, 4, 4, 4)
        sk.setSpacing(12)
        sk.addWidget(SkeletonBlock(120))
        sk.addWidget(SkeletonBlock(96))
        row = QHBoxLayout()
        row.addWidget(SkeletonBlock(160))
        row.addWidget(SkeletonBlock(160))
        sk.addLayout(row)
        root.addWidget(self.skeleton)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        host.setMinimumWidth(860)
        self._layout = QVBoxLayout(host)
        self._layout.setContentsMargins(4, 4, 4, 24)
        self._layout.setSpacing(20)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)
        self._scroll = scroll
        self._scroll.hide()

        self.offline = OperationalBanner()
        self.offline.action_clicked.connect(self.view_diagnostics.emit)
        self._layout.addWidget(self.offline)

        self.stale = OperationalBanner()
        self.stale.action_clicked.connect(self.view_diagnostics.emit)
        self._layout.addWidget(self.stale)

        self.clock = ClockMismatchPanel()
        self.clock.sync_clicked.connect(self._sync_clock)
        self.clock.details_clicked.connect(self.view_diagnostics.emit)
        self._layout.addWidget(self.clock)

        self.hero = ConnectorHealthHero()
        self.hero.sync_clicked.connect(self.sync_requested.emit)
        self.hero.diagnostics_clicked.connect(self.view_diagnostics.emit)
        self.hero.action_clicked.connect(self._hero_action)
        self._layout.addWidget(self.hero)

        self.flow = ConnectivityFlow()
        self.flow.node_clicked.connect(self._route)
        self._layout.addWidget(self.flow)

        status_row = QHBoxLayout()
        status_row.setSpacing(16)
        self.system = SystemStatusStrip()
        self.system.clicked.connect(self._route)
        status_row.addWidget(self.system, 2)
        right_status = QVBoxLayout()
        right_status.setSpacing(16)
        self.queue = QueueStatusPanel()
        self.queue.clicked.connect(self.view_queue.emit)
        self.health_box = QFrame()
        self.health_box.setObjectName("Surface")
        hb = QVBoxLayout(self.health_box)
        hb.setContentsMargins(16, 12, 16, 14)
        hb.addWidget(SectionHeader("HEALTH SCORE", "Component breakdown"))
        self.breakdown = HealthBreakdown()
        hb.addWidget(self.breakdown)
        right_status.addWidget(self.queue)
        right_status.addWidget(self.health_box, 1)
        status_row.addLayout(right_status, 2)
        self._layout.addLayout(status_row)

        self.metrics = MetricsOverview()
        self.metrics.group_clicked.connect(self._route)
        self._layout.addWidget(self.metrics)

        flow_row = QHBoxLayout()
        flow_row.setSpacing(16)
        self.pipeline = AttendancePipeline()
        self.pipeline.stage_clicked.connect(self._route)
        flow_row.addWidget(self.pipeline, 3)
        self._layout.addLayout(flow_row)

        analytics_head = QHBoxLayout()
        analytics_head.setContentsMargins(0, 4, 0, 0)
        analytics_head.setSpacing(8)
        analytics_head.addWidget(SectionHeader("ANALYTICS", "Attendance and device trends"), 1)
        self.branch = QComboBox()
        self.branch.addItem("All branches", "")
        self.branch.setVisible(False)
        self.branch.currentIndexChanged.connect(self._on_branch)
        self.btn_24h = QPushButton("24H")
        self.btn_7d = QPushButton("7D")
        self.btn_30d = QPushButton("30D")
        for btn in (self.btn_24h, self.btn_7d, self.btn_30d):
            btn.setCheckable(True)
            btn.setObjectName("RangeChip")
            analytics_head.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        analytics_head.addWidget(self.branch)
        self.btn_7d.setChecked(True)
        self.btn_24h.clicked.connect(lambda: self._set_range("24h"))
        self.btn_7d.clicked.connect(lambda: self._set_range("7d"))
        self.btn_30d.clicked.connect(lambda: self._set_range("30d"))
        self._layout.addLayout(analytics_head)

        charts = QHBoxLayout()
        charts.setSpacing(16)
        self.trend = SimpleBarChart("Punch trend")
        self.upload_perf = UploadPerformancePanel()
        self.device_chart = DeviceStatusChart("Device status")
        charts.addWidget(self.trend, 2)
        charts.addWidget(self.upload_perf, 1)
        charts.addWidget(self.device_chart, 1)
        self._layout.addLayout(charts)

        mid = QHBoxLayout()
        mid.setSpacing(16)
        self.devices = DeviceOverview()
        self.devices.view_all.connect(self.view_devices.emit)
        self.devices.device_clicked.connect(self.device_clicked.emit)
        self.devices.sync_clicked.connect(self.device_sync_requested.emit)
        self.devices.diagnostics_clicked.connect(lambda _did: self.view_diagnostics.emit())
        mid.addWidget(self.devices, 3)
        self.alerts = AlertsPanel()
        self.alerts.view_all.connect(self.view_alerts.emit)
        self.alerts.alert_clicked.connect(self.view_alerts.emit)
        mid.addWidget(self.alerts, 2)
        self._layout.addLayout(mid)

        lower = QHBoxLayout()
        lower.setSpacing(16)
        self.activity = ActivityTimeline()
        self.activity.view_all.connect(self.view_activity.emit)
        self.activity.item_clicked.connect(self.view_activity.emit)
        lower.addWidget(self.activity, 1)
        punches_box = QFrame()
        punches_box.setObjectName("Surface")
        punches_l = QVBoxLayout(punches_box)
        punches_l.setContentsMargins(16, 12, 16, 12)
        punch_head = SectionHeader("RECENT ATTENDANCE", "Newest first")
        punch_head.action.setText("Open attendance")
        punch_head.action.clicked.connect(self.view_attendance.emit)
        punches_l.addWidget(punch_head)
        self.punch_hint = QLabel("")
        self.punch_hint.setObjectName("Muted")
        punches_l.addWidget(self.punch_hint)
        self.punches = AttendanceTable()
        self.punches.setMinimumHeight(260)
        punches_l.addWidget(self.punches)
        self.punch_empty = EmptyState("No attendance punches found for this period.")
        self.punch_empty.hide()
        punches_l.addWidget(self.punch_empty)
        btn_details = QPushButton("View details")
        btn_details.setObjectName("Secondary")
        btn_details.clicked.connect(self._open_punch)
        punches_l.addWidget(btn_details, 0, Qt.AlignmentFlag.AlignLeft)
        self.punches.doubleClicked.connect(self._open_punch)
        lower.addWidget(punches_box, 1)
        self._layout.addLayout(lower)

        self.actions = QuickActions()
        self.actions.sync_clicked.connect(self.sync_requested.emit)
        self.actions.discover_clicked.connect(self.discover_requested.emit)
        self.actions.diagnostics_clicked.connect(self.view_diagnostics.emit)
        self.actions.alerts_clicked.connect(self.view_alerts.emit)
        self.actions.queue_clicked.connect(self.view_queue.emit)
        self._layout.addWidget(self.actions)

        self._clock_device_id = ""
        self._range = "7d"
        self._dash: dict = {}

    def apply(self, dash: dict) -> None:
        self.error.hide()
        self.skeleton.hide()
        self._scroll.show()
        self._dash = dash or {}
        health = dash.get("health") or {}
        self.hero.apply(dash)
        self.breakdown.set_health(health)
        self.clock.set_mismatch(dash.get("clock_mismatch") or {})
        samples = (dash.get("clock_mismatch") or {}).get("samples") or []
        self._clock_device_id = str((samples[0] or {}).get("device_id") or "") if samples else ""
        if self.clock.device_id:
            self._clock_device_id = self.clock.device_id
        self._apply_banners(dash)
        self.flow.apply(dash)
        self.system.apply(dash)
        self.queue.apply(dash)
        self.metrics.apply(dash)
        self.pipeline.apply(dash)
        self.devices.apply(dash)
        self.alerts.apply(dash)
        self.activity.apply(dash)
        self.upload_perf.apply(dash)
        self._fill_branches(dash.get("branches") or [])
        self._apply_charts(dash)
        punches = dash.get("punch_history") or dash.get("today_punch_list") or []
        self.punches.set_punches(punches[:12])
        self.punches.setVisible(bool(punches))
        self.punch_empty.setVisible(not punches)
        today = int(dash.get("today_punches") or 0)
        self.punch_hint.setText(
            f"{len(punches[:12])} recent  ·  {today} today. "
            "Technical vendor payload is in View details."
        )

    def show_error(self, message: str) -> None:
        self.skeleton.hide()
        self._scroll.show()
        self.error.show_error(message)

    def _apply_banners(self, dash: dict) -> None:
        cloud = str(dash.get("cloud_status") or "").lower()
        inet = str(dash.get("internet_status") or "").lower()
        pending = int(dash.get("queue_count") or 0)
        if inet in {"offline", "unavailable", "error"}:
            self.offline.set_banner(
                kind="critical",
                title="Internet offline",
                body="Device collection can continue locally. Uploads wait until the path to Brixigo Cloud returns.",
                action="View diagnostics",
            )
        elif cloud in {"unavailable", "timeout", "tls_error", "auth_error"}:
            self.offline.set_banner(
                kind="warning",
                title="Cloud disconnected",
                body=f"Device collection continues. {pending} punch{'es' if pending != 1 else ''} waiting in the local queue.",
                action="View diagnostics",
            )
        else:
            self.offline.hide()
        if dash.get("telemetry_stale"):
            age = dash.get("telemetry_age_seconds")
            mins = int(age // 60) if isinstance(age, (int, float)) else None
            when = f"{mins} minutes ago" if mins is not None else "unknown"
            self.stale.set_banner(
                kind="warning",
                title="Stale data",
                body=f"Last successful telemetry update: {when}. This is different from a disconnected system.",
                action="Run diagnostics",
            )
        else:
            self.stale.hide()

    def _fill_branches(self, branches: list[str]) -> None:
        current = self.branch.currentData()
        self.branch.blockSignals(True)
        self.branch.clear()
        self.branch.addItem("All branches", "")
        for name in branches:
            self.branch.addItem(name, name)
        idx = self.branch.findData(current)
        if idx >= 0:
            self.branch.setCurrentIndex(idx)
        self.branch.blockSignals(False)
        self.branch.setVisible(len(branches) > 1)

    def _on_branch(self) -> None:
        self.devices.set_branch(self.branch.currentData() or "")
        if self._dash:
            self.devices.apply(self._dash)

    def _apply_charts(self, dash: dict) -> None:
        if self._range == "24h":
            series = dash.get("punch_trend_hourly") or []
            self.trend.set_title("Punch trend — last 24 hours")
        elif self._range == "30d":
            series = dash.get("punch_trend_30") or []
            self.trend.set_title("Punch trend — last 30 days")
        else:
            series = dash.get("punch_trend") or []
            self.trend.set_title("Punch trend — last 7 days")
        self.trend.set_series(series)
        unknown = int((dash.get("health_summary") or {}).get("unknown") or 0)
        self.device_chart.set_counts(
            online=int(dash.get("connected_devices") or 0),
            offline=int(dash.get("offline_devices") or 0),
            unknown=unknown,
        )

    def _set_range(self, key: str) -> None:
        self._range = key
        self.btn_24h.setChecked(key == "24h")
        self.btn_7d.setChecked(key == "7d")
        self.btn_30d.setChecked(key == "30d")
        if self._dash:
            self._apply_charts(self._dash)

    def _route(self, key: str) -> None:
        mapping = {
            "devices": self.view_devices,
            "attendance": self.view_attendance,
            "queue": self.view_queue,
            "diagnostics": self.view_diagnostics,
            "alerts": self.view_alerts,
            "activity": self.view_activity,
        }
        sig = mapping.get(key)
        if sig:
            sig.emit()

    def _hero_action(self) -> None:
        kind = getattr(self.hero, "_next_kind", "ok")
        if kind == "devices":
            self.view_devices.emit()
        elif kind == "queue":
            self.view_queue.emit()
        elif kind == "clock":
            self._sync_clock()
        else:
            self.view_diagnostics.emit()

    def _sync_clock(self) -> None:
        device_id = self._clock_device_id or self.clock.device_id
        if device_id:
            self.sync_time_requested.emit(device_id)

    def _open_punch(self) -> None:
        punch = self.punches.selected_punch()
        if punch:
            PunchDetailsDialog(punch, self).exec()


DashboardPage = ConnectorPage
