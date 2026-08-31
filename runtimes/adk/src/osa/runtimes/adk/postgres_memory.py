"""PostgreSQL-backed memory provider (ADR-003).

Uses the ratified stack — SQLAlchemy 2.0 async over asyncpg — with a single
dedicated table and substring search mirroring the in-memory provider's
semantics (``ILIKE``). Per-scope limits and retention are enforced in SQL via
:meth:`PostgresMemoryProvider.enforce`. Requires the optional ``postgres``
extra (``osa-adk-runtime[postgres]``); the DSN comes from external
configuration (``OSA_MEMORY_DATABASE_URL``), never from agent definitions.

The table is created with ``CREATE TABLE IF NOT EXISTS`` at startup; schema
management moves to Alembic migrations with the Control Plane persistence
work (P1.1).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from typing import Any

from osa.generic_agent import MemoryEntry, MemoryProvider, MemoryScope
from osa.generic_agent.errors import MemoryConfigurationError

_MEMORY_TABLE = "osa_memory_entries"

_SCHEMA_DDL = f"""
CREATE TABLE IF NOT EXISTS {_MEMORY_TABLE} (
    entry_id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_MEMORY_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS ix_{_MEMORY_TABLE}_scope ON {_MEMORY_TABLE} (scope, scope_id, key, created_at)"
)


def _require_sqlalchemy() -> None:
    if find_spec("sqlalchemy") is None or find_spec("asyncpg") is None:
        raise MemoryConfigurationError(
            "The PostgreSQL memory provider requires the optional 'sqlalchemy' and 'asyncpg' "
            "dependencies; install the 'osa-adk-runtime[postgres]' extra"
        )


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PostgresMemoryProvider(MemoryProvider):
    """Persistent memory provider backed by PostgreSQL.

    Entries are stored under ``(scope, scope_id)``; the same key may hold
    multiple entries (append semantics, matching the in-memory provider).
    """

    def __init__(self, dsn: str, *, connect_args: dict[str, Any] | None = None) -> None:
        _require_sqlalchemy()
        from sqlalchemy.ext.asyncio import create_async_engine

        self._engine = create_async_engine(dsn, connect_args=connect_args or {})

    async def ensure_schema(self) -> None:
        """Create the memory table if absent; validates connectivity."""
        from sqlalchemy import text

        async with self._engine.begin() as connection:
            await connection.execute(text(_SCHEMA_DDL))
            await connection.execute(text(_MEMORY_INDEX_DDL))

    async def close(self) -> None:
        await self._engine.dispose()

    async def load(self, key: str, scope: MemoryScope, scope_id: str = "") -> list[MemoryEntry]:
        from sqlalchemy import text

        query = text(
            f"SELECT entry_id, key, content, scope, scope_id, metadata, created_at, updated_at "
            f"FROM {_MEMORY_TABLE} WHERE key = :key AND scope = :scope AND scope_id = :scope_id "
            f"ORDER BY created_at ASC"
        )
        async with self._engine.begin() as connection:
            rows = (await connection.execute(query, {"key": key, "scope": str(scope), "scope_id": scope_id})).fetchall()
        return [_row_to_entry(row) for row in rows]

    async def store(self, entry: MemoryEntry) -> None:
        from sqlalchemy import text

        insert = text(
            f"INSERT INTO {_MEMORY_TABLE} "
            f"(entry_id, key, content, scope, scope_id, metadata, created_at, updated_at) "
            f"VALUES (:entry_id, :key, :content, :scope, :scope_id, "
            f"CAST(:metadata AS jsonb), :created_at, :updated_at)"
        )
        async with self._engine.begin() as connection:
            await connection.execute(
                insert,
                {
                    "entry_id": entry.entry_id,
                    "key": entry.key,
                    "content": entry.content,
                    "scope": str(entry.scope),
                    "scope_id": entry.scope_id,
                    "metadata": _metadata_json(entry.metadata),
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at,
                },
            )

    async def delete(self, key: str, scope: MemoryScope, scope_id: str = "") -> bool:
        from sqlalchemy import text

        delete = text(f"DELETE FROM {_MEMORY_TABLE} WHERE key = :key AND scope = :scope AND scope_id = :scope_id")
        async with self._engine.begin() as connection:
            result = await connection.execute(delete, {"key": key, "scope": str(scope), "scope_id": scope_id})
        return bool(result.rowcount)

    async def search(self, query: str, scope: MemoryScope, scope_id: str = "", limit: int = 10) -> list[MemoryEntry]:
        from sqlalchemy import text

        search = text(
            f"SELECT entry_id, key, content, scope, scope_id, metadata, created_at, updated_at "
            f"FROM {_MEMORY_TABLE} WHERE scope = :scope AND scope_id = :scope_id "
            f"AND content ILIKE :pattern ORDER BY updated_at DESC LIMIT :limit"
        )
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    search,
                    {"scope": str(scope), "scope_id": scope_id, "pattern": f"%{_escape_like(query)}%", "limit": limit},
                )
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    async def enforce(
        self,
        scope: MemoryScope,
        scope_id: str,
        *,
        max_entries: int | None = None,
        retention_days: int | None = None,
    ) -> None:
        from sqlalchemy import text

        async with self._engine.begin() as connection:
            if retention_days is not None:
                cutoff = datetime.now(UTC) - timedelta(days=retention_days)
                await connection.execute(
                    text(
                        f"DELETE FROM {_MEMORY_TABLE} WHERE scope = :scope AND scope_id = :scope_id "
                        f"AND updated_at < :cutoff"
                    ),
                    {"scope": str(scope), "scope_id": scope_id, "cutoff": cutoff},
                )
            if max_entries is not None:
                await connection.execute(
                    text(
                        f"DELETE FROM {_MEMORY_TABLE} WHERE entry_id IN ("
                        f"SELECT entry_id FROM {_MEMORY_TABLE} "
                        f"WHERE scope = :scope AND scope_id = :scope_id "
                        f"ORDER BY created_at ASC OFFSET :keep)"
                    ),
                    {"scope": str(scope), "scope_id": scope_id, "keep": max_entries},
                )


def _metadata_json(metadata: dict[str, Any]) -> str:
    import json

    return json.dumps(metadata)


def _row_to_entry(row: Any) -> MemoryEntry:
    import json

    metadata: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        parsed = json.loads(row.metadata) if isinstance(row.metadata, str) else row.metadata
        if isinstance(parsed, dict):
            metadata = parsed
    return MemoryEntry(
        key=row.key,
        content=row.content,
        scope=MemoryScope(row.scope),
        scope_id=row.scope_id,
        metadata=metadata,
        entry_id=row.entry_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
