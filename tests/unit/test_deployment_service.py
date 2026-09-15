import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from osa.control_plane.backend.agent_catalog import AgentRecord, AgentRecordStatus
from osa.control_plane.backend.deployment import Deployment, DeploymentProvider, DeploymentStatus
from osa.control_plane.backend.deployment_errors import DeploymentError, DeploymentOperationBusyError
from osa.control_plane.backend.deployment_ownership import InMemoryDeploymentOperationOwnershipStore
from osa.control_plane.backend.deployment_service import DeploymentService, deployment_port
from osa.control_plane.backend.repositories import (
    InMemoryAgentRepository,
    InMemoryDeploymentRecordRepository,
)
from osa.control_plane.backend.resource_catalogs import ResourceCatalogs
from osa.generic_agent import AgentDefinition, AgentMetadataConfig, AgentSpec, ModelDefinition, ModelRef


def _record(*, model: str = "shared-model", tenant_id: str | None = None) -> AgentRecord:
    definition = AgentDefinition(
        metadata=AgentMetadataConfig(name="bundle-agent"),
        spec=AgentSpec(instruction="Help.", model=ModelRef(ref=model)),
    )
    return AgentRecord(
        name="bundle-agent",
        tenant_id=tenant_id,
        status=AgentRecordStatus.ACTIVE,
        definition=definition,
        current_version="1.2.3",
    )


def _service(catalogs: ResourceCatalogs) -> DeploymentService:
    provider: Any = object()
    return DeploymentService(
        provider=provider,
        record_repository=InMemoryDeploymentRecordRepository(),
        agent_repository=InMemoryAgentRepository(),
        resource_catalogs=catalogs,
    )


def test_export_uses_opaque_root_and_hashed_resource_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DEPLOY_ROOT", str(tmp_path))
    catalogs = ResourceCatalogs()
    catalogs.register_model(ModelDefinition(name="shared-model", provider="fake", model_id="fake"))
    path = Path(_service(catalogs)._export_bundle(_record(), version="1.2.3"))

    assert path.parent == tmp_path
    assert path.name.isalnum()
    assert (path / "agent.yaml").is_file()
    assert (path / "bundle.yaml").is_file()
    model_files = list((path / "models").glob("*.yaml"))
    assert len(model_files) == 1
    assert model_files[0].stem != "shared-model"


def test_export_cleans_partial_bundle_when_resource_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DEPLOY_ROOT", str(tmp_path))
    with pytest.raises(DeploymentError, match="not present"):
        _service(ResourceCatalogs())._export_bundle(_record(model="missing"))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_reconcile_provider_state_refreshes_persisted_records_after_restart() -> None:
    from osa.control_plane.backend.repositories import DeploymentRecord

    class Provider:
        async def list_deployments(self) -> list[Deployment]:
            return [Deployment(deployment_id="d-1", agent_id="agent-1", status=DeploymentStatus.RUNNING)]

    records = InMemoryDeploymentRecordRepository()
    await records.upsert(DeploymentRecord(deployment_id="d-1", agent_id="agent-1", status="starting"))
    service = DeploymentService(
        provider=cast("DeploymentProvider", Provider()),
        record_repository=records,
        agent_repository=InMemoryAgentRepository(),
        resource_catalogs=ResourceCatalogs(),
    )

    assert await service.reconcile_provider_state() == 1
    refreshed = await records.get("d-1")
    assert refreshed is not None
    assert refreshed.status == "running"


@pytest.mark.asyncio
async def test_mutating_operations_are_serialized_across_service_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingProvider(DeploymentProvider):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.deploy_count = 0

        async def deploy(self, spec: Any) -> Deployment:
            self.deploy_count += 1
            self.started.set()
            await self.release.wait()
            return Deployment(
                deployment_id=f"deployment-{self.deploy_count}",
                agent_id=spec.agent_id,
                status=DeploymentStatus.RUNNING,
            )

        async def restart(self, deployment_id: str) -> Deployment:
            raise NotImplementedError

        async def stop(self, deployment_id: str) -> Deployment:
            raise NotImplementedError

        async def status(self, deployment_id: str) -> Deployment:
            raise NotImplementedError

        async def list_deployments(self) -> list[Deployment]:
            return []

    monkeypatch.setattr(DeploymentService, "_export_bundle", lambda self, record, **kwargs: "bundle")
    agents = InMemoryAgentRepository()
    agent = _record()
    await agents.create(agent)
    records = InMemoryDeploymentRecordRepository()
    ownership = InMemoryDeploymentOperationOwnershipStore(lease_seconds=5)
    provider = BlockingProvider()
    services = [
        DeploymentService(
            provider=provider,
            record_repository=records,
            agent_repository=agents,
            resource_catalogs=ResourceCatalogs(),
            operation_ownership=ownership,
        )
        for _ in range(2)
    ]

    first_task = asyncio.create_task(services[0].deploy(agent.agent_id))
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    with pytest.raises(DeploymentOperationBusyError, match="already active"):
        await services[1].deploy(agent.agent_id)
    provider.release.set()
    result = await first_task

    assert result.status == "running"
    assert provider.deploy_count == 1


def test_deployment_port_accepts_operator_fixed_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DEPLOY_PORT", "8081")
    assert deployment_port() == 8081


def test_deployment_port_rejects_privileged_or_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DEPLOY_PORT", "80")
    with pytest.raises(DeploymentError, match="OSA_DEPLOY_PORT"):
        deployment_port()

    monkeypatch.setenv("OSA_DEPLOY_PORT", "not-a-port")
    with pytest.raises(DeploymentError, match="OSA_DEPLOY_PORT"):
        deployment_port()
