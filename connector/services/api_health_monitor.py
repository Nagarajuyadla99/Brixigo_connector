"""Independent Brixigo API / TLS / DNS / internet health monitor.

Results never update biometric device status.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from api.cloud_client import CloudAPIError, CloudClient
from app.constants import DEFAULT_API_HEALTH_INTERVAL_SECONDS
from services.connection_state import (
    ConnectionStateRegistry,
    InternetStatus,
    classify_transport_error,
)

logger = logging.getLogger("brixigo.health")


class ApiHealthMonitor:
    def __init__(
        self,
        cloud: CloudClient,
        states: ConnectionStateRegistry,
        *,
        interval_seconds: int = DEFAULT_API_HEALTH_INTERVAL_SECONDS,
    ) -> None:
        self._cloud = cloud
        self._states = states
        self.interval_seconds = max(15, int(interval_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="brixigo-api-health", daemon=True
        )
        self._thread.start()
        logger.info("API health monitor started interval=%ss", self.interval_seconds)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def probe_once(self) -> dict:
        internet_ok = self._check_internet()
        self._states.set_internet(
            InternetStatus.ONLINE if internet_ok else InternetStatus.OFFLINE
        )
        dns_ok, dns_msg = self._check_dns()
        self._states.set_dns(dns_ok, dns_msg)

        kind = "unavailable"
        latency_ms = 0.0
        error = ""
        status_code = None
        timings: dict = {}
        try:
            result = self._cloud.probe_api_health()
            latency_ms = float(result.get("latency_ms") or 0)
            status_code = result.get("status_code")
            timings = {
                "dns_ms": float(result.get("dns_ms") or 0),
                "tcp_ms": float(result.get("tcp_ms") or 0),
                "tls_ms": float(result.get("tls_ms") or 0),
                "ttfb_ms": float(result.get("ttfb_ms") or 0),
            }
            if result.get("ok"):
                kind = "ok"
            else:
                kind = str(result.get("kind") or "unavailable")
                error = str(result.get("error") or "")
        except CloudAPIError as exc:
            kind = exc.error_kind or classify_transport_error(exc)
            error = str(exc)
            status_code = exc.status_code
            latency_ms = float(getattr(exc, "duration_ms", 0) or 0)
        except Exception as exc:
            kind = classify_transport_error(exc)
            error = str(exc)

        if kind == "ok" and not internet_ok:
            internet_ok = True
            self._states.set_internet(InternetStatus.ONLINE)

        server = self._states.record_api_result(
            kind=kind,
            latency_ms=latency_ms,
            error=error,
            status_code=status_code,
            dns_ms=timings.get("dns_ms", 0.0),
            tcp_ms=timings.get("tcp_ms", 0.0),
            tls_ms=timings.get("tls_ms", 0.0),
            ttfb_ms=timings.get("ttfb_ms", 0.0),
            clears_auth=False,
        )
        logger.info(
            "API_HEALTH status=%s latency_ms=%.0f dns_ms=%.0f tcp_ms=%.0f tls_ms=%.0f "
            "ttfb_ms=%.0f internet=%s dns=%s http=%s error=%s",
            server.value,
            latency_ms,
            timings.get("dns_ms", 0.0),
            timings.get("tcp_ms", 0.0),
            timings.get("tls_ms", 0.0),
            timings.get("ttfb_ms", 0.0),
            "online" if internet_ok else "offline",
            "ok" if dns_ok else "failed",
            status_code,
            (error or "")[:160],
        )
        return {
            "internet": internet_ok,
            "dns": dns_ok,
            "server": server.value,
            "latency_ms": latency_ms,
            "error": error,
        }

    def _check_internet(self) -> bool:
        """True internet reachability — not Brixigo API health."""
        targets = [("1.1.1.1", 443), ("8.8.8.8", 443)]
        parsed = urlparse(self._cloud.settings.cloud_base_url)
        if parsed.hostname:
            targets.append((parsed.hostname, parsed.port or 443))
        for host, port in targets:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            try:
                sock.connect((host, port))
                return True
            except OSError:
                continue
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
        return False

    def _check_dns(self) -> tuple[bool, str]:
        parsed = urlparse(self._cloud.settings.cloud_base_url)
        host = parsed.hostname or "brixigo.com"
        try:
            infos = socket.getaddrinfo(host, parsed.port or 443)
            ips = sorted({item[4][0] for item in infos})
            return True, ",".join(ips[:8])
        except OSError as exc:
            return False, str(exc)

    def _loop(self) -> None:
        self.probe_once()
        while not self._stop.is_set():
            self._stop.wait(self.interval_seconds)
            if self._stop.is_set():
                break
            try:
                self.probe_once()
            except Exception as exc:
                logger.warning("API health probe failed: %s", exc)
            # Tiny pause so a tight misconfiguration cannot spin.
            time.sleep(0.05)
