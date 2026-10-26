"""Cloud API package."""

from __future__ import annotations

from typing import Any

__all__ = ["CloudClient", "CloudAPIError"]


def __getattr__(name: str) -> Any:
    if name in {"CloudClient", "CloudAPIError"}:
        from api.cloud_client import CloudAPIError, CloudClient

        return CloudClient if name == "CloudClient" else CloudAPIError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
