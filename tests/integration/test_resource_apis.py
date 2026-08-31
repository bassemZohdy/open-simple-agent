"""Control Plane resource and template API contract tests (P1.2)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import agent_catalog, app


@pytest.fixture(autouse=True)
def clear_state() -> Any:
    """Clear agents and resources before each test."""
    agent_catalog._records.clear()
    app.state.resource_repository._items.clear()  # noqa: SLF001 - test reset
    yield


async def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _model_spec(name: str, **overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {"name": name, "provider": "fake", "model_id": "fake-x"}
    spec.update(overrides)
    return {"apiVersion": "osa/v1alpha1", "kind": "Model", "spec": spec}


def _definition(name: str, **spec: object) -> dict[str, object]:
    return {
        "apiVersion": "osa/v1alpha1",
        "kind": "Agent",
        "metadata": {"name": name},
        "spec": {"instruction": "Help.", **spec},
    }


async def _create_agent(c: AsyncClient, name: str, **spec: object) -> str:
    response = await c.post("/agents", json={"name": name, "definition": _definition(name, **spec)})
    assert response.status_code == 201, response.text
    return str(response.json()["agent_id"])


class TestResourceCrud:
    async def test_create_get_list_round_trip(self) -> None:
        async with await client() as c:
            created = await c.post("/resources/Model", json=_model_spec("gpt"))
            assert created.status_code == 201
            assert created.json()["spec"]["name"] == "gpt"

            got = await c.get("/resources/Model/gpt")
            assert got.status_code == 200
            assert got.json()["spec"]["provider"] == "fake"

            listed = await c.get("/resources/Model", params={"q": "gp"})
            assert listed.status_code == 200
            body: dict[str, Any] = listed.json()
            assert body["total"] == 1
            resources: list[dict[str, Any]] = body["resources"]
            assert resources[0]["spec"]["name"] == "gpt"

            empty: dict[str, Any] = (await c.get("/resources/Model", params={"q": "zzz"})).json()
            assert empty["total"] == 0

    async def test_duplicate_create_is_409(self) -> None:
        async with await client() as c:
            assert (await c.post("/resources/Model", json=_model_spec("dup"))).status_code == 201
            conflict = await c.post("/resources/Model", json=_model_spec("dup"))
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "conflict"

    async def test_put_replaces_and_validates_name_match(self) -> None:
        async with await client() as c:
            await c.post("/resources/Model", json=_model_spec("m1"))
            replaced = _model_spec("m1", model_id="fake-y")
            response = await c.put("/resources/Model/m1", json=replaced)
            assert response.status_code == 200
            assert response.json()["spec"]["model_id"] == "fake-y"
            assert (await c.get("/resources/Model/m1")).json()["spec"]["model_id"] == "fake-y"

            mismatch = await c.put("/resources/Model/m1", json=_model_spec("other"))
            assert mismatch.status_code == 422

            missing = await c.put("/resources/Model/ghost", json=_model_spec("ghost"))
            assert missing.status_code == 404

    async def test_invalid_spec_is_422(self) -> None:
        async with await client() as c:
            response = await c.post(
                "/resources/Model",
                json={"apiVersion": "osa/v1alpha1", "kind": "Model", "spec": {"name": "bad", "bogus": True}},
            )
            assert response.status_code == 422

    async def test_unknown_kind_is_404(self) -> None:
        async with await client() as c:
            response = await c.get("/resources/Widget")
            assert response.status_code == 404
            assert "Supported kinds" in response.json()["error"]["message"]

    async def test_delete_missing_is_404(self) -> None:
        async with await client() as c:
            response = await c.delete("/resources/Model/ghost")
            assert response.status_code == 404


class TestAllKinds:
    async def test_every_kind_supports_crud(self) -> None:
        specs: dict[str, dict[str, Any]] = {
            "Model": {"name": "r1", "provider": "fake", "model_id": "m"},
            "Tool": {"name": "r2", "description": "t"},
            "Skill": {"name": "r3", "description": "s"},
            "Mcp": {"name": "r4", "transport": "stdio", "command": "echo"},
            "MemoryPolicy": {"name": "r5", "scope": "user", "max_entries": 5},
        }
        async with await client() as c:
            for kind, spec in specs.items():
                envelope = {"apiVersion": "osa/v1alpha1", "kind": kind, "spec": spec}
                assert (await c.post(f"/resources/{kind}", json=envelope)).status_code == 201
                resource_name = str(spec["name"])
                got = await c.get(f"/resources/{kind}/{resource_name}")
                assert got.status_code == 200, (kind, got.text)
                deleted = await c.delete(f"/resources/{kind}/{resource_name}")
                assert deleted.status_code == 204, (kind, deleted.text)


class TestReferenceChecks:
    async def test_referenced_model_cannot_be_deleted(self) -> None:
        async with await client() as c:
            await c.post("/resources/Model", json=_model_spec("used-model"))
            await _create_agent(c, "consumer", model={"ref": "used-model"})

            response = await c.delete("/resources/Model/used-model")
            assert response.status_code == 409
            assert "consumer" in response.json()["error"]["message"]

    async def test_unreferenced_model_deletes(self) -> None:
        async with await client() as c:
            await c.post("/resources/Model", json=_model_spec("free-model"))
            assert (await c.delete("/resources/Model/free-model")).status_code == 204

    async def test_referenced_tool_and_memory_policy_blocked(self) -> None:
        async with await client() as c:
            await c.post(
                "/resources/Tool", json={"apiVersion": "osa/v1alpha1", "kind": "Tool", "spec": {"name": "calc"}}
            )
            await c.post(
                "/resources/MemoryPolicy",
                json={"apiVersion": "osa/v1alpha1", "kind": "MemoryPolicy", "spec": {"name": "org-memory"}},
            )
            await _create_agent(
                c,
                "tool-user",
                tools=[{"ref": "calc"}],
                memory={"enabled": True, "policy": "org-memory"},
            )

            assert (await c.delete("/resources/Tool/calc")).status_code == 409
            assert (await c.delete("/resources/MemoryPolicy/org-memory")).status_code == 409


class TestSecretRedaction:
    async def test_credential_reference_exposes_no_values(self) -> None:
        async with await client() as c:
            envelope = {
                "apiVersion": "osa/v1alpha1",
                "kind": "Model",
                "spec": {
                    "name": "secure-model",
                    "provider": "litellm",
                    "model_id": "openai/gpt-4o-mini",
                    "credential_ref": {"source": "env", "key": "OPENAI_API_KEY", "env_var": "OPENAI_API_KEY"},
                },
            }
            created = await c.post("/resources/Model", json=envelope)
            assert created.status_code == 201
            body = created.json()
            reference = body["spec"]["credential_ref"]
            assert set(reference) <= {"source", "key", "env_var"}
            # The value coordinates only; never a resolved secret.
            assert all(v and "sk-" not in str(v) for v in reference.values())

            fetched = await c.get("/resources/Model/secure-model")
            assert set(fetched.json()["spec"]["credential_ref"]) <= {"source", "key", "env_var"}


class TestImportExport:
    async def test_import_then_export_round_trip(self) -> None:
        async with await client() as c:
            bundle = {
                "resources": [
                    _model_spec("io-model"),
                    {"apiVersion": "osa/v1alpha1", "kind": "Skill", "spec": {"name": "io-skill"}},
                ]
            }
            imported = await c.post("/resources/import", json=bundle)
            assert imported.status_code == 200
            report = imported.json()
            assert report["imported"]["Model"] == ["io-model"]
            assert report["imported"]["Skill"] == ["io-skill"]

            exported = await c.get("/resources/export")
            assert exported.status_code == 200
            names = {(e["kind"], e["spec"]["name"]) for e in exported.json()["resources"]}
            assert ("Model", "io-model") in names
            assert ("Skill", "io-skill") in names

    async def test_import_replaces_existing(self) -> None:
        async with await client() as c:
            await c.post("/resources/Model", json=_model_spec("io-model"))
            replaced = _model_spec("io-model", model_id="fake-new")
            imported = await c.post("/resources/import", json={"resources": [replaced]})
            assert imported.status_code == 200
            got = await c.get("/resources/Model/io-model")
            assert got.json()["spec"]["model_id"] == "fake-new"

    async def test_import_invalid_resource_is_422(self) -> None:
        async with await client() as c:
            response = await c.post(
                "/resources/import",
                json={"resources": [{"apiVersion": "osa/v1alpha1", "kind": "Widget", "spec": {}}]},
            )
            assert response.status_code == 422


class TestTemplates:
    async def test_templates_listed_read_only(self) -> None:
        async with await client() as c:
            response = await c.get("/templates")
            assert response.status_code == 200
            names = {t["name"] for t in response.json()}
            assert {"generic", "support", "research"} <= names
