"""Tool domain types and catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import Field

from osa.generic_agent.config import StrictModel


class ToolCategory(StrEnum):
    """Categories of tools."""

    NATIVE = "native"
    MCP = "mcp"
    OPENAPI = "openapi"


class ToolCapability(StrictModel):
    """Metadata about what a tool can do."""

    name: str
    description: str = ""
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    required_scopes: list[str] = Field(default_factory=list)


class ToolDefinition(StrictModel):
    """Definition of a tool in the Tool Catalog."""

    name: str
    description: str = ""
    category: ToolCategory = ToolCategory.NATIVE
    capabilities: list[ToolCapability] = Field(default_factory=list)
    timeout_seconds: float | None = Field(default=None, gt=0)
    enabled: bool = True


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    output: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """Structured error from tool execution."""

    def __init__(self, tool_name: str, message: str, cause: Exception | None = None) -> None:
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"Tool '{tool_name}': {message}")


class ToolTimeoutError(ToolError):
    """Tool execution exceeded its timeout."""

    def __init__(self, tool_name: str, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(tool_name, f"timed out after {timeout_seconds}s")


class Tool(StrictModel):
    """Runtime tool interface — a callable tool.

    This is the contract for tools that agents can invoke.
    """

    model_config = StrictModel.model_config

    name: str
    description: str = ""

    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given parameters.

        Override in subclasses to implement tool behavior.
        """
        raise NotImplementedError(f"Tool '{self.name}' does not implement execute()")


class ToolCatalog:
    """In-memory catalog of tool definitions and implementations."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._tools: dict[str, Tool] = {}

    def register_definition(self, definition: ToolDefinition) -> None:
        """Register a tool definition."""
        self._definitions[definition.name] = definition

    def register_tool(self, tool: Tool) -> None:
        """Register a runtime tool implementation."""
        self._tools[tool.name] = tool

    def get_definition(self, name: str) -> ToolDefinition:
        """Get a tool definition by name."""
        if name not in self._definitions:
            raise KeyError(f"Tool definition not found: {name}")
        return self._definitions[name]

    def get_tool(self, name: str) -> Tool:
        """Get a runtime tool by name."""
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")
        return self._tools[name]

    def list_definitions(self) -> list[ToolDefinition]:
        """List all registered tool definitions."""
        return list(self._definitions.values())

    def list_tools(self) -> list[Tool]:
        """List all registered runtime tools."""
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._definitions)

    def __contains__(self, name: str) -> bool:
        return name in self._definitions
