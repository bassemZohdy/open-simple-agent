"""Tests for the migration-owned A2A event cursor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")
pytest.importorskip("a2a")


@pytest.mark.asyncio
async def test_event_store_orders_events_and_rejects_stale_fences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore

    monkeypatch.delenv("OSA_A2A_OWNER_ID", raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")
    owner_store = A2aTaskOwnershipStore(engine, table_name="a2a_event_ownership", lease_seconds=5)
    successor_store = A2aTaskOwnershipStore(
        engine,
        table_name="a2a_event_ownership",
        lease_seconds=5,
    )
    event_store = A2aTaskEventStore(engine, table_name="a2a_task_events")
    async with engine.begin() as connection:
        await connection.run_sync(owner_store.schema_metadata.create_all)
        await connection.run_sync(event_store.schema_metadata.create_all)

    try:
        old_lease = await owner_store.acquire(
            "task-1",
            "context-1",
            "tenant:tenant-a:subject:alice",
        )
        assert old_lease is not None
        assert (
            await event_store.append_if_owned(
                old_lease,
                event_type="Task",
                payload=b"submitted",
                ownership_store=owner_store,
            )
            == 1
        )
        assert (
            await event_store.append_if_owned(
                old_lease,
                event_type="TaskStatusUpdateEvent",
                payload=b"working",
                ownership_store=owner_store,
            )
            == 2
        )

        assert await event_store.read_after_scope(
            scope_key="tenant:tenant-a:subject:alice",
            task_id="task-1",
        )
        events = await event_store.read_after_scope(
            scope_key="tenant:tenant-a:subject:alice",
            task_id="task-1",
            after_sequence=1,
        )
        assert [(event.sequence, event.event_type, event.payload) for event in events] == [
            (2, "TaskStatusUpdateEvent", b"working"),
        ]
        assert (
            await event_store.read_after_scope(
                scope_key="tenant:tenant-b:subject:alice",
                task_id="task-1",
            )
            == []
        )
        assert (
            await event_store.append_if_owned(
                old_lease,
                event_type="late",
                payload=b"rejected-before-takeover",
                ownership_store=successor_store,
            )
            is None
        )

        async with engine.begin() as connection:
            await connection.execute(
                update(owner_store._table)  # noqa: SLF001 - expiry is test setup
                .where(owner_store._table.c.task_id == "task-1")
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        new_lease = await successor_store.acquire(
            "task-1",
            "context-1",
            "tenant:tenant-a:subject:alice",
        )
        assert new_lease is not None
        assert new_lease.fence == old_lease.fence + 1
        assert (
            await event_store.append_if_owned(
                new_lease,
                event_type="TaskStatusUpdateEvent",
                payload=b"completed",
                ownership_store=successor_store,
            )
            == 3
        )
        assert await successor_store.release(new_lease, "completed")
    finally:
        await engine.dispose()


def test_event_table_name_and_payload_validation() -> None:
    from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore, event_table_name

    assert event_table_name("osa_a2a_tasks") == "osa_a2a_tasks_events"
    with pytest.raises(ValueError, match="simple SQL identifiers"):
        event_table_name("tasks;drop")
    with pytest.raises(ValueError, match="positive"):
        A2aTaskEventStore(object(), table_name="events", max_payload_bytes=0)


@pytest.mark.asyncio
async def test_a2a_migration_provisions_and_validates_event_table(tmp_path: Path) -> None:
    from a2a.server.tasks import DatabaseTaskStore
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.a2a import _a2a_task_owner
    from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore, event_table_name
    from osa.runtimes.adk.a2a_migrations import (
        CURRENT_SCHEMA_VERSION,
        ensure_a2a_schema,
        migrate_a2a_schema,
    )
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore
    from osa.runtimes.adk.a2a_task_store import FencedDatabaseTaskStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")
    ownership_store = A2aTaskOwnershipStore(
        engine,
        table_name="migration_ownership",
        lease_seconds=5,
    )
    sdk_store = DatabaseTaskStore(
        engine,
        create_table=False,
        table_name="migration_tasks",
        owner_resolver=_a2a_task_owner,
    )
    event_store = A2aTaskEventStore(
        engine,
        table_name=event_table_name("migration_tasks"),
    )
    task_store = FencedDatabaseTaskStore(sdk_store, ownership_store, event_store)
    try:
        assert (
            await migrate_a2a_schema(
                engine,
                task_table_name="migration_tasks",
                task_store=task_store,
                ownership_store=ownership_store,
            )
            == CURRENT_SCHEMA_VERSION
            == 2
        )
        await ensure_a2a_schema(
            engine,
            task_table_name="migration_tasks",
            ownership_store=ownership_store,
        )
        async with engine.begin() as connection:
            await connection.run_sync(event_store.schema_metadata.drop_all)
            await connection.execute(
                text("UPDATE osa_a2a_schema_versions SET version = 1 WHERE table_name = :table_name"),
                {"table_name": "migration_tasks"},
            )
        assert (
            await migrate_a2a_schema(
                engine,
                task_table_name="migration_tasks",
                task_store=task_store,
                ownership_store=ownership_store,
            )
            == CURRENT_SCHEMA_VERSION
        )
        await ensure_a2a_schema(
            engine,
            task_table_name="migration_tasks",
            ownership_store=ownership_store,
        )

        from a2a.server.context import ServerCallContext
        from a2a.types import Task, TaskState, TaskStatus

        from osa.runtimes.adk.a2a_task_store import bind_task_event, bind_task_ownership

        ownership = await ownership_store.acquire(
            "migration-event-task",
            "migration-event-context",
            "anonymous",
        )
        assert ownership is not None
        context = ServerCallContext()
        bind_task_ownership(context, ownership)
        task = Task(
            id="migration-event-task",
            context_id="migration-event-context",
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )
        bind_task_event(context, task)
        await task_store.save(task, context)
        events = await event_store.read_after_scope(
            scope_key="anonymous",
            task_id=task.id,
        )
        assert len(events) == 1
        assert events[0].event_type == "Task"
        assert events[0].payload == task.SerializeToString(deterministic=True)
        assert await ownership_store.release(ownership, "completed")
    finally:
        await engine.dispose()
