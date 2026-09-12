"""Provider-selection policy tests for PostgreSQL-only runtime stores."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_memory_selector_rejects_empty_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.runtimes.adk.service import create_memory_provider

    monkeypatch.setenv("OSA_MEMORY_DATABASE_URL", "")
    from osa.generic_agent import MemoryConfigurationError

    with pytest.raises(MemoryConfigurationError, match="must not be empty"):
        await create_memory_provider()


def test_memory_provider_rejects_sqlite_dsn() -> None:
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("asyncpg")

    from osa.generic_agent import MemoryConfigurationError
    from osa.runtimes.adk.postgres_memory import PostgresMemoryProvider

    with pytest.raises(MemoryConfigurationError, match="must use PostgreSQL"):
        PostgresMemoryProvider("sqlite+aiosqlite:///memory.db")


def test_session_provider_rejects_sqlite_dsn() -> None:
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("pg8000")

    from osa.generic_agent import SessionConfigurationError
    from osa.runtimes.adk.postgres_session import PostgresSessionProvider

    with pytest.raises(SessionConfigurationError, match="must use PostgreSQL"):
        PostgresSessionProvider("sqlite:///sessions.db")


def test_session_selector_rejects_empty_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.generic_agent import SessionConfigurationError
    from osa.runtimes.adk.service import create_session_provider

    monkeypatch.setenv("OSA_SESSION_DATABASE_URL", "")
    with pytest.raises(SessionConfigurationError, match="empty"):
        create_session_provider(persistence=True)
