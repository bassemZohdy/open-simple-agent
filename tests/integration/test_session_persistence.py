"""PostgreSQL runtime-session acceptance tests."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OSA_TEST_DATABASE_URL"),
    reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL session tests skipped",
)


@pytest.fixture()
def dsn() -> str:
    return os.environ["OSA_TEST_DATABASE_URL"]


@pytest.fixture()
def provider(dsn: str) -> Any:
    from osa.runtimes.adk.postgres_session import PostgresSessionProvider

    value = PostgresSessionProvider(dsn)
    value.migrate()
    yield value
    value.close()


@pytest.fixture(autouse=True)
def cleanup(dsn: str) -> Any:
    yield
    from osa.runtimes.adk.postgres_session import PostgresSessionProvider

    value = PostgresSessionProvider(dsn)
    from sqlalchemy import text

    with value._engine.begin() as connection:  # noqa: SLF001 - test cleanup
        connection.execute(text("DELETE FROM osa_runtime_sessions WHERE agent_name LIKE 'session-test-%'"))
    value.close()


def test_session_survives_provider_restart_and_preserves_owner(provider: Any, dsn: str) -> None:
    session = provider.create("session-test-agent", user_id="ada", tenant_id="acme", max_history_messages=2)
    session.add_message("user", "one")
    session.add_message("assistant", "two")
    provider.save(session)
    provider.close()

    from osa.generic_agent import SessionAccessError
    from osa.runtimes.adk.postgres_session import PostgresSessionProvider

    fresh = PostgresSessionProvider(dsn)
    try:
        loaded = fresh.resolve(
            str(session.session_id), agent_name="session-test-agent", user_id="ada", tenant_id="acme"
        )
        assert [message["content"] for message in loaded.conversation_history] == ["one", "two"]
        with pytest.raises(SessionAccessError):
            fresh.resolve(str(session.session_id), agent_name="session-test-agent", user_id="mallory", tenant_id="acme")
    finally:
        fresh.close()


def test_concurrent_update_is_rejected(provider: Any, dsn: str) -> None:
    session = provider.create("session-test-concurrency", user_id="ada")
    from osa.generic_agent import SessionConcurrencyError
    from osa.runtimes.adk.postgres_session import PostgresSessionProvider

    first = PostgresSessionProvider(dsn)
    second = PostgresSessionProvider(dsn)
    try:
        left = first.get_runtime_view(str(session.session_id))
        right = second.get_runtime_view(str(session.session_id))
        assert left is not None and right is not None
        left.add_message("user", "first")
        first.save(left)
        right.add_message("user", "stale")
        with pytest.raises(SessionConcurrencyError):
            second.save(right)
    finally:
        first.close()
        second.close()


def test_expired_session_is_deleted_on_access(provider: Any) -> None:
    session = provider.create("session-test-expiry", user_id="ada", ttl_seconds=10)
    from sqlalchemy import text

    from osa.generic_agent import SessionNotFoundError

    with provider._engine.begin() as connection:  # noqa: SLF001 - expiry setup
        connection.execute(
            text("UPDATE osa_runtime_sessions SET last_active_at = :expired WHERE session_id = :session_id"),
            {"expired": datetime.now(UTC) - timedelta(seconds=20), "session_id": str(session.session_id)},
        )
    with pytest.raises(SessionNotFoundError):
        provider.resolve(str(session.session_id), agent_name="session-test-expiry", user_id="ada")
