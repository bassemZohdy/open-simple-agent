"""Tiny MCP server exposing one tool, for the MCP example bundle.

Run automatically by the runtime when it connects the bundle's stdio MCP
server (the runtime spawns `python server.py` from the bundle directory).
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("example-tools")


@mcp.tool()
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run(transport="stdio")
