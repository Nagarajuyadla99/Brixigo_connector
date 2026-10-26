"""Remote command and replay-nonce repositories."""

from __future__ import annotations

import json
from typing import Optional

from models import CommandStatus, RemoteCommand, new_id, utcnow
from storage.database import Database


class CommandRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, command: RemoteCommand) -> RemoteCommand:
        if not command.id:
            command.id = new_id()
        existing = None
        if command.cloud_command_id:
            existing = self.get_by_cloud_id(command.cloud_command_id)
        if existing:
            command.id = existing.id
            self._db.execute(
                """
                UPDATE remote_commands SET
                    command_type=?, device_id=?, payload_json=?, status=?,
                    result_message=?, result_json=?, signature=?, nonce=?,
                    issued_at=?, expires_at=?, completed_at=?
                WHERE id=?
                """,
                (
                    command.command_type,
                    command.device_id,
                    json.dumps(command.payload or {}),
                    command.status,
                    command.result_message,
                    json.dumps(command.result_payload or {}),
                    command.signature,
                    command.nonce,
                    command.issued_at,
                    command.expires_at,
                    command.completed_at,
                    command.id,
                ),
            )
            return command

        self._db.execute(
            """
            INSERT INTO remote_commands (
                id, cloud_command_id, command_type, device_id, payload_json,
                status, result_message, result_json, signature, nonce,
                issued_at, expires_at, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command.id,
                command.cloud_command_id,
                command.command_type,
                command.device_id,
                json.dumps(command.payload or {}),
                command.status,
                command.result_message,
                json.dumps(command.result_payload or {}),
                command.signature,
                command.nonce,
                command.issued_at,
                command.expires_at,
                command.created_at or utcnow().isoformat(),
                command.completed_at,
            ),
        )
        return command

    def get(self, command_id: str) -> Optional[RemoteCommand]:
        row = self._db.fetchone("SELECT * FROM remote_commands WHERE id = ?", (command_id,))
        return self._row_to_command(row) if row else None

    def get_by_cloud_id(self, cloud_command_id: str) -> Optional[RemoteCommand]:
        row = self._db.fetchone(
            "SELECT * FROM remote_commands WHERE cloud_command_id = ?",
            (cloud_command_id,),
        )
        return self._row_to_command(row) if row else None

    def list_pending(self, limit: int = 50) -> list[RemoteCommand]:
        rows = self._db.fetchall(
            """
            SELECT * FROM remote_commands
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (CommandStatus.PENDING.value, limit),
        )
        return [self._row_to_command(r) for r in rows]

    def update_status(
        self,
        command_id: str,
        *,
        status: str,
        result_message: str = "",
        result_payload: dict | None = None,
    ) -> None:
        completed = (
            utcnow().isoformat()
            if status
            in (
                CommandStatus.SUCCEEDED.value,
                CommandStatus.FAILED.value,
                CommandStatus.REJECTED.value,
            )
            else None
        )
        self._db.execute(
            """
            UPDATE remote_commands SET
                status=?, result_message=?, result_json=?, completed_at=COALESCE(?, completed_at)
            WHERE id=?
            """,
            (
                status,
                result_message[:2000],
                json.dumps(result_payload or {}),
                completed,
                command_id,
            ),
        )

    def recent(self, limit: int = 100) -> list[RemoteCommand]:
        rows = self._db.fetchall(
            "SELECT * FROM remote_commands ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_command(r) for r in rows]

    def _row_to_command(self, row) -> RemoteCommand:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            result = json.loads(row["result_json"] or "{}")
        except json.JSONDecodeError:
            result = {}
        return RemoteCommand(
            id=row["id"],
            cloud_command_id=row["cloud_command_id"] or "",
            command_type=row["command_type"],
            device_id=row["device_id"] or "",
            payload=payload if isinstance(payload, dict) else {},
            status=row["status"],
            result_message=row["result_message"] or "",
            result_payload=result if isinstance(result, dict) else {},
            signature=row["signature"] or "",
            nonce=row["nonce"] or "",
            issued_at=row["issued_at"] or "",
            expires_at=row["expires_at"] or "",
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )


class NonceRepository:
    """Replay protection store for signed cloud commands."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def seen(self, nonce: str) -> bool:
        row = self._db.fetchone("SELECT nonce FROM command_nonces WHERE nonce = ?", (nonce,))
        return row is not None

    def remember(self, nonce: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO command_nonces (nonce, created_at) VALUES (?, ?)",
            (nonce, utcnow().isoformat()),
        )

    def cleanup(self, max_rows: int = 10000) -> None:
        self._db.execute(
            """
            DELETE FROM command_nonces WHERE nonce IN (
                SELECT nonce FROM command_nonces
                ORDER BY created_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (max_rows,),
        )
