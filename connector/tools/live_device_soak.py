"""Live ZK/eSSL soak: register with local cloud, add device, poll+upload cycles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Live device soak against ZKTeco/eSSL")
    parser.add_argument("--ip", required=True, help="Device IP address")
    parser.add_argument("--port", type=int, default=4370)
    parser.add_argument("--password", default="0", help="Device communication password")
    parser.add_argument("--manufacturer", default="zkteco", choices=("zkteco", "essl", "zk"))
    parser.add_argument("--terminal", default="", help="Terminal code for cloud push")
    parser.add_argument("--name", default="", help="Friendly device name")
    parser.add_argument("--cloud", default="http://127.0.0.1:9990", help="Cloud base URL")
    parser.add_argument("--org-name", default="Brixigo Demo Organisation")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=5.0)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--discover-only", action="store_true", help="Only test TCP/device health")
    args = parser.parse_args()

    manufacturer = "zkteco" if args.manufacturer == "zk" else args.manufacturer
    terminal = args.terminal or f"LIVE-{args.ip.replace('.', '')[-6:]}"
    name = args.name or f"{manufacturer}-{args.ip}"

    from app.bootstrap import create_container
    from models import Device, new_id
    from adapters.registry import create_adapter
    from api.cloud_client import CloudClient
    from services.soak_service import SoakService

    container = create_container()
    try:
        # Point connector at local/dev cloud for soak
        container.settings.update(
            cloud_base_url=args.cloud.rstrip("/"),
            verify_ssl=not args.cloud.lower().startswith("http://"),
            organisation_id=container.settings.as_model().organisation_id,
        )

        device = Device(
            id=new_id(),
            name=name,
            manufacturer=manufacturer,
            model="",
            ip_address=args.ip,
            port=args.port,
            password=str(args.password),
            branch="SoakLab",
            terminal_code=terminal,
            enabled=True,
        )

        print("=== Device health probe ===")
        adapter = create_adapter(device, connect_timeout=5.0, read_timeout=15.0)
        health = adapter.health()
        print(json.dumps(health.__dict__, indent=2, default=str))
        if not health.online:
            print("DEVICE OFFLINE — fix IP/port/password/network then retry")
            return 3

        if args.discover_only:
            return 0

        # Persist device locally (replace same IP/port if exists)
        existing = container.devices.get_by_ip_port(args.ip, args.port)
        if existing:
            device.id = existing.id
            container.devices.update(
                device.id,
                name=name,
                manufacturer=manufacturer,
                password=str(args.password),
                terminal_code=terminal,
                enabled=True,
            )
            device = container.devices.get(device.id) or device
            print(f"Updated existing device id={device.id}")
        else:
            container.devices.create(device)
            print(f"Created device id={device.id} terminal={terminal}")

        # Register connector with cloud if credentials missing
        settings = container.settings.as_model()
        cloud = CloudClient(settings)
        if not settings.api_key or not settings.connector_id:
            print("=== Registering connector with cloud ===")
            import socket
            from api.cloud_client import CloudAPIError

            try:
                url = f"{args.cloud.rstrip('/')}/api/wf/biometric-enterprise/connectors/register"
                body = {
                    "hostname": socket.gethostname(),
                    "version": "3.0.2",
                    "organisation_name": args.org_name,
                    "name": "Live Soak Connector",
                }
                reg = cloud._request("POST", url, json_body=body)
            except CloudAPIError as exc:
                print("Cloud register failed:", exc)
                print("Start local cloud first: cd cloud && uvicorn app.main:app --port 9990")
                return 4

            updates = {
                "cloud_base_url": args.cloud.rstrip("/"),
                "verify_ssl": not args.cloud.lower().startswith("http://"),
                "connector_id": reg.get("connector_id") or "",
                "organisation_id": reg.get("organisation_id") or "",
                "api_key": reg.get("api_key") or reg.get("device_token") or "",
            }
            if reg.get("command_signing_secret"):
                updates["command_signing_secret"] = reg["command_signing_secret"]
            container.settings.update(**{k: v for k, v in updates.items() if v or k in {"verify_ssl"}})
            print("Registered connector_id=", updates.get("connector_id"))
        else:
            container.settings.update(
                cloud_base_url=args.cloud.rstrip("/"),
                verify_ssl=not args.cloud.lower().startswith("http://"),
            )

        # Ensure cloud knows the device terminal
        settings = container.settings.as_model()
        cloud = CloudClient(settings)
        try:
            cloud.register_device(device.to_public_dict())
        except Exception as exc:
            print("Cloud device register skipped:", exc)

        print("=== Soak start ===")
        soak = SoakService(container)
        report = soak.run(
            cycles=max(1, args.cycles),
            sleep_seconds=max(0.0, args.sleep),
            device_ids=[device.id, terminal],
            upload=not args.no_upload,
        )
        soak.print_report(report)
        ok = bool(report.get("summary", {}).get("ok"))
        return 0 if ok else 2
    finally:
        container.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
