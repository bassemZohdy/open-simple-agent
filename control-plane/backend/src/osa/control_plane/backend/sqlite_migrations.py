"""Explicit local-only SQLite schema management for the Control Plane.

SQLite is intentionally a separate migration path from the PostgreSQL
Alembic history. It is file-backed, single-process storage for local
development and small installations; it is never a PostgreSQL fallback or a
shared-replica coordination backend.
"""

from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from osa.control_plane.backend.agent_catalog import AgentCatalogError

SCHEMA_VERSION_TABLE = "osa_control_plane_sqlite_schema_versions"
CURRENT_SCHEMA_VERSION = 1


def _require_sqlite_stack() -> None:
    missing = [name for name in ("sqlalchemy", "aiosqlite") if find_spec(name) is None]
    if missing:
        raise AgentCatalogError(
            "SQLite persistence requires the missing dependencies: "
            f"{', '.join(missing)}; install the 'osa-control-plane[sqlite]' extra"
        )


def _sqlite_url(dsn: str) -> Any:
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError) as exc:
        raise AgentCatalogError("Control Plane SQLite URL is invalid") from exc
    if url.get_backend_name() != "sqlite":
        raise AgentCatalogError("Control Plane SQLite migrations require a SQLite DSN")
    if url.database in {None, ":memory:"}:
        raise AgentCatalogError("Control Plane SQLite persistence requires a file-backed database")
    if url.get_driver_name() not in {"pysqlite", "aiosqlite"}:
        raise AgentCatalogError("Control Plane SQLite requires the pysqlite or aiosqlite driver")
    return url


def normalize_async_sqlite_dsn(dsn: str) -> str:
    """Normalize a SQLite URL for SQLAlchemy's async engine."""
    url = _sqlite_url(dsn)
    return str(url.set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False))


def _normalize_sync_sqlite_dsn(dsn: str) -> str:
    return str(_sqlite_url(dsn).set(drivername="sqlite+pysqlite").render_as_string(hide_password=False))


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
        raise AgentCatalogError("Unable to restrict Control Plane SQLite file permissions") from exc


def _configure_sync_sqlite_engine(engine: Any) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def run_sqlite_migrations(dsn: str, revision: str = "head") -> None:
    """Create or upgrade the current explicit SQLite schema."""
    _require_sqlite_stack()
    if revision not in {"head", str(CURRENT_SCHEMA_VERSION)}:
        raise AgentCatalogError(f"SQLite Control Plane migrations support only 'head' or '{CURRENT_SCHEMA_VERSION}'")
    from sqlalchemy import create_engine, text

    from osa.control_plane.backend.tables import METADATA

    engine = create_engine(_normalize_sync_sqlite_dsn(dsn))
    _configure_sync_sqlite_engine(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            current = int(
                connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}")).scalar_one()
            )
            if current < CURRENT_SCHEMA_VERSION:
                METADATA.create_all(bind=connection)
                connection.execute(
                    text(f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES (:version)"),
                    {"version": CURRENT_SCHEMA_VERSION},
                )
        restrict_sqlite_file_permissions(dsn)
    finally:
        engine.dispose()


async def ensure_sqlite_schema(engine: Any) -> None:
    """Validate the current SQLite schema without mutating it at startup."""
    from sqlalchemy import text

    required_tables = {
        "osa_agents",
        "osa_agent_versions",
        "osa_deployments",
        "osa_resource_definitions",
        "osa_audit_events",
        "osa_external_agents",
    }
    try:
        async with engine.connect() as connection:
            version = int(
                (
                    await connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}"))
                ).scalar_one()
            )
            rows = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'osa_%'")
            )
            present = {str(row[0]) for row in rows.fetchall()}
    except Exception as exc:
        raise AgentCatalogError(
            "The Control Plane SQLite schema is unavailable; run 'osa-cp-migrate' "
            "against OSA_CONTROL_PLANE_DATABASE_URL before starting"
        ) from exc
    if version < CURRENT_SCHEMA_VERSION or not required_tables.issubset(present):
        raise AgentCatalogError(
            f"Control Plane SQLite schema version {version} is older than required "
            f"{CURRENT_SCHEMA_VERSION}; run 'osa-cp-migrate' before starting"
        )


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SCHEMA_VERSION_TABLE",
    "ensure_sqlite_schema",
    "normalize_async_sqlite_dsn",
    "restrict_sqlite_file_permissions",
    "run_sqlite_migrations",
]
