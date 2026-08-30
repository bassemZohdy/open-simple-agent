"""Tests for the ADK runtime vertical slice.

Verifies the end-to-end flow:
    AgentDefinition -> ADK Runtime -> Fake Model -> Response
"""

import time

import pytest

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    CalculatorTool,
    FakeModelProvider,
    InMemoryProvider,
    MemoryConfig,
    MemoryEntry,
    MemoryScope,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
    ModelResponse,
    SkillCatalog,
    SkillDefinition,
    SkillRef,
    Tool,
    ToolCatalog,
    ToolDefinition,
    ToolRef,
    ToolResult,
    ToolTimeoutError,
)
from osa.runtimes.adk import AdkAgentFactory, AdkRuntime, GenericAdkAgent


def _make_definition(name: str = "test-agent", instruction: str = "Help users.") -> AgentDefinition:
    return AgentDefinition(
        metadata=AgentMetadataConfig(name=name),
        spec=AgentSpec(instruction=instruction, model=ModelRef(ref="default")),
    )


def _make_catalog_with_default() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model"))
    return catalog


class TestGenericAdkAgent:
    async def test_invoke_returns_response(self) -> None:
        provider = FakeModelProvider(response="Hello! How can I help?")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        request = AgentRequest(input="Hi there")
        response = await agent.invoke(request)

        assert response.output == "Hello! How can I help?"
        assert response.error is None
        assert response.invocation_id == request.invocation_id

    async def test_invoke_includes_instruction_in_prompt(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition(instruction="You are a support agent.")

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        await agent.invoke(AgentRequest(input="help me"))
        prompt = provider.calls[0]["prompt"]
        assert isinstance(prompt, str)
        assert "You are a support agent." in prompt
        assert "help me" in prompt

    async def test_invoke_creates_session(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        response = await agent.invoke(AgentRequest(input="hello"))
        assert response.session_id is not None

    async def test_invoke_tracks_conversation(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        r1 = await agent.invoke(AgentRequest(input="first"))
        await agent.invoke(AgentRequest(input="second", session_id=r1.session_id))
        assert len(provider.calls) == 2

    async def test_invoke_handles_model_error(self) -> None:
        class ErrorProvider(FakeModelProvider):
            async def generate(self, prompt: str, model_id: str, **kwargs: object) -> ModelResponse:
                raise RuntimeError("model failed")

        provider = ErrorProvider()
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        response = await agent.invoke(AgentRequest(input="hello"))
        assert response.error is not None
        assert "model failed" in response.error

    async def test_invoke_uses_resolved_model(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        await agent.invoke(AgentRequest(input="hello"))
        assert provider.calls[0]["model_id"] == "fake-model"

    async def test_shutdown(self) -> None:
        provider = FakeModelProvider()
        catalog = _make_catalog_with_default()
        agent = GenericAdkAgent(
            definition=_make_definition(),
            model_provider=provider,
            model_catalog=catalog,
        )
        await agent.invoke(AgentRequest(input="hi"))
        await agent.shutdown()


class TestAdkRuntime:
    async def test_create_agent(self) -> None:
        runtime = AdkRuntime(
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_make_catalog_with_default(),
        )
        definition = _make_definition()
        agent = await runtime.create(definition)

        assert isinstance(agent, GenericAdkAgent)
        assert agent.metadata.name == "test-agent"

    async def test_invoke_through_runtime(self) -> None:
        provider = FakeModelProvider(response="I can help with that!")
        runtime = AdkRuntime(
            model_provider=provider,
            model_catalog=_make_catalog_with_default(),
        )
        definition = _make_definition(instruction="Be helpful.")
        agent = await runtime.create(definition)

        request = AgentRequest(input="I need help")
        response = await agent.invoke(request)

        assert response.output == "I can help with that!"
        assert response.error is None

    async def test_shutdown_all_agents(self) -> None:
        runtime = AdkRuntime(
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
        )
        await runtime.create(_make_definition("a"))
        await runtime.create(_make_definition("b"))
        await runtime.shutdown()


class TestAdkAgentFactory:
    def test_create_agent(self) -> None:
        factory = AdkAgentFactory(
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_make_catalog_with_default(),
        )
        definition = _make_definition()
        agent = factory.create(definition)

        assert isinstance(agent, GenericAdkAgent)
        assert agent.metadata.name == "test-agent"


class SlowTool(Tool):
    """Tool that always takes longer than a short test timeout."""

    name: str = "slow"
    description: str = "Sleeps so tests can trigger timeouts"

    def execute(self, **kwargs: object) -> ToolResult:
        time.sleep(0.5)
        return ToolResult(success=True, output="finally done")


class ScriptedProvider(FakeModelProvider):
    """Returns scripted responses in order, then repeats the last one."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__(response=responses[-1])
        self._script = list(responses)

    async def generate(self, prompt: str, model_id: str, **kwargs: object) -> ModelResponse:
        self.calls.append({"prompt": prompt, "model_id": model_id, **kwargs})
        text = self._script.pop(0) if self._script else self._response
        return ModelResponse(text=text, model_id=model_id)


def _make_tool_catalog(*tools: Tool, timeout_seconds: float | None = None) -> ToolCatalog:
    catalog = ToolCatalog()
    for tool in tools:
        catalog.register_definition(
            ToolDefinition(name=tool.name, description=tool.description, timeout_seconds=timeout_seconds)
        )
        catalog.register_tool(tool)
    return catalog


def _make_tool_definition(*tool_names: str, skill: str | None = None) -> AgentDefinition:
    definition = _make_definition()
    return definition.model_copy(
        update={
            "spec": AgentSpec(
                instruction=definition.spec.instruction,
                model=ModelRef(ref="default"),
                tools=[ToolRef(ref=name) for name in tool_names],
                skills=[SkillRef(ref=skill)] if skill else [],
            )
        }
    )


class TestNativeToolResolution:
    async def test_tool_call_loop_derives_response_from_tool_result(self) -> None:
        provider = ScriptedProvider(
            [
                'TOOL_CALL calculator {"operation": "add", "a": 2, "b": 3}',
                "The answer is 5.",
            ]
        )
        agent = GenericAdkAgent(
            definition=_make_tool_definition("calculator"),
            model_provider=provider,
            model_catalog=_make_catalog_with_default(),
            tool_catalog=_make_tool_catalog(CalculatorTool()),
        )
        assert agent.tools == ["calculator"]

        response = await agent.invoke(AgentRequest(input="what is 2+3?"))

        assert response.output == "The answer is 5."
        assert response.error is None
        assert len(provider.calls) == 2
        second_prompt = provider.calls[1]["prompt"]
        assert isinstance(second_prompt, str)
        assert "success=True" in second_prompt
        assert "5.0" in second_prompt

    async def test_unknown_tool_reference_fails_at_construction(self) -> None:
        definition = _make_definition()
        broken = definition.model_copy(
            update={"spec": AgentSpec(model=ModelRef(ref="default"), tools=[ToolRef(ref="missing")])}
        )
        with pytest.raises(ValueError, match="not found in the tool catalog"):
            GenericAdkAgent(
                definition=broken,
                model_provider=FakeModelProvider(),
                model_catalog=_make_catalog_with_default(),
            )

    async def test_unknown_skill_reference_fails_at_construction(self) -> None:
        definition = _make_definition()
        broken = definition.model_copy(
            update={"spec": AgentSpec(model=ModelRef(ref="default"), skills=[SkillRef(ref="missing")])}
        )
        with pytest.raises(ValueError, match="not found in the skill catalog"):
            GenericAdkAgent(
                definition=broken,
                model_provider=FakeModelProvider(),
                model_catalog=_make_catalog_with_default(),
            )

    async def test_skills_resolved_at_construction(self) -> None:
        skill_catalog = SkillCatalog()
        skill_catalog.register(SkillDefinition(name="customer-support", description="Handles support requests"))
        definition = _make_definition()
        with_skills = definition.model_copy(
            update={"spec": AgentSpec(model=ModelRef(ref="default"), skills=[SkillRef(ref="customer-support")])}
        )

        agent = GenericAdkAgent(
            definition=with_skills,
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
            skill_catalog=skill_catalog,
        )

        assert [skill.name for skill in agent.skills] == ["customer-support"]

    async def test_execute_tool_returns_result(self) -> None:
        agent = GenericAdkAgent(
            definition=_make_tool_definition("calculator"),
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
            tool_catalog=_make_tool_catalog(CalculatorTool()),
        )

        result = await agent.execute_tool("calculator", operation="multiply", a=3, b=4)

        assert result.success is True
        assert result.output == "12.0"

    async def test_execute_tool_enforces_timeout(self) -> None:
        agent = GenericAdkAgent(
            definition=_make_tool_definition("slow"),
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
            tool_catalog=_make_tool_catalog(SlowTool(), timeout_seconds=0.05),
        )

        with pytest.raises(ToolTimeoutError, match="timed out after 0.05"):
            await agent.execute_tool("slow")

    async def test_invoke_captures_timeout_in_error(self) -> None:
        provider = ScriptedProvider(["TOOL_CALL slow {}", "This will never be reached."])
        agent = GenericAdkAgent(
            definition=_make_tool_definition("slow"),
            model_provider=provider,
            model_catalog=_make_catalog_with_default(),
            tool_catalog=_make_tool_catalog(SlowTool(), timeout_seconds=0.05),
        )

        response = await agent.invoke(AgentRequest(input="go"))

        assert response.output == ""
        assert response.error is not None
        assert "timed out" in response.error

    async def test_runtime_passes_skill_catalog(self) -> None:
        skill_catalog = SkillCatalog()
        skill_catalog.register(SkillDefinition(name="support"))
        definition = _make_definition()
        with_skills = definition.model_copy(
            update={"spec": AgentSpec(model=ModelRef(ref="default"), skills=[SkillRef(ref="support")])}
        )

        runtime = AdkRuntime(
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
            skill_catalog=skill_catalog,
        )
        agent = await runtime.create(with_skills)

        assert [skill.name for skill in agent.skills] == ["support"]

    async def test_factory_passes_skill_catalog(self) -> None:
        skill_catalog = SkillCatalog()
        skill_catalog.register(SkillDefinition(name="support"))
        definition = _make_definition()
        with_skills = definition.model_copy(
            update={"spec": AgentSpec(model=ModelRef(ref="default"), skills=[SkillRef(ref="support")])}
        )

        factory = AdkAgentFactory(
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
            skill_catalog=skill_catalog,
        )
        agent = factory.create(with_skills)

        assert [skill.name for skill in agent.skills] == ["support"]


class TestAdkLlmAgentConstruction:
    def test_builds_llm_agent_and_runner(self) -> None:
        agent = GenericAdkAgent(
            definition=_make_definition(name="support-agent", instruction="Be helpful."),
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
        )

        assert agent.llm_agent.name == "support_agent"
        assert agent.llm_agent.instruction == "Be helpful."
        assert agent.runner.agent is agent.llm_agent
        assert agent.runner.app_name == "support_agent"

    def test_llm_agent_carries_function_tools(self) -> None:
        agent = GenericAdkAgent(
            definition=_make_tool_definition("calculator"),
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
            tool_catalog=_make_tool_catalog(CalculatorTool()),
        )

        assert len(agent.llm_agent.tools) == 1


class TestMemoryIntegration:
    async def test_memory_context_injected_into_prompt(self) -> None:
        memory = InMemoryProvider()
        await memory.store(MemoryEntry(key="pref", content="Prefers dark mode", scope_id="user1"))
        definition = _make_definition(instruction="Help.")
        with_memory = definition.model_copy(
            update={
                "spec": AgentSpec(
                    instruction=definition.spec.instruction,
                    model=ModelRef(ref="default"),
                    memory=MemoryConfig(enabled=True),
                )
            }
        )
        model = FakeModelProvider(response="ok")
        agent = GenericAdkAgent(
            definition=with_memory,
            model_provider=model,
            model_catalog=_make_catalog_with_default(),
            memory_provider=memory,
        )

        await agent.invoke(AgentRequest(input="dark mode", user_id="user1"))

        prompt = model.calls[0]["prompt"]
        assert isinstance(prompt, str)
        assert "Memory:" in prompt
        assert "Prefers dark mode" in prompt

    async def test_memory_disabled_by_default(self) -> None:
        memory = InMemoryProvider()
        await memory.store(MemoryEntry(key="pref", content="Prefers dark mode", scope_id="user1"))
        model = FakeModelProvider(response="ok")
        agent = GenericAdkAgent(
            definition=_make_definition(),
            model_provider=model,
            model_catalog=_make_catalog_with_default(),
            memory_provider=memory,
        )

        await agent.invoke(AgentRequest(input="theme question", user_id="user1"))

        prompt = model.calls[0]["prompt"]
        assert isinstance(prompt, str)
        assert "Memory:" not in prompt

    async def test_remember_stores_entry(self) -> None:
        memory = InMemoryProvider()
        definition = _make_definition()
        with_memory = definition.model_copy(
            update={
                "spec": AgentSpec(
                    instruction=definition.spec.instruction,
                    model=ModelRef(ref="default"),
                    memory=MemoryConfig(enabled=True),
                )
            }
        )
        agent = GenericAdkAgent(
            definition=with_memory,
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
            memory_provider=memory,
        )

        await agent.remember("preference", "dark mode", scope_id="user1")

        entries = await memory.load("preference", MemoryScope.USER, "user1")
        assert len(entries) == 1
        assert entries[0].content == "dark mode"

    async def test_no_auto_persist_of_raw_interactions(self) -> None:
        memory = InMemoryProvider()
        definition = _make_definition()
        with_memory = definition.model_copy(
            update={
                "spec": AgentSpec(
                    instruction=definition.spec.instruction,
                    model=ModelRef(ref="default"),
                    memory=MemoryConfig(enabled=True),
                )
            }
        )
        agent = GenericAdkAgent(
            definition=with_memory,
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_make_catalog_with_default(),
            memory_provider=memory,
        )

        await agent.invoke(AgentRequest(input="please remember this", user_id="user1"))

        assert await memory.search("remember this", MemoryScope.USER, "user1") == []

    async def test_remember_requires_provider(self) -> None:
        agent = GenericAdkAgent(
            definition=_make_definition(),
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
        )

        with pytest.raises(RuntimeError, match="No memory provider"):
            await agent.remember("key", "value")
