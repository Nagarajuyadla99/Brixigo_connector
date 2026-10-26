"""Register Matrix pack with the adapter registry."""

from __future__ import annotations

from adapters.packs.matrix.adapter import MatrixAdapter
from adapters.registry import register_adapter


def register() -> dict:
    register_adapter("matrix", MatrixAdapter)
    return {
        "manufacturer": "matrix",
        "class": MatrixAdapter.__name__,
        "status": "scaffold",
        "default_port": MatrixAdapter.default_port,
    }
