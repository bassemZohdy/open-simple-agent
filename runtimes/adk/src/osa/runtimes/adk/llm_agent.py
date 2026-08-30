"""ADK LlmAgent and Runner construction from an AgentDefinition.

Builds the Google ADK objects for a definition so that a real model can be
attached later. Deterministic invocation keeps flowing through the injected
ModelProvider until a live model is configured.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.memory import InMemoryMemoryService
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool

if TYPE_CHECKING:
    from osa.generic_agent import AgentDefinition, Tool

AdkTool = Callable[..., Any] | BaseTool | BaseToolset


def adk_name(name: str) -> str:
    """Sanitize a name into a valid ADK node name (a Python identifier)."""
    sanitized = re.sub(r"\W", "_", name)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def _tool_function(tool: Tool) -> Any:
    def tool_fn(**kwargs: Any) -> dict[str, Any]:
        result = tool.execute(**kwargs)
        return {"success": result.success, "output": result.output, "error": result.error}

    tool_fn.__name__ = tool.name
    tool_fn.__doc__ = tool.description or f"Execute the {tool.name} tool."
    return tool_fn


def build_function_tools(tools: dict[str, Tool]) -> list[AdkTool]:
    """Wrap resolved runtime tools as ADK function tools.

    Declarations are currently schema-less — ADK only sees the tool's
    description — until parameter declarations are synthesized from
    ``ToolDefinition.capabilities``.
    """
    wrapped: list[AdkTool] = [FunctionTool(func=_tool_function(tool)) for tool in tools.values()]
    return wrapped


def build_llm_agent(definition: AgentDefinition, model: str, tools: dict[str, Tool]) -> LlmAgent:
    """Build an ADK ``LlmAgent`` from an AgentDefinition.

    ``model`` is the ADK model identifier, already resolved through the Model
    Catalog. The agent name is sanitized to satisfy ADK's identifier
    requirement (``customer-support`` becomes ``customer_support``).
    """
    return LlmAgent(
        name=adk_name(definition.metadata.name),
        description=definition.spec.description or "",
        model=model,
        instruction=definition.spec.instruction or "",
        tools=build_function_tools(tools),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def build_runner(llm_agent: LlmAgent, *, memory_service: InMemoryMemoryService | None = None) -> Runner:
    """Configure an ADK ``Runner`` with in-memory session and memory services."""
    return Runner(
        app_name=llm_agent.name,
        agent=llm_agent,
        session_service=InMemorySessionService(),
        memory_service=memory_service or InMemoryMemoryService(),
    )
