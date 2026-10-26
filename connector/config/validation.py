"""User-friendly configuration validation for connector settings."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def raise_if_invalid(self) -> None:
        if self.errors:
            raise ValueError("; ".join(self.errors))


_UUIDish = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$")


def validate_settings_dict(data: dict[str, Any], *, require_cloud: bool = False) -> ValidationResult:
    """Validate settings fields with clear operator-facing messages."""
    errors: list[str] = []
    warnings: list[str] = []

    org = str(data.get("organisation_id") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    connector_id = str(data.get("connector_id") or "").strip()
    cloud_url = str(data.get("cloud_base_url") or "").strip()

    if require_cloud or org or api_key:
        if not org:
            errors.append("Organisation ID is required.")
        if not api_key:
            errors.append("API Key / Device Token is required.")
        elif api_key in {"0", "00"}:
            errors.append(
                "API Key cannot be 0. That is the device comm key. "
                "Paste the Brixigo Cloud API key from the admin portal."
            )

    if connector_id and not (
        _UUIDish.match(connector_id)
        or re.match(r"^[A-Za-z0-9][A-Za-z0-9 _\-.]{1,127}$", connector_id)
    ):
        errors.append(
            "Connector ID is invalid. Use the UUID from Cloud → Connectors → Register "
            "(or 3–128 letters, numbers, spaces, dots, dashes, or underscores)."
        )

    if cloud_url:
        parsed = urlparse(cloud_url)
        if parsed.scheme not in ("https", "http"):
            errors.append("Cloud Base URL must start with https:// (or http:// for lab only).")
        elif not parsed.netloc:
            errors.append("Cloud Base URL is missing a host name.")
        elif parsed.scheme == "http":
            warnings.append("Cloud Base URL uses HTTP. Production installs must use HTTPS.")
    else:
        errors.append("Cloud Base URL is required.")

    port = data.get("port")
    if port is not None:
        try:
            p = int(port)
            if p < 1 or p > 65535:
                errors.append(f"Port {p} is invalid. Use a value between 1 and 65535.")
        except (TypeError, ValueError):
            errors.append("Port must be a number between 1 and 65535.")

    ip = data.get("ip_address")
    if ip is not None and str(ip).strip():
        ip_s = str(ip).strip()
        try:
            ipaddress.ip_address(ip_s)
        except ValueError:
            # Allow hostnames for rare deployments
            host = ip_s.split(".")[0]
            if not (_HOSTNAME.match(host) or "." in ip_s):
                errors.append(f"IP address '{ip_s}' is not a valid IPv4/IPv6 address.")

    batch = data.get("upload_batch_size")
    if batch is not None:
        try:
            b = int(batch)
            if b < 1 or b > 5000:
                errors.append("Upload batch size must be between 1 and 5000.")
        except (TypeError, ValueError):
            errors.append("Upload batch size must be a number.")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def validate_device_fields(
    *,
    name: str,
    ip_address: str,
    port: int,
    manufacturer: str,
    terminal_code: str = "",
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not (name or "").strip():
        errors.append("Device name is required.")
    if not (ip_address or "").strip():
        errors.append("Device IP address is required.")
    else:
        try:
            ipaddress.ip_address(ip_address.strip())
        except ValueError:
            errors.append(
                f"Device IP '{ip_address}' is invalid. Enter a valid IPv4 address (example: 192.168.1.50)."
            )
    try:
        p = int(port)
        if p < 1 or p > 65535:
            errors.append("Device port must be between 1 and 65535 (ZK/eSSL default is 4370).")
    except (TypeError, ValueError):
        errors.append("Device port must be a number (default 4370).")
    mfg = (manufacturer or "").strip().lower()
    if not mfg:
        errors.append("Manufacturer is required.")
    elif mfg in ("anviz", "matrix", "hikvision", "suprema", "realtime", "fingertec", "dahua"):
        warnings.append(
            f"Manufacturer '{mfg}' is not production-certified yet. Use zkteco or essl for live sites."
        )
    if terminal_code and len(terminal_code) > 64:
        errors.append("Terminal code must be 64 characters or fewer.")
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
