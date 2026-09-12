"""Explicit migrations and startup validation for durable A2A state."""

from __future__ import annotations

import re
from typing import Any

from osa.runtimes.adk.a2a_event_store import (
    MAX_A2A_TASK_TABLE_NAME_LENGTH,
    A2aTaskEventStore,
    event_table_name,
)

A2A_SCHEMA_VERSION_TABLE = "osa_a2a_schema_versions"
CURRENT_SCHEMA_VERSION = 2


def _validate_table_name(table_name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
        raise ValueError("A2A table names must be simple SQL identifiers")
    if len(table_name) > MAX_A2A_TASK_TABLE_NAME_LENGTH:
        raise ValueError(f"A2A task table names must be at most {MAX_A2A_TASK_TABLE_NAME_LENGTH} characters")
    return table_name


def _version_table(metadata: Any) -> Any:
    from sqlalchemy import Column, Integer, String, Table

    return Table(
        A2A_SCHEMA_VERSION_TABLE,
        metadata,
        Column("table_name", String(255), primary_key=True),
        Column("version", Integer, nullable=False),
    )


async def migrate_a2a_schema(
    engine: Any,
    *,
    task_table_name: str,
    task_store: Any,
    ownership_store: Any,
    event_store: A2aTaskEventStore | None = None,
) -> int:
    """Apply the A2A SDK task, ownership, and event schema explicitly."""
    from a2a.server.models import Base
    from sqlalchemy import MetaData, Table, select
    from sqlalchemy.orm import class_mapper

    task_table_name = _validate_table_name(task_table_name)
    event_store = event_store or A2aTaskEventStore(
        engine,
        table_name=event_table_name(task_table_name),
    )
    version_metadata = MetaData()
    version_table = _version_table(version_metadata)
    task_tables = [table for table in class_mapper(task_store.task_model).tables if isinstance(table, Table)]

    async with engine.begin() as connection:
        await connection.run_sync(version_metadata.create_all)
        current = (
            await connection.execute(
                select(version_table.c.version).where(version_table.c.table_name == task_table_name)
            )
        ).scalar_one_or_none()
        current_version = int(current or 0)
        if current_version >= CURRENT_SCHEMA_VERSION:
            return current_version

        await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=task_tables))
        await connection.run_sync(ownership_store.schema_metadata.create_all)
        await connection.run_sync(event_store.schema_metadata.create_all)
        if current is None:
            await connection.execute(
                version_table.insert().values(table_name=task_table_name, version=CURRENT_SCHEMA_VERSION)
            )
        else:
            from sqlalchemy import update

            await connection.execute(
                update(version_table)
                .where(version_table.c.table_name == task_table_name)
                .values(version=CURRENT_SCHEMA_VERSION)
            )
    return CURRENT_SCHEMA_VERSION


async def ensure_a2a_schema(
    engine: Any,
    *,
    task_table_name: str,
    ownership_store: Any,
    event_store: A2aTaskEventStore | None = None,
) -> None:
    """Validate A2A tables without mutating the database during startup."""
    from sqlalchemy import MetaData, inspect, select

    task_table_name = _validate_table_name(task_table_name)
    event_store = event_store or A2aTaskEventStore(
        engine,
        table_name=event_table_name(task_table_name),
    )
    metadata = MetaData()
    version_table = _version_table(metadata)
    try:
        async with engine.connect() as connection:
            version = (
                await connection.execute(
                    select(version_table.c.version).where(version_table.c.table_name == task_table_name)
                )
            ).scalar_one_or_none()
            present = await connection.run_sync(
                lambda sync_connection: (
                    inspect(sync_connection).has_table(task_table_name),
                    inspect(sync_connection).has_table(ownership_store.table_name),
                    inspect(sync_connection).has_table(event_store.table_name),
                )
            )
    except Exception as exc:
        raise RuntimeError("The A2A task schema is unavailable; run 'osa-a2a-migrate' before starting") from exc
    if int(version or 0) < CURRENT_SCHEMA_VERSION or not all(present):
        raise RuntimeError(
            f"A2A schema version {int(version or 0)} is older than required "
            f"{CURRENT_SCHEMA_VERSION}; run 'osa-a2a-migrate' before starting"
        )


__all__ = [
    "A2A_SCHEMA_VERSION_TABLE",
    "CURRENT_SCHEMA_VERSION",
    "ensure_a2a_schema",
    "migrate_a2a_schema",
]
