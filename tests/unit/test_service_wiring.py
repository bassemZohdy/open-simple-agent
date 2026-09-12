"""Control Plane service assembly wiring tests (BF3 regression)."""

import pytest

from osa.control_plane.backend.deployment import LocalDeploymentProvider
from osa.control_plane.backend.deployment_service import DeploymentError, create_deployment_provider
from osa.control_plane.backend.repositories import (
    InMemoryDeploymentRecordRepository,
    PostgresDeploymentRecordRepository,
)
from osa.control_plane.backend.service import create_control_plane_app


def test_dsn_selects_postgres_deployment_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured DSN must wire durable deployment records (ADR-004)."""
    monkeypatch.setenv("OSA_DEPLOY_PROVIDER", "kubernetes")
    monkeypatch.setenv("OSA_KUBERNETES_IMAGE", "ghcr.io/example/osa-runtime:ci")
    app = create_control_plane_app(database_url="postgresql+asyncpg://osa:osa@localhost:5432/osa")

    assert isinstance(app.state.deployment_service._records, PostgresDeploymentRecordRepository)


def test_in_memory_app_keeps_in_memory_deployment_records() -> None:
    app = create_control_plane_app()

    assert isinstance(app.state.deployment_service._records, InMemoryDeploymentRecordRepository)


def test_local_provider_is_the_development_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSA_DEPLOY_PROVIDER", raising=False)
    assert isinstance(create_deployment_provider(), LocalDeploymentProvider)


def test_durable_control_plane_requires_shared_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSA_DEPLOY_PROVIDER", raising=False)
    with pytest.raises(DeploymentError, match="requires OSA_DEPLOY_PROVIDER=kubernetes"):
        create_deployment_provider(require_shared=True)
