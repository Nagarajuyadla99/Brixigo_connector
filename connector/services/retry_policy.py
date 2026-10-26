"""Exponential backoff with jitter for transient network failures."""

from __future__ import annotations

import random
from typing import Sequence

from app.constants import RETRY_BACKOFF_CAP_SECONDS, RETRY_BACKOFF_SCHEDULE


def next_backoff_seconds(
    attempt: int,
    *,
    schedule: Sequence[float] = RETRY_BACKOFF_SCHEDULE,
    cap: float = RETRY_BACKOFF_CAP_SECONDS,
    jitter: bool = True,
) -> float:
    """
    Return delay before the given 1-based retry attempt.

    Example: attempt 1 → ~2s, 2 → ~5s, 3 → ~10s, 4 → ~20s, 5+ → ~30–45s.
    Jitter is applied so multiple connectors do not reconnect in lockstep.
    """
    if attempt < 1:
        attempt = 1
    idx = min(attempt - 1, len(schedule) - 1)
    delay = float(schedule[idx])
    delay = min(delay, float(cap))
    if jitter:
        spread = min(1.0, delay * 0.2)
        delay = min(cap, delay + random.uniform(0.0, spread))
    return delay


def is_transient_error_kind(kind: str) -> bool:
    return kind in {"tls", "timeout", "network", "server", "unavailable", "slow"}
