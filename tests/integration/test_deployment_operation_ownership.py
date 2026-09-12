"""PostgreSQL deployment-operation ownership acceptance tests."""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OSA_TEST_DATABASE_URL"),
    reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL ownership tests skipped",
)


@pytest.fixture(scope="module", autouse=True)
def applied_migrations() -> Any:
    from osa.control_plane.backend.db import run_migrations

    run_migrations(os.environ["OSA_TEST_DATABASE_URL"])


@pytest.fixture()
async def engine() -> Any:
    from osa.control_plane.backend.db import create_db_engine

    database_engine = create_db_engine(os.environ["OSA_TEST_DATABASE_URL"])
    from sqlalchemy import text

    async with database_engine.begin() as connection:
        await connection.execute(text("DELETE FROM osa_deployment_operation_owners"))
    yield database_engine
    async with database_engine.begin() as connection:
        await connection.execute(text("DELETE FROM osa_deployment_operation_owners"))
    await database_engine.dispose()


class TestPostgresDeploymentOperationOwnership:
    async def test_two_workers_serialize_and_fence_late_results(self, engine: Any) -> None:
        from osa.control_plane.backend.deployment_ownership import PostgresDeploymentOperationOwnershipStore

        first_worker = PostgresDeploymentOperationOwnershipStore(engine, owner_id="worker-a", lease_seconds=5)
        second_worker = PostgresDeploymentOperationOwnershipStore(engine, owner_id="worker-b", lease_seconds=5)
        await first_worker.initialize()
        await second_worker.initialize()

        first = await first_worker.acquire("tenant-a", "deployment:one", "op-a")
        assert first is not None
        assert await second_worker.acquire("tenant-a", "deployment:one", "op-b") is None

        assert await first_worker.release(first, "completed")
        second = await second_worker.acquire("tenant-a", "deployment:one", "op-b")
        assert second is not None
        assert second.fencing_epoch > first.fencing_epoch
        assert not await first_worker.heartbeat(first)
        assert not await first_worker.release(first, "late")
        assert await second_worker.release(second, "completed")

    async def test_expired_lease_can_be_taken_over_and_tenants_are_isolated(self, engine: Any) -> None:
        from sqlalchemy import text

        from osa.control_plane.backend.deployment_ownership import (
            PostgresDeploymentOperationOwnershipStore,
        )

        first_worker = PostgresDeploymentOperationOwnershipStore(engine, owner_id="worker-a", lease_seconds=5)
        second_worker = PostgresDeploymentOperationOwnershipStore(engine, owner_id="worker-b", lease_seconds=5)
        first = await first_worker.acquire("tenant-a", "deployment:one", "op-a")
        assert first is not None
        tenant_b = await second_worker.acquire("tenant-b", "deployment:one", "op-b")
        assert tenant_b is not None

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE osa_deployment_operation_owners "
                    "SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                    "WHERE tenant_id = 'tenant-a' AND resource_id = 'deployment:one'"
                )
            )
        takeover = await second_worker.acquire("tenant-a", "deployment:one", "op-c")

        assert takeover is not None
        assert takeover.fencing_epoch == first.fencing_epoch + 1
        assert await second_worker.release(takeover, "completed")
        assert await second_worker.release(tenant_b, "completed")

    async def test_postgres_record_write_is_fenced(self, engine: Any) -> None:
        from osa.control_plane.backend.deployment_ownership import PostgresDeploymentOperationOwnershipStore
        from osa.control_plane.backend.repositories import DeploymentRecord, PostgresDeploymentRecordRepository

        worker = PostgresDeploymentOperationOwnershipStore(engine, owner_id="worker-a", lease_seconds=5)
        lease = await worker.acquire("tenant-a", "deployment:one", "op-a")
        assert lease is not None
        records = PostgresDeploymentRecordRepository(engine)
        record = DeploymentRecord(
            deployment_id="deployment-one",
            agent_id="agent-one",
            tenant_id="tenant-a",
            agent_name="agent",
            version="1.0.0",
            status="running",
        )
        assert await records.upsert_if_owned(record, lease)
        assert await worker.release(lease, "completed")

        record.status = "stopped"
        assert not await records.upsert_if_owned(record, lease)
        stored = await records.get(record.deployment_id)
        assert stored is not None
        assert stored.status == "running"
