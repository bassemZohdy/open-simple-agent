"""File-backed SQLite memory provider for local single-process deployments."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from typing import Any

from osa.generic_agent import MemoryEntry, MemoryProvider, MemoryScope
from osa.generic_agent.errors import MemoryConfigurationError
from osa.runtimes.adk.sqlite_memory_migrations import (
    configure_sqlite_engine,
    ensure_memory_schema,
    migrate_memory_schema,
    normalize_async_sqlite_dsn,
    restrict_sqlite_file_permissions,
)

_MEMORY_TABLE = "osa_memory_entries"


def _require_sqlite() -> None:
    missing = [name for name in ("sqlalchemy", "aiosqlite") if find_spec(name) is None]
    if missing:
        raise MemoryConfigurationError(
            "The SQLite memory provider requires the missing dependencies: "
            f"{', '.join(missing)}; install the 'osa-adk-runtime[sqlite]' extra"
        )


def _validate_sqlite_dsn(dsn: str) -> None:
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError) as exc:
        raise MemoryConfigurationError("Memory database URL must be a valid SQLite DSN") from exc
    if url.get_backend_name() != "sqlite":
        raise MemoryConfigurationError("Memory database URL must use SQLite for the SQLite provider")
    if url.database in {None, ":memory:"}:
        raise MemoryConfigurationError("SQLite memory persistence requires a file-backed database")


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _iso(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat()


def _utc(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class SqliteMemoryProvider(MemoryProvider):
    """Memory provider backed by a file-backed SQLite database."""

    def __init__(self, dsn: str) -> None:
        _require_sqlite()
        _validate_sqlite_dsn(dsn)
        restrict_sqlite_file_permissions(dsn)
        from sqlalchemy.ext.asyncio import create_async_engine

        self._engine = create_async_engine(normalize_async_sqlite_dsn(dsn), connect_args={"timeout": 5})
        configure_sqlite_engine(self._engine)

    async def ensure_schema(self) -> None:
        await ensure_memory_schema(self._engine)

    async def migrate(self) -> int:
        version = await migrate_memory_schema(self._engine)
        restrict_sqlite_file_permissions(str(self._engine.url))
        return version

    async def close(self) -> None:
        await self._engine.dispose()

    async def load(self, key: str, scope: MemoryScope, scope_id: str = "") -> list[MemoryEntry]:
        from sqlalchemy import text

        query = text(
            f"SELECT entry_id, key, content, scope, scope_id, metadata, created_at, updated_at "
            f"FROM {_MEMORY_TABLE} WHERE key = :key AND scope = :scope AND scope_id = :scope_id "
            "ORDER BY created_at ASC"
        )
        async with self._engine.begin() as connection:
            rows = (await connection.execute(query, {"key": key, "scope": str(scope), "scope_id": scope_id})).fetchall()
        return [_row_to_entry(row) for row in rows]

    async def store(self, entry: MemoryEntry) -> None:
        from sqlalchemy import text

        insert = text(
            f"INSERT INTO {_MEMORY_TABLE} "
            f"(entry_id, key, content, scope, scope_id, metadata, created_at, updated_at) "
            "VALUES (:entry_id, :key, :content, :scope, :scope_id, :metadata, :created_at, :updated_at)"
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
                    "metadata": json.dumps(entry.metadata),
                    "created_at": _iso(entry.created_at),
                    "updated_at": _iso(entry.updated_at),
                },
            )

    async def delete(self, key: str, scope: MemoryScope, scope_id: str = "") -> bool:
        from sqlalchemy import text

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(f"DELETE FROM {_MEMORY_TABLE} WHERE key = :key AND scope = :scope AND scope_id = :scope_id"),
                {"key": key, "scope": str(scope), "scope_id": scope_id},
            )
        return bool(result.rowcount)

    async def search(self, query: str, scope: MemoryScope, scope_id: str = "", limit: int = 10) -> list[MemoryEntry]:
        from sqlalchemy import text

        statement = text(
            f"SELECT entry_id, key, content, scope, scope_id, metadata, created_at, updated_at "
            f"FROM {_MEMORY_TABLE} WHERE scope = :scope AND scope_id = :scope_id "
            "AND lower(content) LIKE lower(:pattern) ESCAPE '\\' "
            "ORDER BY updated_at DESC LIMIT :limit"
        )
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    statement,
                    {
                        "scope": str(scope),
                        "scope_id": scope_id,
                        "pattern": f"%{_escape_like(query)}%",
                        "limit": limit,
                    },
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
                cutoff = _iso(datetime.now(UTC) - timedelta(days=retention_days))
                await connection.execute(
                    text(
                        f"DELETE FROM {_MEMORY_TABLE} WHERE scope = :scope AND scope_id = :scope_id "
                        "AND updated_at < :cutoff"
                    ),
                    {"scope": str(scope), "scope_id": scope_id, "cutoff": cutoff},
                )
            if max_entries is not None:
                await connection.execute(
                    text(
                        f"DELETE FROM {_MEMORY_TABLE} WHERE entry_id IN ("
                        f"SELECT entry_id FROM {_MEMORY_TABLE} "
                        "WHERE scope = :scope AND scope_id = :scope_id "
                        "ORDER BY created_at ASC LIMIT -1 OFFSET :keep)"
                    ),
                    {"scope": str(scope), "scope_id": scope_id, "keep": max_entries},
                )


def _row_to_entry(row: Any) -> MemoryEntry:
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
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


__all__ = ["SqliteMemoryProvider"]
