"""Lightweight API liveness probe with DNS / TCP / TLS / TTFB timings.

Does not send credentials or tokens. Used by the background API health monitor.
"""

from __future__ import annotations

import ssl
import socket
import time
from typing import Any
from urllib.parse import urlparse

from app.constants import API_HEALTH_PATHS, API_LATENCY_ACCEPTABLE_MS, API_LATENCY_HEALTHY_MS, API_LATENCY_SLOW_MS
from services.connection_state import classify_transport_error


def classify_latency_class(latency_ms: float) -> str:
    """Map a completed-request duration to a latency class (not availability)."""
    ms = float(latency_ms or 0.0)
    if ms < API_LATENCY_HEALTHY_MS:
        return "healthy"
    if ms < API_LATENCY_ACCEPTABLE_MS:
        return "acceptable"
    if ms < API_LATENCY_SLOW_MS:
        return "slow"
    return "timeout"


def _ssl_context(verify: Any) -> ssl.SSLContext:
    if verify is False:
        ctx = ssl._create_unverified_context()
    elif isinstance(verify, ssl.SSLContext):
        ctx = verify
    else:
        ctx = ssl.create_default_context()
        if isinstance(verify, str) and verify:
            ctx.load_verify_locations(verify)
    try:
        ctx.set_alpn_protocols(["http/1.1"])
    except (AttributeError, NotImplementedError, ssl.SSLError):
        pass
    return ctx


def measure_http_timings(
    url: str,
    *,
    timeout: float = 15.0,
    verify: Any = True,
) -> dict[str, Any]:
    """GET `url` and return HTTP status plus phase timings. Never logs secrets."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = (parsed.scheme or "https").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    result: dict[str, Any] = {
        "ok": False,
        "status_code": None,
        "kind": "unavailable",
        "latency_ms": 0.0,
        "dns_ms": 0.0,
        "tcp_ms": 0.0,
        "tls_ms": 0.0,
        "ttfb_ms": 0.0,
        "download_ms": 0.0,
        "endpoint": path,
        "url": f"{scheme}://{host}{path}",
        "error": "",
    }
    if not host:
        result["error"] = "missing host"
        return result

    started = time.perf_counter()
    sock: socket.socket | None = None
    ssock: ssl.SSLSocket | socket.socket | None = None
    try:
        t0 = time.perf_counter()
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        result["dns_ms"] = (time.perf_counter() - t0) * 1000.0
        ip = infos[0][4][0]

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        t1 = time.perf_counter()
        sock.connect((ip, int(port)))
        result["tcp_ms"] = (time.perf_counter() - t1) * 1000.0

        if scheme == "https":
            ctx = _ssl_context(verify)
            t2 = time.perf_counter()
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            result["tls_ms"] = (time.perf_counter() - t2) * 1000.0
        else:
            ssock = sock
            result["tls_ms"] = 0.0

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Connection: close\r\n"
            "User-Agent: BrixigoConnector-health\r\n"
            "Accept: application/json\r\n"
            "\r\n"
        )
        t3 = time.perf_counter()
        ssock.sendall(request.encode("ascii"))
        first = ssock.recv(1)
        result["ttfb_ms"] = (time.perf_counter() - t3) * 1000.0
        chunks = [first]
        while True:
            data = ssock.recv(4096)
            if not data:
                break
            chunks.append(data)
        result["download_ms"] = max(0.0, (time.perf_counter() - t3) * 1000.0 - result["ttfb_ms"])
        raw = b"".join(chunks)
        header_line = raw.split(b"\r\n", 1)[0].decode("latin1", "replace")
        parts = header_line.split(" ")
        status_code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        result["status_code"] = status_code
        result["latency_ms"] = (time.perf_counter() - started) * 1000.0
        if status_code in (401, 403):
            result["kind"] = "auth"
            result["ok"] = False
        elif status_code is not None and status_code >= 500:
            result["kind"] = "server"
            result["ok"] = False
        elif status_code is not None:
            result["kind"] = "ok"
            result["ok"] = True
        else:
            result["kind"] = "unavailable"
            result["error"] = header_line[:200]
        return result
    except Exception as exc:
        result["latency_ms"] = (time.perf_counter() - started) * 1000.0
        result["kind"] = classify_transport_error(exc)
        result["error"] = str(exc)[:200]
        result["ok"] = False
        return result
    finally:
        for conn in (ssock, sock):
            if conn is None:
                continue
            try:
                conn.close()
            except OSError:
                pass


def probe_liveness(
    base_url: str,
    *,
    timeout: float = 15.0,
    verify: Any = True,
    preferred_path: str | None = None,
) -> dict[str, Any]:
    """Probe a cheap liveness path. 404 still means the host answered over HTTPS."""
    base = (base_url or "").rstrip("/")
    paths: list[str] = []
    if preferred_path:
        paths.append(preferred_path if preferred_path.startswith("/") else f"/{preferred_path}")
    for path in API_HEALTH_PATHS:
        if path not in paths:
            paths.append(path)
    last: dict[str, Any] = {}
    for path in paths:
        last = measure_http_timings(base + path, timeout=timeout, verify=verify)
        last["health_path"] = path
        status = last.get("status_code")
        # Path exists (or auth/server error) — stop. 404: try the next cheap path once.
        if status != 404:
            return last
    return last
