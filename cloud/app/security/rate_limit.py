"""Simple in-process sliding-window rate limiter for FastAPI."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client request budget (best-effort; use nginx/redis at multi-node scale)."""

    def __init__(self, app, *, requests_per_minute: int = 600) -> None:
        super().__init__(app)
        self.limit = max(30, int(requests_per_minute))
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _client_key(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for") or ""
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host or "unknown"
        return "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path or ""
        # Health checks should never be throttled by the app itself.
        if path in {"/health", "/api/health", "/liveness", "/readiness", "/favicon.ico"}:
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        window = 60.0
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "limit_per_minute": self.limit,
                    },
                    headers={"Retry-After": "60"},
                )
            bucket.append(now)

        return await call_next(request)
