"""Register Anviz pack with the adapter registry."""

from __future__ import annotations

from adapters.packs.anviz.adapter import AnvizAdapter
from adapters.registry import register_adapter


def register() -> dict:
    register_adapter("anviz", AnvizAdapter)
    return {
        "manufacturer": "anviz",
        "class": AnvizAdapter.__name__,
        "status": "scaffold",
        "default_port": AnvizAdapter.default_port,
    }
