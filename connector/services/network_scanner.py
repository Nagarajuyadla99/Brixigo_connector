"""Automatic biometric device discovery over an IP range."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from adapters.base import parse_comm_password
from adapters.protocol.zk_const import CMD_CONNECT
from adapters.protocol.zk_packet import encode_payload
from adapters.protocol.zk_protocol import ZKClient
from adapters.protocol.zk_profiles import resolve_profile
from adapters.protocol.zk_transport import abortive_close, select_local_ipv4
from app.constants import DISCOVERY_CONNECT_TIMEOUT, DISCOVERY_MAX_WORKERS, DISCOVERY_PORTS, RELOCATE_SCAN_BUDGET_SECONDS, RELOCATE_TCP_TIMEOUT
from models import Device, DiscoveredDevice, DiscoveryDecision
from storage.repositories.device_repo import DeviceRepository
from storage.repositories.enterprise_repo import DiscoveryRepository

logger = logging.getLogger("brixigo.connection")

_RANGE_RE = re.compile(
    r"^\s*(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\s*-\s*(\d{1,3})\s*$"
)
_CIDR_RE = re.compile(
    r"^\s*(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\s*/\s*(\d{1,2})\s*$"
)
_SINGLE_RE = re.compile(r"^\s*(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\s*$")


def parse_ip_range(spec: str) -> list[str]:
    """
    Parse IP range specifications into individual IPv4 addresses.

    Supported forms:
      - 192.168.1.1-254
      - 192.168.1.0/24
      - 192.168.1.50
    """
    text = (spec or "").strip()
    if not text:
        raise ValueError("IP range is required")

    m = _RANGE_RE.match(text)
    if m:
        a, b, c, start, end = (int(x) for x in m.groups())
        for octet in (a, b, c, start, end):
            if octet < 0 or octet > 255:
                raise ValueError(f"Invalid octet in range: {text}")
        if start > end:
            raise ValueError("Range start must be <= end")
        return [f"{a}.{b}.{c}.{i}" for i in range(start, end + 1)]

    m = _CIDR_RE.match(text)
    if m:
        a, b, c, d, prefix = (int(x) for x in m.groups())
        if prefix < 0 or prefix > 32:
            raise ValueError("CIDR prefix must be 0-32")
        base = (a << 24) | (b << 16) | (c << 8) | d
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
        network = base & mask
        size = 1 << (32 - prefix)
        # Skip network/broadcast for typical LAN prefixes
        start_i = 1 if prefix <= 30 else 0
        end_i = size - 1 if prefix <= 30 else size
        ips: list[str] = []
        for i in range(start_i, end_i):
            value = network + i
            ips.append(
                f"{(value >> 24) & 255}.{(value >> 16) & 255}."
                f"{(value >> 8) & 255}.{value & 255}"
            )
        return ips

    m = _SINGLE_RE.match(text)
    if m:
        octets = [int(x) for x in m.groups()]
        if any(o < 0 or o > 255 for o in octets):
            raise ValueError(f"Invalid IP: {text}")
        return [".".join(str(o) for o in octets)]

    raise ValueError(
        "Unsupported IP range. Use forms like 192.168.1.1-254 or 192.168.1.0/24"
    )


def _udp_primary_ipv4() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        ip = str(sock.getsockname()[0] or "")
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        return ""
    return ""


def local_ipv4_nics() -> list[tuple[str, int]]:
    """Return (ipv4, prefix_len) for up, non-loopback interfaces."""
    nics: list[tuple[str, int]] = []
    seen: set[str] = set()
    try:
        import psutil

        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
    except Exception:
        stats, addrs = {}, {}
    for name, entries in (addrs or {}).items():
        st = stats.get(name) if stats else None
        if st is not None and not getattr(st, "isup", True):
            continue
        for entry in entries:
            family = getattr(entry, "family", None)
            if family not in (socket.AF_INET, 2):
                continue
            ip = str(getattr(entry, "address", "") or "").strip()
            if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if ip in seen:
                continue
            prefix = 24
            mask = str(getattr(entry, "netmask", "") or "")
            if mask:
                try:
                    prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
                except ValueError:
                    prefix = 24
            if prefix < 22:
                prefix = 24
            seen.add(ip)
            nics.append((ip, prefix))
    if not nics:
        primary = _udp_primary_ipv4()
        if primary:
            nics.append((primary, 24))
    return nics


def local_ipv4_addresses() -> list[str]:
    return [ip for ip, _prefix in local_ipv4_nics()]


def local_subnet_specs() -> list[str]:
    """Discovery range strings covering each local IPv4 subnet (typically /24)."""
    specs: list[str] = []
    seen: set[str] = set()
    for ip, prefix in local_ipv4_nics():
        try:
            net = ipaddress.IPv4Network(f"{ip}/{int(prefix)}", strict=False)
        except ValueError:
            continue
        key = str(net)
        if key in seen:
            continue
        seen.add(key)
        hosts = list(net.hosts())
        if not hosts:
            continue
        first = str(hosts[0]).split(".")
        last = str(hosts[-1]).split(".")
        if first[:3] == last[:3]:
            specs.append(f"{first[0]}.{first[1]}.{first[2]}.{first[3]}-{last[3]}")
        else:
            specs.append(f"{net.network_address}/{net.prefixlen}")
    return specs


def default_discovery_range() -> str:
    specs = local_subnet_specs()
    return specs[0] if specs else "192.168.1.1-254"


def host_on_local_subnets(
    host: str, nics: Optional[list[tuple[str, int]]] = None
) -> bool:
    try:
        addr = ipaddress.IPv4Address(str(host or "").strip())
    except ValueError:
        return False
    for nic_ip, prefix in nics if nics is not None else local_ipv4_nics():
        try:
            net = ipaddress.IPv4Network(f"{nic_ip}/{int(prefix)}", strict=False)
        except ValueError:
            continue
        if addr in net:
            return True
    return False


def normalize_serial(value: str) -> str:
    return str(value or "").strip().upper()


def normalize_mac(value: str) -> str:
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def identities_match(device: Device, found: DiscoveredDevice) -> bool:
    serial = normalize_serial(device.serial_number)
    found_serial = normalize_serial(found.serial_number)
    if serial and found_serial and serial == found_serial:
        return True
    mac = normalize_mac(device.mac_address)
    found_mac = normalize_mac(found.mac_address)
    return bool(mac and found_mac and len(mac) >= 12 and mac == found_mac)


@dataclass
class LanRelocateResult:
    relocated: Optional[Device] = None
    scanned: bool = False
    scan_ranges: tuple[str, ...] = ()
    local_ips: tuple[str, ...] = ()
    open_host_count: int = 0
    same_subnet: bool = False
    skipped_cooldown: bool = False


def format_lan_unreachable(
    device: Device,
    probe_error: str,
    *,
    local_ips: Sequence[str] = (),
    scan_ranges: Sequence[str] = (),
    scanned: bool = False,
    open_host_count: int = 0,
    same_subnet: bool = False,
) -> str:
    raw = str(probe_error or "unreachable host").strip()
    if raw.lower().startswith("tcp connect failed"):
        header = raw
    else:
        header = f"TCP connect failed: {raw}"
    ip = str(device.ip_address or "")
    port = int(device.port or 4370)
    pc = ", ".join(str(x) for x in local_ips if x) or "this PC"
    lines = [
        header,
        "",
        f"This PC ({pc}) has no path to the terminal at {ip}:{port}.",
    ]
    if local_ips and not same_subnet:
        lines.append(
            "The registered IP is not on this PC's LAN. Connect to the same "
            "network as the device, or update the stored IP."
        )
    elif same_subnet:
        lines.append(
            "This PC is on the same subnet, so Windows could not reach that host "
            "(powered off, unplugged, asleep, or Wi-Fi client isolation)."
        )
    if scanned:
        ranges = ", ".join(scan_ranges) or "the local LAN"
        if open_host_count:
            lines.append(
                f"A LAN scan of {ranges} found {open_host_count} ZK/eSSL host(s) "
                f"on TCP {port}, but none matched this terminal's serial/MAC."
            )
        else:
            lines.append(
                f"A LAN scan of {ranges} found no ZK/eSSL device on TCP {port}."
            )
    lines.extend(
        [
            "",
            "Stored attendance on this page is unchanged. Power on the terminal, "
            "confirm its IP, or use Discover devices. If this PC is on Wi-Fi, "
            "disable AP/client isolation so the laptop can reach the device.",
        ]
    )
    return "\n".join(lines)


def _tcp_open(ip: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    local = select_local_ipv4(ip)
    if local:
        try:
            sock.bind((local, 0))
        except OSError:
            pass
    try:
        sock.connect((ip, port))
        return True
    except OSError:
        return False
    finally:
        abortive_close(sock)


def udp_broadcast_discover(port: int = 4370, timeout: float = 1.5) -> list[str]:
    """Ask ZK/eSSL terminals to announce themselves on the local subnet broadcast."""
    payload = encode_payload(CMD_CONNECT, 0, 0, b"")
    found: list[str] = []
    seen: set[str] = set()
    nics = local_ipv4_nics()
    if not nics:
        return found
    for local_ip, _prefix in nics:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((local_ip, 0))
            sock.settimeout(0.2)
            parts = local_ip.split(".")
            bcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255" if len(parts) == 4 else ""
            for dest in (bcast, "255.255.255.255"):
                if not dest:
                    continue
                try:
                    sock.sendto(payload, (dest, int(port)))
                except OSError:
                    continue
            deadline = time.perf_counter() + max(0.3, float(timeout))
            while time.perf_counter() < deadline:
                sock.settimeout(max(0.05, deadline - time.perf_counter()))
                try:
                    _data, addr = sock.recvfrom(1024)
                except socket.timeout:
                    break
                except OSError:
                    break
                host = str(addr[0] or "")
                if host and host not in seen and not host.startswith("127."):
                    seen.add(host)
                    found.append(host)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return found


def _probe_zk(ip: str, port: int, password: int, timeout: float) -> Optional[DiscoveredDevice]:
    started = time.perf_counter()
    client = ZKClient(
        ip=ip,
        port=port,
        password=password,
        timeout=timeout,
        profile=resolve_profile(manufacturer="zkteco"),
        max_retries=1,
    )
    try:
        client.connect()
        info = client.get_extended_device_info()
        latency = (time.perf_counter() - started) * 1000
        platform = str(info.get("platform") or "").lower()
        manufacturer = "essl" if "essl" in platform or "eSSL" in str(info) else "zkteco"
        # Prefer eSSL naming when device name/product hints at it
        name = str(info.get("device_name") or "")
        model = str(info.get("model") or info.get("platform") or "")
        if "essl" in name.lower() or "essl" in model.lower():
            manufacturer = "essl"
        return DiscoveredDevice(
            ip_address=ip,
            port=port,
            manufacturer=manufacturer,
            model=model,
            firmware=str(info.get("firmware") or ""),
            mac_address=str(info.get("mac_address") or ""),
            sdk_version=str(info.get("sdk_version") or ""),
            device_name=name or f"{manufacturer.upper()}-{ip.split('.')[-1]}",
            serial_number=str(info.get("serial_number") or ""),
            communication_type="tcp",
            latency_ms=latency,
            decision=DiscoveryDecision.PENDING.value,
            raw=info,
        )
    except Exception:
        return None
    finally:
        client.disconnect()


class NetworkScanner:
    """
    Scan an IP range for supported biometric devices.
    Discovered devices are cached locally and never auto-registered.
    """

    def __init__(
        self,
        discovery_repo: DiscoveryRepository,
        devices: DeviceRepository,
        *,
        connect_timeout: float = DISCOVERY_CONNECT_TIMEOUT,
        max_workers: int = DISCOVERY_MAX_WORKERS,
        ports: tuple[int, ...] = DISCOVERY_PORTS,
    ) -> None:
        self._discovery = discovery_repo
        self._devices = devices
        self.connect_timeout = connect_timeout
        self.max_workers = max(4, max_workers)
        self.ports = ports

    def scan(
        self,
        ip_range: str,
        *,
        password: str = "0",
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> list[DiscoveredDevice]:
        ips = parse_ip_range(ip_range)
        targets = [(ip, port) for ip in ips for port in self.ports]
        total = len(targets)
        found: list[DiscoveredDevice] = []
        completed = 0
        pwd = parse_comm_password(password)

        logger.info("Network scan started range=%s targets=%s", ip_range, total)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._scan_target, ip, port, pwd): (ip, port)
                for ip, port in targets
            }
            for fut in as_completed(futures):
                completed += 1
                ip, port = futures[fut]
                if progress:
                    progress(completed, total, f"{ip}:{port}")
                try:
                    device = fut.result()
                except Exception as exc:
                    logger.debug("Scan error %s:%s %s", ip, port, exc)
                    continue
                if device:
                    # Skip already registered endpoints
                    if self._devices.get_by_ip_port(device.ip_address, device.port):
                        device.decision = DiscoveryDecision.ADDED.value
                    elif self._discovery.is_ignored(device.ip_address, device.port):
                        device.decision = DiscoveryDecision.IGNORED.value
                    found.append(device)

        self._discovery.save_discovered(found)
        logger.info("Network scan finished found=%s", len(found))
        return found

    def _scan_target(
        self,
        ip: str,
        port: int,
        password: int,
        timeout: float | None = None,
    ) -> Optional[DiscoveredDevice]:
        wait = float(timeout if timeout is not None else self.connect_timeout)
        if not _tcp_open(ip, port, wait):
            return None
        time.sleep(0.25)
        return _probe_zk(ip, port, password, max(wait, 2.0))

    def probe_range(
        self,
        ip_range: str,
        *,
        password: str = "0",
        skip_ips: Optional[set[str]] = None,
        ports: Optional[tuple[int, ...]] = None,
    ) -> list[DiscoveredDevice]:
        """Scan without writing the discovery cache (used to relocate a device)."""
        blocked = skip_ips or set()
        ips = [ip for ip in parse_ip_range(ip_range) if ip not in blocked]
        use_ports = ports or self.ports
        targets = [(ip, port) for ip in ips for port in use_ports]
        found: list[DiscoveredDevice] = []
        pwd = parse_comm_password(password)
        timeout = min(
            float(self.connect_timeout or DISCOVERY_CONNECT_TIMEOUT),
            DISCOVERY_CONNECT_TIMEOUT,
            RELOCATE_TCP_TIMEOUT,
        )
        if not targets:
            return found
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._scan_target, ip, port, pwd, timeout): (ip, port)
                for ip, port in targets
            }
            for fut in as_completed(futures):
                ip, port = futures[fut]
                try:
                    device = fut.result()
                except Exception as exc:
                    logger.debug("Relocate probe error %s:%s %s", ip, port, exc)
                    continue
                if device:
                    found.append(device)
        return found

    def find_relocation_candidate(
        self, device: Device
    ) -> tuple[Optional[DiscoveredDevice], list[str], int]:
        specs = local_subnet_specs()
        skip = set(local_ipv4_addresses())
        skip.add(str(device.ip_address or "").strip())
        found: list[DiscoveredDevice] = []
        port = int(device.port or 4370)
        pwd = parse_comm_password(device.password or "0")
        timeout = min(
            float(self.connect_timeout or DISCOVERY_CONNECT_TIMEOUT),
            DISCOVERY_CONNECT_TIMEOUT,
            RELOCATE_TCP_TIMEOUT,
        )
        for ip in udp_broadcast_discover(port=port, timeout=1.5):
            if ip in skip:
                continue
            hit = self._scan_target(ip, port, pwd, timeout)
            if hit is not None:
                found.append(hit)
        matches = [item for item in found if identities_match(device, item)]
        if matches:
            matches.sort(key=lambda item: item.latency_ms)
            return matches[0], specs, len(found)
        deadline = time.perf_counter() + RELOCATE_SCAN_BUDGET_SECONDS
        for spec in specs:
            if time.perf_counter() >= deadline:
                break
            found.extend(
                self.probe_range(
                    spec,
                    password=device.password or "0",
                    skip_ips=skip,
                    ports=(port,),
                )
            )
        matches = [item for item in found if identities_match(device, item)]
        if matches:
            matches.sort(key=lambda item: item.latency_ms)
            return matches[0], specs, len(found)
        if not normalize_serial(device.serial_number) and not normalize_mac(
            device.mac_address
        ):
            unclaimed = [
                item
                for item in found
                if self._devices.get_by_ip_port(item.ip_address, item.port) is None
            ]
            if len(unclaimed) == 1:
                return unclaimed[0], specs, len(found)
        return None, specs, len(found)

    def list_discovered(self) -> list[DiscoveredDevice]:
        return self._discovery.list_all()

    def ignore_device(self, ip_address: str, port: int, reason: str = "ignored by admin") -> None:
        self._discovery.ignore(ip_address, port, reason=reason)

    def mark_added(self, ip_address: str, port: int) -> None:
        self._discovery.mark_added(ip_address, port)
