"""Tests for the Agent Runtime HTTP API."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from osa.generic_agent import AgentDefinition, AgentMetadataConfig, AgentSpec, ModelRef
from osa.runtimes.adk.api import initialize_runtime, runtime_app


@pytest.fixture(autouse=True)
async def setup_runtime() -> AsyncGenerator[None, None]:
    """Initialize the runtime before each test."""
    definition = AgentDefinition(
        metadata=AgentMetadataConfig(name="test-agent", version="1.0.0"),
        spec=AgentSpec(
            instruction="Help users.",
            model=ModelRef(ref="default"),
        ),
    )
    await initialize_runtime(definition)
    yield
    # Reset global state
    import osa.runtimes.adk.api as api_module

    api_module._agent = None
    api_module._runtime = None


async def test_health_live() -> None:
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"


async def test_health_ready() -> None:
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"


async def test_invoke_agent() -> None:
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.post(
            "/v1/invoke",
            json={"input": "Hello, agent!"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["output"] == "I'm a runtime agent. How can I help?"
        assert data["error"] is None
        assert "invocation_id" in data


async def test_invoke_with_session() -> None:
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.post(
            "/v1/invoke",
            json={"input": "Hello", "session_id": "test-session", "user_id": "user-1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] is not None


async def test_get_capabilities() -> None:
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.get("/v1/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["agent_name"] == "test-agent"
        assert data["version"] == "1.0.0"
        assert data["session_support"] is True


async def test_invoke_without_initialization() -> None:
    import osa.runtimes.adk.api as api_module

    api_module._agent = None

    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.post("/v1/invoke", json={"input": "Hello"})
        assert response.status_code == 503
