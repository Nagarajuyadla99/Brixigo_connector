"""Structured rotating logging for the connector agent."""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

LOGGERS = (
    "brixigo",
    "brixigo.connection",
    "brixigo.sync",
    "brixigo.upload",
    "brixigo.error",
    "brixigo.crash",
    "brixigo.heartbeat",
    "brixigo.health",
    "brixigo.ui",
    "brixigo.service",
    "brixigo.startup",
)

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization|bearer)\s*[=:]\s*([^\s,;]+)"), r"\1=***"),
    (re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9\-._~+/]+=*)"), r"\1***"),
    (re.compile(r"(?i)(\"?(?:api_key|jwt_token|password|command_signing_secret|X-Device-Token)\"?\s*:\s*\")([^\"]+)(\")"), r"\1***\3"),
]


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "device_id"):
            record.device_id = "-"
        if not hasattr(record, "connector_id"):
            record.connector_id = "-"
        return True


class RedactingFilter(logging.Filter):
    """Strip secrets from log messages before they hit disk/console."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            redacted = msg
            for pattern, repl in _SECRET_PATTERNS:
                redacted = pattern.sub(repl, redacted)
            if redacted != msg:
                record.msg = redacted
                record.args = ()
        except Exception:
            pass
        return True


def _make_handler(path: Path, level: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(path),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    handler.setLevel(level)
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | device=%(device_id)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter())
    return handler


def setup_logging(log_dir: Path, console: bool = True, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("brixigo")
    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False

    mapping = {
        "connection": ("connection.log", logging.INFO),
        "sync": ("sync.log", logging.INFO),
        "upload": ("upload.log", logging.INFO),
        "error": ("error.log", logging.WARNING),
        "crash": ("crash.log", logging.ERROR),
        "heartbeat": ("heartbeat.log", logging.INFO),
        "health": ("health.log", logging.INFO),
        "service": ("service.log", logging.INFO),
        "ui": ("ui.log", logging.INFO),
        "startup": ("startup.log", logging.INFO),
    }

    # Aggregate app log (DEBUG retained in file only when root level allows)
    root.addHandler(_make_handler(log_dir / "connector.log", logging.DEBUG))

    for name, (filename, file_level) in mapping.items():
        logger = logging.getLogger(f"brixigo.{name}")
        logger.setLevel(file_level)
        logger.handlers.clear()
        logger.propagate = True
        logger.addHandler(_make_handler(log_dir / filename, file_level))

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
        console_handler.addFilter(ContextFilter())
        console_handler.addFilter(RedactingFilter())
        root.addHandler(console_handler)

    # Capture uncaught exceptions
    def _excepthook(exc_type, exc, tb):
        crash = logging.getLogger("brixigo.crash")
        crash.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "Brixigo Connector — Unexpected Error",
                    f"{exc_type.__name__}: {exc}\n\nDetails were written to the crash log.",
                )
        except Exception:
            pass

    import sys

    sys.excepthook = _excepthook
