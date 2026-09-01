"""PostgreSQL Control Plane persistence tests (P1.1, ADR-004).

Runs against a real PostgreSQL when ``OSA_TEST_DATABASE_URL`` is set (CI
service container); skipped otherwise. Covers migrations, restart survival,
two-replica consistency, unique constraints, CAS locking, and cascade
version history.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OSA_TEST_DATABASE_URL"),
        reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL Control Plane tests skipped",
    ),
]


@pytest.fixture()
def dsn() -> str:
    return os.environ["OSA_TEST_DATABASE_URL"]


@pytest.fixture(scope="module", autouse=True)
def applied_migrations() -> None:
    """Apply migrations once per module (idempotent: Alembic tracks state)."""
    from osa.control_plane.backend.db import run_migrations

    run_migrations(os.environ["OSA_TEST_DATABASE_URL"])


@pytest.fixture()
async def engine(dsn: str) -> Any:
    from osa.control_plane.backend.db import create_db_engine

    engine = create_db_engine(dsn)
    yield engine
    # Clean slate between tests: agents cascade their versions.
    from sqlalchemy import text

    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM osa_agents"))
        await connection.execute(text("DELETE FROM osa_resource_definitions"))
        await connection.execute(text("DELETE FROM osa_audit_events"))
    await engine.dispose()


def _record(name: str, definition: Any = None) -> Any:
    from osa.control_plane.backend.agent_catalog import AgentRecord

    return AgentRecord(name=name, definition=definition)


class TestMigrationsAndRestartSurvival:
    async def test_records_and_versions_survive_new_engine(self, engine: Any, dsn: str) -> None:
        from osa.control_plane.backend.agent_catalog import AgentVersion
        from osa.control_plane.backend.db import create_db_engine
        from osa.control_plane.backend.repositories import PostgresAgentRepository

        first = PostgresAgentRepository(engine)
        definition = {
            "apiVersion": "osa/v1alpha1",
            "kind": "Agent",
            "metadata": {"name": "survivor"},
            "spec": {"instruction": "Help."},
        }
        created = await first.create(_record("survivor", definition))
        await first.add_version(created.agent_id, AgentVersion(version="1.0.0"))
        await first.add_version(created.agent_id, AgentVersion(version="2.0.0"))

        # A brand-new engine simulates a restart of one replica.
        fresh_engine = create_db_engine(dsn)
        try:
            restarted = PostgresAgentRepository(fresh_engine)
            record = await restarted.get(created.agent_id)
            assert record is not None
            assert record.name == "survivor"
            assert record.current_version == "2.0.0"
            assert [v.version for v in record.versions] == ["1.0.0", "2.0.0"]
            assert record.versions[0].definition is not None
            await restarted.close()
        finally:
            await fresh_engine.dispose()

    async def test_two_replicas_share_state(self, engine: Any) -> None:
        from osa.control_plane.backend.repositories import PostgresAgentRepository

        replica_a = PostgresAgentRepository(engine)
        replica_b = PostgresAgentRepository(engine)

        created = await replica_a.create(_record("shared"))
        seen_by_b = await replica_b.get(created.agent_id)
        assert seen_by_b is not None
        assert seen_by_b.name == "shared"

        await replica_b.transition(
            created.agent_id,
            __import__(
                "osa.control_plane.backend.agent_catalog", fromlist=["AgentRecordStatus"]
            ).AgentRecordStatus.ARCHIVED,
        )
        seen_by_a = await replica_a.get(created.agent_id)
        assert seen_by_a is not None
        assert seen_by_a.status.value == "archived"

    async def test_unique_name_enforced_across_replicas(self, engine: Any) -> None:
        from osa.control_plane.backend.repositories import (
            DuplicateAgentError,
            PostgresAgentRepository,
        )

        replica_a = PostgresAgentRepository(engine)
        replica_b = PostgresAgentRepository(engine)
        await replica_a.create(_record("only-one"))
        with pytest.raises(DuplicateAgentError, match="only-one"):
            await replica_b.create(_record("only-one"))

    async def test_duplicate_version_enforced(self, engine: Any) -> None:
        from osa.control_plane.backend.agent_catalog import AgentVersion
        from osa.control_plane.backend.repositories import (
            DuplicateVersionError,
            PostgresAgentRepository,
        )

        repo = PostgresAgentRepository(engine)
        created = await repo.create(_record("versioned"))
        await repo.add_version(created.agent_id, AgentVersion(version="1.0.0"))
        with pytest.raises(DuplicateVersionError, match="1.0.0"):
            await repo.add_version(created.agent_id, AgentVersion(version="1.0.0"))

    async def test_audit_events_survive_restart(self, engine: Any, dsn: str) -> None:
        from osa.control_plane.backend.db import create_db_engine
        from osa.control_plane.backend.repositories import AuditEvent, PostgresAuditEventRepository

        first = PostgresAuditEventRepository(engine)
        await first.append(
            AuditEvent(event_id="audit-survivor", actor="user-1", action="agent.create", target="agent-1")
        )

        fresh_engine = create_db_engine(dsn)
        try:
            restarted = PostgresAuditEventRepository(fresh_engine)
            events = await restarted.list_events()
            assert [event.event_id for event in events] == ["audit-survivor"]
            assert events[0].actor == "user-1"
        finally:
            from sqlalchemy import text

            async with fresh_engine.begin() as connection:
                await connection.execute(text("DELETE FROM osa_audit_events WHERE event_id = 'audit-survivor'"))
            await fresh_engine.dispose()


class TestConcurrencyAndTransitions:
    async def test_optimistic_concurrency_cas(self, engine: Any) -> None:
        from osa.control_plane.backend.agent_catalog import AgentVersion
        from osa.control_plane.backend.repositories import (
            ConcurrentUpdateError,
            PostgresAgentRepository,
        )

        repo = PostgresAgentRepository(engine)
        created = await repo.create(_record("cas"))
        await repo.add_version(created.agent_id, AgentVersion(version="1.0.0"))

        # A stale expected_version is rejected...
        with pytest.raises(ConcurrentUpdateError):
            await repo.update(created.agent_id, expected_version="0.0.1", description="stale")
        # ...and a matching one is applied.
        updated = await repo.update(created.agent_id, expected_version="1.0.0", description="fresh")
        assert updated.description == "fresh"

    async def test_invalid_transition_rejected(self, engine: Any) -> None:
        from osa.control_plane.backend.agent_catalog import AgentRecordStatus
        from osa.control_plane.backend.repositories import (
            InvalidTransitionError,
            PostgresAgentRepository,
        )

        repo = PostgresAgentRepository(engine)
        created = await repo.create(_record("lifecycle"))
        with pytest.raises(InvalidTransitionError):
            await repo.transition(created.agent_id, AgentRecordStatus.DISABLED)

    async def test_cascade_delete_removes_versions(self, engine: Any) -> None:
        from sqlalchemy import text

        from osa.control_plane.backend.agent_catalog import AgentVersion
        from osa.control_plane.backend.repositories import PostgresAgentRepository

        repo = PostgresAgentRepository(engine)
        created = await repo.create(_record("doomed"))
        await repo.add_version(created.agent_id, AgentVersion(version="1.0.0"))
        assert await repo.delete(created.agent_id) is True

        async with engine.begin() as connection:
            count = (
                await connection.execute(
                    text("SELECT COUNT(*) FROM osa_agent_versions WHERE agent_id = :id"),
                    {"id": created.agent_id},
                )
            ).scalar()
        assert count == 0


class TestDeploymentRecordPersistence:
    async def test_deployment_records_survive_restart(self, engine: Any, dsn: str) -> None:
        from osa.control_plane.backend.db import create_db_engine
        from osa.control_plane.backend.repositories import (
            DeploymentRecord,
            PostgresDeploymentRecordRepository,
        )

        first = PostgresDeploymentRecordRepository(engine)
        record = DeploymentRecord(
            deployment_id="dep-survive-1",
            agent_id="agent-1",
            agent_name="survivor",
            version="1.0.0",
            status="running",
        )
        await first.upsert(record)

        fresh_engine = create_db_engine(dsn)
        try:
            restarted = PostgresDeploymentRecordRepository(fresh_engine)
            loaded = await restarted.get("dep-survive-1")
            assert loaded is not None
            assert loaded.agent_name == "survivor"
            assert loaded.status == "running"

            # Observed-state updates flow through the same contract.
            record.status = "stopped"
            await restarted.upsert(record)
            reloaded = await restarted.get("dep-survive-1")
            assert reloaded is not None
            assert reloaded.status == "stopped"
        finally:
            await fresh_engine.dispose()

        await first.delete("dep-survive-1")


class TestResourceDefinitionRepository:
    async def test_upsert_get_list_delete(self, engine: Any) -> None:
        from osa.control_plane.backend.repositories import PostgresResourceDefinitionRepository

        repo = PostgresResourceDefinitionRepository(engine)
        await repo.upsert("Model", "gpt", {"name": "gpt", "provider": "litellm", "model_id": "openai/gpt"})
        await repo.upsert("Model", "gpt", {"name": "gpt", "provider": "litellm", "model_id": "openai/gpt-4o"})
        await repo.upsert(
            "Model",
            "gpt",
            {"name": "gpt", "provider": "fake", "model_id": "tenant-a-model"},
            tenant_id="tenant-a",
        )
        await repo.upsert(
            "Model",
            "gpt",
            {"name": "gpt", "provider": "fake", "model_id": "tenant-b-model"},
            tenant_id="tenant-b",
        )
        await repo.upsert("Skill", "support", {"name": "support"})

        spec = await repo.get("Model", "gpt")
        assert spec is not None
        assert spec["model_id"] == "openai/gpt-4o"
        assert set(await repo.list("Model")) == {"gpt"}
        tenant_a = await repo.get("Model", "gpt", tenant_id="tenant-a")
        tenant_b = await repo.get("Model", "gpt", tenant_id="tenant-b")
        assert tenant_a is not None
        assert tenant_b is not None
        assert tenant_a["model_id"] == "tenant-a-model"
        assert tenant_b["model_id"] == "tenant-b-model"
        assert len(await repo.list_all("Model")) == 3
        assert (await repo.list("Skill"))["support"]["name"] == "support"

        assert await repo.delete("Model", "gpt") is True
        assert await repo.get("Model", "gpt") is None

    async def test_api_resource_writes_persist_across_restart(self, engine: Any, dsn: str) -> None:
        from httpx import ASGITransport, AsyncClient

        from osa.control_plane.backend.service import create_control_plane_app

        envelope = {
            "apiVersion": "osa/v1alpha1",
            "kind": "Model",
            "spec": {"name": "persistent-model", "provider": "fake", "model_id": "fake-p"},
        }
        first_app = create_control_plane_app(database_url=dsn)
        async with (
            first_app.router.lifespan_context(first_app),
            AsyncClient(transport=ASGITransport(app=first_app), base_url="http://test") as client,
        ):
            created = await client.post("/resources/Model", json=envelope)
            assert created.status_code == 201, created.text

        # A brand-new app instance (another replica / a restart) sees it.
        second_app = create_control_plane_app(database_url=dsn)
        try:
            async with (
                second_app.router.lifespan_context(second_app),
                AsyncClient(transport=ASGITransport(app=second_app), base_url="http://test") as client,
            ):
                got = await client.get("/resources/Model/persistent-model")
                assert got.status_code == 200
                assert got.json()["spec"]["model_id"] == "fake-p"
        finally:
            from osa.control_plane.backend.repositories import PostgresResourceDefinitionRepository

            cleanup = PostgresResourceDefinitionRepository(engine)
            await cleanup.delete("Model", "persistent-model")
            await cleanup.close()

    async def test_definitions_materialize_into_catalogs(self, engine: Any, dsn: str) -> None:
        from osa.control_plane.backend.repositories import PostgresResourceDefinitionRepository
        from osa.control_plane.backend.service import create_control_plane_app

        repo = PostgresResourceDefinitionRepository(engine)
        await repo.upsert(
            "Model",
            "catalog-model",
            {"name": "catalog-model", "provider": "fake", "model_id": "fake-x", "is_default": True},
        )

        app = create_control_plane_app(database_url=dsn)
        try:
            async with app.router.lifespan_context(app):
                catalogs = app.state.resource_catalogs
                assert catalogs.get_model("catalog-model").model_id == "fake-x"
        finally:
            await repo.delete("Model", "catalog-model")
