"""Session continuity and isolation acceptance tests (P0.3).

Two users and two agents cannot access each other's sessions; conversation
context survives the second request in the same session; unknown caller IDs,
expired sessions, and identity changes are rejected deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    FakeModelProvider,
    InMemoryProvider,
    MemoryConfig,
    MemoryEntry,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
    ModelResponse,
    SessionAccessError,
    SessionManager,
    SessionNotFoundError,
)
from osa.runtimes.adk import GenericAdkAgent

_TENANT_KEY = "tenant_id"


class ScriptedTextProvider(FakeModelProvider):
    """Returns scripted responses in order, then repeats the last one."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__(response=responses[-1])
        self._script = list(responses)

    async def generate(self, prompt: str, model_id: str, **kwargs: object) -> ModelResponse:
        self.calls.append({"prompt": prompt, "model_id": model_id, **kwargs})
        text = self._script.pop(0) if self._script else self._response
        return ModelResponse(text=text, model_id=model_id)


def _catalog() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))
    return catalog


def _agent(
    name: str,
    provider: FakeModelProvider | None = None,
    session_provider: SessionManager | None = None,
    memory_provider: InMemoryProvider | None = None,
    memory_enabled: bool = False,
) -> GenericAdkAgent:
    spec = AgentSpec(instruction="Help users.", model=ModelRef(ref="default"))
    if memory_enabled:
        spec = AgentSpec(
            instruction="Help users.",
            model=ModelRef(ref="default"),
            memory=MemoryConfig(enabled=True),
        )
    return GenericAdkAgent(
        definition=AgentDefinition(metadata=AgentMetadataConfig(name=name), spec=spec),
        model_provider=provider or FakeModelProvider(response="ok"),
        model_catalog=_catalog(),
        session_provider=session_provider,
        memory_provider=memory_provider,
    )


class TestOwnershipIsolation:
    async def test_two_users_cannot_access_each_others_sessions(self) -> None:
        shared = SessionManager()
        agent = _agent("svc", session_provider=shared)

        first = await agent.invoke(AgentRequest(input="hi", user_id="alice"))
        with pytest.raises(SessionAccessError):
            await agent.invoke(AgentRequest(input="hi", session_id=first.session_id, user_id="mallory"))

    async def test_two_agents_cannot_access_each_others_sessions(self) -> None:
        shared = SessionManager()
        sales = _agent("sales", session_provider=shared)
        support = _agent("support", session_provider=shared)

        first = await sales.invoke(AgentRequest(input="hi", user_id="u1"))
        with pytest.raises(SessionAccessError):
            await support.invoke(AgentRequest(input="hi", session_id=first.session_id, user_id="u1"))

    async def test_tenant_change_is_an_access_violation(self) -> None:
        shared = SessionManager()
        agent = _agent("svc", session_provider=shared)

        first = await agent.invoke(AgentRequest(input="hi", user_id="u1", metadata={_TENANT_KEY: "t1"}))
        with pytest.raises(SessionAccessError):
            await agent.invoke(
                AgentRequest(
                    input="hi",
                    session_id=first.session_id,
                    user_id="u1",
                    metadata={_TENANT_KEY: "t2"},
                )
            )


class TestSessionLifecycle:
    async def test_unknown_caller_session_id_is_rejected(self) -> None:
        agent = _agent("svc")
        with pytest.raises(SessionNotFoundError):
            await agent.invoke(AgentRequest(input="hi", session_id="bogus"))

    async def test_session_id_is_stable_across_requests(self) -> None:
        agent = _agent("svc")
        first = await agent.invoke(AgentRequest(input="one"))
        second = await agent.invoke(AgentRequest(input="two", session_id=first.session_id))
        assert second.session_id == first.session_id

    async def test_expired_session_behaves_as_unknown(self) -> None:
        shared = SessionManager()
        agent = _agent("svc", session_provider=shared)
        session = shared.create("svc", user_id="u1", ttl_seconds=60)
        session.last_active_at = datetime.now(UTC) - timedelta(seconds=120)

        with pytest.raises(SessionNotFoundError):
            await agent.invoke(AgentRequest(input="hi", session_id=str(session.session_id), user_id="u1"))

    async def test_purge_expired_removes_only_expired(self) -> None:
        shared = SessionManager()
        fresh = shared.create("a")
        stale = shared.create("a", ttl_seconds=10)
        stale.last_active_at = datetime.now(UTC) - timedelta(seconds=100)

        purged = shared.purge_expired()

        assert purged == 1
        assert shared.get_runtime_view(str(stale.session_id)) is None
        assert shared.get_runtime_view(str(fresh.session_id)) is not None

    async def test_history_is_bounded(self) -> None:
        shared = SessionManager()
        session = shared.create("agent", max_history_messages=3)
        for index in range(10):
            session.add_message("user", f"msg-{index}")
        assert session.message_count == 3
        assert [m["content"] for m in session.conversation_history] == ["msg-7", "msg-8", "msg-9"]


class TestConversationContinuity:
    async def test_context_survives_second_request(self) -> None:
        provider = ScriptedTextProvider(["Hi Ada!", "Your name is Ada."])
        agent = _agent("greeter", provider=provider)

        first = await agent.invoke(AgentRequest(input="My name is Ada", user_id="ada"))
        await agent.invoke(AgentRequest(input="What is my name?", user_id="ada", session_id=first.session_id))

        second_prompt = provider.calls[1]["prompt"]
        assert isinstance(second_prompt, str)
        assert "My name is Ada" in second_prompt
        assert "What is my name?" in second_prompt

    async def test_memory_scope_isolated_per_user(self) -> None:
        memory = InMemoryProvider()
        await memory.store(MemoryEntry(key="pref", content="Ada likes widgets", scope_id="ada"))
        provider = FakeModelProvider(response="ok")
        agent = _agent("mem", provider=provider, memory_provider=memory, memory_enabled=True)

        await agent.invoke(AgentRequest(input="widgets", user_id="bob"))

        prompt = provider.calls[0]["prompt"]
        assert isinstance(prompt, str)
        assert "Ada likes widgets" not in prompt

    async def test_memory_scope_reached_for_owner(self) -> None:
        memory = InMemoryProvider()
        await memory.store(MemoryEntry(key="pref", content="Ada likes widgets", scope_id="ada"))
        provider = FakeModelProvider(response="ok")
        agent = _agent("mem", provider=provider, memory_provider=memory, memory_enabled=True)

        await agent.invoke(AgentRequest(input="widgets", user_id="ada"))

        prompt = provider.calls[0]["prompt"]
        assert isinstance(prompt, str)
        assert "Ada likes widgets" in prompt
