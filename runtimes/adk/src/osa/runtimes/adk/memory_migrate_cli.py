"""Console entry point for the independent memory schema."""

from __future__ import annotations

import argparse
import asyncio
import os

from osa.generic_agent.errors import MemoryConfigurationError

MEMORY_DATABASE_URL_ENV_VAR = "OSA_MEMORY_DATABASE_URL"


async def _migrate(dsn: str) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.memory_migrations import migrate_memory_schema

    engine = create_async_engine(dsn)
    try:
        return await migrate_memory_schema(engine)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="osa-memory-migrate")
    parser.add_argument("--database-url", default=os.environ.get(MEMORY_DATABASE_URL_ENV_VAR))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error(f"--database-url is required (or set {MEMORY_DATABASE_URL_ENV_VAR})")
    try:
        version = asyncio.run(_migrate(args.database_url))
    except Exception as exc:
        raise MemoryConfigurationError(f"Unable to migrate memory schema: {exc}") from exc
    print(f"Applied memory schema version {version}")
    return 0


__all__ = ["main"]
