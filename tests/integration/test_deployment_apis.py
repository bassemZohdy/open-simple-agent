"""Control Plane deployment API contract tests (P1.5).

Uses a scripted fake provider (no process spawning) to verify the API
contract: deploy requires an active agent with a definition, launch commands
are server-owned (never accepted from requests), status/stop/restart/logs/
rollback flows persist through the DeploymentRecordRepository.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import agent_catalog, app
from osa.control_plane.backend.deployment import (
    Deployment,
    DeploymentProvider,
    DeploymentSpec,
    DeploymentStatus,
)


class ScriptedProvider(DeploymentProvider):
    """Records requested specs and reports scripted statuses."""

    def __init__(self) -> None:
        self.requests: list[DeploymentSpec] = []
        self.status_override: DeploymentStatus = DeploymentStatus.RUNNING
        self.last: Deployment | None = None
        self.stopped: bool = False

    async def deploy(self, spec: DeploymentSpec) -> Deployment:
        self.requests.append(spec)
        self.stopped = False
        self.last = Deployment(
            deployment_id=f"dep-{len(self.requests)}",
            agent_id=spec.agent_id,
            status=self.status_override,
            pid=4321 + len(self.requests),
            label=spec.label,
            health_check_url=spec.health_check_url,
        )
        return self.last

    async def restart(self, deployment_id: str) -> Deployment:
        self.requests.append(DeploymentSpec(agent_id="restart", command=[]))
        assert self.last is not None
        self.last.status = DeploymentStatus.RUNNING
        return self.last

    async def stop(self, deployment_id: str) -> Deployment:
        self.stopped = True
        assert self.last is not None
        self.last.status = DeploymentStatus.STOPPED
        return self.last

    async def status(self, deployment_id: str) -> Deployment:
        assert self.last is not None
        return self.last

    async def list_deployments(self) -> list[Deployment]:
        return [self.last] if self.last is not None else []


@pytest.fixture(autouse=True)
def clear_state() -> Any:
    agent_catalog._records.clear()
    app.state.resource_repository._items.clear()  # noqa: SLF001 - test reset
    yield


@pytest.fixture()
def provider() -> ScriptedProvider:
    scripted = ScriptedProvider()
    app.state.deployment_service._provider = scripted  # noqa: SLF001 - test injection
    return scripted


async def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _definition(name: str, **spec: object) -> dict[str, object]:
    return {
        "apiVersion": "osa/v1alpha1",
        "kind": "Agent",
        "metadata": {"name": name},
        "spec": {"instruction": "Help.", **spec},
    }


async def _active_agent(c: AsyncClient, name: str = "deployable") -> str:
    created = await c.post(
        "/agents",
        json={"name": name, "definition": _definition(name)},
    )
    assert created.status_code == 201
    agent_id = str(created.json()["agent_id"])
    activated = await c.post(f"/agents/{agent_id}/activate")
    assert activated.status_code == 200
    return agent_id


class TestDeployContract:
    async def test_deploy_active_agent(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            agent_id = await _active_agent(c)
            response = await c.post(f"/agents/{agent_id}/deploy", json={})
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["agent_id"] == agent_id
            assert body["status"] == "running"
            assert body["deployment_id"] == "dep-1"

    async def test_deploy_without_invoke_url_template_omits_invoke_url(
        self, provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OSA_DEPLOY_INVOKE_URL_TEMPLATE", raising=False)
        async with await client() as c:
            agent_id = await _active_agent(c)
            response = await c.post(f"/agents/{agent_id}/deploy", json={})
            assert response.status_code == 201
            assert response.json()["invoke_url"] is None

    async def test_deploy_synthesizes_invoke_url_from_server_template(
        self, provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "OSA_DEPLOY_INVOKE_URL_TEMPLATE",
            "https://agents.example.test/{agent_id}/{deployment_id}",
        )
        async with await client() as c:
            agent_id = await _active_agent(c)
            response = await c.post(f"/agents/{agent_id}/deploy", json={})
            assert response.status_code == 201
            body = response.json()
            assert body["invoke_url"] == f"https://agents.example.test/{agent_id}/dep-1"
            # The synthesized endpoint persists with the record.
            status = await c.get(f"/deployments/dep-1")
            assert status.json()["invoke_url"] == body["invoke_url"]

            # The command is synthesized server-side from the record.
            assert len(provider.requests) == 1
            spec = provider.requests[0]
            assert spec.command[0].endswith("osa-runtime") or "osa-runtime" in spec.command[0]
            assert any(part == "--config" for part in spec.command)

    async def test_deploy_rejects_command_fields(self) -> None:
        """Arbitrary process commands are never accepted from the API."""
        async with await client() as c:
            agent_id = await _active_agent(c)
            response = await c.post(
                f"/agents/{agent_id}/deploy",
                json={"command": ["rm", "-rf", "/"], "image": "evil:latest"},
            )
            assert response.status_code == 422

    async def test_deploy_requires_existing_agent(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            response = await c.post("/agents/ghost/deploy", json={})
            assert response.status_code == 404

    async def test_deploy_requires_definition(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            created = await c.post("/agents", json={"name": "hollow"})
            agent_id = str(created.json()["agent_id"])
            response = await c.post(f"/agents/{agent_id}/deploy", json={})
            assert response.status_code == 422
            assert "no definition" in response.json()["error"]["message"]

    async def test_deploy_requires_active_agent(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            created = await c.post("/agents", json={"name": "drafty", "definition": _definition("drafty")})
            agent_id = str(created.json()["agent_id"])
            response = await c.post(f"/agents/{agent_id}/deploy", json={})
            assert response.status_code == 422
            assert "active" in response.json()["error"]["message"]

    async def test_failed_provider_start_records_failure(self, provider: ScriptedProvider) -> None:
        provider.status_override = DeploymentStatus.FAILED
        async with await client() as c:
            agent_id = await _active_agent(c)
            response = await c.post(f"/agents/{agent_id}/deploy", json={})
            assert response.status_code == 201
            body = response.json()
            assert body["status"] == "failed"
            # The failure is persisted (visible through the status route).
            status = await c.get(f"/deployments/{body['deployment_id']}")
            assert status.json()["status"] == "failed"


class TestDeploymentLifecycle:
    async def test_status_and_stop(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            agent_id = await _active_agent(c)
            deployed = (await c.post(f"/agents/{agent_id}/deploy", json={})).json()
            deployment_id = str(deployed["deployment_id"])

            stopped = await c.post(f"/deployments/{deployment_id}/stop")
            assert stopped.status_code == 200
            assert stopped.json()["status"] == "stopped"

            unknown = await c.get("/deployments/ghost")
            assert unknown.status_code == 404

    async def test_restart(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            agent_id = await _active_agent(c)
            deployed = (await c.post(f"/agents/{agent_id}/deploy", json={})).json()
            deployment_id = str(deployed["deployment_id"])
            restarted = await c.post(f"/deployments/{deployment_id}/restart")
            assert restarted.status_code == 200
            assert restarted.json()["deployment_id"] == deployment_id

    async def test_agent_deployment_history(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            agent_id = await _active_agent(c)
            await c.post(f"/agents/{agent_id}/deploy", json={})
            history = await c.get(f"/agents/{agent_id}/deployments")
            assert history.status_code == 200
            assert [d["agent_id"] for d in history.json()] == [agent_id]

    async def test_logs_endpoint(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            agent_id = await _active_agent(c)
            deployed = (await c.post(f"/agents/{agent_id}/deploy", json={})).json()
            deployment_id = str(deployed["deployment_id"])
            logs = await c.get(f"/deployments/{deployment_id}/logs", params={"tail": 10})
            assert logs.status_code == 200
            assert logs.json()["deployment_id"] == deployment_id
            assert isinstance(logs.json()["lines"], list)

    async def test_rollback_requires_version_history(self, provider: ScriptedProvider) -> None:
        async with await client() as c:
            agent_id = await _active_agent(c)
            deployed = (await c.post(f"/agents/{agent_id}/deploy", json={})).json()
            deployment_id = str(deployed["deployment_id"])
            response = await c.post(f"/deployments/{deployment_id}/rollback")
            assert response.status_code == 422
            assert "no earlier version" in response.json()["error"]["message"]
