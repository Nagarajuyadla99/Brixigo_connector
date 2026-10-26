"""Application settings for Brixigo Cloud Platform."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("brixigo.cloud")

_DEFAULT_JWT = "change-me-brixigo-cloud-jwt-secret-production"
_DEFAULT_HMAC = "change-me-brixigo-command-hmac-secret"
_DEFAULT_ADMIN_PASSWORD = "BrixigoAdmin!2026"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BRIXIGO_CLOUD_", extra="ignore")

    app_name: str = "Brixigo Cloud Device Management"
    app_version: str = "1.0.2"
    api_prefix: str = "/api/wf/biometric-enterprise"
    admin_api_prefix: str = "/api/v1"
    debug: bool = False
    # development | production — production rejects insecure defaults
    environment: str = "development"

    # Database — SQLite by default; set DATABASE_URL for PostgreSQL horizontal scale
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'brixigo_cloud.db').as_posix()}"

    # Auth
    jwt_secret: str = _DEFAULT_JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    command_hmac_secret: str = _DEFAULT_HMAC

    # CORS — comma-separated origins; empty = same-origin only in production
    cors_origins: str = ""

    # Redis (optional) — falls back to in-process queue when unavailable
    redis_url: str = ""
    enable_redis: bool = False

    # Storage
    storage_dir: Path = ROOT / "storage"
    firmware_dir: Path = ROOT / "storage" / "firmware"
    backup_dir: Path = ROOT / "storage" / "backups"
    artifact_dir: Path = ROOT / "storage" / "artifacts"
    report_dir: Path = ROOT / "storage" / "reports"

    # Policies
    heartbeat_stale_seconds: int = 180
    connector_offline_seconds: int = 300
    device_offline_seconds: int = 180
    queue_overflow_threshold: int = 5000
    default_command_ttl_seconds: int = 3600
    attendance_retention_days: int = 365
    audit_retention_days: int = 730

    # Rate limiting
    rate_limit_per_minute: int = 600

    # Bootstrap admin
    bootstrap_admin_email: str = "admin@brixigo.com"
    bootstrap_admin_password: str = _DEFAULT_ADMIN_PASSWORD
    bootstrap_org_name: str = "Brixigo Demo Organisation"

    # Brixigo.com HR & Payroll outbound sync (server-to-server). Empty URL disables push.
    # Local .env: BRIXIGO_CLOUD_HRMS_ENABLED / BRIXIGO_CLOUD_HRMS_BASE_URL / BRIXIGO_CLOUD_HRMS_API_KEY
    hrms_base_url: str = ""
    hrms_api_key: str = ""
    hrms_enabled: bool = False
    hrms_timeout_seconds: float = 20
    hrms_batch_size: int = 100
    hrms_max_attempts: int = 12

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod", "ga"}

    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if raw:
            return [o.strip() for o in raw.split(",") if o.strip()]
        if self.is_production:
            return []
        # Local dashboard / dev tools
        return [
            "http://127.0.0.1:8080",
            "http://localhost:8080",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ]

    def assert_secure_for_environment(self) -> None:
        problems: list[str] = []
        if self.jwt_secret == _DEFAULT_JWT:
            problems.append("BRIXIGO_CLOUD_JWT_SECRET is still the insecure default")
        if self.command_hmac_secret == _DEFAULT_HMAC:
            problems.append("BRIXIGO_CLOUD_COMMAND_HMAC_SECRET is still the insecure default")
        if self.bootstrap_admin_password == _DEFAULT_ADMIN_PASSWORD:
            problems.append("BRIXIGO_CLOUD_BOOTSTRAP_ADMIN_PASSWORD is still the insecure default")
        if len(self.jwt_secret) < 32:
            problems.append("BRIXIGO_CLOUD_JWT_SECRET must be at least 32 characters")
        if len(self.command_hmac_secret) < 16:
            problems.append("BRIXIGO_CLOUD_COMMAND_HMAC_SECRET must be at least 16 characters")

        if self.is_production:
            if problems:
                raise RuntimeError(
                    "Refusing to start cloud in production with insecure settings: "
                    + "; ".join(problems)
                )
            if not self.cors_origin_list():
                logger.warning(
                    "Production CORS origins empty — browser admin UI must be same-origin "
                    "or set BRIXIGO_CLOUD_CORS_ORIGINS"
                )
        elif problems:
            for item in problems:
                logger.warning("Security: %s (set BRIXIGO_CLOUD_ENVIRONMENT=production to enforce)", item)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for path in (
        settings.storage_dir,
        settings.firmware_dir,
        settings.backup_dir,
        settings.artifact_dir,
        settings.report_dir,
        ROOT / "data",
    ):
        path.mkdir(parents=True, exist_ok=True)
    return settings
