"""Small subprocess worker used by deployment ownership acceptance tests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


async def _run() -> int:
    from osa.control_plane.backend.db import create_db_engine
    from osa.control_plane.backend.deployment_ownership import PostgresDeploymentOperationOwnershipStore
    from osa.control_plane.backend.repositories import DeploymentRecord, PostgresDeploymentRecordRepository

    dsn, owner_id, tenant_token, resource_id, operation_id, output_path, control_path, side_effect_path, mode = (
        sys.argv[1:]
    )
    tenant_id = None if tenant_token == "__none__" else tenant_token
    engine = create_db_engine(dsn)
    try:
        store = PostgresDeploymentOperationOwnershipStore(engine, owner_id=owner_id, lease_seconds=5)
        lease = await store.acquire(tenant_id, resource_id, operation_id)
        if lease is None:
            Path(output_path).write_text(json.dumps({"acquired": False, "owner_id": owner_id}), encoding="utf-8")
            return 0

        Path(side_effect_path).write_text(owner_id, encoding="utf-8")
        result: dict[str, object] = {"acquired": True, "owner_id": owner_id}
        Path(output_path).write_text(json.dumps(result), encoding="utf-8")
        if mode == "hold":
            control = Path(control_path)
            while not control.exists():
                await asyncio.sleep(0.05)
            records = PostgresDeploymentRecordRepository(engine)
            record = DeploymentRecord(
                deployment_id="deployment-operation-acceptance",
                agent_id="agent-operation-acceptance",
                tenant_id=tenant_id,
                agent_name="acceptance-agent",
                version="1.0.0",
                status="late",
            )
            accepted = await records.upsert_if_owned(record, lease)
            result["late_result_accepted"] = accepted
            Path(output_path).write_text(json.dumps(result), encoding="utf-8")
            await store.release(lease, "late")
            return 0

        if mode == "takeover":
            records = PostgresDeploymentRecordRepository(engine)
            record = DeploymentRecord(
                deployment_id="deployment-operation-acceptance",
                agent_id="agent-operation-acceptance",
                tenant_id=tenant_id,
                agent_name="acceptance-agent",
                version="1.0.0",
                status="current",
            )
            result["current_result_accepted"] = await records.upsert_if_owned(record, lease)

        result["released"] = await store.release(lease, "completed")
        Path(output_path).write_text(json.dumps(result), encoding="utf-8")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
