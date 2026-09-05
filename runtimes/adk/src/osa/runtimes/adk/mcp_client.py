"""MCP runtime client: connections, discovery, filters, and bounded calls.

Implements the OSA MCP runtime policy on top of the official `mcp` SDK
(ADR-002): lazy connections from `McpDefinition` settings, a per-server
connection pool shared across a runtime, stdio and Streamable HTTP
transports (legacy SSE is rejected), bounded retries, response-size caps,
credential resolution through the `SecretResolver` contract, tool filtering,
namespacing, and origin metadata.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import mcp.types as mcp_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from osa.generic_agent import (
    CredentialResolutionError,
    McpDefinition,
    Observability,
    ResolvedOutboundCredential,
    SecretError,
    SecretResolver,
    resolve_outbound_credential,
)
from osa.generic_agent.errors import (
    McpConnectionError,
    McpResponseTooLargeError,
    McpToolExecutionError,
    McpTransportNotSupportedError,
)

logger = logging.getLogger(__name__)

SUPPORTED_TRANSPORTS = ("stdio", "streamable_http")
_HTTP_HEADERS_KEY = "Authorization"


def sanitize_tool_name(name: str) -> str:
    """Reduce a name to a valid ADK identifier fragment."""
    sanitized = re.sub(r"\W", "_", name)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def namespaced_tool_name(server_name: str, tool_name: str) -> str:
    """Deterministic ADK tool name for an MCP tool, preserving origin."""
    return f"{sanitize_tool_name(server_name)}_{sanitize_tool_name(tool_name)}"


@dataclass(frozen=True)
class McpToolHandle:
    """A discovered MCP tool as exposed to agents."""

    #: Namespaced ADK-safe tool name (``<server>_<tool>``).
    namespaced_name: str
    #: Original tool name on the MCP server.
    server_tool_name: str
    server_name: str
    description: str
    parameters_schema: dict[str, Any]


class McpConnection:
    """One lazily-connected MCP server session built from an `McpDefinition`.

    The transport contexts and ``ClientSession`` are owned by a dedicated
    keeper task: anyio cancel scopes must be entered and exited in the same
    task, while calls arrive from whatever task is running the invocation
    (the same pattern ADK's own MCP session manager uses). Settings —
    timeouts, retries, TLS, response cap, credentials — come from the
    definition and the supplied resolver; secret values are resolved per
    connect and never retained.
    """

    def __init__(
        self,
        definition: McpDefinition,
        secret_resolver: SecretResolver | None = None,
        observability: Observability | None = None,
    ) -> None:
        self._definition = definition
        self._secret_resolver = secret_resolver
        self._observability = observability or Observability()
        self._session: ClientSession | None = None
        self._keeper: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._definition.name

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def _resolve_credentials(self) -> ResolvedOutboundCredential:
        """Resolve configured credentials for one transport connection."""
        headers: dict[str, str] = {}
        environment: dict[str, str] = {}
        reference = self._definition.credential_ref
        credential = self._definition.credential
        if reference is not None and credential is not None:
            raise CredentialResolutionError("mcp", "credential and credential_ref cannot both be configured")
        if credential is not None:
            material = await resolve_outbound_credential(credential, self._secret_resolver)
            if self._definition.transport == "stdio" and not material.environment:
                raise McpConnectionError(
                    self.name,
                    "stdio credentials require an explicit environment_variable",
                )
            return material
        if reference is not None:
            if self._secret_resolver is None:
                raise McpConnectionError(
                    self.name,
                    f"credential '{reference.key}' requires a secret resolver",
                )
            value = self._secret_resolver.resolve(reference)
            headers[_HTTP_HEADERS_KEY] = f"Bearer {value}"
            var_name = reference.env_var or reference.key
            environment[var_name] = value
        return ResolvedOutboundCredential(headers=headers, environment=environment)

    async def _resolve_stdio_env(self) -> dict[str, str]:
        """Stdio environment including configured credentials."""
        import os

        env = {**os.environ, **self._definition.env}
        material = await self._resolve_credentials()
        env.update(material.environment)
        return env

    async def _build_httpx_client(self) -> Any:
        """HTTP client with TLS, timeout, and credential settings resolved."""
        import httpx

        options = self._definition.connection_options
        material = await self._resolve_credentials()
        verify: str | bool = options.tls_verify
        if options.tls_verify and material.verify is not None:
            verify = material.verify
        return httpx.AsyncClient(
            verify=verify,
            cert=material.cert,
            headers=material.headers,
            timeout=httpx.Timeout(options.timeout_seconds),
        )

    async def _enter_streams(self, stack: Any) -> tuple[Any, Any]:
        definition = self._definition
        if definition.transport == "stdio":
            if not definition.command:
                raise McpConnectionError(self.name, "stdio transport requires 'command'")
            server = StdioServerParameters(
                command=definition.command,
                args=list(definition.args),
                env=await self._resolve_stdio_env(),
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server))
            return read_stream, write_stream
        if definition.transport == "streamable_http":
            if not definition.endpoint:
                raise McpConnectionError(self.name, "streamable_http transport requires 'endpoint'")
            http_client = await stack.enter_async_context(await self._build_httpx_client())
            transport_stack: Any = await stack.enter_async_context(
                streamable_http_client(
                    url=definition.endpoint,
                    http_client=http_client,
                    terminate_on_close=True,
                )
            )
            return transport_stack[0], transport_stack[1]
        raise McpTransportNotSupportedError(self.name, str(definition.transport), ", ".join(SUPPORTED_TRANSPORTS))

    async def connect(self) -> ClientSession:
        """Connect (or return the existing session), with bounded retries.

        A keeper task owns the transport contexts; the returned session may
        be used from other tasks, but enter/exit stays inside the keeper.
        """
        async with self._lock:
            if self._session is not None:
                return self._session
            options = self._definition.connection_options
            last_error: Exception | None = None
            for attempt in range(options.max_retries + 1):
                try:
                    async with self._observability.span("mcp.connect", labels={"server": self.name}):
                        return await self._connect_once()
                except McpTransportNotSupportedError:
                    raise
                except SecretError:
                    # Credential resolution failures are deterministic
                    # configuration errors — never retried or masked.
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < options.max_retries:
                        logger.warning(
                            "MCP server '%s' connect attempt %d failed: %s; retrying",
                            self.name,
                            attempt + 1,
                            exc,
                        )
                        await asyncio.sleep(options.retry_delay_seconds)
            raise McpConnectionError(
                self.name, f"connection failed after retries: {last_error}", cause=last_error
            ) from last_error

    async def _connect_once(self) -> ClientSession:
        self._stop = asyncio.Event()
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._keeper = asyncio.create_task(self._keep_connected(ready, self._stop))
        try:
            await asyncio.wait_for(ready, timeout=self._definition.connection_options.timeout_seconds)
        except BaseException:
            await self._stop_keeper()
            raise
        assert self._session is not None
        logger.info("Connected to MCP server '%s'", self.name)
        return self._session

    async def _keep_connected(self, ready: asyncio.Future[None], stop: asyncio.Event) -> None:
        """Own the transport contexts; signal readiness or failure via ``ready``."""
        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream = await self._enter_streams(stack)
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self._definition.connection_options.timeout_seconds),
                    )
                )
                await asyncio.wait_for(
                    session.initialize(),
                    timeout=self._definition.connection_options.timeout_seconds,
                )
                self._session = session
                if not ready.done():
                    ready.set_result(None)
                await stop.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self._session = None

    async def _stop_keeper(self) -> None:
        keeper, self._keeper = self._keeper, None
        if keeper is None:
            return
        self._stop.set()
        with contextlib.suppress(Exception):
            await keeper

    async def close(self) -> None:
        async with self._lock:
            self._session = None
            keeper, self._keeper = self._keeper, None
        if keeper is not None:
            self._stop.set()
            with contextlib.suppress(Exception):
                await keeper
            logger.info("Disconnected from MCP server '%s'", self.name)

    async def list_tools(self) -> list[McpToolHandle]:
        """Discover tools, applying the server-level filter."""
        async with self._observability.span("mcp.discovery", labels={"server": self.name}):
            session = await self.connect()
            result = await session.list_tools()
        allowed = set(self._definition.tools_filter) if self._definition.tools_filter else None
        handles: list[McpToolHandle] = []
        for tool in result.tools:
            if allowed is not None and tool.name not in allowed:
                continue
            handles.append(
                McpToolHandle(
                    namespaced_name=namespaced_tool_name(self.name, tool.name),
                    server_tool_name=tool.name,
                    server_name=self.name,
                    description=tool.description or "",
                    parameters_schema=dict(tool.inputSchema) if tool.inputSchema else {},
                )
            )
        return handles

    async def call_tool(self, handle_server_tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool with retries and a bounded response.

        Returns the OSA tool payload (``success``/``output``/``error``);
        transport failures raise :class:`McpConnectionError` or
        :class:`McpToolExecutionError`, oversized responses are reported as
        payloads so the model can adapt.

        Only connection-level failures are retried: they happen before a
        request is sent, so a retry cannot repeat a tool execution. An
        in-flight failure (timeout, protocol error) may have reached the
        server and even completed — retrying it could duplicate a
        non-idempotent side effect, so it surfaces immediately as
        :class:`McpToolExecutionError`.
        """
        session = await self.connect()
        options = self._definition.connection_options
        last_error: Exception | None = None
        result: mcp_types.CallToolResult | None = None
        for attempt in range(options.max_retries + 1):
            try:
                async with self._observability.span(
                    "mcp.call",
                    labels={"server": self.name, "tool": handle_server_tool_name},
                    attributes={"osa.mcp.server": self.name, "osa.mcp.tool": handle_server_tool_name},
                ):
                    # Our own deadline makes the timeout deterministic even
                    # when the underlying transport's cancellation races the
                    # response (which can leak a raw CancelledError).
                    result = await asyncio.wait_for(
                        session.call_tool(handle_server_tool_name, arguments or {}),
                        timeout=options.timeout_seconds,
                    )
                break
            except McpResponseTooLargeError:
                raise
            except McpConnectionError as exc:
                last_error = exc
                logger.warning(
                    "MCP server '%s' tool '%s' attempt %d failed: %s",
                    self.name,
                    handle_server_tool_name,
                    attempt + 1,
                    exc,
                )
                if attempt < options.max_retries:
                    await asyncio.sleep(options.retry_delay_seconds)
                    session = await self.connect()
            except Exception as exc:
                raise McpToolExecutionError(
                    self.name, handle_server_tool_name, f"call failed: {exc}", cause=exc
                ) from exc
        if result is None:
            raise McpToolExecutionError(
                self.name, handle_server_tool_name, "call failed after retries", cause=last_error
            ) from last_error

        text = _extract_text(result)
        if (
            self._definition.connection_options.max_response_bytes is not None
            and len(text.encode("utf-8")) > self._definition.connection_options.max_response_bytes
        ):
            raise McpResponseTooLargeError(
                self.name,
                handle_server_tool_name,
                len(text.encode("utf-8")),
                self._definition.connection_options.max_response_bytes,
            )
        if result.isError:
            return {"success": False, "output": "", "error": text or "tool reported an error"}
        return {"success": True, "output": text, "error": None}


def _extract_text(result: mcp_types.CallToolResult) -> str:
    parts: list[str] = []
    for content in result.content or []:
        text = getattr(content, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


class McpConnectionPool:
    """Per-runtime pool of MCP connections, keyed by server definition name.

    Agents referencing the same server share one connection; the runtime
    closes the pool on shutdown.
    """

    def __init__(
        self,
        secret_resolver: SecretResolver | None = None,
        observability: Observability | None = None,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._observability = observability or Observability()
        self._connections: dict[str, McpConnection] = {}

    def get(self, definition: McpDefinition) -> McpConnection:
        connection = self._connections.get(definition.name)
        if connection is None:
            connection = McpConnection(definition, self._secret_resolver, self._observability)
            self._connections[definition.name] = connection
        return connection

    async def close(self) -> None:
        for connection in list(self._connections.values()):
            await connection.close()
        self._connections.clear()

    def __len__(self) -> int:
        return len(self._connections)
