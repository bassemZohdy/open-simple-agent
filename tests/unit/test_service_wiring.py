"""Control Plane service assembly wiring tests (BF3 regression)."""

from osa.control_plane.backend.repositories import (
    InMemoryDeploymentRecordRepository,
    PostgresDeploymentRecordRepository,
)
from osa.control_plane.backend.service import create_control_plane_app


def test_dsn_selects_postgres_deployment_records() -> None:
    """A configured DSN must wire durable deployment records (ADR-004)."""
    app = create_control_plane_app(database_url="postgresql+asyncpg://osa:osa@localhost:5432/osa")

    assert isinstance(app.state.deployment_service._records, PostgresDeploymentRecordRepository)


def test_in_memory_app_keeps_in_memory_deployment_records() -> None:
    app = create_control_plane_app()

    assert isinstance(app.state.deployment_service._records, InMemoryDeploymentRecordRepository)
