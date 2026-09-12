"""Console entry point for the independent memory schema."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import TYPE_CHECKING

from osa.generic_agent.errors import MemoryConfigurationError

if TYPE_CHECKING:
    from osa.generic_agent import MemoryProvider

MEMORY_DATABASE_URL_ENV_VAR = "OSA_MEMORY_DATABASE_URL"


async def _migrate(dsn: str) -> int:
    backend = dsn.split(":", 1)[0].split("+", 1)[0].lower()
    provider: MemoryProvider
    if backend == "sqlite":
        from osa.runtimes.adk.sqlite_memory import SqliteMemoryProvider

        provider = SqliteMemoryProvider(dsn)
        try:
            return await provider.migrate()
        finally:
            await provider.close()
    if backend == "postgresql":
        from osa.runtimes.adk.postgres_memory import PostgresMemoryProvider

        provider = PostgresMemoryProvider(dsn)
        try:
            return await provider.migrate()
        finally:
            await provider.close()
    raise MemoryConfigurationError("OSA_MEMORY_DATABASE_URL must use PostgreSQL or SQLite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="osa-memory-migrate")
    parser.add_argument("--database-url", default=os.environ.get(MEMORY_DATABASE_URL_ENV_VAR))
    args = parser.parse_args(argv)
    if not args.database_url or not args.database_url.strip():
        parser.error(f"--database-url is required (or set {MEMORY_DATABASE_URL_ENV_VAR})")
    try:
        version = asyncio.run(_migrate(args.database_url))
    except Exception as exc:
        raise MemoryConfigurationError(f"Unable to migrate memory schema: {exc}") from exc
    print(f"Applied memory schema version {version}")
    return 0


__all__ = ["main"]
