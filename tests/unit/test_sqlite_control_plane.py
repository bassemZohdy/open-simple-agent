"""Control Plane local SQLite provider acceptance tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_control_plane_sqlite_schema_and_resources_survive_restart(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")

    from osa.control_plane.backend.db import run_migrations
    from osa.control_plane.backend.service import create_control_plane_app

    dsn = f"sqlite+aiosqlite:///{tmp_path / 'control-plane.db'}"
    run_migrations(dsn)

    app = create_control_plane_app(database_url=dsn)
    async with app.router.lifespan_context(app):
        await app.state.resource_repository.upsert(
            "Model",
            "local-model",
            {"name": "local-model", "provider": "fake", "model_id": "local"},
            tenant_id="acme",
        )
        assert await app.state.resource_repository.get("Model", "local-model", tenant_id="acme") is not None

    second_app = create_control_plane_app(database_url=dsn)
    async with second_app.router.lifespan_context(second_app):
        stored = await second_app.state.resource_repository.get("Model", "local-model", tenant_id="acme")
        assert stored is not None
        assert stored["model_id"] == "local"


@pytest.mark.asyncio
async def test_control_plane_sqlite_requires_explicit_migration(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")

    from osa.control_plane.backend.agent_catalog import AgentCatalogError
    from osa.control_plane.backend.service import create_control_plane_app

    dsn = f"sqlite+aiosqlite:///{tmp_path / 'unmigrated.db'}"
    app = create_control_plane_app(database_url=dsn)
    with pytest.raises(AgentCatalogError, match="osa-cp-migrate"):
        async with app.router.lifespan_context(app):
            pass
