"""Scan local IPv4 /24 for open biometric ports (4370/4371/5010)."""

from __future__ import annotations

import concurrent.futures
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def local_subnet_prefix() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    parts = ip.split(".")
    if len(parts) != 4:
        return "192.168.1"
    return ".".join(parts[:3])


def probe(ip: str, port: int, timeout: float = 0.35) -> tuple[str, int] | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
        return ip, port
    except OSError:
        return None
    finally:
        sock.close()


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else local_subnet_prefix()
    ports = (4370, 4371, 5010)
    print(f"Scanning {prefix}.1-254 ports={ports} ...")
    found: list[tuple[str, int]] = []
    ips = [f"{prefix}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        futs = [pool.submit(probe, ip, port) for ip in ips for port in ports]
        for fut in concurrent.futures.as_completed(futs):
            hit = fut.result()
            if hit:
                found.append(hit)
                print(f"OPEN {hit[0]}:{hit[1]}")
    print(f"Done. Found {len(found)} endpoint(s).")
    if not found:
        print("No biometric ports open on this subnet. Provide device IP manually.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
