"""Durable, fenced A2A protocol-event storage.

The A2A SDK owns the in-process event queues. This module provides the
replica-shared append-only cursor that a future cross-process stream relay can
read. Event writes run inside the ownership store's row-lock transaction, so a
worker that loses its fencing epoch cannot publish a late event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore, TaskOwnership

A2A_EVENT_TABLE_SUFFIX = "_events"
POSTGRES_IDENTIFIER_MAX_LENGTH = 63
MAX_A2A_TASK_TABLE_NAME_LENGTH = POSTGRES_IDENTIFIER_MAX_LENGTH - len("_ownership")
MAX_A2A_EVENT_TYPE_LENGTH = 64
MAX_A2A_EVENT_PAYLOAD_BYTES = 1024 * 1024
MAX_A2A_PERSISTED_TEXT_LENGTH = 255


@dataclass(frozen=True)
class A2aTaskEvent:
    """One durable protocol event in a task's ordered event stream."""

    tenant_id: str
    task_id: str
    fencing_epoch: int
    sequence: int
    event_type: str
    payload: bytes
    created_at: datetime


def event_table_name(task_table_name: str) -> str:
    """Return the event table paired with one configured task table."""
    _validate_identifier(task_table_name, max_length=MAX_A2A_TASK_TABLE_NAME_LENGTH)
    event_name = f"{task_table_name}{A2A_EVENT_TABLE_SUFFIX}"
    _validate_identifier(event_name)
    return event_name


class A2aTaskEventStore:
    """SQLAlchemy store for ordered events published by the current owner."""

    def __init__(
        self,
        engine: Any,
        *,
        table_name: str,
        max_payload_bytes: int = MAX_A2A_EVENT_PAYLOAD_BYTES,
    ) -> None:
        _validate_identifier(table_name)
        if max_payload_bytes < 1:
            raise ValueError("A2A event payload limit must be positive")

        from sqlalchemy import (
            Column,
            DateTime,
            Index,
            Integer,
            LargeBinary,
            MetaData,
            PrimaryKeyConstraint,
            String,
            Table,
        )

        self._engine = engine
        self._max_payload_bytes = max_payload_bytes
        self._metadata = MetaData()
        self._table = Table(
            table_name,
            self._metadata,
            Column("tenant_id", String(MAX_A2A_PERSISTED_TEXT_LENGTH), nullable=False),
            Column("task_id", String(MAX_A2A_PERSISTED_TEXT_LENGTH), nullable=False),
            Column("fencing_epoch", Integer, nullable=False),
            Column("sequence", Integer, nullable=False),
            Column("event_type", String(MAX_A2A_EVENT_TYPE_LENGTH), nullable=False),
            Column("payload", LargeBinary, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            PrimaryKeyConstraint("tenant_id", "task_id", "sequence"),
            Index(f"ix_{table_name}_cursor", "tenant_id", "task_id", "sequence"),
        )

    @property
    def table_name(self) -> str:
        """Database table used for protocol events."""
        return str(self._table.name)

    @property
    def schema_metadata(self) -> Any:
        """SQLAlchemy metadata used by the explicit A2A migration."""
        return self._metadata

    async def append_if_owned(
        self,
        ownership: TaskOwnership,
        *,
        event_type: str,
        payload: bytes,
        tenant_id: str | None = None,
        ownership_store: A2aTaskOwnershipStore,
    ) -> int | None:
        """Append one event only while the ownership fence is current.

        The sequence allocation and insert execute in the same transaction as
        the ownership-row lock acquired by ``run_if_owned``. A ``None`` result
        means the worker no longer owns the task and no event was inserted.
        """
        event_type = _validate_event_type(event_type)
        if not isinstance(payload, bytes):
            raise TypeError("A2A event payload must be bytes")
        if not payload:
            raise ValueError("A2A event payload must not be empty")
        if len(payload) > self._max_payload_bytes:
            raise ValueError("A2A event payload exceeds the configured limit")
        validate_a2a_persisted_text(ownership.task_id, label="task identifier")
        resolved_tenant_id = _resolve_tenant_id(tenant_id, ownership.scope_key)
        sequence_holder: list[int] = []

        async def insert_event(connection: Any) -> None:
            sequence_holder.append(
                await self.append_on_connection(
                    connection,
                    ownership,
                    event_type=event_type,
                    payload=payload,
                    tenant_id=resolved_tenant_id,
                )
            )

        if not await ownership_store.run_if_owned(ownership, insert_event):
            return None
        return sequence_holder[0]

    async def append_on_connection(
        self,
        connection: Any,
        ownership: TaskOwnership,
        *,
        event_type: str,
        payload: bytes,
        tenant_id: str | None = None,
    ) -> int:
        """Append an event inside an already fenced ownership transaction."""
        event_type = _validate_event_type(event_type)
        if not isinstance(payload, bytes):
            raise TypeError("A2A event payload must be bytes")
        if not payload:
            raise ValueError("A2A event payload must not be empty")
        if len(payload) > self._max_payload_bytes:
            raise ValueError("A2A event payload exceeds the configured limit")
        validate_a2a_persisted_text(ownership.task_id, label="task identifier")
        resolved_tenant_id = _resolve_tenant_id(tenant_id, ownership.scope_key)

        from sqlalchemy import func, insert, select

        next_sequence = (
            await connection.execute(
                select(func.coalesce(func.max(self._table.c.sequence), 0) + 1).where(
                    self._table.c.tenant_id == resolved_tenant_id,
                    self._table.c.task_id == ownership.task_id,
                )
            )
        ).scalar_one()
        sequence = int(next_sequence)
        await connection.execute(
            insert(self._table).values(
                tenant_id=resolved_tenant_id,
                task_id=ownership.task_id,
                fencing_epoch=ownership.fence,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )
        return sequence

    async def read_after(
        self,
        *,
        tenant_id: str,
        task_id: str,
        after_sequence: int = 0,
        limit: int = 128,
    ) -> list[A2aTaskEvent]:
        """Read a bounded ordered page after a client cursor."""
        validate_a2a_persisted_text(tenant_id, label="tenant identifier")
        validate_a2a_persisted_text(task_id, label="task identifier")
        if after_sequence < 0:
            raise ValueError("A2A event cursor must not be negative")
        if limit < 1:
            raise ValueError("A2A event page size must be positive")

        from sqlalchemy import select

        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(self._table)
                .where(
                    self._table.c.tenant_id == tenant_id,
                    self._table.c.task_id == task_id,
                    self._table.c.sequence > after_sequence,
                )
                .order_by(self._table.c.sequence.asc())
                .limit(limit)
            )
            rows = result.mappings().all()
        return [self._from_row(row) for row in rows]

    async def read_after_scope(
        self,
        *,
        scope_key: str,
        task_id: str,
        after_sequence: int = 0,
        limit: int = 128,
    ) -> list[A2aTaskEvent]:
        """Read a cursor page using the same tenant scope as ownership."""
        return await self.read_after(
            tenant_id=_resolve_tenant_id(None, scope_key),
            task_id=task_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    @classmethod
    def _from_row(cls, row: Any) -> A2aTaskEvent:
        created_at = row["created_at"]
        if not isinstance(created_at, datetime):
            created_at = datetime.fromisoformat(str(created_at))
        created_at = created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at.astimezone(UTC)
        return A2aTaskEvent(
            tenant_id=str(row["tenant_id"]),
            task_id=str(row["task_id"]),
            fencing_epoch=int(row["fencing_epoch"]),
            sequence=int(row["sequence"]),
            event_type=str(row["event_type"]),
            payload=bytes(row["payload"]),
            created_at=created_at,
        )


def _validate_identifier(value: str, *, max_length: int = POSTGRES_IDENTIFIER_MAX_LENGTH) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError("A2A event table names must be simple SQL identifiers")
    if len(value) > max_length:
        raise ValueError(f"A2A event table names must be at most {max_length} characters")
    return value


def _validate_event_type(value: str) -> str:
    if not value or len(value) > MAX_A2A_EVENT_TYPE_LENGTH or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value) is None:
        raise ValueError("A2A event type is invalid")
    return value


def _resolve_tenant_id(tenant_id: str | None, scope_key: str) -> str:
    if tenant_id is not None:
        return validate_a2a_persisted_text(tenant_id, label="tenant identifier")
    validate_a2a_persisted_text(scope_key, label="scope identifier")
    if scope_key.startswith("tenant:"):
        tenant = scope_key.removeprefix("tenant:").split(":subject:", 1)[0]
        if tenant:
            return validate_a2a_persisted_text(tenant, label="tenant identifier")
    return scope_key


def validate_a2a_persisted_text(value: str, *, label: str) -> str:
    """Validate text that is stored in a bounded A2A identity column."""
    if not isinstance(value, str) or not value or len(value) > MAX_A2A_PERSISTED_TEXT_LENGTH:
        raise ValueError(f"A2A {label} must be non-empty and at most {MAX_A2A_PERSISTED_TEXT_LENGTH} characters")
    return value


__all__ = [
    "A2A_EVENT_TABLE_SUFFIX",
    "A2aTaskEvent",
    "A2aTaskEventStore",
    "MAX_A2A_PERSISTED_TEXT_LENGTH",
    "MAX_A2A_TASK_TABLE_NAME_LENGTH",
    "MAX_A2A_EVENT_PAYLOAD_BYTES",
    "POSTGRES_IDENTIFIER_MAX_LENGTH",
    "event_table_name",
    "validate_a2a_persisted_text",
]
