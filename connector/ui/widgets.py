"""Reusable operations-console widgets for the Connector Command Center."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.table_utils import format_clock_timestamp_display, format_datetime_display
from ui.theme import (
    COLOR_CRIT,
    COLOR_INK,
    COLOR_LINE,
    COLOR_MUTED,
    COLOR_OK,
    COLOR_SURFACE,
    COLOR_WARN,
    format_uptime,
    human_status,
    prefers_reduced_motion,
    qcolor,
    severity_rank,
    status_bg,
    status_color,
)

# Re-export for existing imports
status_color = status_color  # noqa: PLW0127


def _ms(value: object) -> str:
    if value is None or value == "" or value == 0:
        return "—"
    try:
        return f"{float(value):.0f} ms"
    except (TypeError, ValueError):
        return "—"


def _cloud_tone(cloud: str) -> str:
    c = str(cloud or "").lower()
    if c in {"unavailable", "timeout", "tls_error", "auth_error", "disconnected", "offline"}:
        return "error"
    if c in {"slow", "slightly slow", "degraded", "unknown"}:
        return "warning"
    return "ok"


def _queue_tone(pending: int, failed: int, threshold: int) -> str:
    if failed:
        return "error"
    if threshold and pending >= threshold:
        return "error"
    if pending:
        return "warning"
    return "ok"


class IconGlyph(QWidget):
    """Single-language 16px geometric icons — no mixed icon fonts."""

    def __init__(self, name: str, color: str = "#C5D4DE", size: int = 16, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = qcolor(self._color)
        pen = QPen(c, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = self.rect().adjusted(2, 2, -2, -2)
        name = self._name
        if name == "connector":
            p.drawRoundedRect(r.adjusted(1, 3, -1, -3), 2, 2)
            p.drawLine(r.center().x() - 3, r.top() + 1, r.center().x() + 3, r.top() + 1)
            p.drawLine(r.center().x(), r.top() + 1, r.center().x(), r.top() + 3)
        elif name == "device":
            p.drawRoundedRect(r.adjusted(2, 1, -2, -4), 2, 2)
            p.drawLine(r.center().x(), r.bottom() - 4, r.center().x(), r.bottom() - 1)
            p.drawLine(r.center().x() - 3, r.bottom() - 1, r.center().x() + 3, r.bottom() - 1)
        elif name == "attendance":
            p.drawEllipse(r.adjusted(3, 1, -3, -7))
            path = QPainterPath()
            path.moveTo(r.left() + 1, r.bottom() - 1)
            path.quadTo(r.center().x(), r.center().y() + 1, r.right() - 1, r.bottom() - 1)
            p.drawPath(path)
        elif name == "alert":
            path = QPainterPath()
            path.moveTo(r.center().x(), r.top())
            path.lineTo(r.right(), r.bottom() - 1)
            path.lineTo(r.left(), r.bottom() - 1)
            path.closeSubpath()
            p.drawPath(path)
            p.drawPoint(r.center().x(), r.center().y() + 1)
        elif name == "activity":
            p.drawLine(r.left(), r.center().y() + 2, r.left() + 3, r.center().y() + 2)
            p.drawLine(r.left() + 3, r.center().y() + 2, r.left() + 6, r.top() + 2)
            p.drawLine(r.left() + 6, r.top() + 2, r.left() + 9, r.bottom() - 2)
            p.drawLine(r.left() + 9, r.bottom() - 2, r.right(), r.center().y())
        elif name == "diagnostics":
            p.drawEllipse(r.adjusted(1, 1, -4, -4))
            p.drawLine(r.center().x() + 2, r.center().y() + 2, r.right() - 1, r.bottom() - 1)
        elif name == "enterprise":
            p.drawRect(r.adjusted(1, 3, -1, -1))
            p.drawLine(r.left() + 4, r.top() + 3, r.left() + 4, r.bottom() - 1)
            p.drawLine(r.right() - 4, r.top() + 3, r.right() - 4, r.bottom() - 1)
        elif name == "settings":
            p.drawEllipse(r.adjusted(4, 4, -4, -4))
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        elif name == "cloud":
            p.drawRoundedRect(QRectF(r.left() + 1, r.center().y() - 1, r.width() - 2, r.height() / 2), 4, 4)
            p.drawEllipse(QRectF(r.left() + 2, r.top() + 2, 6, 6))
        elif name == "sync":
            p.drawArc(r, 40 * 16, 220 * 16)
            p.drawArc(r, 220 * 16, 220 * 16)
        elif name == "dashboard":
            p.drawRect(r.adjusted(1, 1, -1, -1))
            p.drawLine(r.center().x(), r.top() + 1, r.center().x(), r.bottom() - 1)
            p.drawLine(r.left() + 1, r.center().y(), r.right() - 1, r.center().y())
        else:
            p.drawEllipse(r)


class LivePulse(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = True
        self._phase = 0.4
        self.setFixedSize(10, 10)
        self.setAccessibleName("Live indicator")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        if not prefers_reduced_motion():
            self._timer.start(80)

    def set_live(self, live: bool) -> None:
        self._on = live
        if live and not prefers_reduced_motion() and not self._timer.isActive():
            self._timer.start(80)
        if not live:
            self._timer.stop()
        self.update()

    def _step(self) -> None:
        self._phase = (self._phase + 0.04) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = qcolor(COLOR_OK if self._on else COLOR_MUTED)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(2, 2, 6, 6))
        if self._on and not prefers_reduced_motion():
            glow = QColor(color)
            glow.setAlpha(int(90 * (1 - self._phase)))
            p.setBrush(glow)
            size = 6 + self._phase * 6
            p.drawEllipse(QRectF(5 - size / 2, 5 - size / 2, size, size))


class StatusPill(QLabel):
    def __init__(self, text: str = "", status: str = "info", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(text, status)

    def set_status(self, text: str, status: str = "info") -> None:
        color = status_color(status)
        bg = status_bg(status)
        label = str(text or status or "—")
        self.setText(label)
        self.setStyleSheet(
            f"QLabel {{ color: {color}; font-weight: 700; font-size: 10px; letter-spacing: 0.4px; "
            f"padding: 2px 8px; border: 1px solid {color}33; border-radius: 9px; background: {bg}; }}"
        )
        self.setToolTip(label)
        self.setAccessibleName(f"Status {label}")


StatusBadge = StatusPill


class SectionHeader(QWidget):
    def __init__(self, kicker: str, title: str, action: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(1)
        if kicker:
            kick = QLabel(kicker.upper())
            kick.setObjectName("SectionKicker")
            col.addWidget(kick)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PanelTitle")
        self.title_label.setWordWrap(True)
        col.addWidget(self.title_label)
        layout.addLayout(col, 1)
        self.action = QPushButton(action)
        self.action.setObjectName("Ghost")
        self.action.setVisible(bool(action))
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignTop)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)


class HealthScoreRing(QWidget):
    """Radial operational summary. Derived in the UI layer — not a backend authority."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0
        self._status = "info"
        self.setFixedSize(108, 108)
        self.setToolTip(
            "Calculated from connectivity, device availability, upload success, latency, and clock synchronization."
        )
        self.setAccessibleName("Health score")

    def set_score(self, score: int | None, status: str = "info") -> None:
        self._score = int(score) if score is not None else -1
        self._status = status
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        track = QPen(qcolor("#1F3A4C"), 8)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        if self._score < 0:
            return
        color = status_color(self._status)
        arc = QPen(qcolor(color), 8)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc)
        span = int(max(0, min(100, self._score)) / 100 * 360 * 16)
        p.drawArc(rect, 90 * 16, -span)
        p.setPen(qcolor("#F4F8FB"))
        font = QFont(self.font())
        font.setPixelSize(22)
        font.setBold(True)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{self._score}%")


class ConnectorHealthHero(QFrame):
    sync_clicked = Signal()
    diagnostics_clicked = Signal()
    action_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeroPanel")
        self.setAccessibleName("Connector engine health")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(24)

        self.ring = HealthScoreRing()
        layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)

        mid = QVBoxLayout()
        mid.setSpacing(4)
        kicker = QLabel("CONNECTOR ENGINE")
        kicker.setObjectName("HeroKicker")
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.pulse = LivePulse()
        self.state = QLabel("—")
        self.state.setObjectName("HeroTitle")
        status_row.addWidget(self.pulse, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self.state, 1)
        self.engine = QLabel("Brixigo Connector")
        self.engine.setObjectName("HeroBody")
        self.detail = QLabel("Loading live status")
        self.detail.setObjectName("HeroBody")
        self.detail.setWordWrap(True)
        stats = QHBoxLayout()
        stats.setSpacing(20)
        self.stat_health = QLabel("Health\n—")
        self.stat_uptime = QLabel("Uptime\n—")
        self.stat_sync = QLabel("Last sync\n—")
        self.stat_queue = QLabel("Queue\n—")
        for lab in (self.stat_health, self.stat_uptime, self.stat_sync, self.stat_queue):
            lab.setObjectName("HeroMeta")
            lab.setWordWrap(True)
            stats.addWidget(lab)
        stats.addStretch()
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_sync = QPushButton("Sync now")
        self.btn_diag = QPushButton("Diagnostics")
        self.btn_diag.setObjectName("HeroSecondary")
        self.btn_sync.setToolTip(
            "Optional immediate pull. Punches are already fetched automatically every 1 minute."
        )
        self.btn_sync.clicked.connect(self.sync_clicked.emit)
        self.btn_diag.clicked.connect(self.diagnostics_clicked.emit)
        actions.addWidget(self.btn_sync)
        actions.addWidget(self.btn_diag)
        actions.addStretch()
        mid.addWidget(kicker)
        mid.addLayout(status_row)
        mid.addWidget(self.engine)
        mid.addWidget(self.detail)
        mid.addSpacing(8)
        mid.addLayout(stats)
        mid.addSpacing(8)
        mid.addLayout(actions)
        layout.addLayout(mid, 1)

        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet("background: #1F3A4C;")
        layout.addWidget(divider)

        right = QVBoxLayout()
        right.setSpacing(6)
        hint = QLabel("NEXT ACTION")
        hint.setObjectName("HeroKicker")
        self.next_title = QLabel("—")
        self.next_title.setObjectName("HeroAction")
        self.next_title.setWordWrap(True)
        self.next_body = QLabel("")
        self.next_body.setObjectName("HeroMeta")
        self.next_body.setWordWrap(True)
        self.btn_next = QPushButton("Review")
        self.btn_next.setObjectName("HeroSecondary")
        self.btn_next.clicked.connect(self.action_clicked.emit)
        right.addWidget(hint)
        right.addWidget(self.next_title)
        right.addWidget(self.next_body)
        right.addStretch()
        right.addWidget(self.btn_next, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(right, 0)
        self._next_kind = "ok"

    def apply(self, dash: dict) -> None:
        health = dash.get("health") or {}
        overall = health.get("overall")
        status = str(health.get("status") or dash.get("status_tone") or "info")
        label = str(health.get("label") or human_status(status))
        self.ring.set_score(int(overall) if overall is not None else None, status)
        running = str(dash.get("connector_status") or "") == "running"
        self.pulse.set_live(running and status != "critical")
        self.state.setText(label if overall is not None else ("Operational" if running else "Stopped"))
        color = status_color(status if overall is not None else ("ok" if running else "critical"))
        self.state.setStyleSheet(f"color: {color};")
        self.detail.setText(str(dash.get("status_detail") or dash.get("status_headline") or "Engine status unavailable"))
        uptime = format_uptime(dash.get("started_at"))
        sync = format_datetime_display(dash.get("last_sync") or "") or "—"
        pending = int(dash.get("queue_count") or 0)
        self.stat_health.setText(f"Health\n{int(overall)}%" if overall is not None else "Health\n—")
        self.stat_uptime.setText(f"Uptime\n{uptime}")
        self.stat_sync.setText(f"Last sync\n{sync}")
        self.stat_queue.setText(f"Queue\n{pending} pending" if pending else "Queue\nClear")
        self.stat_queue.setToolTip("Attendance records waiting for cloud upload.")
        self._set_next_action(dash)

    def _set_next_action(self, dash: dict) -> None:
        offline = int(dash.get("offline_devices") or 0)
        failed = int(dash.get("failed_uploads") or 0)
        pending = int(dash.get("queue_count") or 0)
        clock = dash.get("clock_mismatch") or {}
        cloud = str(dash.get("cloud_status") or "").lower()
        running = str(dash.get("connector_status") or "") == "running"
        stale = bool(dash.get("telemetry_stale"))
        self.btn_next.show()
        if not running:
            self.next_title.setText("Connector engine is stopped")
            self.next_body.setText("Polling and uploads are not running.")
            self.btn_next.setText("Open diagnostics")
            self._next_kind = "diagnostics"
        elif stale:
            self.next_title.setText("Telemetry is stale")
            age = dash.get("telemetry_age_seconds")
            mins = int(age // 60) if isinstance(age, (int, float)) else None
            self.next_body.setText(f"Last successful update: {mins} min ago." if mins is not None else "Heartbeat has not refreshed.")
            self.btn_next.setText("Run diagnostics")
            self._next_kind = "diagnostics"
        elif cloud in {"unavailable", "timeout", "tls_error", "auth_error"}:
            self.next_title.setText("Cloud is not reachable")
            self.next_body.setText(human_status(cloud))
            self.btn_next.setText("Run diagnostics")
            self._next_kind = "diagnostics"
        elif offline:
            self.next_title.setText(f"{offline} device{'s' if offline != 1 else ''} offline")
            self.next_body.setText("Attendance from offline terminals will not be collected.")
            self.btn_next.setText("View devices")
            self._next_kind = "devices"
        elif clock.get("critical"):
            self.next_title.setText("Device clock mismatch")
            self.next_body.setText("A terminal is reporting an impossible date. Sync its clock.")
            self.btn_next.setText("Sync device time")
            self._next_kind = "clock"
        elif failed:
            self.next_title.setText(f"{failed} failed upload{'s' if failed != 1 else ''}")
            self.next_body.setText("Punches are stored locally and can be retried.")
            self.btn_next.setText("View upload queue")
            self._next_kind = "queue"
        elif pending:
            self.next_title.setText(f"{pending} punch{'es' if pending != 1 else ''} waiting")
            self.next_body.setText("The local queue will upload when the cloud is ready.")
            self.btn_next.setText("View queue")
            self._next_kind = "queue"
        else:
            self.next_title.setText("No action required")
            self.next_body.setText("Connector, cloud, and devices are operating normally.")
            self.btn_next.hide()
            self._next_kind = "ok"


class FlowArrow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._healthy = True
        self._active = False
        self._phase = 0.0
        self.setFixedWidth(28)
        self.setMinimumHeight(52)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        if not prefers_reduced_motion():
            self._timer.start(90)

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def _step(self) -> None:
        if not (self._healthy and self._active):
            return
        self._phase = (self._phase + 0.08) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = qcolor(COLOR_OK if self._healthy else COLOR_CRIT)
        cy = self.height() / 2
        left, right = 2, self.width() - 2
        pen = QPen(color, 1.5)
        p.setPen(pen)
        p.drawLine(left, int(cy), right, int(cy))
        p.drawLine(right - 6, int(cy) - 4, right, int(cy))
        p.drawLine(right - 6, int(cy) + 4, right, int(cy))
        if self._healthy and self._active and not prefers_reduced_motion():
            x = left + (right - left) * self._phase
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x - 2.5, cy - 2.5, 5, 5))


class FlowNode(QFrame):
    clicked = Signal()

    def __init__(self, kicker: str, parent=None):
        super().__init__(parent)
        self.setObjectName("FlowNode")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.kicker = QLabel(kicker.upper())
        self.kicker.setObjectName("SectionKicker")
        self.title = QLabel("—")
        self.title.setObjectName("RowTitle")
        self.title.setWordWrap(True)
        self.meta = QLabel("")
        self.meta.setObjectName("RowMeta")
        self.meta.setWordWrap(True)
        layout.addWidget(self.kicker)
        layout.addWidget(self.title)
        layout.addWidget(self.meta)

    def set_node(self, title: str, meta: str, status: str) -> None:
        self.title.setText(title)
        self.title.setStyleSheet(f"color: {status_color(status)};")
        self.meta.setText(meta)
        self.setToolTip(f"{self.kicker.text()}: {title}. {meta}")
        self.setAccessibleName(f"{self.kicker.text()} {title}")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class ConnectivityFlow(QFrame):
    node_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FlowStrip")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        head = SectionHeader("CONNECTION FLOW", "Device network to Brixigo Cloud")
        layout.addWidget(head)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.n_net = FlowNode("Device network")
        self.n_con = FlowNode("Connector engine")
        self.n_col = FlowNode("Punch collector")
        self.n_q = FlowNode("Local queue")
        self.n_up = FlowNode("Upload worker")
        self.n_cloud = FlowNode("Brixigo cloud")
        self.arrows = [FlowArrow() for _ in range(5)]
        nodes = [self.n_net, self.n_con, self.n_col, self.n_q, self.n_up, self.n_cloud]
        keys = ["devices", "diagnostics", "attendance", "queue", "queue", "diagnostics"]
        for i, node in enumerate(nodes):
            row.addWidget(node, 1)
            node.clicked.connect(lambda k=keys[i]: self.node_clicked.emit(k))
            if i < 5:
                row.addWidget(self.arrows[i])
        layout.addLayout(row)

    def apply(self, dash: dict) -> None:
        net = str(dash.get("device_network_status") or "unknown")
        total = int(dash.get("total_devices") or 0)
        online = int(dash.get("connected_devices") or 0)
        offline = int(dash.get("offline_devices") or 0)
        running = str(dash.get("connector_status") or "") == "running"
        pending = int(dash.get("queue_count") or 0)
        uploading = int(dash.get("uploading_count") or 0)
        failed = int(dash.get("failed_uploads") or 0)
        cloud = str(dash.get("cloud_status") or "unknown")
        avg = dash.get("average_upload_ms")
        today = int(dash.get("today_punches") or 0)

        net_ok = net.lower() in {"connected", "online", "probing"} or online > 0
        connecting = int(dash.get("connecting_devices") or 0)
        if connecting or net.lower() in {"probing", "unknown", "connecting"}:
            net_meta = "Reconnecting" if total else "Discover a terminal"
            net_tone = "warning"
        elif offline:
            net_meta = f"{offline} offline"
            net_tone = "error"
        elif net_ok:
            net_meta = human_status(net) if total else "Discover a terminal"
            net_tone = "ok"
        else:
            net_meta = human_status(net) if total else "Discover a terminal"
            net_tone = "warning"
        self.n_net.set_node(
            f"{online} / {total} online" if total else "No devices",
            net_meta,
            net_tone,
        )
        self.n_con.set_node(
            "Running" if running else "Stopped",
            "Engine process",
            "ok" if running else "error",
        )
        collecting = running and (online > 0 or today > 0)
        self.n_col.set_node(
            "Receiving" if collecting else ("Idle" if running else "Stopped"),
            f"{today} today" if today else "No punches today",
            "ok" if collecting or (running and total == 0) else ("warning" if running else "error"),
        )
        q_status = _queue_tone(pending, failed, int(dash.get("queue_alert_threshold") or 0))
        self.n_q.set_node(
            f"{pending} pending" if pending else "Clear",
            f"{uploading} processing" if uploading else "No backlog",
            q_status,
        )
        self.n_q.setToolTip("Attendance records waiting for cloud upload.")
        up_ok = running and failed == 0
        self.n_up.set_node(
            "Healthy" if up_ok else (f"{failed} failed" if failed else "Stopped"),
            "Uploader" if up_ok else "Retrying locally stored punches" if failed else "Not running",
            "ok" if up_ok else ("error" if failed or not running else "warning"),
        )
        cloud_status = _cloud_tone(cloud)
        latency = _ms(avg)
        self.n_cloud.set_node(human_status(cloud), f"Upload {latency}", cloud_status)

        self.arrows[0].set_healthy(net_ok and running)
        self.arrows[0].set_active(net_ok and running and online > 0)
        self.arrows[1].set_healthy(running)
        self.arrows[1].set_active(running and (online > 0 or today > 0))
        self.arrows[2].set_healthy(running)
        self.arrows[2].set_active(running and (today > 0 or pending > 0))
        self.arrows[3].set_healthy(running and failed == 0)
        self.arrows[3].set_active(pending > 0 or uploading > 0)
        self.arrows[4].set_healthy(cloud_status == "ok")
        self.arrows[4].set_active(
            cloud_status == "ok" and (uploading > 0 or int(dash.get("uploaded_today") or 0) > 0)
        )


class MetricColumn(QFrame):
    clicked = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("MetricColumn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(6)
        head = QLabel(title.upper())
        head.setObjectName("SectionKicker")
        layout.addWidget(head)
        self._body = QVBoxLayout()
        self._body.setSpacing(4)
        layout.addLayout(self._body)

    def set_rows(self, rows: list[tuple[str, str, str, str | None]]) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                lay = item.layout()
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        w.deleteLater()
        for row in rows:
            label, value, status = row[0], row[1], row[2]
            tooltip = row[3] if len(row) > 3 else None
            line = QHBoxLayout()
            cap = QLabel(label)
            cap.setObjectName("KpiHint")
            val = QLabel(value)
            val.setObjectName("RowTitle")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            val.setStyleSheet(f"color: {status_color(status)};")
            if tooltip:
                cap.setToolTip(tooltip)
                val.setToolTip(tooltip)
            line.addWidget(cap, 1)
            line.addWidget(val)
            self._body.addLayout(line)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class MetricsOverview(QFrame):
    group_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MetricStrip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(0)
        self.system = MetricColumn("System")
        self.devices = MetricColumn("Devices")
        self.attendance = MetricColumn("Attendance")
        self.performance = MetricColumn("Performance")
        for col, key in (
            (self.system, "diagnostics"),
            (self.devices, "devices"),
            (self.attendance, "attendance"),
            (self.performance, "queue"),
        ):
            layout.addWidget(col, 1)
            col.clicked.connect(lambda k=key: self.group_clicked.emit(k))
            if col is not self.performance:
                line = QFrame()
                line.setFixedWidth(1)
                line.setStyleSheet("background: #E8EDF2;")
                layout.addWidget(line)

    def apply(self, dash: dict) -> None:
        cloud = str(dash.get("cloud_status") or "unknown")
        inet = str(dash.get("internet_status") or "unknown")
        running = str(dash.get("connector_status") or "unknown")
        protocol = str(dash.get("device_protocol_status") or "unknown")
        self.system.set_rows(
            [
                ("Connector", human_status(running), "ok" if running == "running" else "warning", None),
                ("Internet", human_status(inet), "ok" if inet.lower() == "online" else "error", None),
                ("Cloud", human_status(cloud), _cloud_tone(cloud), None),
                ("Protocol", human_status(protocol), "ok" if protocol.lower() in {"connected", "healthy"} else "warning", None),
            ]
        )
        total = int(dash.get("total_devices") or 0)
        online = int(dash.get("connected_devices") or 0)
        offline = int(dash.get("offline_devices") or 0)
        self.devices.set_rows(
            [
                ("Total", str(total), "info", None),
                ("Online", str(online), "ok" if online or not total else "warning", None),
                ("Offline", str(offline), "error" if offline else "ok", None),
            ]
        )
        today = int(dash.get("today_punches") or 0)
        pending = int(dash.get("queue_count") or 0)
        uploaded = int(dash.get("uploaded_today") or 0)
        failed = int(dash.get("failed_uploads") or 0)
        self.attendance.set_rows(
            [
                ("Today", str(today), "info", "Punches collected today."),
                ("Pending", str(pending), "warning" if pending else "ok", "Attendance records waiting for cloud upload."),
                ("Uploaded", str(uploaded), "ok", None),
                ("Failed", str(failed), "error" if failed else "ok", None),
            ]
        )
        avg = dash.get("average_upload_ms")
        p95 = dash.get("p95_upload_ms")
        last = format_datetime_display(dash.get("last_sync") or "") or "—"
        self.performance.set_rows(
            [
                ("Avg upload", _ms(avg) if avg else "—", "info", None),
                ("P95", _ms(p95) if p95 else "Not available", "info", "95% of upload requests completed within this time."),
                ("Last sync", last, "info", None),
            ]
        )


class SystemStatusStrip(QFrame):
    clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(SectionHeader("SYSTEM STATUS", "Connectivity at a glance"))
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        layout.addLayout(self.body)

    def apply(self, dash: dict) -> None:
        cloud = str(dash.get("cloud_status") or "unknown")
        inet = str(dash.get("internet_status") or "unknown")
        running = str(dash.get("connector_status") or "unknown")
        protocol = str(dash.get("device_protocol_status") or "unknown")
        total = int(dash.get("total_devices") or 0)
        online = int(dash.get("connected_devices") or 0)
        avg = dash.get("average_upload_ms")
        rows = [
            ("Internet", human_status(inet), "—" if inet.lower() != "online" else "Path reachable", "ok" if inet.lower() == "online" else "error", "diagnostics"),
            ("Brixigo Cloud", human_status(cloud), _ms(avg), _cloud_tone(cloud), "diagnostics"),
            ("Device network", f"{online} / {total} online" if total else "No devices", human_status(dash.get("device_network_status")), (
                "ok" if str(dash.get("device_network_status") or "").lower() in {"connected", "online"} and not int(dash.get("offline_devices") or 0)
                else ("warning" if int(dash.get("connecting_devices") or 0) or str(dash.get("device_network_status") or "").lower() in {"probing", "unknown"} else "error")
            ), "devices"),
            ("Connector engine", human_status(running), "Process", "ok" if running == "running" else "error", "diagnostics"),
            ("Device protocol", human_status(protocol), "Handshake", "ok" if protocol.lower() in {"connected", "healthy"} else "warning", "diagnostics"),
        ]
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for name, state, meta, status, key in rows:
            row = QFrame()
            row.setObjectName("PipelineStage")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 8, 0, 8)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {status_color(status)}; font-size: 10px;")
            title = QLabel(name)
            title.setObjectName("RowTitle")
            val = QLabel(state)
            val.setObjectName("RowTitle")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            extra = QLabel(meta)
            extra.setObjectName("RowMeta")
            extra.setAlignment(Qt.AlignmentFlag.AlignRight)
            extra.setMinimumWidth(88)
            rl.addWidget(dot)
            rl.addWidget(title, 1)
            rl.addWidget(val)
            rl.addWidget(extra)
            row.mouseReleaseEvent = lambda e, k=key, orig=row.mouseReleaseEvent: (
                self.clicked.emit(k) if e.button() == Qt.MouseButton.LeftButton else orig(e)
            )
            self.body.addWidget(row)


class AttendancePipeline(QFrame):
    stage_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.addWidget(SectionHeader("ATTENDANCE FLOW", "Received through to cloud"))
        self.row = QHBoxLayout()
        self.row.setSpacing(0)
        layout.addLayout(self.row)
        note = QLabel("Validated equals received — punches are stored only after local validation.")
        note.setObjectName("Meta")
        note.setWordWrap(True)
        layout.addWidget(note)

    def apply(self, dash: dict) -> None:
        received = int(dash.get("today_punches") or 0)
        queued = int(dash.get("queue_count") or 0)
        uploaded = int(dash.get("uploaded_today") or 0)
        failed = int(dash.get("failed_uploads") or 0)
        stages = [
            ("Received", received, "attendance", "info"),
            ("Validated", received, "attendance", "info"),
            ("Queued", queued, "queue", "warning" if queued else "ok"),
            ("Uploaded", uploaded, "attendance", "ok"),
        ]
        if failed:
            stages.append(("Failed", failed, "queue", "error"))
        while self.row.count():
            item = self.row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, (name, count, key, tone) in enumerate(stages):
            cell = QFrame()
            cell.setCursor(Qt.CursorShape.PointingHandCursor)
            cell.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(8, 6, 8, 6)
            cl.setSpacing(2)
            num = QLabel(str(count))
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setObjectName("KpiValue")
            num.setStyleSheet(f"color: {status_color(tone)};")
            cap = QLabel(name)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setObjectName("KpiLabel")
            cl.addWidget(num)
            cl.addWidget(cap)
            cell.setToolTip(f"{name}: {count}")
            cell.mouseReleaseEvent = lambda e, k=key, orig=cell.mouseReleaseEvent: (
                self.stage_clicked.emit(k) if e.button() == Qt.MouseButton.LeftButton else orig(e)
            )
            self.row.addWidget(cell, 1)
            if i < len(stages) - 1:
                arrow = QLabel("→")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setObjectName("Muted")
                arrow.setFixedWidth(18)
                self.row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)


class QueueStatusPanel(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.addWidget(SectionHeader("UPLOAD QUEUE", "Local records waiting for cloud"))
        self.pending = QLabel("0")
        self.pending.setObjectName("KpiValue")
        self.pending.setToolTip("Attendance records waiting for cloud upload.")
        cap = QLabel("Pending")
        cap.setObjectName("KpiHint")
        layout.addWidget(self.pending)
        layout.addWidget(cap)
        self.meta = QLabel("")
        self.meta.setObjectName("RowMeta")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)

    def apply(self, dash: dict) -> None:
        pending = int(dash.get("queue_count") or 0)
        uploading = int(dash.get("uploading_count") or 0)
        uploaded = int(dash.get("uploaded_today") or 0)
        failed = int(dash.get("failed_uploads") or 0)
        threshold = int(dash.get("queue_alert_threshold") or 0)
        tone = _queue_tone(pending, failed, threshold)
        self.pending.setText(str(pending))
        self.pending.setStyleSheet(f"color: {status_color(tone)};")
        if pending == 0 and failed == 0:
            self.meta.setText(f"Upload queue is clear.  {uploaded} uploaded today.")
        else:
            self.meta.setText(
                f"{uploading} processing   ·   {uploaded} uploaded today   ·   {failed} failed"
                + (f"   ·   alert at {threshold}" if threshold else "")
            )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class PipelineStageList(QFrame):
    clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(SectionHeader("CONNECTOR PIPELINE", "Collection to cloud"))
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        layout.addLayout(self.body)
        layout.addStretch()

    def apply(self, dash: dict) -> None:
        running = str(dash.get("connector_status") or "") == "running"
        pending = int(dash.get("queue_count") or 0)
        failed = int(dash.get("failed_uploads") or 0)
        cloud = str(dash.get("cloud_status") or "unknown")
        net = str(dash.get("device_network_status") or "unknown")
        stages = [
            ("Device network", human_status(net), "ok" if net.lower() in {"connected", "online"} else "warning", "devices"),
            ("Punch collector", "Healthy" if running else "Stopped", "ok" if running else "error", "diagnostics"),
            ("Local queue", f"{pending} pending", "warning" if pending else "ok", "queue"),
            ("Upload worker", "Healthy" if not failed else f"{failed} failed", "error" if failed else "ok", "queue"),
            ("Brixigo cloud", human_status(cloud), _cloud_tone(cloud), "diagnostics"),
        ]
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for name, state, status, key in stages:
            row = QFrame()
            row.setObjectName("PipelineStage")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 8, 0, 8)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {status_color(status)}; font-size: 10px;")
            title = QLabel(name)
            title.setObjectName("RowTitle")
            val = QLabel(state)
            val.setObjectName("RowMeta")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            val.setStyleSheet(f"color: {status_color(status)};")
            rl.addWidget(dot)
            rl.addWidget(title, 1)
            rl.addWidget(val)
            row.mouseReleaseEvent = lambda e, k=key, orig=row.mouseReleaseEvent: (
                self.clicked.emit(k) if e.button() == Qt.MouseButton.LeftButton else orig(e)
            )
            self.body.addWidget(row)


class ClockMismatchPanel(QFrame):
    sync_clicked = Signal()
    details_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WarningPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        kicker = QLabel("DEVICE CLOCK MISMATCH")
        kicker.setObjectName("SectionKicker")
        kicker.setStyleSheet("color: #A15C00;")
        self.title = QLabel()
        self.title.setObjectName("PanelTitle")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setObjectName("Muted")
        clocks = QHBoxLayout()
        self.device_time = QLabel()
        self.server_time = QLabel()
        self.diff = QLabel()
        for lab in (self.device_time, self.server_time, self.diff):
            lab.setObjectName("RowTitle")
            lab.setWordWrap(True)
        clocks.addWidget(self.device_time, 1)
        clocks.addWidget(self.server_time, 1)
        clocks.addWidget(self.diff, 1)
        row = QHBoxLayout()
        self.btn_action = QPushButton("Sync device time")
        self.btn_details = QPushButton("View diagnostics")
        self.btn_details.setObjectName("Secondary")
        self.btn_action.clicked.connect(self.sync_clicked.emit)
        self.btn_details.clicked.connect(self.details_clicked.emit)
        row.addWidget(self.btn_action)
        row.addWidget(self.btn_details)
        row.addStretch()
        layout.addWidget(kicker)
        layout.addWidget(self.title)
        layout.addLayout(clocks)
        layout.addWidget(self.body)
        layout.addLayout(row)
        self.hide()
        self._device_id = ""

    @property
    def device_id(self) -> str:
        return self._device_id

    def set_mismatch(self, payload: dict) -> None:
        if not payload or not (payload.get("critical") or payload.get("samples")):
            self.hide()
            self._device_id = ""
            return
        samples = payload.get("samples") or [{}]
        sample = samples[0] or {}
        clock = sample.get("clock") or {}
        name = sample.get("device_name") or "Device"
        self._device_id = str(sample.get("device_id") or "")
        count = int(payload.get("count") or len(samples) or 0)
        extra = f" · {count} affected punch{'es' if count != 1 else ''}" if count else ""
        self.title.setText(f"{name}{extra}")
        device_ts = clock.get("device_timestamp") or sample.get("device_timestamp") or "—"
        server_ts = clock.get("server_received_at") or sample.get("created_at") or "—"
        self.device_time.setText(
            f"Device time\n{format_clock_timestamp_display(device_ts) or device_ts}"
        )
        self.server_time.setText(
            f"Server time\n{format_clock_timestamp_display(server_ts) or server_ts}"
        )
        self.diff.setText(f"Difference\n{clock.get('difference_label') or '—'}")
        self.body.setText(
            "Attendance timestamps may be inaccurate. Original device timestamps are retained. "
            "Device clock is kept in sync. Attendance always uses corrected calendar dates (never year-2000 stamps)."
        )
        self.show()


class OperationalBanner(QFrame):
    action_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoPanel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.kicker = QLabel()
        self.kicker.setObjectName("SectionKicker")
        self.title = QLabel()
        self.title.setObjectName("PanelTitle")
        self.body = QLabel()
        self.body.setObjectName("Muted")
        self.body.setWordWrap(True)
        col.addWidget(self.kicker)
        col.addWidget(self.title)
        col.addWidget(self.body)
        layout.addLayout(col, 1)
        self.btn = QPushButton("View")
        self.btn.setObjectName("Secondary")
        self.btn.clicked.connect(self.action_clicked.emit)
        layout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.hide()

    def set_banner(self, *, kind: str, title: str, body: str, action: str) -> None:
        if kind == "critical":
            self.setObjectName("CriticalPanel")
        elif kind == "warning":
            self.setObjectName("WarningPanel")
        else:
            self.setObjectName("InfoPanel")
        self.style().unpolish(self)
        self.style().polish(self)
        self.kicker.setText(kind.upper())
        self.title.setText(title)
        self.body.setText(body)
        self.btn.setText(action)
        self.show()


class DeviceHealthRow(QFrame):
    clicked = Signal(str)
    sync_clicked = Signal(str)
    diagnostics_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DeviceRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._device_id = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        self.dot = QLabel("●")
        self.name = QLabel()
        self.name.setObjectName("RowTitle")
        self.meta = QLabel()
        self.meta.setObjectName("RowMeta")
        self.stat = QLabel()
        self.stat.setObjectName("RowMeta")
        self.stat.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.dot)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(self.name)
        col.addWidget(self.meta)
        layout.addLayout(col, 1)
        layout.addWidget(self.stat)
        self.btn_sync = QPushButton("Sync")
        self.btn_sync.setObjectName("Ghost")
        self.btn_diag = QPushButton("Diagnostics")
        self.btn_diag.setObjectName("Ghost")
        self.btn_details = QPushButton("Details")
        self.btn_details.setObjectName("Ghost")
        for b in (self.btn_sync, self.btn_diag, self.btn_details):
            b.hide()
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(b)
        self.btn_sync.clicked.connect(lambda: self.sync_clicked.emit(self._device_id))
        self.btn_diag.clicked.connect(lambda: self.diagnostics_clicked.emit(self._device_id))
        self.btn_details.clicked.connect(lambda: self.clicked.emit(self._device_id))

    def set_device(self, device: dict, *, latency_ms: float | None, punches_today: int = 0) -> None:
        self._device_id = str(device.get("id") or "")
        name = device.get("name") or "Unnamed device"
        status = str(device.get("status") or "unknown")
        if not device.get("enabled", True):
            status = "disabled"
        online = status.lower() in {"online", "degraded"}
        self.dot.setStyleSheet(f"color: {status_color(status)};")
        self.name.setText(name)
        ip = device.get("ip_address") or "—"
        port = device.get("port") or ""
        last = format_datetime_display(device.get("last_sync_at") or "") or "Never synced"
        addr = f"{ip}{(':' + str(port)) if port else ''}"
        self.meta.setText(f"{human_status(status)}  ·  {addr}  ·  Last sync {last}")
        lat = f"{float(latency_ms):.0f} ms" if latency_ms else "—"
        self.stat.setText(f"{punches_today} today  ·  {lat}")
        self.setToolTip(self.meta.text())
        self.setAccessibleName(f"{name} {status}")
        if not online and status != "disabled":
            self.setStyleSheet("QFrame#DeviceRow { background: #F8EBE9; }")
        else:
            self.setStyleSheet("")

    def enterEvent(self, event) -> None:  # noqa: N802
        for b in (self.btn_sync, self.btn_diag, self.btn_details):
            b.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        for b in (self.btn_sync, self.btn_diag, self.btn_details):
            b.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._device_id:
            self.clicked.emit(self._device_id)
        super().mouseReleaseEvent(event)


class DeviceOverview(QFrame):
    view_all = Signal()
    device_clicked = Signal(str)
    sync_clicked = Signal(str)
    diagnostics_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.header = SectionHeader("DEVICE HEALTH", "Fleet")
        self.header.action.setText("View all devices")
        self.header.action.clicked.connect(self.view_all.emit)
        layout.addWidget(self.header)
        self.summary = QLabel()
        self.summary.setObjectName("Muted")
        layout.addWidget(self.summary)
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        layout.addLayout(self.body)
        self.empty = EmptyState("No biometric devices discovered yet.")
        self.empty.hide()
        layout.addWidget(self.empty)
        self._branch = ""

    def set_branch(self, branch: str) -> None:
        self._branch = branch or ""

    def apply(self, dash: dict) -> None:
        devices = list(dash.get("device_snapshots") or [])
        if self._branch:
            devices = [d for d in devices if str(d.get("branch") or "") == self._branch]
        total = len(devices) if self._branch else int(dash.get("total_devices") or len(devices))
        online = sum(1 for d in devices if str(d.get("status") or "").lower() in {"online", "degraded"})
        offline = sum(1 for d in devices if str(d.get("status") or "").lower() not in {"online", "degraded", "disabled"})
        self.header.set_title(f"{total} device{'s' if total != 1 else ''}")
        self.summary.setText(f"{online} online   ·   {offline} offline")
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not devices:
            self.empty.show()
            self.empty.set_message("No biometric devices discovered yet.", "Use Discover devices to scan the local network.")
            return
        self.empty.hide()
        avg = dash.get("average_upload_ms")
        today_counts = dash.get("device_today_counts") or {}
        shown = devices[:8]
        for device in shown:
            row = DeviceHealthRow()
            row.set_device(
                device,
                latency_ms=avg,
                punches_today=int(today_counts.get(device.get("id")) or 0),
            )
            row.clicked.connect(self.device_clicked.emit)
            row.sync_clicked.connect(self.sync_clicked.emit)
            row.diagnostics_clicked.connect(self.diagnostics_clicked.emit)
            self.body.addWidget(row)
        if len(devices) > 8:
            more = QLabel(f"{len(devices) - 8} more in the fleet — view all devices.")
            more.setObjectName("Meta")
            self.body.addWidget(more)


class AlertItem(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AlertRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        self.bar = QFrame()
        self.bar.setFixedWidth(3)
        self.bar.setStyleSheet("background: #5C6B78; border-radius: 1px;")
        col = QVBoxLayout()
        col.setSpacing(1)
        self.sev = QLabel()
        self.sev.setObjectName("SectionKicker")
        self.title = QLabel()
        self.title.setObjectName("RowTitle")
        self.title.setWordWrap(True)
        self.meta = QLabel()
        self.meta.setObjectName("RowMeta")
        col.addWidget(self.sev)
        col.addWidget(self.title)
        col.addWidget(self.meta)
        layout.addWidget(self.bar)
        layout.addLayout(col, 1)

    def set_alert(self, alert: dict) -> None:
        sev = str(alert.get("severity") or "info")
        color = status_color(sev)
        self.bar.setStyleSheet(f"background: {color}; border-radius: 1px;")
        self.sev.setText(sev.upper())
        self.sev.setStyleSheet(f"color: {color};")
        self.title.setText(str(alert.get("title") or alert.get("alert_type") or "Alert"))
        when = format_datetime_display(alert.get("created_at") or "") or ""
        src = str(alert.get("device_id") or "System")
        self.meta.setText(f"{src[:18]}  ·  {when}" if when else src)
        self.setToolTip(str(alert.get("message") or self.title.text()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class AlertsPanel(QFrame):
    view_all = Signal()
    alert_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.header = SectionHeader("ATTENTION REQUIRED", "What needs action")
        self.header.action.setText("View all")
        self.header.action.clicked.connect(self.view_all.emit)
        layout.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        layout.addLayout(self.body)
        self.empty = EmptyState("All systems healthy")
        self.empty.hide()
        layout.addWidget(self.empty)

    def apply(self, dash: dict) -> None:
        alerts = [a for a in (dash.get("alerts") or []) if not a.get("acknowledged")]
        alerts.sort(key=lambda a: severity_rank(str(a.get("severity") or "")))
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not alerts:
            self.empty.show()
            self.empty.set_message("All systems healthy", "There are no open operational alerts.")
            return
        self.empty.hide()
        for alert in alerts[:5]:
            row = AlertItem()
            row.set_alert(alert)
            row.clicked.connect(self.alert_clicked.emit)
            self.body.addWidget(row)


class ActivityTimeline(QFrame):
    view_all = Signal()
    item_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.header = SectionHeader("LIVE ACTIVITY", "What happened")
        self.header.action.setText("View details")
        self.header.action.clicked.connect(self.view_all.emit)
        layout.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        layout.addLayout(self.body)
        self.empty = EmptyState("No connector events yet")
        self.empty.hide()
        layout.addWidget(self.empty)

    def apply(self, dash: dict) -> None:
        rows = list(dash.get("recent_activity") or [])[:8]
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not rows:
            self.empty.show()
            return
        self.empty.hide()
        for rec in rows:
            item = QFrame()
            item.setObjectName("ActivityRow")
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            hl = QHBoxLayout(item)
            hl.setContentsMargins(4, 7, 4, 7)
            when = format_datetime_display(rec.get("created_at") or "") or "—"
            time_l = QLabel(when[-8:] if len(when) >= 8 else when)
            time_l.setObjectName("Meta")
            time_l.setFixedWidth(64)
            ok = bool(rec.get("success"))
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {status_color('ok' if ok else 'error')};")
            col = QVBoxLayout()
            col.setSpacing(0)
            title = QLabel(_activity_title(rec))
            title.setObjectName("RowTitle")
            meta = QLabel(str(rec.get("message") or "")[:90] or f"{rec.get('records_count') or 0} record(s)")
            meta.setObjectName("RowMeta")
            col.addWidget(title)
            col.addWidget(meta)
            hl.addWidget(time_l)
            hl.addWidget(dot)
            hl.addLayout(col, 1)
            item.mouseReleaseEvent = lambda e, orig=item.mouseReleaseEvent: (
                self.item_clicked.emit() if e.button() == Qt.MouseButton.LeftButton else orig(e)
            )
            self.body.addWidget(item)


def _activity_title(rec: dict) -> str:
    action = str(rec.get("action") or "event").replace("_", " ").strip()
    labels = {
        "sync": "Device sync",
        "upload": "Upload completed",
        "poll": "Punch received",
        "heartbeat": "Device heartbeat",
        "test connection": "Device connection verified",
        "sync device time": "Device time synced",
        "discover": "Device discovery",
    }
    friendly = labels.get(action.lower(), action[:1].upper() + action[1:] if action else "Event")
    if rec.get("success") is False:
        return f"{friendly} failed"
    return friendly


class HealthBreakdown(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        note = QLabel("Operational summary — derived from live connector state")
        note.setObjectName("Meta")
        note.setToolTip(
            "Calculated from connectivity, device availability, upload success, latency and synchronization health."
        )
        layout.addWidget(note)
        self.bars: list[tuple[QLabel, QLabel, QFrame]] = []
        for name in ("Connectivity", "Device availability", "Upload success", "Latency", "Clock sync"):
            row = QHBoxLayout()
            cap = QLabel(name)
            cap.setObjectName("KpiHint")
            cap.setMinimumWidth(130)
            val = QLabel("—")
            val.setObjectName("KpiHint")
            val.setFixedWidth(36)
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            track = QFrame()
            track.setFixedHeight(6)
            track.setStyleSheet("background: #E8EDF2; border-radius: 3px;")
            row.addWidget(cap)
            row.addWidget(track, 1)
            row.addWidget(val)
            layout.addLayout(row)
            self.bars.append((cap, val, track))

    def set_health(self, health: dict) -> None:
        mapping = [
            health.get("connectivity"),
            health.get("device_availability"),
            health.get("upload_success"),
            health.get("latency"),
            health.get("clock_sync"),
        ]
        for (_, value, track), score in zip(self.bars, mapping):
            if score is None:
                value.setText("—")
                track.setStyleSheet("background: #E8EDF2; border-radius: 3px;")
                continue
            n = int(score)
            value.setText(f"{n}%")
            color = COLOR_OK if n >= 85 else (COLOR_WARN if n >= 60 else COLOR_CRIT)
            track.setStyleSheet(
                f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {color}, "
                f"stop:{n/100:.2f} {color}, stop:{min(1.0, n/100+0.001):.2f} #E8EDF2, stop:1 #E8EDF2); border-radius: 3px;"
            )


class UploadPerformancePanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(12)
        layout.addWidget(SectionHeader("UPLOAD PERFORMANCE", "Cloud transfer health"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)
        self.avg = QLabel("—")
        self.p95 = QLabel("—")
        self.fail = QLabel("—")
        self.rate = QLabel("—")
        items = (
            (self.avg, "Average", None, 0, 0),
            (self.p95, "P95", "95% of upload requests completed within this time.", 0, 1),
            (self.fail, "Failures", None, 1, 0),
            (self.rate, "Success rate", None, 1, 1),
        )
        for lab, cap, tip, r, c in items:
            lab.setObjectName("KpiValue")
            caption = QLabel(cap)
            caption.setObjectName("KpiLabel")
            if tip:
                lab.setToolTip(tip)
                caption.setToolTip(tip)
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(lab)
            col.addWidget(caption)
            grid.addLayout(col, r, c)
        layout.addLayout(grid)
        self.note = QLabel("")
        self.note.setObjectName("Meta")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def apply(self, dash: dict) -> None:
        avg = dash.get("average_upload_ms")
        p95 = dash.get("p95_upload_ms")
        failed = int(dash.get("failed_uploads") or 0)
        uploaded = int(dash.get("uploaded_today") or 0)
        denom = uploaded + failed
        self.avg.setText(_ms(avg) if avg else "—")
        self.p95.setText(_ms(p95) if p95 else "Not available")
        self.fail.setText(str(failed))
        self.fail.setStyleSheet(f"color: {status_color('error' if failed else 'ok')};")
        if denom <= 0:
            self.rate.setText("—")
            self.note.setText("No upload volume yet today.")
        else:
            self.rate.setText(f"{round(100.0 * uploaded / denom)}%")
            self.note.setText(f"{uploaded} uploaded today of {denom} completed attempts.")


class QuickActions(QFrame):
    sync_clicked = Signal()
    discover_clicked = Signal()
    diagnostics_clicked = Signal()
    alerts_clicked = Signal()
    queue_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GhostSurface")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.setToolTip(
            "Optional immediate pull. Punches are already fetched automatically every 1 minute."
        )
        self.btn_discover = QPushButton("Discover devices")
        self.btn_discover.setObjectName("Secondary")
        self.btn_diag = QPushButton("Run diagnostics")
        self.btn_diag.setObjectName("Secondary")
        self.btn_alerts = QPushButton("View alerts")
        self.btn_alerts.setObjectName("Secondary")
        self.btn_queue = QPushButton("View upload queue")
        self.btn_queue.setObjectName("Secondary")
        for b, sig in (
            (self.btn_sync, self.sync_clicked),
            (self.btn_discover, self.discover_clicked),
            (self.btn_diag, self.diagnostics_clicked),
            (self.btn_alerts, self.alerts_clicked),
            (self.btn_queue, self.queue_clicked),
        ):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(sig.emit)
            layout.addWidget(b)
        layout.addStretch()


def _legend_row(items: list[tuple[str, str]]) -> QWidget:
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(14)
    for lab, col in items:
        cell = QHBoxLayout()
        cell.setContentsMargins(0, 0, 0, 0)
        cell.setSpacing(6)
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {col}; border-radius: 2px; border: none;")
        name = QLabel(lab)
        name.setObjectName("KpiHint")
        cell.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        cell.addWidget(name)
        row.addLayout(cell)
    row.addStretch(1)
    return host


class _BarChartCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: list[dict] = []
        self._hover = -1
        self.setMinimumHeight(168)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_series(self, series: list[dict]) -> None:
        self._series = series or []
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._series:
            return
        chart = self.rect().adjusted(8, 6, -8, -22)
        n = len(self._series)
        group_w = chart.width() / max(n, 1)
        idx = int((event.position().x() - chart.left()) / group_w) if group_w else -1
        if 0 <= idx < n:
            p = self._series[idx]
            self.setToolTip(
                f"{p.get('day')}\nReceived {p.get('collected') or 0}  ·  "
                f"Uploaded {p.get('uploaded') or 0}  ·  Failed {p.get('failed') or 0}"
            )
            self._hover = idx
        else:
            self.setToolTip("")
            self._hover = -1
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        if not self._series:
            painter.setPen(qcolor(COLOR_MUTED))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No trend data for this period.")
            return
        chart = rect.adjusted(8, 6, -8, -22)
        keys = ("collected", "uploaded", "failed")
        colors = (qcolor("#1D4E89"), qcolor(COLOR_OK), qcolor(COLOR_CRIT))
        max_v = max(max(int(p.get(k) or 0) for k in keys) for p in self._series)
        max_v = max(max_v, 1)
        n = len(self._series)
        group_w = chart.width() / max(n, 1)
        bar_w = max(4.0, min(12.0, group_w / 4.5))
        for i, point in enumerate(self._series):
            x0 = chart.left() + i * group_w + group_w * 0.22
            for idx, key in enumerate(keys):
                value = int(point.get(key) or 0)
                h = (value / max_v) * max(chart.height() - 4, 1)
                bar = QRectF(x0 + idx * (bar_w + 3), chart.bottom() - h, bar_w, h)
                painter.fillRect(bar, colors[idx])
            painter.setPen(qcolor(COLOR_MUTED))
            label = str(point.get("day") or "")
            if len(label) > 5 and "T" not in label:
                label = label[-5:]
            font = QFont(self.font())
            font.setPixelSize(10)
            painter.setFont(font)
            painter.drawText(
                QRectF(chart.left() + i * group_w, chart.bottom() + 2, group_w, 18),
                Qt.AlignmentFlag.AlignHCenter,
                label,
            )


class SimpleBarChart(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        self._title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        self._title_lab = QLabel(title)
        self._title_lab.setObjectName("PanelTitle")
        self._title_lab.setWordWrap(True)
        layout.addWidget(self._title_lab)
        layout.addWidget(
            _legend_row([("Received", "#1D4E89"), ("Uploaded", COLOR_OK), ("Failed", COLOR_CRIT)])
        )
        self._canvas = _BarChartCanvas()
        layout.addWidget(self._canvas, 1)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(title)

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_lab.setText(title)
        self.setAccessibleName(title)

    def set_series(self, series: list[dict]) -> None:
        self._canvas.set_series(series)


class _DonutCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._online = 0
        self._offline = 0
        self._unknown = 0
        self.setFixedSize(132, 132)

    def set_counts(self, *, online: int, offline: int, unknown: int = 0) -> None:
        self._online = int(online)
        self._offline = int(offline)
        self._unknown = int(unknown)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = self._online + self._offline + self._unknown
        cx, cy, radius = 66, 66, 44
        ring = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        if total == 0:
            p.setPen(QPen(qcolor("#E8EDF2"), 14))
            p.drawArc(ring, 0, 360 * 16)
            p.setPen(qcolor(COLOR_MUTED))
            font = QFont(self.font())
            font.setPixelSize(12)
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "—")
            return
        parts = [
            (self._online, COLOR_OK),
            (self._offline, COLOR_CRIT),
            (self._unknown, COLOR_MUTED),
        ]
        start = 90 * 16
        for count, color in parts:
            span = int(count / total * 360 * 16)
            p.setPen(QPen(qcolor(color), 14))
            p.drawArc(ring, start, -span)
            start -= span
        p.setPen(qcolor(COLOR_INK))
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(18)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(total))


class DeviceStatusChart(QFrame):
    def __init__(self, title: str = "Device status", parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        self.setMinimumHeight(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)
        head = QLabel(title)
        head.setObjectName("PanelTitle")
        layout.addWidget(head)
        body = QHBoxLayout()
        body.setSpacing(16)
        self._canvas = _DonutCanvas()
        body.addWidget(self._canvas, 0, Qt.AlignmentFlag.AlignVCenter)
        legend = QVBoxLayout()
        legend.setSpacing(8)
        self._online_lab = QLabel("Online  0")
        self._offline_lab = QLabel("Offline  0")
        self._unknown_lab = QLabel("Unknown  0")
        self._empty = QLabel("No devices registered.")
        self._empty.setObjectName("Muted")
        self._empty.setWordWrap(True)
        self._empty.hide()
        for lab, color in (
            (self._online_lab, COLOR_OK),
            (self._offline_lab, COLOR_CRIT),
            (self._unknown_lab, COLOR_MUTED),
        ):
            lab.setObjectName("RowTitle")
            lab.setStyleSheet(f"color: {color};")
            legend.addWidget(lab)
        legend.addWidget(self._empty)
        legend.addStretch(1)
        body.addLayout(legend, 1)
        layout.addLayout(body, 1)
        self.setAccessibleName(title)

    def set_counts(self, *, online: int, offline: int, unknown: int = 0) -> None:
        self._canvas.set_counts(online=online, offline=offline, unknown=unknown)
        self._online_lab.setText(f"Online  {int(online)}")
        self._offline_lab.setText(f"Offline  {int(offline)}")
        self._unknown_lab.setText(f"Unknown  {int(unknown)}")
        empty = int(online) + int(offline) + int(unknown) == 0
        self._empty.setVisible(empty)
        self._online_lab.setVisible(not empty)
        self._offline_lab.setVisible(not empty)
        self._unknown_lab.setVisible(not empty)


class EmptyState(QLabel):
    def __init__(self, message: str, parent=None):
        super().__init__(message, parent)
        self.setObjectName("EmptyState")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)

    def set_message(self, title: str, detail: str = "") -> None:
        self.setText(f"{title}\n{detail}" if detail else title)


class ErrorSurface(QFrame):
    retry = Signal()
    diagnostics = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CriticalPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.title = QLabel("Unable to retrieve live status")
        self.title.setObjectName("ErrorTitle")
        self.body = QLabel("Potential cause: connector service unavailable.")
        self.body.setWordWrap(True)
        self.body.setObjectName("Muted")
        row = QHBoxLayout()
        retry = QPushButton("Retry")
        diag = QPushButton("Open diagnostics")
        diag.setObjectName("Secondary")
        retry.clicked.connect(self.retry.emit)
        diag.clicked.connect(self.diagnostics.emit)
        row.addWidget(retry)
        row.addWidget(diag)
        row.addStretch()
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addLayout(row)
        self.hide()

    def show_error(self, message: str) -> None:
        self.body.setText(message or "Potential cause: connector service unavailable.")
        self.show()


class SkeletonBlock(QFrame):
    def __init__(self, height: int = 88, parent=None):
        super().__init__(parent)
        self.setObjectName("Skeleton")
        self.setFixedHeight(height)


class KpiTile(QFrame):
    """Legacy compact metric — kept for non-dashboard pages."""

    def __init__(self, label: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        self.caption = QLabel(label)
        self.caption.setObjectName("KpiLabel")
        self.value = QLabel("—")
        self.value.setObjectName("KpiValue")
        self.hint = QLabel("")
        self.hint.setObjectName("KpiHint")
        self.status = StatusPill("—", "info")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        row = QHBoxLayout()
        row.addWidget(self.status)
        row.addWidget(self.hint, 1)
        layout.addLayout(row)
        if tooltip:
            self.setToolTip(tooltip)

    def set_metric(self, value: str, *, status: str = "info", hint: str = "", status_text: str = "") -> None:
        self.value.setText(value)
        self.hint.setText(hint)
        self.status.set_status(status_text or status, status)


class KpiGroup(QFrame):
    def __init__(self, title: str, tiles: list[KpiTile], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(title.upper())
        heading.setObjectName("KpiLabel")
        layout.addWidget(heading)
        row = QHBoxLayout()
        row.setSpacing(8)
        for tile in tiles:
            row.addWidget(tile, 1)
        layout.addLayout(row)


class HealthPanel(QFrame):
    """Compact health used on secondary pages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        layout = QHBoxLayout(self)
        self.breakdown = HealthBreakdown()
        layout.addWidget(self.breakdown)

    def set_health(self, health: dict) -> None:
        self.breakdown.set_health(health)


class AlertBanner(ClockMismatchPanel):
    action_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sync_clicked.connect(self.action_clicked.emit)


class NavRail(QFrame):
    navigated = Signal(str)

    GROUPS = (
        (
            "OPERATIONS",
            (
                ("dashboard", "Dashboard", "dashboard"),
                ("connector", "Connector", "connector"),
                ("devices", "Devices", "device"),
                ("attendance", "Attendance", "attendance"),
            ),
        ),
        (
            "INSIGHTS",
            (
                ("alerts", "Alerts", "alert"),
                ("activity", "Activity", "activity"),
                ("diagnostics", "Diagnostics", "diagnostics"),
            ),
        ),
        (
            "ADMINISTRATION",
            (
                ("enterprise", "Enterprise", "enterprise"),
                ("settings", "Settings", "settings"),
            ),
        ),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 16)
        layout.setSpacing(2)
        brand_row = QHBoxLayout()
        mark = QLabel("BX")
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(32, 32)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Brixigo")
        title.setObjectName("BrandTitle")
        sub = QLabel("Connector")
        sub.setObjectName("BrandSubtitle")
        titles.addWidget(title)
        titles.addWidget(sub)
        brand_row.addWidget(mark)
        brand_row.addLayout(titles, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(12)
        self._buttons: dict[str, QPushButton] = {}
        for section, items in self.GROUPS:
            lab = QLabel(section)
            lab.setObjectName("NavSection")
            layout.addWidget(lab)
            for key, label, icon in items:
                btn = QPushButton(label)
                btn.setObjectName("NavItem")
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setAutoExclusive(True)
                btn.setAccessibleName(label)
                btn.setIcon(_nav_icon(icon))
                btn.setIconSize(QSize(16, 16))
                btn.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
                layout.addWidget(btn)
                self._buttons[key] = btn
        layout.addStretch()
        foot = QFrame()
        foot.setObjectName("SidebarFooter")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(8, 8, 8, 8)
        fl.setSpacing(4)
        eng = QHBoxLayout()
        self.engine_dot = LivePulse()
        self.engine_label = QLabel("Engine  ·  Stopped")
        self.engine_label.setObjectName("BrandSubtitle")
        eng.addWidget(self.engine_dot)
        eng.addWidget(self.engine_label, 1)
        fl.addLayout(eng)
        self.role_label = QLabel("Administrator")
        self.role_label.setObjectName("BrandSubtitle")
        fl.addWidget(self.role_label)
        layout.addWidget(foot)

    def select(self, key: str) -> None:
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)

    def current_key(self) -> str:
        for k, btn in self._buttons.items():
            if btn.isChecked():
                return k
        return "connector"

    def set_runtime(self, *, running: bool, role: str = "admin") -> None:
        self.engine_dot.set_live(running)
        self.engine_label.setText("Engine  ·  Running" if running else "Engine  ·  Stopped")
        self.role_label.setText((role or "admin").title())


def _nav_icon(name: str) -> QIcon:
    glyph = IconGlyph(name, "#C5D4DE", 16)
    pix = QPixmap(16, 16)
    pix.fill(QColor(0, 0, 0, 0))
    glyph.render(pix)
    return QIcon(pix)
