"""Enterprise security — command signatures, replay protection, certificate checks."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models import RemoteCommand, utcnow
from storage.repositories.command_repo import NonceRepository
from storage.repositories.enterprise_repo import AuditRepository

logger = logging.getLogger("brixigo")


def _canonical_command_payload(command: RemoteCommand) -> str:
    body = {
        "cloud_command_id": command.cloud_command_id,
        "command_type": command.command_type,
        "device_id": command.device_id,
        "expires_at": command.expires_at,
        "issued_at": command.issued_at,
        "nonce": command.nonce,
        "payload": command.payload or {},
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def sign_command(command: RemoteCommand, secret: str) -> str:
    """Create HMAC-SHA256 signature for a remote command."""
    if not secret:
        raise ValueError("command signing secret is required")
    digest = hmac.new(
        secret.encode("utf-8"),
        _canonical_command_payload(command).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def verify_command_signature(command: RemoteCommand, secret: str) -> bool:
    """Return True only when secret is set and signature matches. Fail-closed."""
    if not secret:
        return False
    if not command.signature:
        return False
    expected = sign_command(command, secret)
    return hmac.compare_digest(expected, command.signature)


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class CommandSecurityGate:
    """Validates signature, expiry, and replay nonces before command execution."""

    def __init__(
        self,
        nonces: NonceRepository,
        audit: AuditRepository,
        *,
        signing_secret: str = "",
        max_skew_seconds: int = 300,
    ) -> None:
        self._nonces = nonces
        self._audit = audit
        self.signing_secret = signing_secret
        self.max_skew_seconds = max_skew_seconds

    def validate(self, command: RemoteCommand) -> tuple[bool, str]:
        if not command.command_type:
            return False, "Missing command_type"

        # Fail closed: unsigned / unsigned-secret environments cannot execute remote commands.
        if not self.signing_secret:
            self._audit.add(
                action="command_secret_missing",
                actor="cloud",
                success=False,
                message="Command signing secret not configured — rejecting command",
                resource_type="command",
                resource_id=command.cloud_command_id or command.id,
            )
            logger.error("Rejected remote command — command_signing_secret is not configured")
            return False, "Command signing secret not configured"

        if not verify_command_signature(command, self.signing_secret):
            self._audit.add(
                action="command_signature_rejected",
                actor="cloud",
                success=False,
                message="Invalid command signature",
                resource_type="command",
                resource_id=command.cloud_command_id or command.id,
            )
            return False, "Invalid command signature"

        if command.nonce:
            if self._nonces.seen(command.nonce):
                self._audit.add(
                    action="command_replay_rejected",
                    actor="cloud",
                    success=False,
                    message="Replay detected",
                    resource_type="command",
                    resource_id=command.cloud_command_id or command.id,
                    details={"nonce": command.nonce},
                )
                return False, "Replay detected — nonce already used"

        now = utcnow()
        expires = _parse_iso(command.expires_at)
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                return False, "Command expired"

        issued = _parse_iso(command.issued_at)
        if issued is not None:
            if issued.tzinfo is None:
                issued = issued.replace(tzinfo=timezone.utc)
            skew = abs((now - issued).total_seconds())
            if skew > self.max_skew_seconds + 86400:
                # Allow large clock skew only when expiry is present and valid.
                if expires is None:
                    return False, f"Command issued_at skew too large ({int(skew)}s)"

        if command.nonce:
            self._nonces.remember(command.nonce)

        self._audit.add(
            action="command_accepted",
            actor="cloud",
            success=True,
            message=f"Accepted {command.command_type}",
            resource_type="command",
            resource_id=command.cloud_command_id or command.id,
            details={"device_id": command.device_id},
        )
        return True, "ok"


def build_ssl_context(
    *,
    verify_ssl: bool = True,
    ca_bundle_path: str = "",
    client_cert_path: str = "",
    client_key_path: str = "",
    mtls_enabled: bool = False,
) -> ssl.SSLContext | bool | tuple:
    """
    Build TLS context for connector certificate validation and optional mTLS.
    Returns:
      - False to disable verification
      - True for default trust store (no custom context)
      - SSLContext when CA bundle and/or client certs are configured
      - (certfile, keyfile) tuple for httpx cert= when only client certs are set
    """
    if not verify_ssl and not mtls_enabled:
        return False

    needs_context = bool(ca_bundle_path) or (mtls_enabled and client_cert_path)
    if not needs_context:
        if mtls_enabled and client_cert_path:
            return (client_cert_path, client_key_path or client_cert_path)
        return True if verify_ssl else False

    if ca_bundle_path:
        path = Path(ca_bundle_path)
        if not path.exists():
            raise FileNotFoundError(f"CA bundle not found: {ca_bundle_path}")
        ctx = ssl.create_default_context(cafile=str(path))
    else:
        ctx = ssl.create_default_context()

    ctx.verify_mode = ssl.CERT_REQUIRED if verify_ssl else ssl.CERT_NONE
    ctx.check_hostname = bool(verify_ssl)

    if mtls_enabled and client_cert_path:
        cert = Path(client_cert_path)
        if not cert.exists():
            raise FileNotFoundError(f"Client certificate not found: {client_cert_path}")
        key = client_key_path or client_cert_path
        if client_key_path and not Path(client_key_path).exists():
            raise FileNotFoundError(f"Client key not found: {client_key_path}")
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return ctx


def validate_cloud_certificate(
    hostname: str,
    *,
    port: int = 443,
    ca_bundle_path: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Perform an explicit TLS handshake and report certificate validity."""
    import socket

    try:
        ctx = ssl.create_default_context()
        if ca_bundle_path:
            ctx.load_verify_locations(cafile=ca_bundle_path)
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                return {
                    "ok": True,
                    "hostname": hostname,
                    "protocol": ssock.version(),
                    "subject": dict(x[0] for x in cert.get("subject", ())),
                    "issuer": dict(x[0] for x in cert.get("issuer", ())),
                    "notAfter": cert.get("notAfter", ""),
                }
    except Exception as exc:
        return {"ok": False, "hostname": hostname, "error": str(exc)}
