"""Explicit file-backed SQLite migrations for runtime memory."""

from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from osa.generic_agent.errors import MemoryConfigurationError

SCHEMA_VERSION_TABLE = "osa_memory_sqlite_schema_versions"
MEMORY_TABLE = "osa_memory_entries"
CURRENT_SCHEMA_VERSION = 1


def _require_sqlite_stack() -> None:
    missing = [name for name in ("sqlalchemy", "aiosqlite") if find_spec(name) is None]
    if missing:
        raise MemoryConfigurationError(
            "SQLite memory persistence requires the missing dependencies: "
            f"{', '.join(missing)}; install the 'osa-adk-runtime[sqlite]' extra"
        )


def _sqlite_url(dsn: str) -> Any:
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError) as exc:
        raise MemoryConfigurationError("Memory SQLite URL is invalid") from exc
    if url.get_backend_name() != "sqlite":
        raise MemoryConfigurationError("Memory SQLite migrations require a SQLite DSN")
    if url.database in {None, ":memory:"}:
        raise MemoryConfigurationError("SQLite memory persistence requires a file-backed database")
    if url.get_driver_name() not in {"pysqlite", "aiosqlite"}:
        raise MemoryConfigurationError("SQLite memory persistence requires the pysqlite or aiosqlite driver")
    return url


def normalize_async_sqlite_dsn(dsn: str) -> str:
    """Normalize a supported SQLite URL for SQLAlchemy's async engine."""
    return str(_sqlite_url(dsn).set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False))


def restrict_sqlite_file_permissions(dsn: str) -> None:
    """Keep an existing/newly migrated SQLite file private on POSIX hosts."""
    if os.name == "nt":
        return
    database = _sqlite_url(dsn).database
    if database is None or database == ":memory:":
        return
    path = Path(database)
    try:
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if candidate.exists():
                candidate.chmod(0o600)
    except OSError as exc:
        raise MemoryConfigurationError("Unable to restrict SQLite memory file permissions") from exc


def configure_sqlite_engine(engine: Any) -> None:
    """Configure locking and foreign-key behavior for a local SQLite engine."""
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


async def migrate_memory_schema(engine: Any) -> int:
    """Apply the current SQLite memory schema."""
    from sqlalchemy import text

    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        current = int(
            (
                await connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}"))
            ).scalar_one()
        )
        if current < CURRENT_SCHEMA_VERSION:
            await connection.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {MEMORY_TABLE} (
                        entry_id TEXT PRIMARY KEY,
                        key TEXT NOT NULL,
                        content TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        scope_id TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{{}}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{MEMORY_TABLE}_scope "
                    f"ON {MEMORY_TABLE} (scope, scope_id, key, created_at)"
                )
            )
            await connection.execute(
                text(f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES (:version)"),
                {"version": CURRENT_SCHEMA_VERSION},
            )
            current = CURRENT_SCHEMA_VERSION
    return current


async def ensure_memory_schema(engine: Any) -> None:
    """Validate the SQLite memory schema without mutating it."""
    from sqlalchemy import text

    try:
        async with engine.connect() as connection:
            version = int(
                (
                    await connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}"))
                ).scalar_one()
            )
            present = str(
                (
                    await connection.execute(
                        text("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = :name"),
                        {"name": MEMORY_TABLE},
                    )
                ).scalar_one()
            )
    except Exception as exc:
        raise MemoryConfigurationError(
            "The runtime SQLite memory schema is unavailable; run 'osa-memory-migrate' "
            "against OSA_MEMORY_DATABASE_URL before starting"
        ) from exc
    if version < CURRENT_SCHEMA_VERSION or present != "1":
        raise MemoryConfigurationError(
            f"SQLite memory schema version {version} is older than required {CURRENT_SCHEMA_VERSION}; "
            "run 'osa-memory-migrate' before starting"
        )


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "MEMORY_TABLE",
    "SCHEMA_VERSION_TABLE",
    "configure_sqlite_engine",
    "ensure_memory_schema",
    "migrate_memory_schema",
    "normalize_async_sqlite_dsn",
    "restrict_sqlite_file_permissions",
]
