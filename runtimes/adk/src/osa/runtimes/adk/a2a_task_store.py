"""Fenced persistence adapter for the A2A SDK task store.

The A2A SDK persists task events from a background consumer, not from the
executor coroutine that produced them. The ownership snapshot therefore lives
on the SDK ``ServerCallContext`` and is checked by this adapter immediately
before every task mutation.
"""

from __future__ import annotations

from typing import Any

from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore, TaskOwnership

A2A_TASK_OWNERSHIP_CONTEXT_KEY = "osa_a2a_task_ownership"


class A2aTaskOwnershipLostError(RuntimeError):
    """Raised when an executor attempts a task mutation without its fence."""


def bind_task_ownership(context: Any, ownership: TaskOwnership) -> None:
    """Attach an acquired ownership snapshot to the SDK call context."""
    state = getattr(context, "state", None)
    if not isinstance(state, dict):
        raise RuntimeError("A2A call context does not expose mutable state")
    state[A2A_TASK_OWNERSHIP_CONTEXT_KEY] = ownership


class FencedDatabaseTaskStore:
    """Delegate SDK reads while fencing every durable task write.

    The wrapped SDK store continues to own conversion and query semantics. A
    write uses the same SQLAlchemy connection that holds the ownership row
    lock, so a PostgreSQL takeover cannot advance the fence around a stale
    worker's task mutation.
    """

    def __init__(self, store: Any, ownership_store: A2aTaskOwnershipStore) -> None:
        self._store = store
        self._ownership_store = ownership_store

    @property
    def engine(self) -> Any:
        return self._store.engine

    @property
    def task_model(self) -> Any:
        return self._store.task_model

    async def initialize(self) -> None:
        await self._store.initialize()

    async def save(self, task: Any, context: Any) -> None:
        await self._store._ensure_initialized()  # noqa: SLF001 - SDK adapter
        ownership = _context_ownership(context)
        if ownership is None or ownership.task_id != task.id:
            raise A2aTaskOwnershipLostError(f"A2A task {task.id} has no current ownership fence")

        owner = self._store.owner_resolver(context)
        db_task = self._store._to_orm(task, owner)  # noqa: SLF001 - SDK adapter

        async def write(connection: Any) -> None:
            from sqlalchemy.ext.asyncio import AsyncSession

            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
            ) as session:
                await session.merge(db_task)
                await session.flush()

        if not await self._ownership_store.run_if_owned(ownership, write):
            raise A2aTaskOwnershipLostError(
                f"A2A task {task.id} ownership fence {ownership.fence} is no longer current"
            )

    async def get(self, task_id: str, context: Any) -> Any:
        return await self._store.get(task_id, context)

    async def list(self, params: Any, context: Any) -> Any:
        return await self._store.list(params, context)

    async def delete(self, task_id: str, context: Any) -> None:
        await self._store._ensure_initialized()  # noqa: SLF001 - SDK adapter
        ownership = _context_ownership(context)
        if ownership is None or ownership.task_id != task_id:
            raise A2aTaskOwnershipLostError(f"A2A task {task_id} has no current ownership fence")

        owner = self._store.owner_resolver(context)

        async def delete_row(connection: Any) -> None:
            from sqlalchemy import delete

            await connection.execute(
                delete(self._store.task_model.__table__).where(  # noqa: SLF001 - SDK adapter
                    self._store.task_model.id == task_id,
                    self._store.task_model.owner == owner,
                )
            )

        if not await self._ownership_store.run_if_owned(ownership, delete_row):
            raise A2aTaskOwnershipLostError(
                f"A2A task {task_id} ownership fence {ownership.fence} is no longer current"
            )

    def __getattr__(self, name: str) -> Any:
        """Preserve optional SDK store attributes used by integrations/tests."""
        return getattr(self._store, name)


def _context_ownership(context: Any) -> TaskOwnership | None:
    state = getattr(context, "state", None)
    if not isinstance(state, dict):
        return None
    ownership = state.get(A2A_TASK_OWNERSHIP_CONTEXT_KEY)
    return ownership if isinstance(ownership, TaskOwnership) else None


__all__ = [
    "A2A_TASK_OWNERSHIP_CONTEXT_KEY",
    "A2aTaskOwnershipLostError",
    "FencedDatabaseTaskStore",
    "bind_task_ownership",
]
