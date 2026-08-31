"""Tests for MCP, Skill, Session, and Memory domain types."""

import pytest

from osa.generic_agent import (
    InMemoryProvider,
    McpCatalog,
    McpConnectionOptions,
    McpDefinition,
    McpToolMetadata,
    McpTransport,
    MemoryEntry,
    MemoryPolicy,
    MemoryScope,
    Session,
    SessionId,
    SessionManager,
    SessionNotFoundError,
    SkillCatalog,
    SkillDefinition,
)

# --- MCP Tests ---


class TestMcpDefinition:
    def test_create(self) -> None:
        d = McpDefinition(name="crm", endpoint="http://localhost:3000")
        assert d.name == "crm"
        assert d.transport == McpTransport.STDIO
        assert d.enabled is True

    def test_with_connection_options(self) -> None:
        d = McpDefinition(
            name="payments",
            transport=McpTransport.SSE,
            endpoint="http://payments:3000",
            connection_options=McpConnectionOptions(timeout_seconds=60.0, tls_verify=False),
        )
        assert d.transport == McpTransport.SSE
        assert d.connection_options.timeout_seconds == 60.0

    def test_with_credential_ref(self) -> None:
        from osa.generic_agent import SecretReference

        d = McpDefinition(
            name="secure",
            credential_ref=SecretReference(source="vault", key="mcp-key"),
        )
        assert d.credential_ref is not None
        assert d.credential_ref.source == "vault"


class TestMcpCatalog:
    def test_register_and_resolve(self) -> None:
        catalog = McpCatalog()
        catalog.register(McpDefinition(name="crm"))
        assert catalog.resolve("crm").name == "crm"

    def test_resolve_missing_raises(self) -> None:
        catalog = McpCatalog()
        with pytest.raises(KeyError, match="MCP server not found"):
            catalog.resolve("nonexistent")

    def test_contains(self) -> None:
        catalog = McpCatalog()
        catalog.register(McpDefinition(name="x"))
        assert "x" in catalog
        assert "y" not in catalog


class TestMcpToolMetadata:
    def test_create(self) -> None:
        m = McpToolMetadata(name="search_contacts", mcp_name="crm")
        assert m.name == "search_contacts"
        assert m.mcp_name == "crm"


# --- Skill Tests ---


class TestSkillDefinition:
    def test_create(self) -> None:
        s = SkillDefinition(name="support", description="Handle support requests")
        assert s.name == "support"
        assert s.tags == []

    def test_with_tags(self) -> None:
        s = SkillDefinition(name="billing", tags=["finance", "payments"])
        assert "finance" in s.tags


class TestSkillCatalog:
    def test_register_and_resolve(self) -> None:
        catalog = SkillCatalog()
        catalog.register(SkillDefinition(name="support"))
        assert catalog.resolve("support").name == "support"

    def test_search(self) -> None:
        catalog = SkillCatalog()
        catalog.register(SkillDefinition(name="support", description="Handle support requests"))
        catalog.register(SkillDefinition(name="billing", description="Handle billing"))
        results = catalog.search("support")
        assert len(results) == 1
        assert results[0].name == "support"

    def test_search_by_tag(self) -> None:
        catalog = SkillCatalog()
        catalog.register(SkillDefinition(name="a", tags=["finance"]))
        catalog.register(SkillDefinition(name="b", tags=["tech"]))
        results = catalog.search("finance")
        assert len(results) == 1


# --- Session Tests ---


class TestSessionId:
    def test_generate(self) -> None:
        sid = SessionId.generate()
        assert str(sid) == sid.value
        assert len(sid.value) > 0


class TestSession:
    def test_create(self) -> None:
        sid = SessionId.generate()
        session = Session(session_id=sid, agent_name="test")
        assert session.agent_name == "test"
        assert session.message_count == 0

    def test_add_message(self) -> None:
        sid = SessionId.generate()
        session = Session(session_id=sid, agent_name="test")
        session.add_message("user", "hello")
        session.add_message("assistant", "hi")
        assert session.message_count == 2
        assert session.conversation_history[0]["role"] == "user"


class TestSessionManager:
    def test_create_session(self) -> None:
        manager = SessionManager()
        session = manager.create(agent_name="test")
        assert session.agent_name == "test"
        assert len(manager) == 1

    def test_get_session(self) -> None:
        manager = SessionManager()
        session = manager.create(agent_name="test")
        retrieved = manager.get(str(session.session_id))
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent_returns_none(self) -> None:
        manager = SessionManager()
        assert manager.get("nonexistent") is None

    def test_resolve_returns_owned_session(self) -> None:
        manager = SessionManager()
        s1 = manager.create(agent_name="test", user_id="u1")
        s2 = manager.resolve(str(s1.session_id), agent_name="test", user_id="u1")
        assert s1.session_id == s2.session_id

    def test_resolve_rejects_unknown_session_id(self) -> None:
        manager = SessionManager()
        with pytest.raises(SessionNotFoundError):
            manager.resolve("does-not-exist", agent_name="test")

    def test_delete(self) -> None:
        manager = SessionManager()
        session = manager.create(agent_name="test")
        assert manager.delete(str(session.session_id), agent_name="test") is True
        assert len(manager) == 0
        assert manager.delete("nonexistent", agent_name="test") is False


# --- Memory Tests ---


class TestMemoryPolicy:
    def test_defaults(self) -> None:
        p = MemoryPolicy(name="default")
        assert p.scope == MemoryScope.USER
        assert p.enabled is True
        assert p.max_entries is None


class TestMemoryEntry:
    def test_create(self) -> None:
        e = MemoryEntry(key="prefs", content="dark mode")
        assert e.key == "prefs"
        assert e.content == "dark mode"
        assert e.scope == MemoryScope.USER


class TestInMemoryProvider:
    @pytest.mark.asyncio
    async def test_store_and_load(self) -> None:
        provider = InMemoryProvider()
        entry = MemoryEntry(key="prefs", content="dark mode", scope_id="user1")
        await provider.store(entry)
        results = await provider.load("prefs", MemoryScope.USER, "user1")
        assert len(results) == 1
        assert results[0].content == "dark mode"

    @pytest.mark.asyncio
    async def test_load_empty(self) -> None:
        provider = InMemoryProvider()
        results = await provider.load("nonexistent", MemoryScope.USER)
        assert results == []

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        provider = InMemoryProvider()
        await provider.store(MemoryEntry(key="x", content="y"))
        assert await provider.delete("x", MemoryScope.USER) is True
        assert await provider.delete("x", MemoryScope.USER) is False

    @pytest.mark.asyncio
    async def test_search(self) -> None:
        provider = InMemoryProvider()
        await provider.store(MemoryEntry(key="a", content="dark mode preference"))
        await provider.store(MemoryEntry(key="b", content="language: english"))
        results = await provider.search("dark", MemoryScope.USER)
        assert len(results) == 1
        assert results[0].content == "dark mode preference"
