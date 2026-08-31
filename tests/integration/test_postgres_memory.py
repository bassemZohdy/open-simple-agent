"""PostgreSQL memory provider integration tests (P1.4, ADR-003).

Run against a real PostgreSQL 16 when ``OSA_TEST_DATABASE_URL`` is set
(CI provides a service container); skipped otherwise so default test runs
stay offline.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from osa.generic_agent import MemoryEntry, MemoryScope

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OSA_TEST_DATABASE_URL"),
        reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL memory tests skipped",
    ),
]


@pytest.fixture()
def dsn() -> str:
    return os.environ["OSA_TEST_DATABASE_URL"]


def _entry(key: str, content: str, scope_id: str = "ada", scope: MemoryScope = MemoryScope.USER) -> MemoryEntry:
    return MemoryEntry(key=key, content=content, scope=scope, scope_id=scope_id)


@pytest.fixture()
async def clean_provider(dsn: str) -> Any:
    """Provider with a dedicated scope prefix per test for isolation."""
    from osa.runtimes.adk.postgres_memory import PostgresMemoryProvider

    provider = PostgresMemoryProvider(dsn)
    await provider.ensure_schema()
    scope_id = f"test-{uuid4().hex[:12]}"
    yield provider, scope_id
    # Cleanup: remove entries created under the test's scope id.
    from sqlalchemy import text

    async with provider._engine.begin() as connection:  # noqa: SLF001 - test cleanup
        await connection.execute(text("DELETE FROM osa_memory_entries WHERE scope_id = :sid"), {"sid": scope_id})
    await provider.close()


class TestPersistence:
    async def test_store_load_round_trip(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        await provider.store(_entry("pref", "prefers dark mode", scope_id))
        entries = await provider.load("pref", MemoryScope.USER, scope_id)
        assert len(entries) == 1
        assert entries[0].content == "prefers dark mode"

    async def test_memory_survives_provider_restart(self, clean_provider: Any, dsn: str) -> None:
        provider, scope_id = clean_provider
        await provider.store(_entry("durable", "survives restart", scope_id))
        await provider.close()

        from osa.runtimes.adk.postgres_memory import PostgresMemoryProvider

        fresh = PostgresMemoryProvider(dsn)
        try:
            entries = await fresh.load("durable", MemoryScope.USER, scope_id)
            assert [e.content for e in entries] == ["survives restart"]
        finally:
            await fresh.close()

    async def test_append_semantics_for_same_key(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        await provider.store(_entry("log", "first", scope_id))
        await provider.store(_entry("log", "second", scope_id))
        entries = await provider.load("log", MemoryScope.USER, scope_id)
        assert [e.content for e in entries] == ["first", "second"]


class TestScopeIsolation:
    async def test_entries_invisible_across_scope_ids(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        await provider.store(_entry("secret", "ada-private", scope_id))
        assert await provider.search("ada-private", MemoryScope.USER, "mallory") == []
        assert await provider.search("ada-private", MemoryScope.USER, scope_id)

    async def test_entries_invisible_across_scopes(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        await provider.store(_entry("secret", "user-scoped-value", scope_id, scope=MemoryScope.USER))
        assert await provider.search("user-scoped-value", MemoryScope.AGENT, scope_id) == []

    async def test_search_is_case_insensitive_substring(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        await provider.store(_entry("note", "Prefers DARK Mode", scope_id))
        assert await provider.search("dark mode", MemoryScope.USER, scope_id)
        assert not await provider.search("bright mode", MemoryScope.USER, scope_id)

    async def test_like_metacharacters_are_escaped(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        await provider.store(_entry("literal", "progress_100%", scope_id))
        assert await provider.search("progress_100", MemoryScope.USER, scope_id)
        assert not await provider.search("progressX100", MemoryScope.USER, scope_id)


class TestEnforcement:
    async def test_max_entries_eviction(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        for index in range(5):
            await provider.store(_entry(f"k{index}", f"v{index}", scope_id))
        await provider.enforce(MemoryScope.USER, scope_id, max_entries=3)

        from sqlalchemy import text

        async with provider._engine.begin() as connection:  # noqa: SLF001 - test assertion
            count = (
                await connection.execute(
                    text("SELECT COUNT(*) FROM osa_memory_entries WHERE scope_id = :sid"),
                    {"sid": scope_id},
                )
            ).scalar()
        assert count == 3

    async def test_retention_purge(self, clean_provider: Any) -> None:
        provider, scope_id = clean_provider
        stale = _entry("stale", "old value", scope_id)
        stale.created_at = datetime.now(UTC) - timedelta(days=30)
        stale.updated_at = datetime.now(UTC) - timedelta(days=30)
        await provider.store(stale)
        await provider.store(_entry("fresh", "new value", scope_id))

        await provider.enforce(MemoryScope.USER, scope_id, retention_days=7)

        assert await provider.load("stale", MemoryScope.USER, scope_id) == []
        assert len(await provider.load("fresh", MemoryScope.USER, scope_id)) == 1
