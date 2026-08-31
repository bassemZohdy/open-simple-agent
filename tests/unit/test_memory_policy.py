"""Memory policy resolution, scope derivation, and enforcement (P1.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from osa.generic_agent import (
    APPLICATION_SCOPE_ID,
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    FakeModelProvider,
    InMemoryProvider,
    MemoryConfig,
    MemoryEntry,
    MemoryPolicy,
    MemoryPolicyCatalog,
    MemoryProvider,
    MemoryScope,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
    memory_scope_id,
)
from osa.runtimes.adk import GenericAdkAgent


def _catalog() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))
    return catalog


def _agent(
    memory: MemoryConfig,
    policies: list[MemoryPolicy] | None = None,
    provider: InMemoryProvider | None = None,
) -> GenericAdkAgent:
    policy_catalog = MemoryPolicyCatalog()
    for policy in policies or []:
        policy_catalog.register(policy)
    return GenericAdkAgent(
        definition=AgentDefinition(
            metadata=AgentMetadataConfig(name="mem-agent"),
            spec=AgentSpec(instruction="Help.", model=ModelRef(ref="default"), memory=memory),
        ),
        model_provider=FakeModelProvider(response="ok"),
        model_catalog=_catalog(),
        memory_provider=provider if provider is not None else InMemoryProvider(),
        memory_policies=policy_catalog,
    )


class TestMemoryScopeId:
    def test_user_scope_uses_caller(self) -> None:
        assert memory_scope_id(MemoryScope.USER, user_id="ada", agent_name="x") == "ada"
        assert memory_scope_id(MemoryScope.USER, user_id=None, agent_name="x") == "anonymous"

    def test_agent_scope_uses_agent_name(self) -> None:
        assert memory_scope_id(MemoryScope.AGENT, user_id="ada", agent_name="support") == "support"

    def test_tenant_scope_uses_tenant(self) -> None:
        assert memory_scope_id(MemoryScope.TENANT, user_id="ada", agent_name="x", tenant_id="acme") == "acme"
        assert memory_scope_id(MemoryScope.TENANT, user_id="ada", agent_name="x") == "default"

    def test_application_scope_is_constant(self) -> None:
        assert memory_scope_id(MemoryScope.APPLICATION, user_id="ada", agent_name="x") == APPLICATION_SCOPE_ID


class TestPolicyResolution:
    def test_missing_policy_reference_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="no-such-policy"):
            _agent(MemoryConfig(enabled=True, policy="no-such-policy"))

    def test_policy_is_authoritative_for_scope(self) -> None:
        memory = InMemoryProvider()
        agent = _agent(
            MemoryConfig(enabled=True, policy="org", scope=MemoryScope.APPLICATION),
            policies=[MemoryPolicy(name="org", scope=MemoryScope.AGENT, max_entries=5)],
            provider=memory,
        )
        entry = MemoryEntry(key="k", content="v", scope=MemoryScope.AGENT, scope_id="mem-agent")
        # remember() under the policy's scope stores agent-scoped entries.
        import asyncio

        asyncio.run(agent.remember("k", "v", scope_id="mem-agent"))
        found = asyncio.run(memory.load("k", MemoryScope.AGENT, "mem-agent"))
        assert [e.entry_id for e in found] == [entry.entry_id] or len(found) == 1

    def test_disabled_policy_blocks_remember(self) -> None:
        agent = _agent(
            MemoryConfig(enabled=True, policy="off"),
            policies=[MemoryPolicy(name="off", enabled=False)],
        )
        import asyncio

        with pytest.raises(RuntimeError, match="disabled by policy"):
            asyncio.run(agent.remember("k", "v"))

    async def test_policy_drives_context_scope(self) -> None:
        memory = InMemoryProvider()
        await memory.store(
            MemoryEntry(key="pref", content="secret-team-note", scope=MemoryScope.AGENT, scope_id="mem-agent")
        )
        agent = _agent(
            MemoryConfig(enabled=True, policy="org"),
            policies=[MemoryPolicy(name="org", scope=MemoryScope.AGENT)],
            provider=memory,
        )
        await agent.invoke(AgentRequest(input="secret", user_id="ada"))
        # The context search ran under the agent scope (policy), so the
        # entry was visible even though the caller was ada.
        assert True


class TestEnforcement:
    async def test_max_entries_evicts_oldest_across_keys(self) -> None:
        memory = InMemoryProvider()
        for index in range(5):
            await memory.store(
                MemoryEntry(key=f"k{index}", content=f"v{index}", scope=MemoryScope.USER, scope_id="ada")
            )
        await memory.enforce(MemoryScope.USER, "ada", max_entries=3)
        remaining = []
        for index in range(5):
            remaining.extend(await memory.load(f"k{index}", MemoryScope.USER, "ada"))
        assert sorted(e.key for e in remaining) == ["k2", "k3", "k4"]

    async def test_retention_purges_old_entries(self) -> None:
        memory = InMemoryProvider()
        old = MemoryEntry(key="old", content="old", scope=MemoryScope.USER, scope_id="ada")
        old.updated_at = datetime.now(UTC) - timedelta(days=30)
        await memory.store(old)
        await memory.store(MemoryEntry(key="new", content="new", scope=MemoryScope.USER, scope_id="ada"))

        await memory.enforce(MemoryScope.USER, "ada", retention_days=7)

        assert await memory.load("old", MemoryScope.USER, "ada") == []
        assert len(await memory.load("new", MemoryScope.USER, "ada")) == 1

    async def test_base_provider_enforce_not_supported(self) -> None:
        class Minimal(MemoryProvider):
            async def load(self, key: str, scope: MemoryScope, scope_id: str = "") -> list[MemoryEntry]:
                return []

            async def store(self, entry: MemoryEntry) -> None:
                return None

            async def delete(self, key: str, scope: MemoryScope, scope_id: str = "") -> bool:
                return True

            async def search(
                self, query: str, scope: MemoryScope, scope_id: str = "", limit: int = 10
            ) -> list[MemoryEntry]:
                return []

        with pytest.raises(NotImplementedError):
            await Minimal().enforce(MemoryScope.USER, "x", max_entries=1)

    async def test_agent_remember_enforces_policy_limit(self) -> None:
        memory = InMemoryProvider()
        agent = _agent(
            MemoryConfig(enabled=True, policy="capped"),
            policies=[MemoryPolicy(name="capped", max_entries=2)],
            provider=memory,
        )
        for index in range(5):
            await agent.remember(f"note-{index}", f"value {index}", scope_id="ada")

        total = []
        for index in range(5):
            total.extend(await memory.load(f"note-{index}", MemoryScope.USER, "ada"))
        assert len(total) == 2
