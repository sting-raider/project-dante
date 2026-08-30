"""Small process-local API edge rate limiter for production deployments.

The buildathon deployment is intentionally single-replica, so a bounded
in-process limiter is useful defense-in-depth without adding another service.
It is disabled in development/test mode to keep local workflows and tests
deterministic. Authentication and webhook signature verification remain
separate concerns; the Razorpay webhook endpoint is exempt so provider retry
traffic is not turned into a second failure mode.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

from project_dante.settings import get_settings

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_EXEMPT_PATHS = frozenset(
    {
        "/api/health",
        "/api/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


class RateLimitMiddleware:
    """Apply separate read/write budgets per connected client address.

    Defaults are deliberately generous for the five-minute demo: 120 reads
    and 30 writes per minute. A contract page polling every two seconds stays
    below the read budget while an accidental write loop is stopped quickly.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[Any]],
        *,
        window_seconds: int = 60,
        read_limit: int = 120,
        write_limit: int = 30,
        max_clients: int = 10_000,
    ) -> None:
        self.app = app
        self.window_seconds = max(1, int(window_seconds))
        self.read_limit = max(1, int(read_limit))
        self.write_limit = max(1, int(write_limit))
        self.max_clients = max(1, int(max_clients))
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Local development and the test suite should remain deterministic;
        # deployment is the only posture where this edge defense is enabled.
        if get_settings().app_env.strip().lower() != "production":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if not self._should_limit(path, method):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        host = str(client[0]) if isinstance(client, (tuple, list)) and client else "unknown"
        bucket_key = f"{host}:{'write' if method in _WRITE_METHODS else 'read'}"
        limit = self.write_limit if method in _WRITE_METHODS else self.read_limit
        allowed, remaining, retry_after = self._consume(bucket_key, limit)
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "rate_limit_exceeded",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            await response(scope, receive, send)
            return

        # Keep the normal response path untouched, but expose enough metadata
        # for an operator/browser to understand a throttled client.
        async def send_with_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.extend(
                    [
                        (b"x-ratelimit-limit", str(limit).encode("ascii")),
                        (b"x-ratelimit-remaining", str(remaining).encode("ascii")),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _should_limit(path: str, method: str) -> bool:
        if method == "OPTIONS" or path in _EXEMPT_PATHS:
            return False
        # The gateway signs and retries this endpoint independently; its HMAC
        # gate is the correct abuse control and must not be rate-limited here.
        if path == "/api/webhooks/razorpay":
            return False
        return path.startswith("/api/")

    def _consume(self, key: str, limit: int) -> tuple[bool, int, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_clients:
                    self._buckets.pop(next(iter(self._buckets)), None)
                bucket = deque()
                self._buckets[key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, math.ceil(bucket[0] + self.window_seconds - now))
                return False, 0, retry_after
            bucket.append(now)
            return True, limit - len(bucket), 0


__all__ = ["RateLimitMiddleware"]
