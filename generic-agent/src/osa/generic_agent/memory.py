"""Memory domain types, policies, and providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from osa.generic_agent.config import MemoryScope, StrictModel


class MemoryPolicy(StrictModel):
    """Policy controlling memory behavior."""

    name: str
    scope: MemoryScope = MemoryScope.USER
    enabled: bool = True
    max_entries: int | None = None
    retention_days: int | None = None
    auto_extract: bool = False


@dataclass
class MemoryEntry:
    """A single memory entry."""

    key: str
    content: str
    scope: MemoryScope = MemoryScope.USER
    scope_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    entry_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MemoryProvider(ABC):
    """Interface for memory storage providers."""

    @abstractmethod
    async def load(self, key: str, scope: MemoryScope, scope_id: str = "") -> list[MemoryEntry]:
        """Load memory entries by key and scope."""
        ...

    @abstractmethod
    async def store(self, entry: MemoryEntry) -> None:
        """Store a memory entry."""
        ...

    @abstractmethod
    async def delete(self, key: str, scope: MemoryScope, scope_id: str = "") -> bool:
        """Delete memory entries. Returns True if any entries were deleted."""
        ...

    @abstractmethod
    async def search(self, query: str, scope: MemoryScope, scope_id: str = "", limit: int = 10) -> list[MemoryEntry]:
        """Search memory entries."""
        ...


class InMemoryProvider(MemoryProvider):
    """In-memory memory provider for testing and development."""

    def __init__(self) -> None:
        self._entries: dict[str, list[MemoryEntry]] = {}

    def _make_key(self, key: str, scope: MemoryScope, scope_id: str) -> str:
        return f"{scope}:{scope_id}:{key}"

    async def load(self, key: str, scope: MemoryScope, scope_id: str = "") -> list[MemoryEntry]:
        full_key = self._make_key(key, scope, scope_id)
        return list(self._entries.get(full_key, []))

    async def store(self, entry: MemoryEntry) -> None:
        full_key = self._make_key(entry.key, entry.scope, entry.scope_id)
        if full_key not in self._entries:
            self._entries[full_key] = []
        self._entries[full_key].append(entry)

    async def delete(self, key: str, scope: MemoryScope, scope_id: str = "") -> bool:
        full_key = self._make_key(key, scope, scope_id)
        return self._entries.pop(full_key, None) is not None

    async def search(self, query: str, scope: MemoryScope, scope_id: str = "", limit: int = 10) -> list[MemoryEntry]:
        query_lower = query.lower()
        results = []
        for entries in self._entries.values():
            for entry in entries:
                if (
                    entry.scope == scope
                    and (not scope_id or entry.scope_id == scope_id)
                    and query_lower in entry.content.lower()
                ):
                    results.append(entry)
                    if len(results) >= limit:
                        return results
        return results
