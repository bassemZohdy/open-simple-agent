"""Tests for the bounded durable A2A event relay."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")
pytest.importorskip("a2a")


@pytest.mark.asyncio
async def test_relay_reconnects_from_cursor_and_stops_at_terminal_event(tmp_path: Path) -> None:
    from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.a2a_event_relay import A2aTaskEventRelay
    from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'relay.db'}")
    ownership_store = A2aTaskOwnershipStore(engine, table_name="relay_ownership", lease_seconds=5)
    event_store = A2aTaskEventStore(engine, table_name="relay_events")
    async with engine.begin() as connection:
        await connection.run_sync(ownership_store.schema_metadata.create_all)
        await connection.run_sync(event_store.schema_metadata.create_all)

    scope_key = "tenant:tenant-a:subject:alice"
    task_id = "relay-task"
    ownership = await ownership_store.acquire(task_id, "relay-context", scope_key)
    assert ownership is not None
    try:
        submitted = Task(
            id=task_id,
            context_id="relay-context",
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )
        working = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id="relay-context",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        completed = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id="relay-context",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        for event in (submitted, working, completed):
            assert (
                await event_store.append_if_owned(
                    ownership,
                    event_type=type(event).__name__,
                    payload=event.SerializeToString(deterministic=True),
                    ownership_store=ownership_store,
                )
                is not None
            )

        relay = A2aTaskEventRelay(event_store, ownership_store, poll_interval_seconds=0.005)
        first_page = [event async for event in relay.stream(scope_key=scope_key, task_id=task_id)]
        assert [type(event).__name__ for event in first_page] == [
            "Task",
            "TaskStatusUpdateEvent",
            "TaskStatusUpdateEvent",
        ]
        assert first_page[-1].status.state == TaskState.TASK_STATE_COMPLETED

        replayed_page = [
            event
            async for event in relay.stream(
                scope_key=scope_key,
                task_id=task_id,
                after_sequence=1,
            )
        ]
        assert len(replayed_page) == 2
        assert replayed_page[0].status.state == TaskState.TASK_STATE_WORKING
        assert replayed_page[1].status.state == TaskState.TASK_STATE_COMPLETED

        assert [event async for event in relay.stream(scope_key="tenant:tenant-b:subject:alice", task_id=task_id)] == []
        assert [event async for event in relay.stream(scope_key="tenant:tenant-a:subject:bob", task_id=task_id)] == []
    finally:
        await ownership_store.release(ownership, "completed")
        await engine.dispose()


@pytest.mark.asyncio
async def test_relay_waits_with_bounded_polling_and_times_out(tmp_path: Path) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.a2a_event_relay import A2aEventStreamTimeoutError, A2aTaskEventRelay
    from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'relay-timeout.db'}")
    ownership_store = A2aTaskOwnershipStore(engine, table_name="relay_timeout_ownership", lease_seconds=5)
    event_store = A2aTaskEventStore(engine, table_name="relay_timeout_events")
    async with engine.begin() as connection:
        await connection.run_sync(ownership_store.schema_metadata.create_all)
        await connection.run_sync(event_store.schema_metadata.create_all)

    ownership = await ownership_store.acquire(
        "relay-timeout-task",
        "relay-timeout-context",
        "tenant:tenant-a:subject:alice",
    )
    assert ownership is not None
    try:
        relay = A2aTaskEventRelay(
            event_store,
            ownership_store,
            poll_interval_seconds=0.005,
            timeout_seconds=0.02,
        )
        with pytest.raises(A2aEventStreamTimeoutError):
            await anext(
                relay.stream(
                    scope_key="tenant:tenant-a:subject:alice",
                    task_id="relay-timeout-task",
                )
            )
    finally:
        await ownership_store.release(ownership)
        await engine.dispose()
