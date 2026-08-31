"""Bridge MCP tools to ADK as a lazily-resolved toolset (ADR-002).

``OsaMcpToolset`` implements ADK's ``BaseToolset`` so MCP tools are discovered
when an invocation runs (not at construction), keeping agent construction
synchronous and connections lazy. Tools are namespaced ``<server>_<tool>``,
filtered by the server definition and the agent reference, validated against
the server's ``inputSchema``, and produce bounded OSA payloads; MCP failures
(``McpError`` subclasses) are surfaced as error payloads the model can react
to.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from google.adk.tools.base_toolset import BaseToolset  # noqa: TC002 - runtime base class
from google.adk.tools.function_tool import FunctionTool

from osa.generic_agent.errors import McpError
from osa.runtimes.adk.llm_agent import _validate_tool_arguments, build_tool_declaration

if TYPE_CHECKING:
    from collections.abc import Callable

    from google.genai import types

    from osa.generic_agent import McpDefinition
    from osa.runtimes.adk.mcp_client import McpConnection, McpToolHandle

logger = logging.getLogger(__name__)


class McpFunctionTool(FunctionTool):
    """ADK function tool backed by an MCP server tool."""

    def __init__(self, connection: McpConnection, handle: McpToolHandle) -> None:
        self._osa_connection = connection
        self._osa_handle = handle
        super().__init__(func=self._make_function())

    def _make_function(self) -> Callable[..., Any]:
        connection = self._osa_connection
        handle = self._osa_handle

        async def tool_fn(**kwargs: Any) -> dict[str, Any]:
            schema = handle.parameters_schema
            if schema:
                violation = _validate_tool_arguments(handle.namespaced_name, schema, kwargs)
                if violation is not None:
                    return {"success": False, "output": "", "error": violation}
            try:
                return await connection.call_tool(handle.server_tool_name, kwargs)
            except McpError as exc:
                logger.error("MCP tool invocation failed: %s", exc)
                return {"success": False, "output": "", "error": str(exc)}

        tool_fn.__name__ = handle.namespaced_name
        tool_fn.__doc__ = handle.description or f"Call the {handle.server_tool_name} MCP tool."
        return tool_fn

    def _prepare_invocation_args(self, args: dict[str, Any], tool_context: Any) -> dict[str, Any]:
        """Forward the model's arguments unchanged (validated in the wrapper)."""
        return dict(args)

    def _get_declaration(self) -> types.FunctionDeclaration | None:
        declaration = build_tool_declaration(
            self._osa_handle.namespaced_name,
            self._osa_handle.parameters_schema,
            self._osa_handle.description,
        )
        if declaration is not None:
            return declaration
        return super()._get_declaration()


class OsaMcpToolset(BaseToolset):
    """ADK toolset exposing one MCP server's (filtered) tools to an agent.

    Filters combine the server definition's ``tools_filter`` (applied during
    discovery) with the agent reference's ``tools_filter`` (applied here, by
    original server tool name).
    """

    def __init__(
        self, definition: McpDefinition, connection: McpConnection, tool_filter: list[str] | None = None
    ) -> None:
        super().__init__()
        self._definition = definition
        self._connection = connection
        self._agent_filter = set(tool_filter) if tool_filter else None

    @property
    def connection(self) -> McpConnection:
        """The underlying connection (used for pre-flight checks)."""
        return self._connection

    async def get_tools(self, readonly_context: Any = None) -> list[Any]:
        handles = await self._connection.list_tools()
        tools: list[McpFunctionTool] = []
        for handle in handles:
            if self._agent_filter is not None and handle.server_tool_name not in self._agent_filter:
                continue
            tools.append(McpFunctionTool(self._connection, handle))
        logger.info("MCP server '%s' exposes %d tool(s) to this agent", self._definition.name, len(tools))
        return tools

    async def close(self) -> None:
        # Connection lifecycle is owned by the McpConnectionPool.
        return None
