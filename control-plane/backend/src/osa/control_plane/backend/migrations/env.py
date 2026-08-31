"""Alembic environment for the Open Simple Agent Control Plane.

Async recipe: the DSN comes from ``OSA_CONTROL_PLANE_DATABASE_URL`` (or the
config's ``sqlalchemy.url``) and migrations run through ``run_sync``.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from osa.control_plane.backend.tables import METADATA

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = METADATA


def _database_url() -> str:
    url = os.environ.get("OSA_CONTROL_PLANE_DATABASE_URL")
    if url:
        return url
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("No database URL: set OSA_CONTROL_PLANE_DATABASE_URL or sqlalchemy.url")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Any) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
