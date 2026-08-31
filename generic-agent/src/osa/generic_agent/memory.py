"""Memory domain types, policies, catalogs, and providers.

Scope semantics (P1.4): entries live under ``(scope, scope_id)``. The scope
ID is derived from the invocation context via :func:`memory_scope_id` —
``user`` scopes to the caller, ``agent`` to the agent, ``tenant`` to the
request tenant, ``application`` to the deployment. Entries are never visible
across scope IDs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import Field

from osa.generic_agent.config import MemoryScope, StrictModel

APPLICATION_SCOPE_ID = "osa"


class MemoryPolicy(StrictModel):
    """Policy controlling memory behavior.

    When an agent's ``spec.memory.policy`` references a policy, the policy is
    authoritative for scope, limits, and retention. ``auto_extract`` is
    reserved: extraction is explicit (``remember()``) and raw turns are never
    persisted automatically.
    """

    name: str
    scope: MemoryScope = MemoryScope.USER
    enabled: bool = True
    max_entries: int | None = Field(default=None, ge=1)
    retention_days: int | None = Field(default=None, ge=1)
    auto_extract: bool = False


class MemoryPolicyCatalog:
    """In-memory catalog of memory policies."""

    def __init__(self) -> None:
        self._policies: dict[str, MemoryPolicy] = {}

    def register(self, policy: MemoryPolicy) -> None:
        self._policies[policy.name] = policy

    def resolve(self, ref: str) -> MemoryPolicy:
        if ref not in self._policies:
            raise KeyError(f"Memory policy not found: {ref}")
        return self._policies[ref]

    def list_policies(self) -> list[MemoryPolicy]:
        return list(self._policies.values())

    def __len__(self) -> int:
        return len(self._policies)

    def __contains__(self, ref: str) -> bool:
        return ref in self._policies


def memory_scope_id(
    scope: MemoryScope,
    *,
    user_id: str | None,
    agent_name: str,
    tenant_id: str | None = None,
) -> str:
    """Derive the scope ID for a memory scope from invocation context."""
    if scope == MemoryScope.USER:
        return user_id or "anonymous"
    if scope == MemoryScope.AGENT:
        return agent_name
    if scope == MemoryScope.TENANT:
        return tenant_id or "default"
    return APPLICATION_SCOPE_ID


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
    """Interface for memory storage providers.

    Implementations may enforce per-scope limits and retention through
    :meth:`enforce`; providers without enforcement raise
    NotImplementedError.
    """

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

    async def enforce(
        self,
        scope: MemoryScope,
        scope_id: str,
        *,
        max_entries: int | None = None,
        retention_days: int | None = None,
    ) -> None:
        """Enforce per-scope entry limits and retention.

        ``max_entries`` evicts the oldest entries beyond the cap for the
        ``(scope, scope_id)`` pair; ``retention_days`` purges entries not
        updated within the window. The base implementation does not support
        enforcement.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support enforce()")


class InMemoryProvider(MemoryProvider):
    """In-memory memory provider for testing and development.

    Supports optional per-scope limits and retention via :meth:`enforce`;
    entries past retention are also purged lazily on load/search.
    """

    def __init__(self, *, max_entries: int | None = None, retention_days: int | None = None) -> None:
        self._entries: dict[str, list[MemoryEntry]] = {}
        self._max_entries = max_entries
        self._retention_days = retention_days

    def _make_key(self, key: str, scope: MemoryScope, scope_id: str) -> str:
        return f"{scope}:{scope_id}:{key}"

    def _scope_prefix(self, scope: MemoryScope, scope_id: str) -> str:
        return f"{scope}:{scope_id}:"

    def _purge_expired(self, prefix: str, now: datetime) -> None:
        if self._retention_days is None:
            return
        cutoff = now - timedelta(days=self._retention_days)
        for bucket_key in [k for k in self._entries if k.startswith(prefix)]:
            kept = [e for e in self._entries[bucket_key] if e.updated_at > cutoff]
            if kept:
                self._entries[bucket_key] = kept
            else:
                del self._entries[bucket_key]

    async def load(self, key: str, scope: MemoryScope, scope_id: str = "") -> list[MemoryEntry]:
        full_key = self._make_key(key, scope, scope_id)
        self._purge_expired(self._scope_prefix(scope, scope_id), datetime.now(UTC))
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
        now = datetime.now(UTC)
        self._purge_expired(self._scope_prefix(scope, scope_id), now)
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

    async def enforce(
        self,
        scope: MemoryScope,
        scope_id: str,
        *,
        max_entries: int | None = None,
        retention_days: int | None = None,
    ) -> None:
        effective_max = max_entries if max_entries is not None else self._max_entries
        effective_retention = retention_days if retention_days is not None else self._retention_days
        now = datetime.now(UTC)
        prefix = self._scope_prefix(scope, scope_id)

        if effective_retention is not None:
            cutoff = now - timedelta(days=effective_retention)
            for bucket_key in [k for k in self._entries if k.startswith(prefix)]:
                kept = [e for e in self._entries[bucket_key] if e.updated_at > cutoff]
                if kept:
                    self._entries[bucket_key] = kept
                else:
                    del self._entries[bucket_key]

        if effective_max is not None:
            bucket = sorted(
                (e for k, entries in self._entries.items() if k.startswith(prefix) for e in entries),
                key=lambda e: e.created_at,
            )
            excess = len(bucket) - effective_max
            if excess > 0:
                evict_ids = {e.entry_id for e in bucket[:excess]}
                for bucket_key in [k for k in self._entries if k.startswith(prefix)]:
                    kept = [e for e in self._entries[bucket_key] if e.entry_id not in evict_ids]
                    if kept:
                        self._entries[bucket_key] = kept
                    else:
                        del self._entries[bucket_key]
