"""Provider-selection and local SQLite persistence contract tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def _sqlite_dsn(path: Path, *, async_driver: bool = False) -> str:
    driver = "+aiosqlite" if async_driver else ""
    return f"sqlite{driver}:///{path}"


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


def test_shared_policy_rejects_invalid_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.generic_agent import PersistenceConfigurationError, get_persistence_policy

    monkeypatch.setenv("OSA_PERSISTENCE_POLICY", "sometimes")
    with pytest.raises(PersistenceConfigurationError, match="local.*shared"):
        get_persistence_policy()


@pytest.mark.asyncio
async def test_shared_policy_requires_database_for_enabled_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.generic_agent import PersistenceConfigurationError
    from osa.runtimes.adk.service import create_memory_provider

    monkeypatch.setenv("OSA_PERSISTENCE_POLICY", "shared")
    monkeypatch.delenv("OSA_MEMORY_DATABASE_URL", raising=False)
    with pytest.raises(PersistenceConfigurationError, match="runtime memory state"):
        await create_memory_provider(required=True)


def test_shared_policy_rejects_process_local_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.generic_agent import SessionConfigurationError
    from osa.runtimes.adk.service import create_session_provider

    monkeypatch.setenv("OSA_PERSISTENCE_POLICY", "shared")
    with pytest.raises(SessionConfigurationError, match="durable session"):
        create_session_provider(persistence=False)


def test_shared_policy_rejects_control_plane_without_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.control_plane.backend.service import create_control_plane_app
    from osa.generic_agent import PersistenceConfigurationError

    monkeypatch.setenv("OSA_PERSISTENCE_POLICY", "shared")
    with pytest.raises(PersistenceConfigurationError, match="PostgreSQL"):
        create_control_plane_app()

    with pytest.raises(PersistenceConfigurationError, match="PostgreSQL"):
        create_control_plane_app(database_url="sqlite+aiosqlite:///local.db")


def test_shared_policy_requires_kubernetes_for_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.control_plane.backend.service import create_control_plane_app
    from osa.generic_agent import PersistenceConfigurationError

    monkeypatch.setenv("OSA_PERSISTENCE_POLICY", "shared")
    monkeypatch.setenv("OSA_DEPLOY_PROVIDER", "local")
    with pytest.raises(PersistenceConfigurationError, match="Kubernetes"):
        create_control_plane_app(database_url="postgresql+asyncpg://osa:osa@localhost/osa")


def test_shared_policy_requires_database_for_enabled_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.generic_agent import PersistenceConfigurationError, RateLimitConfig, build_rate_limiter

    monkeypatch.setenv("OSA_PERSISTENCE_POLICY", "shared")
    monkeypatch.delenv("OSA_RATE_LIMIT_DATABASE_URL", raising=False)
    with pytest.raises(PersistenceConfigurationError, match="rate-limit state"):
        build_rate_limiter(RateLimitConfig(requests=1))


@pytest.mark.asyncio
async def test_sqlite_memory_provider_survives_restart(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")

    from osa.generic_agent import MemoryEntry, MemoryScope
    from osa.runtimes.adk.sqlite_memory import SqliteMemoryProvider

    dsn = _sqlite_dsn(tmp_path / "memory.db", async_driver=True)
    provider = SqliteMemoryProvider(dsn)
    try:
        assert await provider.migrate() == 1
        await provider.ensure_schema()
        await provider.store(
            MemoryEntry(key="preference", content="prefers concise answers", scope=MemoryScope.USER, scope_id="ada")
        )
    finally:
        await provider.close()

    fresh = SqliteMemoryProvider(dsn)
    try:
        loaded = await fresh.load("preference", MemoryScope.USER, "ada")
        assert [entry.content for entry in loaded] == ["prefers concise answers"]
        assert [entry.content for entry in await fresh.search("CONCISE", MemoryScope.USER, "ada")] == [
            "prefers concise answers"
        ]
        assert await fresh.delete("preference", MemoryScope.USER, "ada")
        assert await fresh.load("preference", MemoryScope.USER, "ada") == []
    finally:
        await fresh.close()


def test_sqlite_session_provider_survives_restart_and_enforces_ownership(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")

    from osa.generic_agent import SessionAccessError
    from osa.runtimes.adk.sqlite_session import SqliteSessionProvider

    dsn = _sqlite_dsn(tmp_path / "sessions.db")
    provider = SqliteSessionProvider(dsn)
    provider.migrate()
    provider.ensure_schema()
    session = provider.create("sqlite-agent", user_id="ada", tenant_id="acme", max_history_messages=2)
    session.add_message("user", "one")
    session.add_message("assistant", "two")
    provider.save(session)
    provider.close()

    fresh = SqliteSessionProvider(dsn)
    try:
        loaded = fresh.resolve(str(session.session_id), agent_name="sqlite-agent", user_id="ada", tenant_id="acme")
        assert [message["content"] for message in loaded.conversation_history] == ["one", "two"]
        with pytest.raises(SessionAccessError):
            fresh.resolve(str(session.session_id), agent_name="sqlite-agent", user_id="mallory", tenant_id="acme")
    finally:
        fresh.close()
