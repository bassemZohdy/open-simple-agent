"""BF4 regression: PATCH /agents/{id} must persist the derived skills column.

The in-memory repository mutates the stored record in place, which masked the
bug (``_sync_derived_fields`` mutated the same object it returned). The
PostgreSQL repository returns a freshly-read record on every operation, so the
in-place mutation was never written back. This double reproduces those
semantics so a stale skills column cannot hide.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.repositories import (
    ConcurrentUpdateError,
    InMemoryAgentRepository,
)
from osa.control_plane.backend.service import create_control_plane_app

if TYPE_CHECKING:
    from osa.control_plane.backend.agent_catalog import AgentRecord


class CopyOnWriteAgentRepository(InMemoryAgentRepository):
    """Reads and writes through copies, like the PostgreSQL repository."""

    async def create(self, record: AgentRecord) -> AgentRecord:
        return await super().create(copy.deepcopy(record))

    async def get(self, agent_id: str) -> AgentRecord | None:
        record = await super().get(agent_id)
        return copy.deepcopy(record) if record is not None else None

    async def update(self, agent_id: str, *, expected_version: str | None = None, **fields: Any) -> AgentRecord:
        record = await super().get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
        if expected_version is not None and record.current_version != expected_version:
            raise ConcurrentUpdateError(agent_id, expected_version)
        await super().update(agent_id, expected_version=expected_version, **fields)
        stored = await super().get(agent_id)
        assert stored is not None
        return copy.deepcopy(stored)


def _definition(name: str, skills: list[str]) -> dict[str, Any]:
    return {
        "apiVersion": "osa/v1alpha1",
        "kind": "Agent",
        "metadata": {"name": name},
        "spec": {"instruction": "Help.", "skills": skills},
    }


async def test_patch_definition_persists_skills() -> None:
    repository = CopyOnWriteAgentRepository()
    app = create_control_plane_app(agent_repository=repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agents",
            json={"name": "skills-agent", "definition": _definition("skills-agent", ["alpha"])},
        )
        assert created.status_code == 201, created.text
        agent_id = created.json()["agent_id"]

        patched = await client.patch(
            f"/agents/{agent_id}",
            json={"definition": _definition("skills-agent", ["beta", "gamma"])},
        )
        assert patched.status_code == 200, patched.text

        fetched = await client.get(f"/agents/{agent_id}")
        assert fetched.status_code == 200
        assert fetched.json()["skills"] == ["beta", "gamma"]

        listed = await client.get("/agents", params={"skill": "gamma"})
        assert listed.status_code == 200
        assert any(item["agent_id"] == agent_id for item in listed.json()["agents"])
