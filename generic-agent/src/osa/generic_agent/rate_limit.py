"""Small, opt-in HTTP rate and concurrency guard.

The default is disabled. When enabled, this middleware applies a fixed-window
request budget per route and caller identity, returns standard retry headers,
and keeps labels bounded. The default store is process-local; an explicit
PostgreSQL DSN enables an atomic shared store for multiple service replicas.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import os
import re
import threading
import time
from argparse import ArgumentParser
from asyncio import Lock, run
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.types import Receive, Scope, Send

RATE_LIMIT_REQUESTS_ENV_VAR = "OSA_RATE_LIMIT_REQUESTS"
RATE_LIMIT_WINDOW_ENV_VAR = "OSA_RATE_LIMIT_WINDOW_SECONDS"
RATE_LIMIT_BURST_ENV_VAR = "OSA_RATE_LIMIT_BURST"
RATE_LIMIT_DATABASE_URL_ENV_VAR = "OSA_RATE_LIMIT_DATABASE_URL"
RATE_LIMIT_TABLE_ENV_VAR = "OSA_RATE_LIMIT_TABLE"
DEFAULT_RATE_LIMIT_TABLE = "osa_rate_limit_windows"


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


class RateLimiter(Protocol):
    """Synchronous or asynchronous fixed-window decision provider."""

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision | Awaitable[RateLimitDecision]:
        """Consume one request and return its decision."""


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


class PostgresRateLimiter:
    """Atomic fixed-window limiter shared by service replicas.

    The table is deliberately small: one row per bounded route/identity key,
    with stale windows pruned during writes. PostgreSQL row-level conflict
    handling serializes concurrent requests for the same key. The implementation
    uses SQLAlchemy's async engine and accepts an async SQLAlchemy DSN.
    """

    def __init__(
        self,
        config: RateLimitConfig,
        database_url: str,
        *,
        table_name: str = DEFAULT_RATE_LIMIT_TABLE,
    ) -> None:
        if not database_url.strip():
            raise ValueError(f"{RATE_LIMIT_DATABASE_URL_ENV_VAR} must not be empty")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
            raise ValueError(f"{RATE_LIMIT_TABLE_ENV_VAR} must be a simple SQL identifier")
        self.config = config
        self.database_url = database_url
        self.table_name = table_name
        self._engine: Any = None
        self._initialized = False
        self._initialize_lock = Lock()

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from sqlalchemy.ext.asyncio import create_async_engine
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("PostgreSQL rate limiting requires the service postgres extra") from exc
        database_url = self.database_url
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self._engine = create_async_engine(database_url, pool_pre_ping=True)
        return self._engine

    async def initialize(self) -> None:
        """Create the shared window table if it is not present."""
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            try:
                from sqlalchemy import BigInteger, Column, Integer, MetaData, String, Table
            except ImportError as exc:  # pragma: no cover - optional dependency path
                raise RuntimeError("PostgreSQL rate limiting requires the service postgres extra") from exc
            metadata = MetaData()
            Table(
                self.table_name,
                metadata,
                Column("key", String(512), primary_key=True),
                Column("window_id", BigInteger, nullable=False),
                Column("count", Integer, nullable=False),
            )
            engine = self._get_engine()
            async with engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
            self._initialized = True

    async def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        """Atomically consume one request in the current wall-clock window."""
        await self.initialize()
        from sqlalchemy import text

        current = time.time() if now is None else now
        window_id = math.floor(current / self.config.window_seconds)
        limit = self.config.capacity
        table = self.table_name
        upsert = text(
            f"INSERT INTO {table} (key, window_id, count) VALUES (:key, :window_id, 1) "
            f"ON CONFLICT (key) DO UPDATE SET "
            f"window_id = EXCLUDED.window_id, "
            f"count = CASE WHEN {table}.window_id = EXCLUDED.window_id "
            f"THEN {table}.count + 1 ELSE 1 END "
            "RETURNING count, window_id"
        )
        prune = text(f"DELETE FROM {table} WHERE window_id < :oldest_window")
        async with self._get_engine().begin() as connection:
            row = (await connection.execute(upsert, {"key": key[:512], "window_id": window_id})).mappings().one()
            await connection.execute(prune, {"oldest_window": window_id - 1})
        count = int(row["count"])
        allowed = count <= limit
        remaining = max(0, limit - count)
        reset_at = (window_id + 1) * self.config.window_seconds
        retry_after = max(1, math.ceil(reset_at - current))
        return RateLimitDecision(allowed, limit, remaining, retry_after)

    async def close(self) -> None:
        """Dispose the owned SQLAlchemy engine."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
        self._initialized = False


def build_rate_limiter(config: RateLimitConfig) -> RateLimiter:
    """Select the process-local or explicitly configured shared store."""
    database_url = os.environ.get(RATE_LIMIT_DATABASE_URL_ENV_VAR)
    if database_url:
        from osa.generic_agent.persistence import require_shared_database

        require_shared_database(database_url, "HTTP rate-limit state")
        return PostgresRateLimiter(
            config,
            database_url,
            table_name=os.environ.get(RATE_LIMIT_TABLE_ENV_VAR, DEFAULT_RATE_LIMIT_TABLE),
        )
    if config.enabled:
        from osa.generic_agent.persistence import require_shared_database

        require_shared_database(None, "HTTP rate-limit state")
    return InMemoryRateLimiter(config)


class RateLimitMiddleware:
    """ASGI middleware with bounded 429 responses and retry headers."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        config: RateLimitConfig,
        *,
        exempt_paths: frozenset[str] = frozenset(),
        limiter: RateLimiter | None = None,
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
        method = str(scope.get("method", "GET"))[:16]
        path = str(scope.get("path", ""))[:256]
        key = f"{method}:{path}:{identity}"
        decision_result = self.limiter.check(key)
        decision = await decision_result if inspect.isawaitable(decision_result) else decision_result
        application = scope.get("app")
        observability = getattr(getattr(application, "state", None), "observability", None)
        metrics = getattr(observability, "metrics", None)
        if metrics is not None:
            metrics.increment(
                "osa_rate_limit_decisions_total",
                {"route": path, "decision": "allowed" if decision.allowed else "rejected"},
            )
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
        limiter = build_rate_limiter(config)
        app.state.osa_rate_limit_limiter = limiter  # type: ignore[attr-defined]
        app.add_middleware(RateLimitMiddleware, config=config, exempt_paths=exempt_paths, limiter=limiter)  # type: ignore[attr-defined]
    return config


async def initialize_rate_limit_limiter(app: object) -> None:
    """Initialize the configured shared limiter before service readiness."""
    limiter = getattr(getattr(app, "state", None), "osa_rate_limit_limiter", None)
    initialize = getattr(limiter, "initialize", None)
    if initialize is not None:
        try:
            await initialize()
        except Exception:
            close = getattr(limiter, "close", None)
            if close is not None:
                await close()
            raise


async def close_rate_limit_limiter(app: object) -> None:
    """Close the configured shared limiter at service shutdown."""
    limiter = getattr(getattr(app, "state", None), "osa_rate_limit_limiter", None)
    close = getattr(limiter, "close", None)
    if close is not None:
        await close()


async def _migrate(database_url: str, table_name: str) -> None:
    limiter = PostgresRateLimiter(RateLimitConfig(requests=1), database_url, table_name=table_name)
    await limiter.initialize()
    await limiter.close()


def migrate_cli(argv: list[str] | None = None) -> int:
    """Create the shared rate-limit table for an operator-selected database."""
    parser = ArgumentParser(prog="osa-rate-limit-migrate")
    parser.add_argument("--database-url", default=os.environ.get(RATE_LIMIT_DATABASE_URL_ENV_VAR))
    parser.add_argument("--table", default=os.environ.get(RATE_LIMIT_TABLE_ENV_VAR, DEFAULT_RATE_LIMIT_TABLE))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error(f"--database-url is required (or set {RATE_LIMIT_DATABASE_URL_ENV_VAR})")
    run(_migrate(args.database_url, args.table))
    print(f"Ensured rate-limit schema table {args.table}")
    return 0


__all__ = [
    "DEFAULT_RATE_LIMIT_TABLE",
    "InMemoryRateLimiter",
    "PostgresRateLimiter",
    "RateLimitConfig",
    "RateLimiter",
    "RateLimitDecision",
    "RateLimitMiddleware",
    "add_rate_limit_middleware",
    "build_rate_limiter",
    "close_rate_limit_limiter",
    "initialize_rate_limit_limiter",
    "migrate_cli",
]
