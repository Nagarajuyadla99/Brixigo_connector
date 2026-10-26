"""Key-value settings repository (runtime overrides)."""

from __future__ import annotations

from typing import Optional

from models import utcnow
from storage.database import Database


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._db.fetchone(
            "SELECT value FROM connector_settings WHERE key = ?", (key,)
        )
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self._db.execute(
            """
            INSERT INTO connector_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, utcnow().isoformat()),
        )

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM connector_settings WHERE key = ?", (key,))
