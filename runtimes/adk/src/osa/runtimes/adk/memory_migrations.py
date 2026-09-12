"""Explicit versioned migrations for the independent memory database."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION_TABLE = "osa_memory_schema_versions"
MEMORY_TABLE = "osa_memory_entries"
CURRENT_SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        f"""
        CREATE TABLE IF NOT EXISTS {MEMORY_TABLE} (
            entry_id TEXT PRIMARY KEY,
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS ix_{MEMORY_TABLE}_scope ON {MEMORY_TABLE} (scope, scope_id, key, created_at)",
    ),
}


async def migrate_memory_schema(engine: Any) -> int:
    """Apply all pending migrations and return the resulting version."""
    from sqlalchemy import text

    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} "
                "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        current = int(
            (
                await connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}"))
            ).scalar_one()
        )
        for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
            for statement in _MIGRATIONS[version]:
                await connection.execute(text(statement))
            await connection.execute(
                text(f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES (:version)"),
                {"version": version},
            )
            current = version
    return current


async def ensure_memory_schema(engine: Any) -> None:
    """Validate the schema without mutating it during runtime startup."""
    from sqlalchemy import text

    async with engine.connect() as connection:
        result = await connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}"))
        version = int(result.scalar_one())
    if version < CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Memory schema version {version} is older than required {CURRENT_SCHEMA_VERSION}; "
            "run 'osa-memory-migrate' before starting the runtime"
        )


__all__ = ["CURRENT_SCHEMA_VERSION", "MEMORY_TABLE", "migrate_memory_schema", "ensure_memory_schema"]
