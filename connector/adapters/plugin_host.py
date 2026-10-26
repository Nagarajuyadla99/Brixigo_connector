"""Thin plugin host — discover vendor packs under adapters/packs/."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

logger = logging.getLogger("brixigo")


def discover_and_register_packs() -> list[dict[str, Any]]:
    """
    Import adapters.packs.<vendor> modules that expose register().
    Built-in ZK/eSSL stay registered by registry.py; packs add more vendors.
    """
    registered: list[dict[str, Any]] = []
    try:
        import adapters.packs as packs_pkg
    except ImportError:
        return registered

    prefix = packs_pkg.__name__ + "."
    for mod in pkgutil.iter_modules(packs_pkg.__path__, prefix):
        name = mod.name
        try:
            module = importlib.import_module(name)
        except Exception as exc:
            logger.warning("Failed to import adapter pack %s: %s", name, exc)
            continue
        register = getattr(module, "register", None)
        if not callable(register):
            # Try plugin submodule
            try:
                plugin = importlib.import_module(name + ".plugin")
                register = getattr(plugin, "register", None)
            except Exception:
                register = None
        if not callable(register):
            continue
        try:
            info = register() or {}
            if isinstance(info, dict):
                registered.append(info)
            logger.info("Registered adapter pack: %s", info or name)
        except Exception as exc:
            logger.warning("Adapter pack register failed %s: %s", name, exc)
    return registered
