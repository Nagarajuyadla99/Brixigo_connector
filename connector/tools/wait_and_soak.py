"""Wait until device TCP port opens, then run live soak."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tcp_open(ip: str, port: int, timeout: float = 2.0) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ip", default="192.168.100.201")
    p.add_argument("--port", type=int, default=4370)
    p.add_argument("--password", default="0")
    p.add_argument("--manufacturer", default="essl")
    p.add_argument("--wait-seconds", type=int, default=300)
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--cloud", default="http://127.0.0.1:8080")
    p.add_argument("--cycles", type=int, default=3)
    p.add_argument("--sleep", type=float, default=5.0)
    args = p.parse_args()

    print(f"Waiting up to {args.wait_seconds}s for {args.ip}:{args.port} ...", flush=True)
    print("On device Ethernet screen set:", flush=True)
    print(f"  IP Address = {args.ip}", flush=True)
    print("  Subnet Mask = 255.255.255.0", flush=True)
    print("  Gateway     = 192.168.100.1   (or your LAN router)", flush=True)
    print(f"  TCP Port    = {args.port}", flush=True)
    print("  DHCP        = Off", flush=True)
    print("Then Save/Reboot if prompted.", flush=True)

    deadline = time.time() + max(30, args.wait_seconds)
    while time.time() < deadline:
        if tcp_open(args.ip, args.port):
            print(f"DEVICE ONLINE {args.ip}:{args.port}", flush=True)
            break
        print(f"  still offline... {int(deadline - time.time())}s left", flush=True)
        time.sleep(max(1.0, args.interval))
    else:
        print("Timed out waiting for device. Save Ethernet settings on the terminal and retry.", flush=True)
        return 3

    cmd = [
        sys.executable,
        "-u",
        str(ROOT / "tools" / "live_device_soak.py"),
        "--ip",
        args.ip,
        "--port",
        str(args.port),
        "--password",
        args.password,
        "--manufacturer",
        args.manufacturer,
        "--name",
        "eSSL-Face-100-201",
        "--terminal",
        "ESSL201",
        "--cycles",
        str(args.cycles),
        "--sleep",
        str(args.sleep),
        "--cloud",
        args.cloud,
    ]
    print("Starting soak:", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
