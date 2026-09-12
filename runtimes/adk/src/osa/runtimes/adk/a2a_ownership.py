"""Replica-shared A2A task ownership leases.

The A2A SDK owns request routing and its active-task registry remains local to
one process. This store adds the OSA-level lease/fencing boundary used by
executors when the SDK task store is shared: one worker owns a task at a time,
expired ownership can be reclaimed by a retry, and cancellation is durable.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True)
class TaskOwnership:
    """Snapshot of the replica-shared ownership row."""

    task_id: str
    context_id: str
    scope_key: str
    owner_id: str | None
    fence: int
    lease_until: datetime | None
    session_id: str | None
    cancel_requested: bool
    state: str


class A2aTaskOwnershipStore:
    """SQLAlchemy store for task leases, fencing, and cancellation."""

    def __init__(self, engine: Any, *, table_name: str, lease_seconds: int = 30) -> None:
        if lease_seconds < 5:
            raise ValueError("A2A task lease must be at least 5 seconds")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
            raise ValueError("A2A ownership table name must be a simple SQL identifier")
        from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text

        self._engine = engine
        self._lease_seconds = lease_seconds
        self._owner_id = self._make_owner_id()
        self._metadata = MetaData()
        self._table = Table(
            table_name,
            self._metadata,
            Column("task_id", String(255), primary_key=True),
            Column("context_id", String(255), nullable=False),
            Column("scope_key", String(255), nullable=False),
            Column("owner_id", String(255), nullable=True),
            Column("fence", Integer, nullable=False, default=0),
            Column("lease_until", DateTime(timezone=True), nullable=True),
            Column("session_id", String(255), nullable=True),
            Column("cancel_requested", Boolean, nullable=False, default=False),
            Column("state", Text, nullable=False, default="released"),
        )

    @staticmethod
    def _make_owner_id() -> str:
        configured = os.environ.get("OSA_A2A_OWNER_ID")
        if configured and configured.strip():
            return configured.strip()[:255]
        return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"

    @property
    def owner_id(self) -> str:
        """The unique worker identity used for lease ownership."""
        return self._owner_id

    @property
    def heartbeat_interval_seconds(self) -> float:
        """Interval that keeps a healthy worker ahead of lease expiry."""
        return max(1.0, self._lease_seconds / 3)

    @property
    def table_name(self) -> str:
        """Database table used for ownership coordination."""
        return str(self._table.name)

    @property
    def schema_metadata(self) -> Any:
        """SQLAlchemy metadata used by the explicit migration command."""
        return self._metadata

    async def get(self, task_id: str, scope_key: str) -> TaskOwnership | None:
        from sqlalchemy import select

        async with self._engine.connect() as connection:
            query = select(self._table).where(
                self._table.c.task_id == task_id,
                self._table.c.scope_key == scope_key,
            )
            row = (await connection.execute(query)).mappings().first()
        return self._from_row(row) if row is not None else None

    async def acquire(self, task_id: str, context_id: str, scope_key: str) -> TaskOwnership | None:
        """Acquire a lease, reclaiming only an expired prior owner."""
        from sqlalchemy import insert, select, update

        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=self._lease_seconds)
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(self._table).where(self._table.c.task_id == task_id).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                await connection.execute(
                    insert(self._table).values(
                        task_id=task_id,
                        context_id=context_id,
                        scope_key=scope_key,
                        owner_id=self._owner_id,
                        fence=1,
                        lease_until=lease_until,
                        cancel_requested=False,
                        state="running",
                    )
                )
                return TaskOwnership(
                    task_id,
                    context_id,
                    scope_key,
                    self._owner_id,
                    1,
                    lease_until,
                    None,
                    False,
                    "running",
                )

            if row["scope_key"] != scope_key:
                return None

            existing_lease = self._as_utc(row["lease_until"])
            lease_active = row["state"] == "running" and existing_lease is not None and existing_lease > now
            if lease_active:
                return None
            if row["state"] in {"completed", "failed", "canceled"}:
                return None

            fence = int(row["fence"]) + 1
            cancel_requested = bool(row["cancel_requested"])
            await connection.execute(
                update(self._table)
                .where(
                    self._table.c.task_id == task_id,
                    self._table.c.scope_key == scope_key,
                )
                .values(
                    context_id=context_id,
                    scope_key=scope_key,
                    owner_id=self._owner_id,
                    fence=fence,
                    lease_until=lease_until,
                    cancel_requested=cancel_requested,
                    state="running",
                )
            )
            return TaskOwnership(
                task_id,
                context_id,
                scope_key,
                self._owner_id,
                fence,
                lease_until,
                row["session_id"],
                cancel_requested,
                "running",
            )

    async def heartbeat(self, ownership: TaskOwnership) -> bool:
        """Extend a lease only when its owner and fencing token still match."""
        from sqlalchemy import update

        lease_until = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(self._table)
                .where(
                    self._table.c.task_id == ownership.task_id,
                    self._table.c.scope_key == ownership.scope_key,
                    self._table.c.owner_id == self._owner_id,
                    self._table.c.fence == ownership.fence,
                    self._table.c.state == "running",
                )
                .values(lease_until=lease_until)
            )
        return bool(int(result.rowcount or 0) == 1)

    async def run_if_owned(
        self,
        ownership: TaskOwnership,
        operation: Callable[[Any], Awaitable[None]],
    ) -> bool:
        """Run a database mutation while holding the current ownership row lock.

        PostgreSQL row locking makes the ownership check and the caller's task
        mutation one serialization point: a takeover cannot advance the fence
        until the mutation transaction commits or rolls back. SQLite has no
        equivalent row lock, but durable local mode is single-replica by
        contract and still receives the same lease/fence validation.
        """
        from sqlalchemy import select

        now = datetime.now(UTC)
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(self._table)
                        .where(
                            self._table.c.task_id == ownership.task_id,
                            self._table.c.scope_key == ownership.scope_key,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            lease_until = self._as_utc(row["lease_until"])
            is_current_owner = (
                row["owner_id"] == self._owner_id
                and int(row["fence"]) == ownership.fence
                and row["state"] == "running"
                and lease_until is not None
                and lease_until > now
            )
            if not is_current_owner:
                return False
            await operation(connection)
        return True

    async def is_cancel_requested(self, ownership: TaskOwnership) -> bool:
        current = await self.get(ownership.task_id, ownership.scope_key)
        return current is None or current.fence != ownership.fence or current.cancel_requested

    async def request_cancel(self, task_id: str, scope_key: str) -> bool:
        """Persist cancellation without requiring the requesting replica to own the task."""
        from sqlalchemy import update

        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(self._table)
                .where(
                    self._table.c.task_id == task_id,
                    self._table.c.scope_key == scope_key,
                    self._table.c.state == "running",
                )
                .values(cancel_requested=True)
            )
        return bool(int(result.rowcount or 0) == 1)

    async def bind_session(self, ownership: TaskOwnership, session_id: str) -> bool:
        from sqlalchemy import update

        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(self._table)
                .where(
                    self._table.c.task_id == ownership.task_id,
                    self._table.c.scope_key == ownership.scope_key,
                    self._table.c.owner_id == self._owner_id,
                    self._table.c.fence == ownership.fence,
                    self._table.c.state == "running",
                )
                .values(session_id=session_id)
            )
        return bool(int(result.rowcount or 0) == 1)

    async def release(self, ownership: TaskOwnership, state: str = "released") -> bool:
        """Release or terminally close a lease using its fencing token."""
        if state not in {"released", "completed", "failed", "canceled"}:
            raise ValueError("A2A task ownership state is invalid")
        from sqlalchemy import update

        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(self._table)
                .where(
                    self._table.c.task_id == ownership.task_id,
                    self._table.c.scope_key == ownership.scope_key,
                    self._table.c.owner_id == self._owner_id,
                    self._table.c.fence == ownership.fence,
                )
                .values(
                    owner_id=None,
                    lease_until=None,
                    cancel_requested=False if state == "released" else ownership.cancel_requested,
                    state=state,
                )
            )
        return bool(int(result.rowcount or 0) == 1)

    async def close(self) -> None:
        """The parent A2A engine owns connection disposal."""
        return None

    @staticmethod
    def _as_utc(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    @classmethod
    def _from_row(cls, row: Any) -> TaskOwnership:
        return TaskOwnership(
            task_id=str(row["task_id"]),
            context_id=str(row["context_id"]),
            scope_key=str(row["scope_key"]),
            owner_id=str(row["owner_id"]) if row["owner_id"] is not None else None,
            fence=int(row["fence"]),
            lease_until=cls._as_utc(row["lease_until"]),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            cancel_requested=bool(row["cancel_requested"]),
            state=str(row["state"]),
        )


async def heartbeat_loop(store: A2aTaskOwnershipStore, ownership: TaskOwnership) -> None:
    """Keep a long-running task lease alive until the worker loses ownership."""
    while await store.heartbeat(ownership):
        await asyncio.sleep(store.heartbeat_interval_seconds)


__all__ = ["A2aTaskOwnershipStore", "TaskOwnership", "heartbeat_loop"]
