"""Production readiness checks for the on-premise connector agent."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from security.encryption import has_master_key_env, is_production_env

logger = logging.getLogger("brixigo")


@dataclass
class GuardResult:
    ok: bool
    errors: list[str]
    warnings: list[str]

    def raise_if_blocking(self) -> None:
        if self.errors:
            raise RuntimeError("; ".join(self.errors))


def validate_connector_settings(settings: Any, *, force_production: bool | None = None) -> GuardResult:
    """
    Validate runtime settings before engine start.
    Production profile blocks unsafe configuration; development warns only.

    Fresh installs (no organisation/api yet) stay non-blocking so the service
    can start and the tray UI can complete configuration. Once cloud credentials
    are present, production rules become hard failures.
    """
    production = is_production_env() if force_production is None else force_production
    errors: list[str] = []
    warnings: list[str] = []

    configured = bool(
        getattr(settings, "organisation_id", "") and getattr(settings, "api_key", "")
    )
    # Harden fully once the connector is registered/configured for a customer.
    enforce = production and configured

    if not getattr(settings, "command_signing_secret", ""):
        msg = (
            "command_signing_secret is empty — remote commands will be rejected "
            "(must match cloud BRIXIGO_CLOUD_COMMAND_HMAC_SECRET)"
        )
        if enforce:
            errors.append(msg)
        else:
            warnings.append(msg)

    if not getattr(settings, "verify_ssl", True):
        msg = "verify_ssl is disabled — TLS certificate validation is off"
        if production:
            errors.append(msg)
        else:
            warnings.append(msg)

    cloud_url = str(getattr(settings, "cloud_base_url", "") or "")
    if production and cloud_url.lower().startswith("http://"):
        errors.append("cloud_base_url must use HTTPS in production")
    if cloud_url and "://" not in cloud_url:
        errors.append("cloud_base_url is malformed — expected https://host")

    if not has_master_key_env():
        msg = (
            "BRIXIGO_CONNECTOR_MASTER_KEY is not set — config encryption uses machine-derived key"
        )
        if enforce:
            errors.append(msg)
        else:
            warnings.append(msg)

    if not configured:
        warnings.append("Cloud organisation_id / api_key not configured yet")

    for warning in warnings:
        logger.warning("Production guard: %s", warning)
    for error in errors:
        logger.error("Production guard: %s", error)

    return GuardResult(ok=not errors, errors=errors, warnings=warnings)
