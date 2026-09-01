"""Control Plane audit emission and tenant filtering tests (P2.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import agent_catalog, app
from osa.control_plane.backend.repositories import AuditEvent, InMemoryAuditEventRepository

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def clear_state() -> Generator[None, None, None]:
    agent_catalog._records.clear()
    repository = app.state.audit_repository
    assert isinstance(repository, InMemoryAuditEventRepository)
    repository._events.clear()
    yield
    repository._events.clear()


def _definition(name: str) -> dict[str, object]:
    return {
        "apiVersion": "osa/v1alpha1",
        "kind": "Agent",
        "metadata": {"name": name},
        "spec": {"instruction": "Keep this private."},
    }


@pytest.mark.asyncio
async def test_management_mutations_are_audited_without_payload_values() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/agents", json={"name": "audited", "definition": _definition("audited")})
        assert created.status_code == 201, created.text
        agent_id = created.json()["agent_id"]
        assert (await client.patch(f"/agents/{agent_id}", json={"description": "updated"})).status_code == 200
        assert (await client.post(f"/agents/{agent_id}/versions", json={"version": "1.0.0"})).status_code == 201
        assert (await client.post(f"/agents/{agent_id}/activate")).status_code == 200
        assert (await client.post(f"/agents/{agent_id}/disable")).status_code == 200
        assert (await client.delete(f"/agents/{agent_id}")).status_code == 204

        response = await client.get("/audit-events")

    assert response.status_code == 200
    events = response.json()
    assert [event["action"] for event in events] == [
        "agent.create",
        "agent.update",
        "agent.version.create",
        "agent.activate",
        "agent.disable",
        "agent.delete",
    ]
    assert all(event["actor"] == "anonymous" for event in events)
    assert all("instruction" not in str(event) and "private" not in str(event) for event in events)


@pytest.mark.asyncio
async def test_audit_repository_filters_by_tenant() -> None:
    repository = InMemoryAuditEventRepository()
    await repository.append(AuditEvent(event_id="one", actor="a", action="agent.create", target="a", tenant_id="t1"))
    await repository.append(AuditEvent(event_id="two", actor="b", action="agent.create", target="b", tenant_id="t2"))
    await repository.append(AuditEvent(event_id="shared", actor="c", action="resource.create", target="c"))

    assert [event.event_id for event in await repository.list_events(tenant_id="t1")] == ["one"]
    assert [event.event_id for event in await repository.list_events(tenant_id="t2")] == ["two"]
    assert [event.event_id for event in await repository.list_events()] == ["shared"]
