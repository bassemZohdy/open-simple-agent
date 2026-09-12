"""Tiny MCP server exposing one tool, for the MCP example bundle.

Run automatically by the runtime when it connects the bundle's stdio MCP
server (the runtime spawns `python server.py` from the bundle directory).
"""

from importlib import import_module
from typing import Any


def _create_mcp_server(name: str) -> Any:
    """Create a server across the MCP 1.x and 2.x server module layouts."""
    try:
        module = import_module("mcp.server.fastmcp")
        server_type = module.FastMCP
    except ModuleNotFoundError:
        # MCP 2 renamed FastMCP to MCPServer; the example supports both SDK
        # lines.
        module = import_module("mcp.server.mcpserver")
        server_type = module.MCPServer
    return server_type(name)


mcp = _create_mcp_server("example-tools")


@mcp.tool()  # type: ignore[untyped-decorator]
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run(transport="stdio")
