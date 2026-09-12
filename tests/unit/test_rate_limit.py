"""Rate-limit policy and isolation tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from osa.generic_agent import InMemoryRateLimiter, RateLimitConfig


def test_rate_limit_defaults_are_disabled() -> None:
    assert not RateLimitConfig.from_env({}).enabled


def test_rate_limit_isolated_by_key_and_resets() -> None:
    limiter = InMemoryRateLimiter(RateLimitConfig(requests=2, window_seconds=10))
    assert limiter.check("tenant-a", now=0).allowed
    assert limiter.check("tenant-a", now=1).allowed
    rejected = limiter.check("tenant-a", now=2)
    assert not rejected.allowed
    assert rejected.retry_after_seconds == 8
    assert limiter.check("tenant-b", now=2).allowed
    assert limiter.check("tenant-a", now=10).allowed


def test_rate_limit_rejects_invalid_environment_values() -> None:
    with pytest.raises(ValueError, match="OSA_RATE_LIMIT_REQUESTS"):
        RateLimitConfig.from_env({"OSA_RATE_LIMIT_REQUESTS": "-1"})


@pytest.mark.asyncio
async def test_middleware_returns_retry_contract_and_exempts_health() -> None:
    from osa.generic_agent.rate_limit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        config=RateLimitConfig(requests=1, window_seconds=60),
        exempt_paths=frozenset({"/health/live"}),
    )

    @app.get("/health/live")
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/limited")
    async def limited() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/invoke/stream")
    async def stream() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/a2a")
    async def a2a() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health/live")).status_code == 200
        first = await client.get("/limited", headers={"Authorization": "Bearer token-a"})
        second = await client.get("/limited", headers={"Authorization": "Bearer token-a"})
        other = await client.get("/limited", headers={"Authorization": "Bearer token-b"})
        stream_first = await client.post("/v1/invoke/stream", headers={"Authorization": "Bearer token-a"})
        stream_second = await client.post("/v1/invoke/stream", headers={"Authorization": "Bearer token-a"})
        a2a_response = await client.post("/a2a", headers={"Authorization": "Bearer token-a"})

    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "1"
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"
    assert second.json()["error"]["code"] == "rate_limit_exceeded"
    assert other.status_code == 200
    assert stream_first.status_code == 200
    assert stream_second.status_code == 429
    assert a2a_response.status_code == 200
