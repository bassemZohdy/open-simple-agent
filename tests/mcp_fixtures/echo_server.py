"""Deterministic stdio MCP server used by the protocol tests.

Run as a subprocess by ``tests/integration/test_mcp_client.py`` and
``test_mcp_agent.py`` via the official SDK's stdio transport. Completely
offline and deterministic.
"""

from __future__ import annotations

import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-echo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"hello {name}"


@mcp.tool()
def failing_tool() -> str:
    """Always raises an error."""
    raise RuntimeError("intentional failure")


@mcp.tool()
def slow_tool() -> str:
    """Takes about 3 seconds; used to trigger client timeouts."""
    time.sleep(3.0)
    return "finally done"


@mcp.tool()
def big_text() -> str:
    """Returns a large payload; used to trigger response-size limits."""
    return "x" * 10_000


if __name__ == "__main__":
    mcp.run(transport="stdio")
