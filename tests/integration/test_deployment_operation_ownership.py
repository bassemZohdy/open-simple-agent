"""PostgreSQL deployment-operation ownership acceptance tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

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

    async def test_independent_worker_processes_serialize_takeover_and_reject_late_results(
        self,
        engine: Any,
        tmp_path: Path,
    ) -> None:
        """Exercise the lease boundary across real Python process boundaries."""
        from sqlalchemy import text

        worker_script = Path(__file__).with_name("deployment_operation_worker.py")
        output_a = tmp_path / "worker-a.json"
        output_busy = tmp_path / "worker-busy.json"
        output_tenant_b = tmp_path / "worker-tenant-b.json"
        output_takeover = tmp_path / "worker-takeover.json"
        control_a = tmp_path / "release-a"
        side_effect_a = tmp_path / "provider-a.started"
        side_effect_busy = tmp_path / "provider-busy.started"
        side_effect_tenant_b = tmp_path / "provider-tenant-b.started"
        side_effect_takeover = tmp_path / "provider-takeover.started"

        def start_worker(
            owner_id: str,
            tenant_id: str,
            operation_id: str,
            output_path: Path,
            control_path: Path,
            side_effect_path: Path,
            mode: str,
        ) -> subprocess.Popen[str]:
            return subprocess.Popen(
                [
                    sys.executable,
                    str(worker_script),
                    os.environ["OSA_TEST_DATABASE_URL"],
                    owner_id,
                    tenant_id,
                    "deployment:one",
                    operation_id,
                    str(output_path),
                    str(control_path),
                    str(side_effect_path),
                    mode,
                ],
                cwd=worker_script.parents[2],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        async def wait_for_json(path: Path) -> dict[str, object]:
            deadline = asyncio.get_running_loop().time() + 15
            while not path.exists() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
            assert path.exists(), f"worker did not write {path}"
            return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))

        async def finish(process: subprocess.Popen[str]) -> tuple[str, str]:
            await asyncio.to_thread(process.wait)
            stdout, stderr = process.communicate()
            assert process.returncode == 0, stderr or stdout
            return stdout, stderr

        worker_a = start_worker(
            "worker-a",
            "tenant-a",
            "operation-a",
            output_a,
            control_a,
            side_effect_a,
            "hold",
        )
        try:
            first = await wait_for_json(output_a)
            assert first["acquired"] is True
            assert side_effect_a.read_text(encoding="utf-8") == "worker-a"

            busy = start_worker(
                "worker-b",
                "tenant-a",
                "operation-b",
                output_busy,
                tmp_path / "unused-busy-control",
                side_effect_busy,
                "once",
            )
            await finish(busy)
            assert (await wait_for_json(output_busy))["acquired"] is False
            assert not side_effect_busy.exists()

            tenant_b = start_worker(
                "worker-b",
                "tenant-b",
                "operation-b-tenant",
                output_tenant_b,
                tmp_path / "unused-tenant-control",
                side_effect_tenant_b,
                "once",
            )
            await finish(tenant_b)
            assert (await wait_for_json(output_tenant_b))["acquired"] is True
            assert side_effect_tenant_b.read_text(encoding="utf-8") == "worker-b"

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE osa_deployment_operation_owners "
                        "SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                        "WHERE tenant_id = 'tenant-a' AND resource_id = 'deployment:one'"
                    )
                )

            takeover = start_worker(
                "worker-b",
                "tenant-a",
                "operation-b-takeover",
                output_takeover,
                tmp_path / "unused-takeover-control",
                side_effect_takeover,
                "takeover",
            )
            await finish(takeover)
            takeover_result = await wait_for_json(output_takeover)
            assert takeover_result["acquired"] is True
            assert takeover_result["current_result_accepted"] is True
            assert takeover_result["released"] is True
            assert side_effect_takeover.read_text(encoding="utf-8") == "worker-b"

            control_a.touch()
            await finish(worker_a)
            late_result = await wait_for_json(output_a)
            assert late_result["late_result_accepted"] is False

            from osa.control_plane.backend.repositories import PostgresDeploymentRecordRepository

            records = PostgresDeploymentRecordRepository(engine)
            stored = await records.get("deployment-operation-acceptance")
            assert stored is not None
            assert stored.status == "current"
        finally:
            if worker_a.poll() is None:
                worker_a.terminate()
                await asyncio.to_thread(worker_a.wait)
