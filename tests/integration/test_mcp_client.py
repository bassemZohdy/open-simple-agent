"""MCP runtime client protocol tests (P1.3, ADR-002).

Uses a deterministic in-repo stdio MCP server (tests/mcp_fixtures/
echo_server.py) and a localhost Streamable HTTP server to verify discovery,
filtering, invocation, and predictable failure behavior — all offline.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from osa.generic_agent import (
    EnvironmentSecretResolver,
    McpConnectionOptions,
    McpDefinition,
    McpTransport,
    SecretReference,
)
from osa.generic_agent.errors import (
    McpConnectionError,
    McpResponseTooLargeError,
    McpToolExecutionError,
    McpTransportNotSupportedError,
)
from osa.runtimes.adk import McpConnection, McpConnectionPool, namespaced_tool_name

SERVER_PATH = str(Path(__file__).resolve().parents[1] / "mcp_fixtures" / "echo_server.py")


def _stdio_definition(**overrides: object) -> McpDefinition:
    values: dict[str, object] = {
        "name": "test-echo",
        "transport": "stdio",
        "command": sys.executable,
        "args": [SERVER_PATH],
        "connection_options": McpConnectionOptions(timeout_seconds=20, max_retries=0),
    }
    values.update(overrides)
    return McpDefinition(**values)  # type: ignore[arg-type]


class TestNamespacing:
    def test_namespaced_tool_name_sanitizes(self) -> None:
        assert namespaced_tool_name("crm-api", "get.customer") == "crm_api_get_customer"
        assert namespaced_tool_name("2servers", "tool") == "_2servers_tool"

    def test_origin_metadata_preserved(self) -> None:
        import asyncio

        connection = McpConnection(_stdio_definition())
        try:
            handles = asyncio.run(connection.list_tools())
            add = next(h for h in handles if h.server_tool_name == "add")
            assert add.server_name == "test-echo"
            assert add.namespaced_name == "test_echo_add"
            assert add.parameters_schema.get("type") == "object"
        finally:
            asyncio.run(connection.close())


class TestStdioProtocol:
    async def test_discovery_and_filtered_invocation(self) -> None:
        connection = McpConnection(_stdio_definition())
        try:
            handles = await connection.list_tools()
            names = {h.server_tool_name for h in handles}
            assert {"add", "greet", "failing_tool", "slow_tool", "big_text"} <= names

            add = next(h for h in handles if h.server_tool_name == "add")
            result = await connection.call_tool(add.server_tool_name, {"a": 2, "b": 40})
            assert result == {"success": True, "output": "42", "error": None}
        finally:
            await connection.close()

    async def test_server_level_tool_filter(self) -> None:
        connection = McpConnection(_stdio_definition(tools_filter=["add"]))
        try:
            handles = await connection.list_tools()
            assert [h.server_tool_name for h in handles] == ["add"]
        finally:
            await connection.close()

    async def test_tool_error_is_reported_as_payload(self) -> None:
        connection = McpConnection(_stdio_definition())
        try:
            result = await connection.call_tool("failing_tool", {})
            assert result["success"] is False
            assert "intentional failure" in str(result["error"])
        finally:
            await connection.close()

    async def test_timeout_is_deterministic(self) -> None:
        connection = McpConnection(
            _stdio_definition(connection_options=McpConnectionOptions(timeout_seconds=1, max_retries=0))
        )
        try:
            with pytest.raises(McpToolExecutionError, match="slow_tool"):
                await connection.call_tool("slow_tool", {})
        finally:
            await connection.close()

    async def test_oversize_response_raises_deterministic_error(self) -> None:
        connection = McpConnection(
            _stdio_definition(
                connection_options=McpConnectionOptions(timeout_seconds=20, max_retries=0, max_response_bytes=100)
            )
        )
        try:
            with pytest.raises(McpResponseTooLargeError, match="exceeding the 100-byte limit"):
                await connection.call_tool("big_text", {})
        finally:
            await connection.close()

    async def test_unreachable_command_fails_deterministically(self) -> None:
        connection = McpConnection(
            _stdio_definition(
                command="osa-no-such-executable-xyz",
                connection_options=McpConnectionOptions(timeout_seconds=5, max_retries=0),
            )
        )
        with pytest.raises(McpConnectionError, match="connection failed after retries"):
            await connection.list_tools()

    async def test_legacy_sse_rejected(self) -> None:
        connection = McpConnection(
            McpDefinition(name="legacy", transport=McpTransport.SSE, endpoint="http://127.0.0.1:1/sse")
        )
        with pytest.raises(McpTransportNotSupportedError, match="streamable_http"):
            await connection.list_tools()

    async def test_pool_shares_connections_by_server(self) -> None:
        pool = McpConnectionPool()
        try:
            first = pool.get(_stdio_definition())
            second = pool.get(_stdio_definition())
            assert first is second
            handles = await first.list_tools()
            assert handles
        finally:
            await pool.close()


class TestCredentialResolution:
    async def test_missing_credential_fails_without_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from osa.generic_agent import SecretResolutionError

        monkeypatch.delenv("TEST_MCP_TOKEN", raising=False)
        connection = McpConnection(
            _stdio_definition(
                credential_ref=SecretReference(source="env", key="TEST_MCP_TOKEN"),
            ),
            secret_resolver=EnvironmentSecretResolver(),
        )
        try:
            with pytest.raises(SecretResolutionError, match="TEST_MCP_TOKEN") as excinfo:
                await connection.list_tools()
            assert "secret-value" not in str(excinfo.value)
        finally:
            await connection.close()


class TestStreamableHttp:
    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _wait_for_server(port: int) -> None:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.01)
        raise RuntimeError(f"localhost server on port {port} did not start")

    @staticmethod
    def _serve(port: int, *, require_token: str | None = None) -> threading.Event:
        """Serve the echo tools over Streamable HTTP on a localhost port."""
        import uvicorn
        from mcp.server.fastmcp import FastMCP
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        http = FastMCP("test-echo-http")

        @http.tool()
        def add(a: int, b: int) -> int:
            """Add two integers."""
            return a + b

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):  # noqa: ANN001
                if require_token is not None:
                    header = request.headers.get("authorization", "")
                    if header != f"Bearer {require_token}":
                        return JSONResponse({"error": "unauthorized"}, status_code=401)
                return await call_next(request)

        http_app = http.streamable_http_app()
        if require_token is not None:
            http_app.add_middleware(AuthMiddleware)
        stop = threading.Event()

        config = uvicorn.Config(
            http_app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
        server = uvicorn.Server(config)

        def run() -> None:
            server.run()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        TestStreamableHttp._wait_for_server(port)
        return stop

    async def test_http_discovery_and_invocation(self) -> None:
        port = self._free_port()
        self._serve(port)
        try:
            connection = McpConnection(
                McpDefinition(
                    name="http-echo",
                    transport=McpTransport.STREAMABLE_HTTP,
                    endpoint=f"http://127.0.0.1:{port}/mcp",
                    connection_options=McpConnectionOptions(timeout_seconds=20, max_retries=0),
                )
            )
            try:
                handles = await connection.list_tools()
                assert [h.server_tool_name for h in handles] == ["add"]
                result = await connection.call_tool("add", {"a": 1, "b": 2})
                assert result == {"success": True, "output": "3", "error": None}
            finally:
                await connection.close()
        finally:
            # Give the daemon uvicorn thread a moment; tests exit regardless.
            await __import__("asyncio").sleep(0.1)

    async def test_http_auth_failure_is_deterministic(self) -> None:
        port = self._free_port()
        self._serve(port, require_token="secret-token")
        try:
            connection = McpConnection(
                McpDefinition(
                    name="http-secure",
                    transport=McpTransport.STREAMABLE_HTTP,
                    endpoint=f"http://127.0.0.1:{port}/mcp",
                    credential_ref=SecretReference(source="env", key="HTTP_MCP_TOKEN"),
                    connection_options=McpConnectionOptions(timeout_seconds=10, max_retries=0),
                ),
                secret_resolver=EnvironmentSecretResolver(),
            )
            try:
                import os

                monkey_wrong = os.environ.get("HTTP_MCP_TOKEN") != "wrong"
                os.environ["HTTP_MCP_TOKEN"] = "wrong"
                try:
                    with pytest.raises(McpConnectionError, match="connection failed"):
                        await connection.list_tools()
                finally:
                    if monkey_wrong:
                        os.environ.pop("HTTP_MCP_TOKEN", None)
                    else:
                        os.environ["HTTP_MCP_TOKEN"] = "wrong"
            finally:
                await connection.close()
        finally:
            await __import__("asyncio").sleep(0.1)
