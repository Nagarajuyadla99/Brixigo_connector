"""Network diagnostics for devices, internet, DNS, API, and latency."""

from __future__ import annotations

import logging
import platform
import socket
import subprocess
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from api.cloud_client import CloudClient
from adapters.base import parse_comm_password
from adapters.protocol.zk_const import PROTOCOL_VERSION
from adapters.protocol.zk_protocol import ZKClient
from models import Device
from security.command_security import validate_cloud_certificate

logger = logging.getLogger("brixigo.connection")


class NetworkDiagnostics:
    def __init__(self, cloud: CloudClient, sessions=None, states=None) -> None:
        self._cloud = cloud
        self._sessions = sessions
        self._states = states

    def ping_host(self, host: str, *, count: int = 2, timeout: float = 2.0) -> dict[str, Any]:
        system = platform.system().lower()
        if system == "windows":
            cmd = ["ping", "-n", str(count), "-w", str(int(timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", str(count), "-W", str(int(timeout)), host]
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout * count + 5,
            )
            elapsed = (time.perf_counter() - started) * 1000
            ok = proc.returncode == 0
            return {
                "ok": ok,
                "host": host,
                "latency_ms": elapsed / max(count, 1),
                "output": (proc.stdout or proc.stderr or "")[:2000],
                "status": "reachable" if ok else "unreachable",
            }
        except Exception as exc:
            return {
                "ok": False,
                "host": host,
                "latency_ms": 0,
                "output": str(exc),
                "status": "error",
            }

    def check_port(self, host: str, port: int, *, timeout: float = 3.0) -> dict[str, Any]:
        from services.connection_state import tcp_probe

        ok, latency, message = tcp_probe(host, int(port), timeout=timeout)
        return {
            "ok": ok,
            "host": host,
            "port": port,
            "latency_ms": latency,
            "status": "open" if ok else "closed",
            "error": "" if ok else message,
        }

    def check_internet(self) -> dict[str, Any]:
        targets = ["1.1.1.1", "8.8.8.8"]
        results = [self.ping_host(t, count=1) for t in targets]
        ok = any(r.get("ok") for r in results)
        return {
            "ok": ok,
            "status": "online" if ok else "offline",
            "checks": results,
        }

    def check_api(self) -> dict[str, Any]:
        base = self._cloud.settings.cloud_base_url.rstrip("/")
        started = time.perf_counter()
        try:
            result = self._cloud.probe_api_health()
            latency = result.get("latency_ms") or ((time.perf_counter() - started) * 1000)
            kind = str(result.get("kind") or "")
            ok = bool(result.get("ok"))
            status = "reachable"
            if not ok:
                status = kind or "unreachable"
            elif latency >= 10000:
                status = "slow"
            elif latency >= 3000:
                status = "slow"
            elif latency >= 1500:
                status = "acceptable"
            return {
                "ok": ok,
                "status": status,
                "url": base,
                "latency_ms": latency,
                "kind": kind,
                "error": result.get("error") or "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "error",
                "url": base,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error": str(exc),
            }

    def traceroute(self, host: str, *, max_hops: int = 20) -> dict[str, Any]:
        system = platform.system().lower()
        if system == "windows":
            cmd = ["tracert", "-d", "-h", str(max_hops), host]
        else:
            cmd = ["traceroute", "-n", "-m", str(max_hops), host]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return {
                "ok": proc.returncode == 0,
                "host": host,
                "output": (proc.stdout or proc.stderr or "")[:8000],
                "status": "complete" if proc.returncode == 0 else "failed",
            }
        except Exception as exc:
            return {"ok": False, "host": host, "output": str(exc), "status": "error"}

    def dns_test(self, hostname: Optional[str] = None) -> dict[str, Any]:
        if not hostname:
            parsed = urlparse(self._cloud.settings.cloud_base_url)
            hostname = parsed.hostname or "brixigo.com"
        started = time.perf_counter()
        try:
            infos = socket.getaddrinfo(hostname, None)
            ips = sorted({item[4][0] for item in infos})
            latency = (time.perf_counter() - started) * 1000
            return {
                "ok": True,
                "hostname": hostname,
                "addresses": ips,
                "latency_ms": latency,
                "status": "resolved",
            }
        except Exception as exc:
            return {
                "ok": False,
                "hostname": hostname,
                "addresses": [],
                "latency_ms": (time.perf_counter() - started) * 1000,
                "status": "failed",
                "error": str(exc),
            }

    def latency_test(self, url: Optional[str] = None, samples: int = 3) -> dict[str, Any]:
        target = (url or self._cloud.settings.cloud_base_url).rstrip("/")
        times: list[float] = []
        errors: list[str] = []
        verify = self._cloud.settings.verify_ssl
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=10.0),
            verify=verify,
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        ) as client:
            for _ in range(max(1, samples)):
                started = time.perf_counter()
                try:
                    resp = client.get(target)
                    _ = resp.status_code
                    times.append((time.perf_counter() - started) * 1000)
                except Exception as exc:
                    errors.append(str(exc))
        avg = sum(times) / len(times) if times else 0.0
        status = "ok" if times else "failed"
        if times and avg >= 3000:
            status = "slow"
        elif times and avg >= 1500:
            status = "acceptable"
        return {
            "ok": bool(times),
            "url": target,
            "samples": times,
            "average_ms": avg,
            "min_ms": min(times) if times else 0.0,
            "max_ms": max(times) if times else 0.0,
            "errors": errors,
            "status": status,
        }

    def certificate_check(self) -> dict[str, Any]:
        parsed = urlparse(self._cloud.settings.cloud_base_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.scheme != "https":
            return {"ok": True, "status": "skipped", "message": "Non-HTTPS endpoint"}
        ipv4 = None
        ipv6 = None
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(host, port):
                if family == socket.AF_INET and ipv4 is None:
                    ipv4 = sockaddr[0]
                elif family == socket.AF_INET6 and ipv6 is None:
                    ipv6 = sockaddr[0]
        except OSError as exc:
            return {"ok": False, "status": "dns_failed", "hostname": host, "error": str(exc)}
        result = validate_cloud_certificate(
            host,
            port=port,
            ca_bundle_path=self._cloud.settings.ca_bundle_path,
            timeout=10.0,
        )
        result["ipv4"] = ipv4
        result["ipv6"] = ipv6
        if result.get("ok"):
            result["status"] = "healthy"
        else:
            err = str(result.get("error") or "").lower()
            if "handshake" in err or "timed out" in err:
                result["status"] = "timeout"
            else:
                result["status"] = "error"
        return result

    def ping_device(self, device: Device) -> dict[str, Any]:
        return self.ping_host(device.ip_address)

    def check_device_port(self, device: Device) -> dict[str, Any]:
        return self.check_port(device.ip_address, device.port)

    def protocol_handshake(self, device: Device) -> dict[str, Any]:
        """
        Full ZK TCP protocol handshake diagnostic (framing + session + auth).
        Does not log or return the communication key value.
        """
        if self._sessions is not None:
            diag = self._sessions.test_protocol(device)
            tcp_status = "PASS" if diag.tcp_ok else "FAIL"
            proto_status = "PASS" if diag.protocol_ok else "FAIL"
            auth_label = diag.auth_status if diag.auth_completed else "NOT COMPLETED"
            last_proto = "never"
            last_tcp = "never"
            if self._states is not None:
                link = self._states.device(device.id)
                if link:
                    last_proto = link.last_protocol_success_at or "never"
                    last_tcp = link.last_tcp_heartbeat_at or link.last_heartbeat_at or "never"
            return {
                "ok": diag.protocol_ok,
                "status": "protocol_ok" if diag.protocol_ok else "protocol_failed",
                "message": diag.protocol_message,
                "latency_ms": diag.latency_ms or diag.tcp_latency_ms,
                "protocol_version": PROTOCOL_VERSION,
                "device_ip": device.ip_address,
                "port": int(device.port or 4370),
                "tcp_test": f"{tcp_status} - {diag.tcp_latency_ms:.0f} ms",
                "tcp_ok": diag.tcp_ok,
                "protocol_test": proto_status,
                "protocol_ok": diag.protocol_ok,
                "protocol_error": "" if diag.protocol_ok else (diag.protocol_message or "unknown"),
                "protocol_code": diag.protocol_code,
                "failure_stage": diag.failure_stage,
                "authentication": auth_label,
                "last_successful_protocol_session": last_proto,
                "last_tcp_heartbeat": last_tcp,
                "session_id": diag.session_id,
                "auth_status": diag.auth_status,
                "comm_key_is_zero": diag.comm_key_is_zero,
                "device_info": diag.info,
                "device_time": diag.device_time,
            }

        started = time.perf_counter()
        client = ZKClient(
            ip=device.ip_address,
            port=int(device.port or 4370),
            password=parse_comm_password(device.password),
            timeout=8.0,
            read_timeout=15.0,
            force_udp=device.communication_type == "udp",
            manufacturer=device.manufacturer or "",
            model=device.model or "",
            max_retries=2,
        )
        try:
            online, latency, message = client.health_check()
            snap = client.diagnostics_snapshot()
            info: dict[str, Any] = {}
            if online:
                try:
                    info = {
                        "serial_number": client.get_serial_number(),
                        "firmware": client.get_firmware_version(),
                        "platform": client.get_platform(),
                    }
                except Exception as exc:
                    info = {"info_error": str(exc)}
            return {
                "ok": online,
                "status": "protocol_ok" if online else "protocol_failed",
                "message": message,
                "latency_ms": latency,
                "protocol_version": PROTOCOL_VERSION,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "session_id": snap.get("session_id"),
                "reply_id": snap.get("reply_id"),
                "auth_status": snap.get("auth_status"),
                "session_state": snap.get("state"),
                "profile": snap.get("profile"),
                "stats": snap.get("stats") or {},
                "device_info": info,
                "comm_key_is_zero": snap.get("comm_key_is_zero"),
            }
        except Exception as exc:
            snap = client.diagnostics_snapshot()
            return {
                "ok": False,
                "status": "protocol_failed",
                "message": str(exc),
                "latency_ms": (time.perf_counter() - started) * 1000,
                "protocol_version": PROTOCOL_VERSION,
                "session_id": snap.get("session_id"),
                "reply_id": snap.get("reply_id"),
                "auth_status": snap.get("auth_status"),
                "session_state": snap.get("state"),
                "stats": snap.get("stats") or {},
            }
        finally:
            client.disconnect()

    def run_full_suite(self, device: Optional[Device] = None) -> dict[str, Any]:
        suite = {
            "internet": self.check_internet(),
            "dns": self.dns_test(),
            "api": self.check_api(),
            "latency": self.latency_test(),
            "certificate": self.certificate_check(),
        }
        if device:
            suite["device_ping"] = self.ping_device(device)
            suite["device_port"] = self.check_device_port(device)
            suite["protocol_handshake"] = self.protocol_handshake(device)
        return suite
