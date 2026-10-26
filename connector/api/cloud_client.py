"""Brixigo Cloud HTTP client — registration, push, heartbeat, config, commands."""

from __future__ import annotations

import gzip
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from api.json_codec import JSONSerializationError, dumps_bytes
from app.constants import (
    API_PREFIX,
    APP_VERSION,
    DEFAULT_HTTP_CONNECT_TIMEOUT,
    DEFAULT_HTTP_POOL_TIMEOUT,
    DEFAULT_HTTP_READ_TIMEOUT,
    DEFAULT_HTTP_REQUEST_TIMEOUT,
)
from models import AttendanceRecord, ConnectorSettings, HeartbeatSnapshot
from security.command_security import build_ssl_context
from services.connection_state import classify_transport_error
from services.retry_policy import is_transient_error_kind

logger = logging.getLogger("brixigo.upload")


class CloudAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
        error_kind: str = "network",
        duration_ms: float = 0.0,
        retry_count: int = 0,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.error_kind = error_kind
        self.duration_ms = duration_ms
        self.retry_count = retry_count

    @property
    def transient(self) -> bool:
        if self.status_code in (401, 403, 400, 404, 422):
            return False
        if self.error_kind == "serialization":
            return False
        if self.status_code is not None and self.status_code >= 500:
            return True
        return is_transient_error_kind(self.error_kind)


class CloudClient:
    """HTTPS client for Brixigo biometric-enterprise APIs.

    Uses a persistent session/connection pool. Certificate verification stays enabled
    unless the administrator explicitly disabled it in settings.
    """

    def __init__(self, settings: ConnectorSettings) -> None:
        self.settings = settings
        self._client: Optional[httpx.Client] = None
        self.on_credentials_updated: Optional[Callable[[ConnectorSettings], None]] = None
        self._auth_refreshing = False
        self._health_path: str | None = None

    def _base(self) -> str:
        return self.settings.cloud_base_url.rstrip("/") + API_PREFIX

    def _headers(self, *, device_token: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": f"BrixigoConnector/{APP_VERSION}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Connector-Version": APP_VERSION,
        }
        if self.settings.organisation_id:
            headers["X-Organisation-Id"] = self.settings.organisation_id
        if self.settings.connector_id:
            headers["X-Connector-Id"] = self.settings.connector_id
        if device_token or self.settings.api_key:
            headers["X-Device-Token"] = device_token or self.settings.api_key
        if self.settings.jwt_token:
            headers["Authorization"] = f"Bearer {self.settings.jwt_token}"
        return headers

    def _verify(self) -> Any:
        try:
            return build_ssl_context(
                verify_ssl=self.settings.verify_ssl,
                ca_bundle_path=self.settings.ca_bundle_path,
                client_cert_path=getattr(self.settings, "client_cert_path", "") or "",
                client_key_path=getattr(self.settings, "client_key_path", "") or "",
                mtls_enabled=bool(getattr(self.settings, "mtls_enabled", False)),
            )
        except FileNotFoundError as exc:
            from security.encryption import is_production_env

            if is_production_env() or bool(getattr(self.settings, "mtls_enabled", False)):
                logger.error("TLS material missing — failing closed: %s", exc)
                raise CloudAPIError(f"TLS material missing: {exc}") from exc
            logger.error(
                "TLS material missing — falling back to default verify=%s",
                self.settings.verify_ssl,
            )
            return self.settings.verify_ssl

    def _timeouts(self) -> httpx.Timeout:
        """Separate connect (includes TLS handshake), read, write, and pool budgets."""
        connect = max(
            DEFAULT_HTTP_CONNECT_TIMEOUT,
            float(getattr(self.settings, "http_connect_timeout", 0) or 0)
            or DEFAULT_HTTP_CONNECT_TIMEOUT,
        )
        read = max(
            DEFAULT_HTTP_READ_TIMEOUT,
            float(self.settings.request_timeout or DEFAULT_HTTP_REQUEST_TIMEOUT),
        )
        read = min(read, 60.0)
        connect = min(connect, 20.0)
        return httpx.Timeout(
            connect=connect,
            read=read,
            write=read,
            pool=DEFAULT_HTTP_POOL_TIMEOUT,
        )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            verify = self._verify()
            kwargs: dict[str, Any] = {
                "timeout": self._timeouts(),
                "follow_redirects": True,
                "limits": httpx.Limits(
                    max_keepalive_connections=10,
                    max_connections=20,
                    keepalive_expiry=45.0,
                ),
            }
            # httpx accepts SSLContext/bool for verify; cert tuple separately
            if isinstance(verify, tuple):
                kwargs["verify"] = bool(self.settings.verify_ssl)
                kwargs["cert"] = verify
            else:
                kwargs["verify"] = verify
            if self.settings.proxy_url:
                kwargs["proxy"] = self.settings.proxy_url
            try:
                self._client = httpx.Client(**kwargs)
            except TypeError:
                kwargs.pop("proxy", None)
                if self.settings.proxy_url:
                    kwargs["proxies"] = self.settings.proxy_url
                try:
                    self._client = httpx.Client(**kwargs)
                except TypeError:
                    kwargs.pop("limits", None)
                    self._client = httpx.Client(**kwargs)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def update_settings(self, settings: ConnectorSettings) -> None:
        self.settings = settings
        self.close()

    def internet_ok(self) -> bool:
        """
        Compatibility helper: API reachability with full TLS/connect budgets.

        Do not use this as a 5-second preflight gate. Prefer probe_api_health()
        for classification and the independent internet TCP check in ApiHealthMonitor.
        """
        result = self.probe_api_health()
        return bool(result.get("ok"))

    def probe_api_health(self) -> dict[str, Any]:
        """Classify Brixigo API health without affecting device connectivity state."""
        from api.health_probe import probe_liveness

        base = self.settings.cloud_base_url.rstrip("/")
        verify = self._verify()
        if isinstance(verify, tuple):
            verify = bool(self.settings.verify_ssl)
        timeout = min(
            20.0,
            max(10.0, float(getattr(self._timeouts(), "connect", 10) or 10) + 8.0),
        )
        if self.settings.proxy_url:
            return self._probe_api_health_httpx(base, timeout=timeout)

        result = probe_liveness(
            base,
            timeout=timeout,
            verify=verify,
            preferred_path=self._health_path,
        )
        path = result.get("health_path")
        if path:
            self._health_path = str(path)
        logger.info(
            "API_REQUEST endpoint=%s status=%s duration_ms=%.0f http=%s "
            "dns_ms=%.0f tcp_ms=%.0f tls_ms=%.0f ttfb_ms=%.0f",
            result.get("endpoint") or "/api/health",
            result.get("kind") or "unknown",
            float(result.get("latency_ms") or 0),
            result.get("status_code"),
            float(result.get("dns_ms") or 0),
            float(result.get("tcp_ms") or 0),
            float(result.get("tls_ms") or 0),
            float(result.get("ttfb_ms") or 0),
        )
        return result

    def _probe_api_health_httpx(self, base: str, *, timeout: float) -> dict[str, Any]:
        from app.constants import API_HEALTH_PATHS
        from urllib.parse import urlparse
        import socket

        path = self._health_path or API_HEALTH_PATHS[0]
        url = base + path
        parsed = urlparse(url)
        dns_ms = 0.0
        host = parsed.hostname or ""
        t0 = time.perf_counter()
        if host:
            try:
                socket.getaddrinfo(host, parsed.port or 443)
                dns_ms = (time.perf_counter() - t0) * 1000.0
            except OSError:
                dns_ms = (time.perf_counter() - t0) * 1000.0
        started = time.perf_counter()
        try:
            client = self._get_client()
            resp = client.get(url)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if resp.status_code == 404 and path != "/api/health":
                self._health_path = "/api/health"
            else:
                self._health_path = path
            ok = resp.status_code < 500 and resp.status_code not in (401, 403)
            kind = "ok"
            if resp.status_code in (401, 403):
                kind = "auth"
            elif resp.status_code >= 500:
                kind = "server"
            logger.info(
                "API_REQUEST endpoint=%s status=%s duration_ms=%.0f http=%s dns_ms=%.0f",
                path,
                kind,
                latency_ms,
                resp.status_code,
                dns_ms,
            )
            return {
                "ok": ok,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "dns_ms": dns_ms,
                "tcp_ms": 0.0,
                "tls_ms": 0.0,
                "ttfb_ms": latency_ms,
                "kind": kind,
                "url": url,
                "endpoint": path,
                "health_path": self._health_path,
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            kind = classify_transport_error(exc)
            logger.warning(
                "API_REQUEST endpoint=%s status=%s duration_ms=%.0f error=%s",
                path,
                kind,
                latency_ms,
                str(exc)[:200],
            )
            return {
                "ok": False,
                "status_code": None,
                "latency_ms": latency_ms,
                "dns_ms": dns_ms,
                "kind": kind,
                "error": str(exc),
                "url": url,
                "endpoint": path,
            }

    def register_connector(self, *, hostname: str, version: str) -> dict[str, Any]:
        url = f"{self._base()}/connectors/register"
        body = {
            "connector_id": self.settings.connector_id or None,
            "hostname": hostname,
            "version": version,
            "organisation_id": self.settings.organisation_id,
        }
        return self._request("POST", url, json_body=body)

    def download_configuration(self) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/config"
        return self._request("GET", url)

    def push_attendance(
        self,
        *,
        terminal_code: str,
        punches: list[dict[str, Any]],
        compress: bool = True,
        device_token: str | None = None,
        employees: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not terminal_code:
            raise CloudAPIError("terminal_code required for push")
        url = f"{self._base()}/connectors/{terminal_code}/push"
        body: dict[str, Any] = {"punches": punches, "source": "brixigo-connector", "version": APP_VERSION}
        if employees:
            body["employees"] = employees
        return self._request(
            "POST",
            url,
            json_body=body,
            compress=compress,
            extra_headers=self._headers(device_token=device_token),
        )

    def push_records_for_device(
        self,
        *,
        terminal_code: str,
        records: list[AttendanceRecord],
        device_token: str | None = None,
        manufacturer: str = "",
        device_name: str = "",
        branch: str = "",
        device_id: str = "",
        timezone: str = "",
        employee_names: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from contracts.payroll_punch import build_payroll_punch

        punches = []
        for r in records:
            code = str(r.device_employee_code or "").strip()
            punches.append(
                build_payroll_punch(
                    r,
                    terminal_code=terminal_code,
                    manufacturer=manufacturer or str((r.raw_payload or {}).get("vendor") or ""),
                    device_name=device_name,
                    organisation_id=self.settings.organisation_id,
                    connector_id=self.settings.connector_id,
                    branch=branch,
                    timezone=timezone,
                    employee_name=str((employee_names or {}).get(code) or ""),
                )
            )
        return self.push_attendance(
            terminal_code=terminal_code,
            punches=punches,
            device_token=device_token,
            employees=[
                {"employee_code": code, "name": name, "device_id": device_id, "device_name": device_name, "branch": branch}
                for code, name in (employee_names or {}).items()
                if str(name or "").strip()
            ],
        )

    def send_heartbeat(self, snapshot: HeartbeatSnapshot, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        connector_id = self.settings.connector_id or "unregistered"
        url = f"{self._base()}/connectors/{connector_id}/heartbeat"
        body = {
            "connector_version": snapshot.connector_version,
            "status": snapshot.status,
            "cpu_percent": snapshot.cpu_percent,
            "ram_percent": snapshot.ram_percent,
            "disk_percent": snapshot.disk_percent,
            "connected_devices": snapshot.connected_devices,
            "offline_devices": snapshot.offline_devices,
            "last_sync": snapshot.last_sync,
            "pending_queue": snapshot.pending_queue,
            "failed_uploads": snapshot.failed_uploads,
            "today_punches": snapshot.today_punches,
            "average_upload_ms": snapshot.average_upload_ms,
            "last_error": snapshot.last_error,
            "last_restart_at": snapshot.last_restart_at,
            "organisation_id": self.settings.organisation_id,
            "created_at": snapshot.created_at,
        }
        if extra:
            for key in (
                "internet_status",
                "server_status",
                "tls_status",
                "authentication_status",
                "sync_status",
                "connection_states",
                "employees",
            ):
                if key in extra:
                    body[key] = extra[key]
        return self._request("POST", url, json_body=body)

    def get_connector_status(self) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/status"
        return self._request("GET", url)

    def push_device_health(self, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/device-health"
        return self._request("POST", url, json_body={"snapshots": snapshots})

    def push_device_status(self, devices: list[dict[str, Any]]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/devices/status"
        return self._request("POST", url, json_body={"devices": devices})

    def fetch_commands(self) -> list[dict[str, Any]]:
        connector_id = self.settings.connector_id
        if not connector_id:
            return []
        url = f"{self._base()}/connectors/{connector_id}/commands"
        data = self._request("GET", url)
        commands = data.get("commands") if isinstance(data, dict) else None
        if isinstance(commands, list):
            return [c for c in commands if isinstance(c, dict)]
        if isinstance(data, list):
            return [c for c in data if isinstance(c, dict)]
        return []

    def acknowledge_command(
        self,
        *,
        command_id: str,
        status: str,
        message: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/commands/{command_id}/ack"
        body = {
            "status": status,
            "message": message,
            "result": result or {},
            "connector_version": APP_VERSION,
        }
        return self._request("POST", url, json_body=body)

    def push_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/alerts"
        return self._request("POST", url, json_body=alert)

    def push_monitoring_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Cloud monitoring feed — connector status, queue, version, last error.
        Used by cloud dashboards that monitor all connectors.
        """
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/monitoring"
        return self._request("POST", url, json_body=payload)

    def check_update(self) -> dict[str, Any]:
        url = f"{self._base()}/connectors/updates/latest"
        return self._request("GET", url, params={"version": APP_VERSION})

    def register_device(self, device_payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base()}/devices"
        return self._request("POST", url, json_body=device_payload)

    # ------------------------------------------------------------------
    # Phase 3 cloud APIs — additive, non-breaking
    # ------------------------------------------------------------------

    def fetch_employee_sync_events(self) -> dict[str, Any] | list:
        connector_id = self.settings.connector_id
        if not connector_id:
            return {"events": []}
        url = f"{self._base()}/connectors/{connector_id}/employees/sync"
        return self._request("GET", url)

    def push_employee_directory(self, employees: list[dict[str, Any]]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/employees/directory"
        return self._request("POST", url, json_body={"employees": employees, "users": employees})

    def acknowledge_employee_sync(self, event_id: str) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/employees/sync/{event_id}/ack"
        return self._request("POST", url, json_body={"status": "applied"})

    def download_device_policies(self, *, device_id: str = "") -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            return {}
        url = f"{self._base()}/connectors/{connector_id}/policies"
        params = {"device_id": device_id} if device_id else None
        return self._request("GET", url, params=params)

    def push_inventory(self, inventory: list[dict[str, Any]]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/inventory"
        return self._request("POST", url, json_body={"devices": inventory})

    def get_server_time(self) -> dict[str, Any]:
        url = f"{self._base()}/time"
        try:
            return self._request("GET", url)
        except CloudAPIError:
            return {}

    def push_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/notifications"
        return self._request("POST", url, json_body=payload)

    def push_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/reports"
        return self._request("POST", url, json_body=payload)

    def push_support_diagnostics(self, suite: dict[str, Any]) -> dict[str, Any]:
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/support/diagnostics"
        return self._request("POST", url, json_body=suite)

    def upload_support_artifact(
        self,
        *,
        artifact_type: str,
        file_path: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Notify cloud of a local support artifact. Binary upload uses multipart
        when the endpoint accepts it; otherwise metadata-only registration.
        """
        connector_id = self.settings.connector_id
        if not connector_id:
            raise CloudAPIError("connector_id not configured")
        url = f"{self._base()}/connectors/{connector_id}/support/artifacts"
        path = Path(file_path)
        body = {
            "artifact_type": artifact_type,
            "filename": path.name,
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "meta": meta or {},
            "local_path": str(path),
        }
        return self._request("POST", url, json_body=body)

    def fetch_dashboard_summary(self) -> dict[str, Any]:
        """Cloud dashboard aggregate for organizations/connectors/devices."""
        connector_id = self.settings.connector_id
        if not connector_id:
            return {}
        url = f"{self._base()}/connectors/{connector_id}/dashboard"
        return self._request("GET", url)

    def _recover_auth(self) -> bool:
        """Retry once after dropping an expired JWT. Device token is unchanged."""
        if self._auth_refreshing:
            return False
        jwt = self.settings.jwt_token
        if not jwt:
            return False
        self._auth_refreshing = True
        try:
            logger.warning("API auth expired — retrying with device token only (JWT not logged)")
            self.settings.jwt_token = ""
            if self.on_credentials_updated:
                self.on_credentials_updated(self.settings)
            return True
        except Exception as exc:
            logger.warning("Auth recovery failed: %s", exc)
            return False
        finally:
            self._auth_refreshing = False

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        compress: bool = False,
        extra_headers: dict[str, str] | None = None,
        auth_retried: bool = False,
        retry_count: int = 0,
    ) -> dict[str, Any]:
        client = self._get_client()
        headers = extra_headers or self._headers()
        content: bytes | None = None
        if json_body is not None:
            try:
                raw = dumps_bytes(json_body)
            except (JSONSerializationError, TypeError) as exc:
                message = str(exc)
                if "datetime" in message.lower() or "serializ" in message.lower():
                    message = "Attendance timestamp could not be serialized to JSON."
                raise CloudAPIError(
                    message,
                    error_kind="serialization",
                    retry_count=retry_count,
                ) from exc
            if compress and len(raw) > 512:
                content = gzip.compress(raw)
                headers["Content-Encoding"] = "gzip"
                headers["Content-Type"] = "application/json"
            else:
                content = raw

        started = time.perf_counter()
        try:
            resp = client.request(method, url, content=content, headers=headers, params=params)
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            kind = classify_transport_error(exc)
            logger.error(
                "API_REQUEST method=%s endpoint=%s status=%s duration_ms=%.0f retry_count=%s error=%s",
                method,
                url,
                kind.upper(),
                duration_ms,
                retry_count,
                str(exc)[:200],
            )
            raise CloudAPIError(
                f"Network error: {exc}",
                error_kind=kind,
                duration_ms=duration_ms,
                retry_count=retry_count,
            ) from exc

        duration_ms = (time.perf_counter() - started) * 1000.0
        if resp.status_code in (401, 403) and not auth_retried:
            if self._recover_auth():
                return self._request(
                    method,
                    url,
                    json_body=json_body,
                    params=params,
                    compress=compress,
                    extra_headers=None,
                    auth_retried=True,
                    retry_count=retry_count + 1,
                )

        if resp.status_code >= 400:
            detail: Any
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            safe_detail = _redact_cloud_detail(detail)
            kind = "auth" if resp.status_code in (401, 403) else (
                "server" if resp.status_code >= 500 else "client"
            )
            logger.error(
                "API_REQUEST method=%s endpoint=%s status=%s duration_ms=%.0f http=%s retry_count=%s detail=%s",
                method,
                url,
                kind.upper(),
                duration_ms,
                resp.status_code,
                retry_count,
                safe_detail,
            )
            raise CloudAPIError(
                f"Cloud API {resp.status_code}",
                status_code=resp.status_code,
                payload=detail,
                error_kind=kind,
                duration_ms=duration_ms,
                retry_count=retry_count,
            )

        if not resp.content:
            return {}
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return {"raw": resp.text}


def _redact_cloud_detail(detail: Any) -> Any:
    """Truncate/redact cloud error payloads so secrets never hit logs."""
    sensitive = {
        "token",
        "api_key",
        "password",
        "secret",
        "authorization",
        "jwt",
        "device_token",
    }
    if isinstance(detail, dict):
        out = {}
        for i, (k, v) in enumerate(detail.items()):
            if i >= 20:
                out["…"] = "truncated"
                break
            if str(k).lower() in sensitive or any(s in str(k).lower() for s in sensitive):
                out[k] = "***"
            else:
                out[k] = v if not isinstance(v, (dict, list)) else _redact_cloud_detail(v)
        return out
    if isinstance(detail, list):
        return [_redact_cloud_detail(x) for x in detail[:20]]
    text = str(detail)
    if len(text) > 500:
        return text[:500] + "…"
    return text
