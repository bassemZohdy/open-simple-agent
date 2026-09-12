"""Single-process coverage for deployment lease and fencing semantics."""

from datetime import UTC, datetime, timedelta

import pytest

from osa.control_plane.backend.deployment_errors import DeploymentError
from osa.control_plane.backend.deployment_ownership import (
    InMemoryDeploymentOperationOwnershipStore,
    deployment_operation_lease_seconds_from_env,
)


@pytest.mark.asyncio
async def test_in_memory_store_serializes_and_fences_released_operations() -> None:
    store = InMemoryDeploymentOperationOwnershipStore(owner_id="worker-a", lease_seconds=5)

    first = await store.acquire("tenant-a", "deployment:one", "op-1")
    assert first is not None
    assert await store.acquire("tenant-a", "deployment:one", "op-2") is None
    assert await store.is_current(first)
    assert await store.release(first, "completed")

    second = await store.acquire("tenant-a", "deployment:one", "op-2")
    assert second is not None
    assert second.fencing_epoch > first.fencing_epoch
    assert not await store.is_current(first)
    assert not await store.release(first, "late")
    assert await store.release(second, "completed")


@pytest.mark.asyncio
async def test_in_memory_store_reclaims_expired_lease_with_new_fence() -> None:
    store = InMemoryDeploymentOperationOwnershipStore(owner_id="worker-a", lease_seconds=5)
    first = await store.acquire(None, "agent:one", "op-1")
    assert first is not None

    store._entries[("", "agent:one")].lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)  # noqa: SLF001
    second = await store.acquire(None, "agent:one", "op-2")

    assert second is not None
    assert second.fencing_epoch == first.fencing_epoch + 1
    assert not await store.heartbeat(first)
    assert await store.release(second, "completed")


@pytest.mark.asyncio
async def test_tenant_scope_isolation() -> None:
    store = InMemoryDeploymentOperationOwnershipStore(owner_id="worker-a", lease_seconds=5)
    tenant_a = await store.acquire("tenant-a", "deployment:one", "op-a")
    tenant_b = await store.acquire("tenant-b", "deployment:one", "op-b")

    assert tenant_a is not None
    assert tenant_b is not None
    assert await store.release(tenant_a, "completed")
    assert await store.release(tenant_b, "completed")


def test_lease_seconds_from_environment_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DEPLOYMENT_OPERATION_LEASE_SECONDS", "12")
    assert deployment_operation_lease_seconds_from_env() == 12

    monkeypatch.setenv("OSA_DEPLOYMENT_OPERATION_LEASE_SECONDS", "4")
    with pytest.raises(DeploymentError, match="at least 5"):
        deployment_operation_lease_seconds_from_env()
