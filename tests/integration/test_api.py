"""Tests for the Control Plane API."""

from collections.abc import Generator

import pytest
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import agent_catalog, app


@pytest.fixture(autouse=True)
def clear_catalog() -> Generator[None, None, None]:
    """Clear the agent catalog before each test."""
    agent_catalog._records.clear()
    yield


async def test_health_live() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"


async def test_health_ready() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"


async def test_create_agent() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/agents",
            json={"name": "test-agent", "description": "A test agent"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-agent"
        assert data["status"] == "draft"


async def test_create_agent_from_template() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/agents",
            json={"name": "support-agent", "template": "support"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "support-agent"


async def test_list_agents() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/agents", json={"name": "agent-1"})
        await client.post("/agents", json={"name": "agent-2"})

        response = await client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["agents"]) == 2


async def test_get_agent() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/agents", json={"name": "test-agent"})
        agent_id = create_response.json()["agent_id"]

        response = await client.get(f"/agents/{agent_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "test-agent"


async def test_get_agent_not_found() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/agents/nonexistent")
        assert response.status_code == 404


async def test_update_agent() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/agents", json={"name": "test-agent"})
        agent_id = create_response.json()["agent_id"]

        response = await client.patch(
            f"/agents/{agent_id}",
            json={"description": "Updated description"},
        )
        assert response.status_code == 200
        assert response.json()["description"] == "Updated description"


async def test_disable_agent() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/agents", json={"name": "test-agent"})
        agent_id = create_response.json()["agent_id"]

        response = await client.post(f"/agents/{agent_id}/disable")
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"


async def test_delete_agent() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/agents", json={"name": "test-agent"})
        agent_id = create_response.json()["agent_id"]

        response = await client.delete(f"/agents/{agent_id}")
        assert response.status_code == 204

        response = await client.get(f"/agents/{agent_id}")
        assert response.status_code == 404


async def test_create_agent_version() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/agents", json={"name": "test-agent"})
        agent_id = create_response.json()["agent_id"]

        response = await client.post(
            f"/agents/{agent_id}/versions",
            params={"version": "2.0.0", "change_summary": "Major update"},
        )
        assert response.status_code == 200
        assert response.json()["current_version"] == "2.0.0"


async def test_list_agents_with_filter() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/agents", json={"name": "agent-1"})
        create_response = await client.post("/agents", json={"name": "agent-2"})
        agent_id = create_response.json()["agent_id"]
        await client.post(f"/agents/{agent_id}/disable")

        response = await client.get("/agents", params={"status": "disabled"})
        assert response.status_code == 200
        assert response.json()["total"] == 1
