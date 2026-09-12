"""Small, opt-in HTTP rate and concurrency guard.

The default is disabled. When enabled, this middleware applies a fixed-window
request budget per route and caller identity, returns standard retry headers,
and keeps labels bounded. It is intentionally process-local; production
replicas should use the same policy at an API gateway or service mesh until a
shared store is selected for the deployment.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.types import Receive, Scope, Send

RATE_LIMIT_REQUESTS_ENV_VAR = "OSA_RATE_LIMIT_REQUESTS"
RATE_LIMIT_WINDOW_ENV_VAR = "OSA_RATE_LIMIT_WINDOW_SECONDS"
RATE_LIMIT_BURST_ENV_VAR = "OSA_RATE_LIMIT_BURST"


@dataclass(frozen=True)
class RateLimitConfig:
    """Operator-owned request budget."""

    requests: int
    window_seconds: int = 60
    burst: int | None = None

    @property
    def enabled(self) -> bool:
        return self.requests > 0

    @property
    def capacity(self) -> int:
        return self.burst if self.burst is not None else self.requests

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> RateLimitConfig:
        values = os.environ if environ is None else environ
        requests = _positive_or_zero(values.get(RATE_LIMIT_REQUESTS_ENV_VAR), 0, RATE_LIMIT_REQUESTS_ENV_VAR)
        window = _positive_or_zero(values.get(RATE_LIMIT_WINDOW_ENV_VAR), 60, RATE_LIMIT_WINDOW_ENV_VAR)
        burst_value = values.get(RATE_LIMIT_BURST_ENV_VAR)
        burst = (
            None
            if burst_value is None or not burst_value.strip()
            else _positive_or_zero(burst_value, requests, RATE_LIMIT_BURST_ENV_VAR)
        )
        if burst is not None and burst < 1:
            raise ValueError(f"{RATE_LIMIT_BURST_ENV_VAR} must be positive")
        return cls(requests=requests, window_seconds=window, burst=burst)


def _positive_or_zero(raw: str | None, default: int, name: str) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0 or (name != RATE_LIMIT_REQUESTS_ENV_VAR and value == 0):
        message = f"{name} must be positive" if name != RATE_LIMIT_REQUESTS_ENV_VAR else f"{name} cannot be negative"
        raise ValueError(message)
    return value


@dataclass
class _Window:
    started_at: float
    count: int = 0


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class InMemoryRateLimiter:
    """Thread-safe fixed-window limiter for one process."""

    def __init__(self, config: RateLimitConfig) -> None:
        self.config = config
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        current = time.monotonic() if now is None else now
        with self._lock:
            window = self._windows.get(key)
            if window is None or current - window.started_at >= self.config.window_seconds:
                window = _Window(started_at=current)
                self._windows[key] = window
            window.count += 1
            limit = self.config.capacity
            allowed = window.count <= limit
            remaining = max(0, limit - window.count)
            retry_after = max(1, math.ceil(self.config.window_seconds - (current - window.started_at)))
            return RateLimitDecision(allowed, limit, remaining, retry_after)


class RateLimitMiddleware:
    """ASGI middleware with bounded 429 responses and retry headers."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        config: RateLimitConfig,
        *,
        exempt_paths: frozenset[str] = frozenset(),
        limiter: InMemoryRateLimiter | None = None,
    ) -> None:
        self.app = app
        self.config = config
        self.exempt_paths = exempt_paths
        self.limiter = limiter or InMemoryRateLimiter(config)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not self.config.enabled
            or scope.get("path") in self.exempt_paths
            or scope.get("method") == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        principal = scope.get("state", {}).get("osa_principal")
        if principal is not None:
            subject = str(getattr(principal, "subject", "anonymous"))
            tenant = str(getattr(principal, "tenant_id", "") or "")
            identity = hashlib.sha256(f"{tenant}\x00{subject}".encode()).hexdigest()
        else:
            authorization = headers.get(b"authorization", b"")
        if principal is None and authorization:
            identity = hashlib.sha256(authorization).hexdigest()
        elif principal is None:
            client = scope.get("client")
            identity = str(client[0] if client else "anonymous")
        key = f"{scope.get('method', 'GET')}:{scope.get('path', '')}:{identity}"
        decision = self.limiter.check(key)
        response_headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Reset": str(decision.retry_after_seconds),
        }
        if not decision.allowed:
            from fastapi.responses import JSONResponse

            response_headers["Retry-After"] = str(decision.retry_after_seconds)
            response = JSONResponse(
                {"error": {"code": "rate_limit_exceeded", "message": "Request rate limit exceeded"}},
                status_code=429,
                headers=response_headers,
            )
            await response(scope, receive, send)
            return

        async def send_with_headers(message: object) -> None:
            if isinstance(message, dict) and message.get("type") == "http.response.start":
                raw_headers = list(message.get("headers", []))
                raw_headers.extend((name.lower().encode(), value.encode()) for name, value in response_headers.items())
                message = {**message, "headers": raw_headers}
            await send(message)  # type: ignore[arg-type]

        await self.app(scope, receive, send_with_headers)


def add_rate_limit_middleware(app: object, *, exempt_paths: frozenset[str]) -> RateLimitConfig:
    """Attach the configured limiter and return the effective policy."""
    config = RateLimitConfig.from_env()
    if config.enabled:
        app.add_middleware(RateLimitMiddleware, config=config, exempt_paths=exempt_paths)  # type: ignore[attr-defined]
    return config


__all__ = [
    "InMemoryRateLimiter",
    "RateLimitConfig",
    "RateLimitDecision",
    "RateLimitMiddleware",
    "add_rate_limit_middleware",
]
