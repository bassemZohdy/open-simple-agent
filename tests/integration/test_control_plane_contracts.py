"""Control Plane API contract tests (P0.5).

Covers successful paths and every documented validation, conflict, and
not-found transition, with stable error bodies (`{"error": {"code",
"message"}}`) and no unexpected 500 responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import agent_catalog, app


@pytest.fixture(autouse=True)
def clear_catalog() -> Generator[None, None, None]:
    """Clear the agent catalog before each test."""
    agent_catalog._records.clear()
    yield


async def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _definition(name: str, **spec_overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {"instruction": "Help."}
    spec.update(spec_overrides)
    return {
        "apiVersion": "osa/v1alpha1",
        "kind": "Agent",
        "metadata": {"name": name},
        "spec": spec,
    }


async def _create(client_: AsyncClient, name: str, **payload: object) -> dict[str, object]:
    response = await client_.post("/agents", json={"name": name, **payload})
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestCreationValidation:
    async def test_template_and_definition_are_mutually_exclusive(self) -> None:
        async with await client() as c:
            response = await c.post(
                "/agents",
                json={"name": "a", "template": "generic", "definition": _definition("a")},
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "validation_error"

    async def test_draft_without_definition_is_explicit(self) -> None:
        async with await client() as c:
            response = await c.post("/agents", json={"name": "placeholder"})
            assert response.status_code == 201
            assert response.json()["status"] == "draft"

    async def test_definition_name_must_match_request_name(self) -> None:
        async with await client() as c:
            response = await c.post(
                "/agents",
                json={"name": "wanted-name", "definition": _definition("other-name")},
            )
            assert response.status_code == 422
            assert "does not match" in response.json()["error"]["message"]

    async def test_unknown_template_is_404(self) -> None:
        async with await client() as c:
            response = await c.post("/agents", json={"name": "a", "template": "no-such-template"})
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"

    async def test_duplicate_agent_name_is_409(self) -> None:
        async with await client() as c:
            await _create(c, "dup")
            response = await c.post("/agents", json={"name": "dup"})
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "conflict"

    async def test_invalid_definition_schema_is_422(self) -> None:
        async with await client() as c:
            response = await c.post(
                "/agents",
                json={"name": "a", "definition": {"apiVersion": "osa/v1alpha1", "kind": "Agent", "bogus": True}},
            )
            assert response.status_code == 422

    async def test_create_derives_skills_from_definition(self) -> None:
        async with await client() as c:
            created = await _create(
                c,
                "skilled",
                definition=_definition("skilled", skills=[{"ref": "support"}]),
            )
            assert created["skills"] == ["support"]


class TestListSemantics:
    async def test_filters_are_cumulative(self) -> None:
        async with await client() as c:
            await _create(c, "one")
            active = await _create(c, "two", definition=_definition("two"))
            await c.post(f"/agents/{active['agent_id']}/activate")
            third = await _create(c, "three", definition=_definition("three"))
            await c.post(f"/agents/{third['agent_id']}/activate")

            response = await c.get("/agents", params={"status": "active", "q": "t"})
            assert response.status_code == 200
            body = response.json()
            assert body["total"] == 2  # "two" and "three", not "one"

            response = await c.get("/agents", params={"status": "active", "q": "two"})
            assert response.json()["total"] == 1

    async def test_unknown_status_filter_is_400(self) -> None:
        async with await client() as c:
            response = await c.get("/agents", params={"status": "bogus"})
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "bad_request"

    async def test_unknown_sort_field_is_422(self) -> None:
        async with await client() as c:
            response = await c.get("/agents", params={"sort_by": "bogus"})
            assert response.status_code == 422

    async def test_pagination_windows_results(self) -> None:
        async with await client() as c:
            for index in range(5):
                await _create(c, f"agent-{index}")
            response = await c.get("/agents", params={"limit": 2, "offset": 1})
            body = response.json()
            assert body["total"] == 5
            assert [a["name"] for a in body["agents"]] == ["agent-1", "agent-2"]

    async def test_sort_order(self) -> None:
        async with await client() as c:
            await _create(c, "b-name")
            await _create(c, "a-name")
            response = await c.get("/agents", params={"sort_by": "name", "order": "desc"})
            names = [a["name"] for a in response.json()["agents"]]
            assert names == sorted(names, reverse=True)


class TestLifecycleTransitions:
    async def test_draft_to_active(self) -> None:
        async with await client() as c:
            created = await _create(c, "a", definition=_definition("a"))
            response = await c.post(f"/agents/{created['agent_id']}/activate")
            assert response.status_code == 200
            assert response.json()["status"] == "active"

    async def test_activation_requires_definition(self) -> None:
        async with await client() as c:
            created = await _create(c, "empty")
            response = await c.post(f"/agents/{created['agent_id']}/activate")
            assert response.status_code == 422

    async def test_activation_validates_resource_references(self) -> None:
        async with await client() as c:
            created = await _create(
                c,
                "refd",
                definition=_definition("refd", tools=[{"ref": "missing-tool"}]),
            )
            response = await c.post(f"/agents/{created['agent_id']}/activate")
            assert response.status_code == 422
            assert "missing-tool" in response.json()["error"]["message"]

    async def test_draft_to_disabled_is_rejected(self) -> None:
        async with await client() as c:
            created = await _create(c, "a")
            response = await c.post(f"/agents/{created['agent_id']}/disable")
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "invalid_transition"

    async def test_archived_is_terminal(self) -> None:
        async with await client() as c:
            created = await _create(c, "a", definition=_definition("a"))
            agent_id = created["agent_id"]
            assert (await c.post(f"/agents/{agent_id}/activate")).status_code == 200
            assert (await c.post(f"/agents/{agent_id}/archive")).status_code == 200
            assert (await c.post(f"/agents/{agent_id}/disable")).status_code == 400
            assert (await c.post(f"/agents/{agent_id}/activate")).status_code == 400

    async def test_transition_unknown_agent_is_404(self) -> None:
        async with await client() as c:
            response = await c.post("/agents/nope/activate")
            assert response.status_code == 404


class TestVersions:
    async def test_version_history_returns_redacted_metadata(self) -> None:
        async with await client() as c:
            created = await _create(c, "a", definition=_definition("a"))
            agent_id = str(created["agent_id"])
            snapshot = await c.post(
                f"/agents/{agent_id}/versions",
                json={"version": "1.0.0", "change_summary": "Initial release"},
            )
            assert snapshot.status_code == 201

            response = await c.get(f"/agents/{agent_id}/versions")

            assert response.status_code == 200
            versions = response.json()
            assert len(versions) == 1
            assert versions[0]["version"] == "1.0.0"
            assert versions[0]["change_summary"] == "Initial release"
            assert versions[0]["has_definition"] is True
            assert "definition" not in versions[0]

    async def test_version_is_immutable_snapshot(self) -> None:
        async with await client() as c:
            created = await _create(c, "a", definition=_definition("a"))
            agent_id = str(created["agent_id"])
            assert (await c.post(f"/agents/{agent_id}/versions", json={"version": "1.0.0"})).status_code == 201

            # Mutate the record after the snapshot.
            updated_definition = _definition("a", instruction="Changed instruction.")
            await c.patch(f"/agents/{agent_id}", json={"definition": updated_definition})

            from osa.control_plane.backend.api import agent_catalog as catalog

            record = catalog.get(str(agent_id))
            assert record is not None
            assert record.definition is not None
            assert record.versions[0].definition is not None
            assert record.versions[0].definition.spec.instruction == "Help."
            assert record.definition.spec.instruction == "Changed instruction."

    async def test_duplicate_version_is_409(self) -> None:
        async with await client() as c:
            created = await _create(c, "a", definition=_definition("a"))
            agent_id = created["agent_id"]
            first = await c.post(f"/agents/{agent_id}/versions", json={"version": "1.0.0"})
            second = await c.post(f"/agents/{agent_id}/versions", json={"version": "1.0.0"})
            assert first.status_code == 201
            assert second.status_code == 409
            assert second.json()["error"]["code"] == "conflict"

    async def test_version_without_definition_is_422(self) -> None:
        async with await client() as c:
            created = await _create(c, "empty")
            response = await c.post(f"/agents/{created['agent_id']}/versions", json={"version": "1.0.0"})
            assert response.status_code == 422


class TestOptimisticConcurrency:
    async def test_update_with_matching_expected_version(self) -> None:
        async with await client() as c:
            created = await _create(c, "a")
            agent_id = created["agent_id"]
            response = await c.patch(
                f"/agents/{agent_id}",
                json={"description": "new", "expected_version": created["current_version"]},
            )
            assert response.status_code == 200

    async def test_update_with_stale_expected_version_is_409(self) -> None:
        async with await client() as c:
            created = await _create(c, "a", definition=_definition("a"))
            agent_id = created["agent_id"]
            await c.post(f"/agents/{agent_id}/versions", json={"version": "2.0.0"})

            response = await c.patch(
                f"/agents/{agent_id}",
                json={"description": "stale write", "expected_version": "1.0.0"},
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "conflict"


class TestStableErrorSchema:
    async def test_not_found_shape(self) -> None:
        async with await client() as c:
            response = await c.get("/agents/none")
            body = response.json()
            assert response.status_code == 404
            assert set(body) == {"error"}
            assert set(body["error"]) == {"code", "message"}

    async def test_request_validation_uses_stable_shape(self) -> None:
        async with await client() as c:
            response = await c.post("/agents", json={"name": "a", "bogus_field": 1})
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "validation_error"

    async def test_no_unknown_500_on_domain_errors(self) -> None:
        async with await client() as c:
            created = await _create(c, "a")
            agent_id = created["agent_id"]
            for path in (
                f"/agents/{agent_id}/activate",
                f"/agents/{agent_id}/disable",
                f"/agents/{agent_id}/archive",
            ):
                response = await c.post(path)
                assert response.status_code < 500, (path, response.status_code)
            response = await c.delete(f"/agents/{agent_id}")
            assert response.status_code in {200, 202, 204, 400, 409, 422}
