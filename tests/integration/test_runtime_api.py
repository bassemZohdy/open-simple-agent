"""Tests for the Agent Runtime HTTP API."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from osa.generic_agent import AgentDefinition, AgentMetadataConfig, AgentSpec, InMemoryAuditEventSink, ModelRef
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
    sink = runtime_app.state.audit_sink
    if isinstance(sink, InMemoryAuditEventSink):
        sink.events.clear()
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
            headers={"X-Request-ID": "runtime-test-1"},
            json={"input": "Hello, agent!"},
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "runtime-test-1"
        data = response.json()
        assert data["output"] == "I'm a runtime agent. How can I help?"
        assert data["error"] is None
        assert "invocation_id" in data
        events = runtime_app.state.audit_sink.events
        assert len(events) == 1
        assert events[0].action == "runtime.invoke"
        assert events[0].detail == {"decision": "succeeded", "status_code": 200, "method": "POST"}

        metrics = await client.get("/metrics")
        assert metrics.status_code == 200
        assert "osa_http_requests_total" in metrics.text
        assert "osa_operations_total" in metrics.text


async def test_runtime_audit_does_not_capture_prompt_or_output() -> None:
    prompt = "do not store this prompt"
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.post("/v1/invoke", json={"input": prompt})
        assert response.status_code == 200
    event = runtime_app.state.audit_sink.events[0]
    assert prompt not in str(event)
    assert "How can I help" not in str(event)


async def test_invoke_with_session_reuse() -> None:
    """A returned session ID is stable and reusable by the same caller."""
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        first = await client.post("/v1/invoke", json={"input": "Hello", "user_id": "user-1"})
        assert first.status_code == 200
        session_id = first.json()["session_id"]
        assert session_id

        second = await client.post(
            "/v1/invoke",
            json={"input": "Again", "session_id": session_id, "user_id": "user-1"},
        )
        assert second.status_code == 200
        assert second.json()["session_id"] == session_id


async def test_invoke_rejects_unknown_session_id() -> None:
    """Caller-supplied unknown session IDs are rejected, not silently created."""
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        response = await client.post(
            "/v1/invoke",
            json={"input": "Hello", "session_id": "no-such-session", "user_id": "user-1"},
        )
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "session_not_found"


async def test_invoke_rejects_foreign_user_on_session() -> None:
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        first = await client.post("/v1/invoke", json={"input": "Hello", "user_id": "user-1"})
        session_id = first.json()["session_id"]

        second = await client.post(
            "/v1/invoke",
            json={"input": "Hello", "session_id": session_id, "user_id": "user-2"},
        )
        assert second.status_code == 403
        assert second.json()["error"]["code"] == "session_access_denied"


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
