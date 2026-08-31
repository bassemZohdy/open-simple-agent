"""Control Plane database layer (ADR-004).

Owns the async engine and the table definitions for the PostgreSQL
repositories. Schema changes go through Alembic (``osa-cp-migrate``); the
application verifies connectivity only and never creates tables.
"""

from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
from typing import Any

DATABASE_URL_ENV_VAR = "OSA_CONTROL_PLANE_DATABASE_URL"

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _require_postgres_stack() -> None:
    missing = [name for name in ("sqlalchemy", "asyncpg", "alembic") if find_spec(name) is None]
    if missing:
        from osa.control_plane.backend.agent_catalog import AgentCatalogError

        raise AgentCatalogError(
            "PostgreSQL persistence requires the missing dependencies: "
            f"{', '.join(missing)}; install the 'osa-control-plane[postgres]' extra"
        )


def database_url_from_env() -> str | None:
    """The configured Control Plane DSN, if any."""
    return os.environ.get(DATABASE_URL_ENV_VAR)


def create_db_engine(dsn: str) -> Any:
    """Create the async engine for the Control Plane database."""
    _require_postgres_stack()
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(dsn)


def alembic_config(dsn: str) -> Any:
    """Build an Alembic config pointing at the packaged migrations."""
    _require_postgres_stack()
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", dsn)
    return config


def run_migrations(dsn: str, revision: str = "head") -> None:
    """Apply Alembic migrations up to ``revision`` (explicit ops step)."""
    from alembic import command

    command.upgrade(alembic_config(dsn), revision)


def migrate_cli(argv: list[str] | None = None) -> int:
    """``osa-cp-migrate`` console entry point.

    Applies pending Control Plane migrations to the database named by
    ``OSA_CONTROL_PLANE_DATABASE_URL``. Runs synchronously (Alembic's async
    recipe handles the event loop).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="osa-cp-migrate",
        description="Apply Open Simple Agent Control Plane database migrations.",
    )
    parser.add_argument(
        "--revision",
        default="head",
        help="Target Alembic revision (default: head)",
    )
    args = parser.parse_args(argv)

    dsn = database_url_from_env()
    if not dsn:
        parser.error(f"{DATABASE_URL_ENV_VAR} is required")
    run_migrations(dsn, args.revision)
    return 0
