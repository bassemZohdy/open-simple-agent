from pathlib import Path
from typing import Any

import pytest

from osa.control_plane.backend.agent_catalog import AgentRecord, AgentRecordStatus
from osa.control_plane.backend.deployment_service import DeploymentError, DeploymentService
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
