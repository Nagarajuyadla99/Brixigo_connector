"""Shared dashboard table layout helpers — full-width columns, no clipped cells."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QSizePolicy, QTableWidget

from api.json_codec import correct_unset_device_clock

_TZ_TAIL = re.compile(r"([+-]\d{2}:\d{2})$")
MIN_TABLE_ROWS = 10


def parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text in {"—", "-", "never"}:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        compact = text.replace("T", " ")
        try:
            parsed = datetime.fromisoformat(compact)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_datetime_display(value: object) -> str:
    """Local calendar date + time on one aligned line. Never show year-2000 clocks."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        text = str(value or "").strip()
        return "" if text in {"—", "-"} else text
    if parsed.year < 2010:
        corrected = correct_unset_device_clock(value, datetime.now(timezone.utc))
        if corrected is not None:
            return corrected.astimezone().strftime("%d %b %Y %H:%M:%S")
        return ""
    return parsed.astimezone().strftime("%d %b %Y %H:%M:%S")


def format_clock_timestamp_display(value: object) -> str:
    """Exact stamp for clock comparison. Do not rewrite year-2000 onto today."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        text = str(value or "").strip()
        return "" if text in {"—", "-"} else text
    return parsed.astimezone().strftime("%d %b %Y %H:%M:%S")


def format_punch_time_display(value: object, collected_at: object | None = None) -> str:
    """Readable punch time. Unset ZK clocks show the connector date and time."""
    parsed = parse_iso_datetime(value)
    # Modern stamps: format as-is (do not re-map onto "now").
    if parsed is not None and parsed.year >= 2010:
        return parsed.astimezone().strftime("%d %b %Y %H:%M:%S")

    corrected = correct_unset_device_clock(value, collected_at)
    if corrected is None and collected_at is None:
        corrected = correct_unset_device_clock(value, datetime.now(timezone.utc))
    if corrected is not None and corrected.year >= 2010:
        return corrected.astimezone().strftime("%d %b %Y %H:%M:%S")

    # Last resort: never render a year-2000/2002 string to the user.
    fallback = datetime.now(timezone.utc)
    if collected_at is not None:
        parsed_collected = parse_iso_datetime(collected_at)
        if parsed_collected is not None:
            fallback = parsed_collected
    return fallback.astimezone().strftime("%d %b %Y %H:%M:%S")


def format_punch_data_display(raw: object) -> str:
    """Compact device payload for a dedicated Data column."""
    payload = raw
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or "{}")
        except json.JSONDecodeError:
            return payload
    if not isinstance(payload, dict) or not payload:
        return ""
    preferred = (
        "uid",
        "punch",
        "status",
        "vendor",
        "terminal_code",
        "event_type",
        "verification_method",
        "device_timezone",
    )
    parts: list[str] = []
    seen: set[str] = set()
    for key in preferred:
        if key not in payload:
            continue
        val = payload.get(key)
        if val in (None, ""):
            continue
        parts.append(f"{key}={val}")
        seen.add(key)
    for key, val in payload.items():
        if key in seen or val in (None, ""):
            continue
        parts.append(f"{key}={val}")
    return "  ".join(str(p) for p in parts)


def configure_data_table(
    table: QTableWidget,
    *,
    min_section: int = 72,
    min_height: int = 160,
    stretch_last: bool = True,
    wrap: bool = True,
    row_height: int | None = None,
) -> None:
    """Stop Stretch-all from clipping Punch Time / Error / Data / Message."""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(stretch_last)
    header.setMinimumSectionSize(min_section)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setWordWrap(wrap)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setAlternatingRowColors(True)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    table.setMinimumHeight(min_height)
    vheader = table.verticalHeader()
    if wrap:
        vheader.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    else:
        vheader.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vheader.setDefaultSectionSize(row_height or 34)
    table.setSortingEnabled(False)


def fit_table_height(
    table: QTableWidget,
    *,
    min_height: int = 180,
    max_height: int = 720,
    row_height: int = 36,
) -> None:
    """Grow the table to its rows so the page scrollbar, not a trapped pane, is used."""
    header_h = table.horizontalHeader().height() or 36
    rows = max(int(table.rowCount()), 4)
    frame = table.frameWidth() * 2
    hbar = 18 if table.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff else 4
    height = header_h + rows * row_height + frame + hbar
    height = max(min_height, min(max_height, height))
    table.setMinimumHeight(height)
    table.setMaximumHeight(height)


def set_table_viewport_rows(
    table: QTableWidget,
    *,
    min_rows: int = MIN_TABLE_ROWS,
    max_rows: int = 16,
    row_height: int = 36,
) -> None:
    """Keep a stable viewport of at least ``min_rows`` so short tables are not clipped."""
    header_h = max(table.horizontalHeader().height(), 36)
    frame = table.frameWidth() * 2 + 6
    hbar = 22
    visible = max(int(min_rows), min(int(max_rows), max(int(table.rowCount()), int(min_rows))))
    height = header_h + visible * row_height + frame + hbar
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    table.setMinimumHeight(height)
    table.setMaximumHeight(height)


def apply_column_widths(table: QTableWidget, widths: dict[int, int]) -> None:
    for index, width in widths.items():
        if 0 <= index < table.columnCount():
            table.setColumnWidth(index, width)


def fit_visible_columns(table: QTableWidget, min_widths: dict[int, int] | None = None) -> None:
    """Size columns to cell contents, then enforce readable minimums."""
    table.resizeColumnsToContents()
    for index, width in (min_widths or {}).items():
        if 0 <= index < table.columnCount() and table.columnWidth(index) < width:
            table.setColumnWidth(index, width)


def comm_key_display(password: str | None) -> str:
    text = str(password or "").strip()
    return text if text else "0"
