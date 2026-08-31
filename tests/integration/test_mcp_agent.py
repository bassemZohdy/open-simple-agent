"""End-to-end MCP acceptance tests (P1.3).

A configured agent discovers and invokes a filtered MCP tool through the ADK
Runner using native function calling, offline against the deterministic stdio
server in ``tests/mcp_fixtures``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    FakeModelProvider,
    McpCatalog,
    McpConnectionOptions,
    McpDefinition,
    McpRef,
    McpTransport,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
)
from osa.runtimes.adk import AdkRuntime
from tests.integration.test_native_function_calling import (
    ScriptedLlm,
    _call,
    _final,
    scripted_registry,
)

SERVER_PATH = str(Path(__file__).resolve().parents[1] / "mcp_fixtures" / "echo_server.py")

_ECHO_TOOLS = {"add", "greet", "failing_tool", "slow_tool", "big_text"}


def _catalogs() -> tuple[ModelCatalog, McpCatalog]:
    model_catalog = ModelCatalog()
    model_catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))
    mcp_catalog = McpCatalog()
    mcp_catalog.register(
        McpDefinition(
            name="test-echo",
            transport=McpTransport.STDIO,
            command=sys.executable,
            args=[SERVER_PATH],
            connection_options=McpConnectionOptions(timeout_seconds=20, max_retries=0),
        )
    )
    return model_catalog, mcp_catalog


def _agent_definition(**spec_overrides: object) -> AgentDefinition:
    spec: dict[str, Any] = {
        "instruction": "Use MCP tools when helpful.",
        "model": ModelRef(ref="default"),
        "mcps": [McpRef(ref="test-echo")],
    }
    spec.update(spec_overrides)
    return AgentDefinition(
        metadata=AgentMetadataConfig(name="mcp-agent"),
        spec=AgentSpec(**spec),
    )


async def _make_runtime(model: ScriptedLlm, **runtime_kwargs: Any) -> AdkRuntime:
    model_catalog, mcp_catalog = _catalogs()
    runtime = AdkRuntime(
        model_provider=FakeModelProvider(),
        model_catalog=model_catalog,
        mcp_catalog=mcp_catalog,
        model_adapters=scripted_registry(model),
        **runtime_kwargs,
    )
    await runtime.create(_agent_definition())
    return runtime


class TestMcpAgentAcceptance:
    async def test_agent_discovers_and_invokes_filtered_mcp_tool(self) -> None:
        model = ScriptedLlm(
            model="fake-model",
            script=[
                _call("test_echo_add", {"a": 2, "b": 40}),
                _final("The sum is 42."),
            ],
        )
        runtime = await _make_runtime(model)
        try:
            agent = runtime._agents[0]
            response = await agent.invoke(AgentRequest(input="add 2 and 40"))

            assert response.output == "The sum is 42."
            assert response.error is None
            tool_response = model.last_function_response()
            assert tool_response is not None
            assert tool_response["success"] is True
            assert tool_response["output"] == "42"
        finally:
            await runtime.shutdown()

    async def test_agent_level_tools_filter_limits_exposure(self) -> None:
        from osa.runtimes.adk import OsaMcpToolset

        model = ScriptedLlm(model="fake-model")
        model_catalog, mcp_catalog = _catalogs()
        runtime = AdkRuntime(
            model_provider=FakeModelProvider(),
            model_catalog=model_catalog,
            mcp_catalog=mcp_catalog,
            model_adapters=scripted_registry(model),
        )
        try:
            agent = await runtime.create(
                AgentDefinition(
                    metadata=AgentMetadataConfig(name="filtered-agent"),
                    spec=AgentSpec(
                        instruction="Use MCP tools.",
                        model=ModelRef(ref="default"),
                        mcps=[McpRef(ref="test-echo", tools_filter=["add", "greet"])],
                    ),
                )
            )
            toolsets = [t for t in agent.llm_agent.tools if isinstance(t, OsaMcpToolset)]
            assert len(toolsets) == 1
            tools = await toolsets[0].get_tools()
            names = {tool.name for tool in tools}
            assert names == {"test_echo_add", "test_echo_greet"}
        finally:
            await runtime.shutdown()

    async def test_unreachable_mcp_server_surfaces_deterministic_error(self) -> None:
        from osa.generic_agent import McpConnectionOptions, McpDefinition

        model = ScriptedLlm(model="fake-model")
        model_catalog, mcp_catalog = _catalogs()
        mcp_catalog.register(
            McpDefinition(
                name="broken",
                transport=McpTransport.STDIO,
                command="osa-no-such-executable-xyz",
                connection_options=McpConnectionOptions(timeout_seconds=2, max_retries=0),
            )
        )
        runtime = AdkRuntime(
            model_provider=FakeModelProvider(),
            model_catalog=model_catalog,
            mcp_catalog=mcp_catalog,
            model_adapters=scripted_registry(model),
        )
        try:
            agent = await runtime.create(
                AgentDefinition(
                    metadata=AgentMetadataConfig(name="broken-agent"),
                    spec=AgentSpec(
                        instruction="Use MCP tools.",
                        model=ModelRef(ref="default"),
                        mcps=[McpRef(ref="broken")],
                    ),
                )
            )
            response = await agent.invoke(AgentRequest(input="anything"))
            assert response.output == ""
            assert response.error is not None
            assert "mcp" in response.error.lower()
        finally:
            await runtime.shutdown()

    async def test_unknown_mcp_reference_fails_at_construction(self) -> None:
        model_catalog, mcp_catalog = _catalogs()
        runtime = AdkRuntime(
            model_provider=FakeModelProvider(),
            model_catalog=model_catalog,
            mcp_catalog=mcp_catalog,
        )
        import pytest

        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="ghost-agent"),
            spec=AgentSpec(
                instruction="Help.",
                model=ModelRef(ref="default"),
                mcps=[McpRef(ref="no-such-server")],
            ),
        )
        with pytest.raises(ValueError, match="no-such-server"):
            await runtime.create(definition)
        await runtime.shutdown()
