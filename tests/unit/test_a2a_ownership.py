"""Tests for replica-shared A2A task ownership leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_ownership_conflict_fencing_cancellation_and_terminal_state(tmp_path: Path) -> None:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ownership.db'}")
    first = A2aTaskOwnershipStore(engine, table_name="osa_a2a_ownership", lease_seconds=5)
    second = A2aTaskOwnershipStore(engine, table_name="osa_a2a_ownership", lease_seconds=5)
    async with engine.begin() as connection:
        await connection.run_sync(first.schema_metadata.create_all)

    lease = await first.acquire("task-1", "context-1", "tenant-a:subject-a")
    assert lease is not None
    assert lease.owner_id == first.owner_id
    assert lease.fence == 1
    assert await first.acquire("task-1", "context-1", "tenant-a:subject-a") is None
    assert await second.acquire("task-1", "context-1", "tenant-a:subject-a") is None
    assert await second.acquire("task-1", "context-1", "tenant-b:subject-b") is None
    assert not await second.request_cancel("task-1", "tenant-b:subject-b")

    assert await first.bind_session(lease, "session-1")
    current = await first.get("task-1", "tenant-a:subject-a")
    assert current is not None
    assert current.session_id == "session-1"
    assert await second.request_cancel("task-1", "tenant-a:subject-a")
    assert await first.is_cancel_requested(lease)
    assert await first.release(lease, "canceled")
    current = await first.get("task-1", "tenant-a:subject-a")
    assert current is not None
    assert current.state == "canceled"
    assert await second.acquire("task-1", "context-1", "tenant-a:subject-a") is None

    expiring = await first.acquire("task-2", "context-2", "tenant-a:subject-a")
    assert expiring is not None
    async with engine.begin() as connection:
        await connection.execute(
            update(first._table)  # noqa: SLF001 - test controls lease expiry
            .where(first._table.c.task_id == "task-2")
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )

    reclaimed = await second.acquire("task-2", "context-2", "tenant-a:subject-a")
    assert reclaimed is not None
    assert reclaimed.owner_id == second.owner_id
    assert reclaimed.fence == expiring.fence + 1
    assert reclaimed.reclaimed
    assert not await first.heartbeat(expiring)
    assert await second.release(reclaimed, "completed")

    canceled_expiring = await first.acquire("task-3", "context-3", "tenant-a:subject-a")
    assert canceled_expiring is not None
    async with engine.begin() as connection:
        await connection.execute(
            update(first._table)  # noqa: SLF001 - test controls lease expiry
            .where(first._table.c.task_id == "task-3")
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert await second.request_cancel("task-3", "tenant-a:subject-a")
    canceled_reclaim = await second.acquire("task-3", "context-3", "tenant-a:subject-a")
    assert canceled_reclaim is not None
    assert canceled_reclaim.cancel_requested
    assert await second.is_cancel_requested(canceled_reclaim)
    assert await second.release(canceled_reclaim, "canceled")

    with pytest.raises(ValueError, match="task identifier"):
        await first.acquire("t" * 256, "context-4", "tenant-a:subject-a")
    with pytest.raises(ValueError, match="context identifier"):
        await first.acquire("task-4", "c" * 256, "tenant-a:subject-a")
    with pytest.raises(ValueError, match="scope identifier"):
        await first.acquire("task-4", "context-4", "s" * 256)

    await engine.dispose()
