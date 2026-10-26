"""Adapter registry — resolve manufacturer → IBiometricAdapter implementation."""

from __future__ import annotations

from typing import Callable, Type

from adapters.base import IBiometricAdapter
from adapters.essl.adapter import ESSLAdapter
from adapters.zkteco.adapter import ZKTecoAdapter
from models import Device

AdapterFactory = Callable[..., IBiometricAdapter]

_REGISTRY: dict[str, Type[IBiometricAdapter]] = {
    "essl": ESSLAdapter,
    "zkteco": ZKTecoAdapter,
    "zk": ZKTecoAdapter,
    "bioface": ESSLAdapter,  # common eSSL branding
}

_PACKS_LOADED = False


class UnsupportedManufacturerError(Exception):
    pass


def register_adapter(manufacturer: str, adapter_cls: Type[IBiometricAdapter]) -> None:
    _REGISTRY[manufacturer.lower().strip()] = adapter_cls


def ensure_packs_loaded() -> None:
    global _PACKS_LOADED
    if _PACKS_LOADED:
        return
    _PACKS_LOADED = True
    try:
        from adapters.plugin_host import discover_and_register_packs

        discover_and_register_packs()
    except Exception:
        # Pack discovery must never break core ZK/eSSL path
        pass


def list_adapters() -> list[dict[str, str]]:
    ensure_packs_loaded()
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for code, cls in _REGISTRY.items():
        if cls.manufacturer in seen:
            continue
        seen.add(cls.manufacturer)
        status = getattr(cls, "status", "live")
        out.append(
            {
                "manufacturer": cls.manufacturer,
                "class": cls.__name__,
                "status": status,
            }
        )
    for planned in ("hikvision", "suprema", "realtime", "fingertec", "dahua"):
        if planned not in seen:
            out.append({"manufacturer": planned, "class": "planned", "status": "planned"})
    return out


def create_adapter(
    device: Device,
    *,
    connect_timeout: float = 8.0,
    read_timeout: float = 30.0,
) -> IBiometricAdapter:
    ensure_packs_loaded()
    key = (device.manufacturer or "").lower().strip()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise UnsupportedManufacturerError(
            f"No adapter registered for manufacturer '{device.manufacturer}'. "
            f"Supported: {', '.join(sorted(set(_REGISTRY)))}"
        )
    status = getattr(cls, "status", "live")
    if status == "scaffold":
        raise UnsupportedManufacturerError(
            f"Manufacturer '{device.manufacturer}' is scaffold-only and not production-ready. "
            "Use zkteco or essl for live deployments."
        )
    return cls(device, connect_timeout=connect_timeout, read_timeout=read_timeout)
