"""Console entry point for the durable A2A task schema."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from osa.generic_agent.errors import PersistenceConfigurationError

A2A_TASK_DATABASE_URL_ENV_VAR = "OSA_A2A_TASK_DATABASE_URL"
A2A_TASK_TABLE_ENV_VAR = "OSA_A2A_TASK_TABLE"
DEFAULT_A2A_TASK_TABLE = "osa_a2a_tasks"


async def _migrate(database_url: str, table_name: str) -> int:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        from osa.runtimes.adk.a2a import _a2a_task_owner
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema
        from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore
    except ImportError as exc:  # pragma: no cover - optional dependency boundary
        raise PersistenceConfigurationError(
            "A2A schema migration requires the runtime a2a and database extras"
        ) from exc

    engine: Any = create_async_engine(database_url, pool_pre_ping=True)
    ownership_store = A2aTaskOwnershipStore(
        engine,
        table_name=f"{table_name}_ownership",
    )
    try:
        from a2a.server.tasks import DatabaseTaskStore

        task_store = DatabaseTaskStore(
            engine,
            create_table=False,
            table_name=table_name,
            owner_resolver=_a2a_task_owner,
        )
        return await migrate_a2a_schema(
            engine,
            task_table_name=table_name,
            task_store=task_store,
            ownership_store=ownership_store,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="osa-a2a-migrate")
    parser.add_argument("--database-url", default=os.environ.get(A2A_TASK_DATABASE_URL_ENV_VAR))
    parser.add_argument("--table", default=os.environ.get(A2A_TASK_TABLE_ENV_VAR, DEFAULT_A2A_TASK_TABLE))
    args = parser.parse_args(argv)
    if not args.database_url or not args.database_url.strip():
        parser.error(f"--database-url is required (or set {A2A_TASK_DATABASE_URL_ENV_VAR})")
    try:
        version = asyncio.run(_migrate(args.database_url, args.table))
    except Exception as exc:
        raise PersistenceConfigurationError(f"Unable to migrate A2A schema: {exc}") from exc
    print(f"Applied A2A schema version {version}")
    return 0


__all__ = ["main"]
