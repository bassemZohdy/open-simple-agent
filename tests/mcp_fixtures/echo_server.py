"""Deterministic stdio MCP server used by the protocol tests.

Run as a subprocess by ``tests/integration/test_mcp_client.py`` and
``test_mcp_agent.py`` via the official SDK's stdio transport. Completely
offline and deterministic.
"""

from __future__ import annotations

import time
from importlib import import_module
from typing import Any


def _create_mcp_server(name: str) -> Any:
    """Create a server across the MCP 1.x and 2.x server module layouts."""
    try:
        module = import_module("mcp.server.fastmcp")
        server_type = module.FastMCP
    except ModuleNotFoundError:
        # MCP 2 renamed FastMCP to MCPServer; keeping this fixture dual-major
        # lets the compatibility suite exercise the same protocol behavior.
        module = import_module("mcp.server.mcpserver")
        server_type = module.MCPServer
    return server_type(name)


mcp = _create_mcp_server("test-echo")


@mcp.tool()  # type: ignore[untyped-decorator]
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()  # type: ignore[untyped-decorator]
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"hello {name}"


@mcp.tool()  # type: ignore[untyped-decorator]
def failing_tool() -> str:
    """Always raises an error."""
    raise RuntimeError("intentional failure")


@mcp.tool()  # type: ignore[untyped-decorator]
def slow_tool() -> str:
    """Takes about 12 seconds; used to trigger client timeouts.

    The long sleep keeps the tool timeout well above the connect/initialize
    budget so the test measures tool-call timeouts, not startup slowness.
    """
    time.sleep(12.0)
    return "finally done"


@mcp.tool()  # type: ignore[untyped-decorator]
def big_text() -> str:
    """Returns a large payload; used to trigger response-size limits."""
    return "x" * 10_000


if __name__ == "__main__":
    mcp.run(transport="stdio")
