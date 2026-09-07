"""Behavioral contract tests for the LangChain/LangGraph runtime backend."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, PrivateAttr

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    CalculatorTool,
    FakeModelProvider,
    McpRef,
    ModelCatalog,
    ModelConfigurationError,
    ModelDefinition,
    ModelRef,
    RuntimeDependencies,
    SessionAccessError,
    ToolCapability,
    ToolCatalog,
    ToolDefinition,
    ToolRef,
)
from osa.runtimes.langgraph import LangChainModelAdapterRegistry, LangGraphRuntime, OsaLangGraphAgent


def _catalog() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))
    return catalog


def _definition(*, tools: bool = False) -> AgentDefinition:
    return AgentDefinition(
        metadata=AgentMetadataConfig(name="langgraph-agent"),
        spec=AgentSpec(
            instruction="You are concise.",
            model=ModelRef(ref="default"),
            tools=[ToolRef(ref="calculator")] if tools else [],
        ),
    )


class ToolCallingModel(BaseChatModel):
    """Small deterministic LangChain model for graph/tool-loop tests."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _calls: int = PrivateAttr(default=0)
    _bound_tools: list[Any] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "test-tool-calling-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolCallingModel:
        del kwargs
        self._bound_tools = list(tools)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self._calls += 1
        if any(isinstance(message, ToolMessage) for message in messages):
            response = AIMessage(content="4")
        else:
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculator",
                        "args": {"operation": "add", "a": 2, "b": 2},
                        "id": "call-1",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


class FixedModelAdapter:
    def __init__(self, model: BaseChatModel) -> None:
        self.model = model

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseChatModel:
        del definition, parameters
        return self.model


def _tool_catalog() -> ToolCatalog:
    catalog = ToolCatalog()
    catalog.register_definition(
        ToolDefinition(
            name="calculator",
            capabilities=[
                ToolCapability(
                    name="calculator",
                    parameters_schema={
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string"},
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["operation", "a", "b"],
                    },
                )
            ],
        )
    )
    catalog.register_tool(CalculatorTool())
    return catalog


class TestOsaLangGraphAgent:
    async def test_invoke_uses_langchain_model_and_returns_osa_response(self) -> None:
        provider = FakeModelProvider(response="hello")
        agent = OsaLangGraphAgent(_definition(), model_provider=provider, model_catalog=_catalog())

        response = await agent.invoke(AgentRequest(input="hi", user_id="user-a"))

        assert response.output == "hello"
        assert response.error is None
        assert response.session_id is not None
        assert provider.calls[0]["model_id"] == "fake-model"

    async def test_langgraph_stategraph_executes_native_tool_node(self) -> None:
        model = ToolCallingModel()
        adapters = LangChainModelAdapterRegistry()
        adapters.register("fake", FixedModelAdapter(model))
        agent = OsaLangGraphAgent(
            _definition(tools=True),
            model_catalog=_catalog(),
            tool_catalog=_tool_catalog(),
            model_adapters=adapters,
        )

        response = await agent.invoke(AgentRequest(input="calculate", user_id="user-a"))

        assert response.output == "4"
        assert response.error is None
        assert model._calls == 2

    async def test_streaming_uses_shared_osa_event_contract(self) -> None:
        agent = OsaLangGraphAgent(
            _definition(),
            model_provider=FakeModelProvider(response="streamed"),
            model_catalog=_catalog(),
        )

        events = [event async for event in agent.stream_invoke(AgentRequest(input="hi"))]

        assert [event.type for event in events] == ["osa.started", "osa.message.delta", "osa.message"]
        assert [event.seq for event in events] == [0, 1, 2]
        assert events[-1].text == "streamed"

    async def test_sessions_keep_ownership_outside_langgraph(self) -> None:
        agent = OsaLangGraphAgent(
            _definition(),
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_catalog(),
        )

        first = await agent.invoke(AgentRequest(input="one", user_id="owner"))
        assert first.session_id is not None

        with pytest.raises(SessionAccessError):
            await agent.invoke(AgentRequest(input="two", session_id=first.session_id, user_id="other"))


class TestLangGraphRuntime:
    async def test_runtime_accepts_shared_runtime_dependencies(self) -> None:
        session_provider = RuntimeDependencies.with_defaults().session_provider
        dependencies = RuntimeDependencies.with_defaults(
            model_provider=FakeModelProvider(response="runtime"),
            model_catalog=_catalog(),
            session_provider=session_provider,
        )
        runtime = LangGraphRuntime(dependencies=dependencies)

        agent = await runtime.create(_definition())
        response = await agent.invoke(AgentRequest(input="hi"))

        assert response.output == "runtime"
        assert runtime.session_provider is session_provider
        await runtime.shutdown()

    async def test_mcp_is_explicitly_rejected_until_adapter_exists(self) -> None:
        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="langgraph-agent"),
            spec=AgentSpec(
                instruction="You are concise.",
                model=ModelRef(ref="default"),
                mcps=[McpRef(ref="server")],
            ),
        )
        with pytest.raises(ModelConfigurationError, match="does not yet support MCP"):
            OsaLangGraphAgent(
                definition,
                model_provider=FakeModelProvider(),
                model_catalog=_catalog(),
            )
