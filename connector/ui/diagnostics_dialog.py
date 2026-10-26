"""Network & protocol diagnostics dialog."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.connector_engine import ConnectorEngine


class _DiagWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.finished.emit(self._fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class DiagnosticsDialog(QDialog):
    def __init__(self, engine: ConnectorEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Network & Protocol Diagnostics")
        self.resize(780, 600)
        self._thread: QThread | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Run diagnostics against devices, internet, DNS, cloud API, and ZK protocol handshake."
            )
        )

        row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.addItem("No device", None)
        for d in engine.device_manager.list_devices():
            self.device_combo.addItem(f"{d.name} ({d.ip_address})", d.id)
        row.addWidget(self.device_combo)

        self.btn_ping = QPushButton("Ping Device")
        self.btn_port = QPushButton("Check Port")
        self.btn_protocol = QPushButton("Test Protocol")
        self.btn_internet = QPushButton("Check Internet")
        self.btn_api = QPushButton("Check API")
        self.btn_dns = QPushButton("DNS Test")
        self.btn_latency = QPushButton("Latency Test")
        self.btn_trace = QPushButton("Traceroute")
        self.btn_full = QPushButton("Run Full Suite")
        for b in (
            self.btn_ping,
            self.btn_port,
            self.btn_protocol,
            self.btn_internet,
            self.btn_api,
            self.btn_dns,
            self.btn_latency,
            self.btn_trace,
            self.btn_full,
        ):
            b.setObjectName("Secondary")
            row.addWidget(b)
        layout.addLayout(row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.btn_ping.clicked.connect(self._ping)
        self.btn_port.clicked.connect(self._port)
        self.btn_protocol.clicked.connect(self._protocol)
        self.btn_internet.clicked.connect(
            lambda: self._run_async(lambda: self.engine.diagnostics.check_internet())
        )
        self.btn_api.clicked.connect(
            lambda: self._run_async(lambda: self.engine.diagnostics.check_api())
        )
        self.btn_dns.clicked.connect(
            lambda: self._run_async(lambda: self.engine.diagnostics.dns_test())
        )
        self.btn_latency.clicked.connect(
            lambda: self._run_async(lambda: self.engine.diagnostics.latency_test())
        )
        self.btn_trace.clicked.connect(self._trace)
        self.btn_full.clicked.connect(self._full)

    def _selected_device(self):
        device_id = self.device_combo.currentData()
        if not device_id:
            return None
        return self.engine.device_manager.get(device_id)

    def _show(self, result: dict) -> None:
        status = result.get("status") or ("OK" if result.get("ok") else "FAILED")
        preferred = [
            "device_ip",
            "port",
            "tcp_test",
            "protocol_test",
            "protocol_error",
            "authentication",
            "last_successful_protocol_session",
            "last_tcp_heartbeat",
            "failure_stage",
            "protocol_code",
            "message",
        ]
        lines = [f"Status: {status}"]
        seen = {"status"}
        for key in preferred:
            if key in result:
                lines.append(f"{key}: {result[key]}")
                seen.add(key)
        for key, value in result.items():
            if key in seen:
                continue
            lines.append(f"{key}: {value}")
        self.output.setPlainText("\n".join(lines))

    def _set_busy(self, busy: bool) -> None:
        for b in (
            self.btn_ping,
            self.btn_port,
            self.btn_protocol,
            self.btn_internet,
            self.btn_api,
            self.btn_dns,
            self.btn_latency,
            self.btn_trace,
            self.btn_full,
        ):
            b.setEnabled(not busy)
        if busy:
            self.output.setPlainText("Running…")

    def _run_async(self, fn) -> None:
        if self._thread and self._thread.isRunning():
            self.output.setPlainText("A diagnostic is already running.")
            return
        self._set_busy(True)
        thread = QThread(self)
        worker = _DiagWorker(fn)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _ok(result: dict) -> None:
            self._show(result)
            self._set_busy(False)
            thread.quit()

        def _err(message: str) -> None:
            self.output.setPlainText(f"Error: {message}")
            self._set_busy(False)
            thread.quit()

        worker.finished.connect(_ok)
        worker.failed.connect(_err)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        thread.start()

    def _ping(self) -> None:
        device = self._selected_device()
        if not device:
            self.output.setPlainText("Select a device first.")
            return
        self._run_async(lambda: self.engine.diagnostics.ping_device(device))

    def _port(self) -> None:
        device = self._selected_device()
        if not device:
            self.output.setPlainText("Select a device first.")
            return
        self._run_async(lambda: self.engine.diagnostics.check_device_port(device))

    def _protocol(self) -> None:
        device = self._selected_device()
        if not device:
            self.output.setPlainText("Select a device first.")
            return
        self._run_async(lambda: self.engine.diagnostics.protocol_handshake(device))

    def _trace(self) -> None:
        device = self._selected_device()
        host = device.ip_address if device else "8.8.8.8"
        self._run_async(lambda: self.engine.diagnostics.traceroute(host))

    def _full(self) -> None:
        device = self._selected_device()

        def _suite():
            suite = self.engine.diagnostics.run_full_suite(device)
            lines = []
            snap = self.engine.states.snapshot()
            lines.append("[connection_state]")
            lines.extend(f"  {row}" for row in snap.diagnostics_lines())
            if snap.resolved:
                lines.append(f"  headline: {snap.resolved.title}")
                lines.append("")
            for name, result in suite.items():
                ok = result.get("ok")
                status = result.get("status") or ("OK" if ok else "FAILED")
                lines.append(f"[{name}] {status}")
                for key, value in result.items():
                    if key in ("ok", "status", "output"):
                        continue
                    lines.append(f"  {key}: {value}")
                if result.get("output"):
                    lines.append(f"  output:\n{result['output']}")
                lines.append("")
            return {"ok": True, "status": "complete", "report": "\n".join(lines)}

        if self._thread and self._thread.isRunning():
            self.output.setPlainText("A diagnostic is already running.")
            return
        self._set_busy(True)
        thread = QThread(self)
        worker = _DiagWorker(_suite)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _show_suite(result: dict) -> None:
            self.output.setPlainText(str(result.get("report") or ""))
            self._set_busy(False)
            thread.quit()

        def _err(message: str) -> None:
            self.output.setPlainText(f"Error: {message}")
            self._set_busy(False)
            thread.quit()

        worker.finished.connect(_show_suite)
        worker.failed.connect(_err)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        thread.start()
