"""Brixigo Connector design tokens — shared by QSS and painted widgets."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from PySide6.QtGui import QColor

from ui.table_utils import parse_iso_datetime

# Surfaces
COLOR_CANVAS = "#E8EDF2"
COLOR_SIDEBAR = "#0B1C28"
COLOR_SIDEBAR_HOVER = "#143044"
COLOR_SIDEBAR_ACTIVE = "#1A3A4E"
COLOR_HERO = "#0E2433"
COLOR_SURFACE = "#FFFFFF"
COLOR_SURFACE_SUBTLE = "#F4F7F9"
COLOR_INK = "#12202B"
COLOR_INK_SOFT = "#F4F8FB"
COLOR_MUTED = "#5C6B78"
COLOR_MUTED_DARK = "#8FA3B3"
COLOR_LINE = "#D3DCE4"
COLOR_LINE_STRONG = "#B7C4D0"
COLOR_ACCENT = "#2A8A9E"

# Semantic — green is health, not decoration
COLOR_OK = "#0F6E56"
COLOR_OK_BG = "#E6F4EF"
COLOR_WARN = "#A15C00"
COLOR_WARN_BG = "#FBF3E4"
COLOR_CRIT = "#B42318"
COLOR_CRIT_BG = "#F8EBE9"
COLOR_INFO = "#1D4E89"
COLOR_INFO_BG = "#E8F0F8"
COLOR_NEUTRAL = "#5C6B78"

STATUS_COLORS = {
    "healthy": COLOR_OK,
    "ok": COLOR_OK,
    "operational": COLOR_OK,
    "online": COLOR_OK,
    "connected": COLOR_OK,
    "running": COLOR_OK,
    "uploaded": COLOR_OK,
    "success": COLOR_OK,
    "warning": COLOR_WARN,
    "warn": COLOR_WARN,
    "degraded": COLOR_WARN,
    "pending": COLOR_WARN,
    "slow": COLOR_WARN,
    "attention required": COLOR_WARN,
    "critical": COLOR_CRIT,
    "error": COLOR_CRIT,
    "offline": COLOR_CRIT,
    "failed": COLOR_CRIT,
    "stopped": COLOR_CRIT,
    "unavailable": COLOR_CRIT,
    "timeout": COLOR_CRIT,
    "tls_error": COLOR_CRIT,
    "auth_error": COLOR_CRIT,
    "info": COLOR_INFO,
    "unknown": COLOR_NEUTRAL,
    "not_reported": COLOR_NEUTRAL,
    "disabled": COLOR_NEUTRAL,
}


def qcolor(value: str) -> QColor:
    return QColor(value)


def status_color(status: str) -> str:
    return STATUS_COLORS.get(str(status or "").strip().lower(), COLOR_NEUTRAL)


def status_bg(status: str) -> str:
    tone = str(status or "").strip().lower()
    if tone in {"healthy", "ok", "operational", "online", "connected", "running", "uploaded", "success"}:
        return COLOR_OK_BG
    if tone in {"warning", "warn", "degraded", "pending", "slow", "attention required"}:
        return COLOR_WARN_BG
    if tone in {"critical", "error", "offline", "failed", "stopped", "unavailable", "timeout", "tls_error", "auth_error"}:
        return COLOR_CRIT_BG
    if tone in {"info"}:
        return COLOR_INFO_BG
    return COLOR_SURFACE_SUBTLE


def human_status(value: object) -> str:
    text = str(value or "").strip().replace("_", " ")
    if not text or text in {"—", "-"}:
        return "—"
    return text.title()


def na(value: object, *, suffix: str = "") -> str:
    if value is None:
        return "—"
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "—", "-"}:
        return "—"
    return f"{text}{suffix}"


def format_uptime(started_at: object) -> str:
    parsed = parse_iso_datetime(started_at)
    if parsed is None:
        return "—"
    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def prefers_reduced_motion() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return enabled.value == 0
    except Exception:
        return False


def severity_rank(severity: str) -> int:
    tone = str(severity or "").strip().lower()
    if tone in {"critical", "error"}:
        return 0
    if tone in {"warning", "warn"}:
        return 1
    if tone in {"info"}:
        return 2
    return 3
