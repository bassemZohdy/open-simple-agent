"""ADK-native function calling, tool schemas, and invocation limits (P0.2).

Uses a scripted ADK model (no network) to drive the real Runner loop: the
model answers with function calls, tools execute through ADK function
calling, and results flow back as function responses.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict, Field, PrivateAttr

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    CalculatorTool,
    FakeModelProvider,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
    RuntimeConfig,
    Tool,
    ToolCapability,
    ToolCatalog,
    ToolDefinition,
    ToolRef,
    ToolResult,
)
from osa.runtimes.adk import (
    AdkRuntime,
    GenericAdkAgent,
    ModelAdapterRegistry,
    OsaFunctionTool,
    build_function_tools,
)

if TYPE_CHECKING:
    from google.adk.models.llm_request import LlmRequest


class ScriptedLlm(BaseLlm):
    """Test double at the ADK model layer: replays scripted LlmResponses.

    Records every incoming LlmRequest so tests can assert on the exact
    function responses the model received.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    script: list[LlmResponse] = Field(default_factory=list)
    delay_seconds: float = 0.0

    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    @property
    def requests(self) -> list[LlmRequest]:
        return self._requests

    def last_function_response(self) -> dict[str, Any] | None:
        """Payload of the most recent function response sent to the model."""
        for request in reversed(self._requests):
            for content in reversed(request.contents):
                for part in content.parts or []:
                    if part.function_response is not None:
                        return part.function_response.response
        return None

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> Any:
        self._requests.append(llm_request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.script:
            yield self.script.pop(0)
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Script exhausted")]))


class StaticAdapter:
    """Model adapter that always returns one prebuilt ADK model."""

    def __init__(self, model: BaseLlm) -> None:
        self._model = model

    def build(self, definition: Any, parameters: dict[str, Any]) -> BaseLlm:
        return self._model


def scripted_registry(model: BaseLlm) -> ModelAdapterRegistry:
    registry = ModelAdapterRegistry()
    registry.register("fake", StaticAdapter(model))
    return registry


def _final(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def _call(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))])
    )


def _catalog() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))
    return catalog


def _tool_catalog(*tools: Tool, timeout_seconds: float | None = None) -> ToolCatalog:
    catalog = ToolCatalog()
    for tool in tools:
        catalog.register_definition(
            ToolDefinition(name=tool.name, description=tool.description, timeout_seconds=timeout_seconds)
        )
        catalog.register_tool(tool)
    return catalog


class RecordingCalculator(CalculatorTool):
    """Calculator that records executed calls (inheritable list field)."""

    executed_calls: list[dict[str, Any]] = Field(default_factory=list)

    def execute(self, **kwargs: Any) -> ToolResult:
        self.executed_calls.append(kwargs)
        return super().execute(**kwargs)


class TestNativeFunctionCalling:
    async def test_function_call_round_trip_through_adk_runner(self) -> None:
        model = ScriptedLlm(model="fake-model", script=[_call("calculator", {"operation": "add", "a": 2, "b": 3})])
        agent = GenericAdkAgent(
            definition=AgentDefinition(
                metadata=AgentMetadataConfig(name="tool-agent"),
                spec=AgentSpec(
                    instruction="Compute.", model=ModelRef(ref="default"), tools=[ToolRef(ref="calculator")]
                ),
            ),
            model_provider=FakeModelProvider(),
            model_catalog=_catalog(),
            tool_catalog=_tool_catalog(RecordingCalculator()),
            model_adapters=scripted_registry(model),
        )

        response = await agent.invoke(AgentRequest(input="what is 2+3?"))

        assert response.output == "Script exhausted"
        assert response.error is None
        tool_response = model.last_function_response()
        assert tool_response is not None
        assert tool_response["success"] is True
        assert tool_response["output"] == "5.0"

    async def test_function_calling_survives_multiple_rounds(self) -> None:
        model = ScriptedLlm(
            model="fake-model",
            script=[
                _call("calculator", {"operation": "add", "a": 1, "b": 1}),
                _call("calculator", {"operation": "multiply", "a": 2, "b": 3}),
                _final("Done: 6"),
            ],
        )
        agent = GenericAdkAgent(
            definition=AgentDefinition(
                metadata=AgentMetadataConfig(name="loop-agent"),
                spec=AgentSpec(
                    instruction="Compute.",
                    model=ModelRef(ref="default"),
                    tools=[ToolRef(ref="calculator")],
                    runtime=RuntimeConfig(max_iterations=5),
                ),
            ),
            model_provider=FakeModelProvider(),
            model_catalog=_catalog(),
            tool_catalog=_tool_catalog(RecordingCalculator()),
            model_adapters=scripted_registry(model),
        )

        response = await agent.invoke(AgentRequest(input="compute"))

        assert response.output == "Done: 6"
        assert response.error is None


class TestIterationLimit:
    async def test_iteration_limit_returns_deterministic_error(self) -> None:
        model = ScriptedLlm(
            model="fake-model",
            script=[
                _call("calculator", {"operation": "add", "a": 1, "b": 1}),
                _call("calculator", {"operation": "add", "a": 2, "b": 2}),
                _final("never reached"),
            ],
        )
        agent = GenericAdkAgent(
            definition=AgentDefinition(
                metadata=AgentMetadataConfig(name="limit-agent"),
                spec=AgentSpec(
                    instruction="Compute.",
                    model=ModelRef(ref="default"),
                    tools=[ToolRef(ref="calculator")],
                    runtime=RuntimeConfig(max_iterations=1),
                ),
            ),
            model_provider=FakeModelProvider(),
            model_catalog=_catalog(),
            tool_catalog=_tool_catalog(RecordingCalculator()),
            model_adapters=scripted_registry(model),
        )

        response = await agent.invoke(AgentRequest(input="compute"))

        assert response.output == ""
        assert response.error is not None
        assert "Tool iteration limit (1) exceeded" in response.error


class TestInvocationTimeout:
    async def test_runtime_timeout_captures_deterministic_error(self) -> None:
        model = ScriptedLlm(model="fake-model", delay_seconds=1.0, script=[_final("too late")])
        agent = GenericAdkAgent(
            definition=AgentDefinition(
                metadata=AgentMetadataConfig(name="timeout-agent"),
                spec=AgentSpec(
                    instruction="Compute.",
                    model=ModelRef(ref="default"),
                    runtime=RuntimeConfig(timeout_seconds=1),
                ),
            ),
            model_provider=FakeModelProvider(),
            model_catalog=_catalog(),
            model_adapters=scripted_registry(model),
        )

        response = await agent.invoke(AgentRequest(input="hello"))

        assert response.output == ""
        assert response.error is not None
        assert "timed out after 1.0s" in response.error


class TestToolSchemas:
    def test_declaration_generated_from_capabilities(self) -> None:
        tool_definition = ToolDefinition(
            name="calculator",
            description="Arithmetic",
            capabilities=[
                ToolCapability(
                    name="compute",
                    description="Compute an arithmetic result",
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
        tools = build_function_tools({"calculator": CalculatorTool()}, {"calculator": tool_definition})
        osa_tool = cast("OsaFunctionTool", tools[0])
        declaration = osa_tool._get_declaration()  # noqa: SLF001 - deliberate seam

        assert declaration is not None
        assert declaration.name == "calculator"
        assert declaration.parameters is not None
        assert set(declaration.parameters.properties or {}) == {"operation", "a", "b"}

    async def test_invalid_arguments_rejected_before_execution(self) -> None:
        tool_definition = ToolDefinition(
            name="calculator",
            capabilities=[
                ToolCapability(
                    name="compute",
                    parameters_schema={
                        "type": "object",
                        "properties": {"a": {"type": "number"}},
                        "required": ["a"],
                    },
                )
            ],
        )
        calculator = RecordingCalculator()
        catalog = _tool_catalog(calculator)
        catalog.register_definition(tool_definition)
        model = ScriptedLlm(
            model="fake-model",
            script=[_call("calculator", {"operation": "add"})],  # missing required "a"
        )
        agent = GenericAdkAgent(
            definition=AgentDefinition(
                metadata=AgentMetadataConfig(name="schema-agent"),
                spec=AgentSpec(
                    instruction="Compute.",
                    model=ModelRef(ref="default"),
                    tools=[ToolRef(ref="calculator")],
                ),
            ),
            model_provider=FakeModelProvider(),
            model_catalog=_catalog(),
            tool_catalog=catalog,
            model_adapters=scripted_registry(model),
        )

        response = await agent.invoke(AgentRequest(input="compute"))

        assert response.error is None
        tool_response = model.last_function_response()
        assert tool_response is not None
        assert tool_response["success"] is False
        assert "Missing required parameter(s): a" in str(tool_response["error"])
        assert calculator.executed_calls == []


class TestModelAdapterConfiguration:
    async def test_runtime_fails_fast_without_any_model(self) -> None:
        from osa.generic_agent import ModelConfigurationError

        runtime = AdkRuntime(model_provider=None, model_catalog=ModelCatalog())
        definition = AgentDefinition(metadata=AgentMetadataConfig(name="bare-agent"), spec=AgentSpec(instruction="Hi."))
        import pytest

        with pytest.raises(ModelConfigurationError, match="no model configured"):
            await runtime.create(definition)

    async def test_unknown_provider_fails_fast(self) -> None:
        from osa.generic_agent import ModelConfigurationError

        catalog = ModelCatalog()
        catalog.register(ModelDefinition(name="exotic", provider="quantum", model_id="q-1", is_default=True))
        runtime = AdkRuntime(model_provider=None, model_catalog=catalog)

        import pytest

        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="exotic-agent"), spec=AgentSpec(model=ModelRef(ref="exotic"))
        )
        with pytest.raises(ModelConfigurationError, match="provider 'quantum'"):
            await runtime.create(definition)
