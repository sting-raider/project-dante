"""Request-level observability for the Project Dante API.

Every HTTP request receives fresh trace and correlation identifiers. The
middleware emits one structured JSON completion record without logging request
bodies, query strings, credentials, or other user-controlled payloads. A
contract id is derived only from the URL when the route makes it explicit.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from starlette.types import Message, Receive, Scope, Send

logger = logging.getLogger("project_dante.http")

_CONTRACT_PATH = re.compile(r"^/api/contracts/([^/]+)(?:/|$)")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _contract_id(path: str) -> str | None:
    match = _CONTRACT_PATH.match(path)
    return match.group(1) if match else None


class ObservabilityMiddleware:
    """Attach request IDs and emit a safe, structured completion log record."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        trace_id = _new_id("trace")
        correlation_id = _new_id("corr")
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        contract_id = _contract_id(path)
        started = time.perf_counter()
        status_code = 500
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["trace_id"] = trace_id
            state["correlation_id"] = correlation_id
            if contract_id is not None:
                state["contract_id"] = contract_id

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                headers = list(message.get("headers") or [])
                headers.extend(
                    [
                        (b"x-trace-id", trace_id.encode("ascii")),
                        (b"x-correlation-id", correlation_id.encode("ascii")),
                    ]
                )
                if contract_id is not None:
                    headers.append((b"x-contract-id", contract_id.encode("utf-8")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as exc:
            self._log_completion(
                method=method,
                path=path,
                status_code=status_code,
                trace_id=trace_id,
                correlation_id=correlation_id,
                contract_id=contract_id,
                started=started,
                error_type=type(exc).__name__,
            )
            raise
        else:
            self._log_completion(
                method=method,
                path=path,
                status_code=status_code,
                trace_id=trace_id,
                correlation_id=correlation_id,
                contract_id=contract_id,
                started=started,
            )

    @staticmethod
    def _log_completion(
        *,
        method: str,
        path: str,
        status_code: int,
        trace_id: str,
        correlation_id: str,
        contract_id: str | None,
        started: float,
        error_type: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "event": "http_request_completed",
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "trace_id": trace_id,
            "correlation_id": correlation_id,
        }
        if contract_id is not None:
            record["contract_id"] = contract_id
        if error_type is not None:
            record["error_type"] = error_type
        logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


__all__ = ["ObservabilityMiddleware"]
