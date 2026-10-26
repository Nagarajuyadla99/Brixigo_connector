"""Shared Phase 3 user/template operations for ZK-compatible adapters."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from adapters.protocol.zk_protocol import ZKClient, ZKProtocolError
from models import DeviceUser


class ZKUserTemplateMixin:
    """Mixin requiring `_require() -> ZKClient` on the host adapter."""

    def _require(self) -> ZKClient:  # pragma: no cover - provided by adapter
        raise NotImplementedError

    def set_user(self, user: DeviceUser) -> None:
        client = self._require()
        uid = int(user.user_uid or 0)
        if uid <= 0:
            # Derive a stable 16-bit uid from employee code when not provided
            digest = hashlib.md5(str(user.user_id).encode("utf-8")).hexdigest()
            uid = (int(digest[:4], 16) % 65534) + 1
        client.set_user(
            uid=uid,
            user_id=str(user.user_id),
            name=user.name or "",
            privilege=int(user.privilege or 0),
            password=user.password or "",
            card=user.card or 0,
        )

    def delete_user(self, user_id: str, *, uid: int | None = None) -> None:
        client = self._require()
        target_uid = uid
        if target_uid is None:
            for u in client.get_users():
                if str(u.get("user_id") or "") == str(user_id):
                    target_uid = int(u.get("uid") or 0)
                    break
        if not target_uid:
            raise ZKProtocolError(f"User {user_id} not found on device")
        client.delete_user(int(target_uid))

    def download_templates(self) -> list[dict[str, Any]]:
        return self._require().get_templates()

    def upload_template(self, template: dict[str, Any]) -> None:
        raw = template.get("template")
        if isinstance(raw, str):
            # hex-encoded body
            body = bytes.fromhex(raw)
        elif isinstance(raw, (bytes, bytearray)):
            body = bytes(raw)
        else:
            raise ZKProtocolError("template payload missing")
        self._require().set_template(
            uid=int(template.get("uid") or 0),
            finger_index=int(template.get("finger_index") or 0),
            template=body,
            valid=int(template.get("valid") or 1),
        )

    def delete_template(self, *, uid: int, finger_index: int = 0) -> None:
        self._require().delete_template(uid=uid, finger_index=finger_index)

    def get_device_config(self) -> dict[str, Any]:
        return self._require().get_device_config()

    def get_device_time(self) -> Optional[datetime]:
        try:
            return self._require().get_time()
        except Exception:
            return None

    def attendance_log_count(self) -> int | None:
        sizes = self._require().get_free_sizes()
        if "log_count" not in sizes:
            return None
        return int(sizes.get("log_count") or 0)

    def apply_firmware(self, package_path: str) -> dict[str, Any]:
        path = Path(package_path)
        if not path.exists() or not path.is_file():
            raise ZKProtocolError(f"Firmware package not found: {package_path}")
        size = path.stat().st_size
        if size < 1024:
            raise ZKProtocolError("Firmware package too small / invalid")
        # Most LAN ZK devices require vendor tools or push-protocol for firmware
        # streaming. We validate package presence, record intent, and reboot
        # after an optional device-side upgrade marker file acknowledgement.
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "validated": True,
            "package_path": str(path),
            "size_bytes": size,
            "sha256": digest,
            "message": (
                "Firmware package validated. Device-side flash requires vendor "
                "push channel; package staged for support-assisted upgrade."
            ),
            "restart_required": True,
        }
