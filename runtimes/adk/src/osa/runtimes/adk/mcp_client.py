"""MCP runtime client: connections, discovery, filters, and bounded calls.

Implements the OSA MCP runtime policy on top of the official `mcp` SDK
(ADR-002): lazy connections from `McpDefinition` settings, a per-server
connection pool shared across a runtime, stdio, Streamable HTTP, and
explicitly selected legacy SSE transports, bounded retries, response-size
caps, credential resolution through the `SecretResolver` contract,
tool/resource/prompt filtering, namespacing, and origin metadata.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import mcp.types as mcp_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from osa.generic_agent import (
    CredentialResolutionError,
    McpDefinition,
    McpPromptMessage,
    McpPromptMetadata,
    McpPromptResult,
    McpResourceContent,
    McpResourceMetadata,
    Observability,
    ResolvedOutboundCredential,
    SecretError,
    SecretResolver,
    outbound_trust_env,
    resolve_outbound_credential,
    validate_outbound_url,
)
from osa.generic_agent.errors import (
    McpConnectionError,
    McpError,
    McpPromptError,
    McpResourceError,
    McpResponseTooLargeError,
    McpToolExecutionError,
    McpTransportNotSupportedError,
)

logger = logging.getLogger(__name__)

SUPPORTED_TRANSPORTS = ("stdio", "streamable_http", "sse")
_HTTP_HEADERS_KEY = "Authorization"


def _mcp_sdk_major() -> int:
    """Return the installed official MCP SDK major, defaulting to v1."""
    try:
        return int(version("mcp").split(".", 1)[0])
    except (PackageNotFoundError, ValueError):
        # The import above already proves an SDK is present. Keep the v1
        # behavior if a non-standard packaging environment omits metadata.
        return 1


_MCP_SDK_MAJOR = _mcp_sdk_major()


def _read_model_field(model: Any, *names: str) -> Any:
    """Read a wire-model field across the MCP 1.x and 2.x spellings."""
    for name in names:
        value = getattr(model, name, None)
        if value is not None:
            return value
    return None


def _model_dump(model: Any) -> dict[str, Any]:
    """Serialize one MCP SDK model without depending on its major version."""
    if isinstance(model, dict):
        return dict(model)
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="json", by_alias=True, exclude_none=True))
    legacy_dump = getattr(model, "dict", None)
    if callable(legacy_dump):
        return dict(legacy_dump(by_alias=True, exclude_none=True))
    raise TypeError(f"unsupported MCP model type: {type(model).__name__}")


def _read_timeout_value(seconds: float) -> Any:
    """Adapt OSA seconds to the MCP SDK major's session timeout contract."""
    if _MCP_SDK_MAJOR >= 2:
        return seconds
    return timedelta(seconds=seconds)


def _mcp_httpx() -> Any:
    """Return the HTTP client module required by the installed MCP major."""
    if _MCP_SDK_MAJOR >= 2:
        import httpx2

        return httpx2
    import httpx

    return httpx


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
        httpx = _mcp_httpx()
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
            follow_redirects=False,
            trust_env=outbound_trust_env(),
        )

    def _sse_httpx_client_factory(self, material: ResolvedOutboundCredential) -> Any:
        """Build the legacy SSE client's HTTP client without enabling redirects."""
        httpx = _mcp_httpx()
        options = self._definition.connection_options
        verify: str | bool = options.tls_verify
        if options.tls_verify and material.verify is not None:
            verify = material.verify

        def factory(*, headers: dict[str, Any] | None = None, timeout: Any = None, auth: Any = None) -> Any:
            merged_headers = dict(material.headers)
            if headers:
                merged_headers.update(headers)
            client_options: dict[str, Any] = {
                "verify": verify,
                "cert": material.cert,
                "headers": merged_headers,
                "timeout": timeout if timeout is not None else httpx.Timeout(options.timeout_seconds),
                "follow_redirects": False,
                "trust_env": outbound_trust_env(),
            }
            if auth is not None:
                client_options["auth"] = auth
            return httpx.AsyncClient(**client_options)

        return factory

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
            try:
                endpoint = validate_outbound_url(definition.endpoint, purpose=f"MCP server '{self.name}' endpoint")
            except ValueError as exc:
                raise McpConnectionError(self.name, str(exc)) from exc
            http_client = await stack.enter_async_context(await self._build_httpx_client())
            transport_stack: Any = await stack.enter_async_context(
                streamable_http_client(
                    url=endpoint,
                    http_client=http_client,
                    terminate_on_close=True,
                )
            )
            return transport_stack[0], transport_stack[1]
        if definition.transport == "sse":
            if not definition.endpoint:
                raise McpConnectionError(self.name, "sse transport requires 'endpoint'")
            try:
                endpoint = validate_outbound_url(definition.endpoint, purpose=f"MCP server '{self.name}' endpoint")
            except ValueError as exc:
                raise McpConnectionError(self.name, str(exc)) from exc
            material = await self._resolve_credentials()
            transport_stack = await stack.enter_async_context(
                sse_client(
                    url=endpoint,
                    headers=material.headers or None,
                    timeout=definition.connection_options.timeout_seconds,
                    sse_read_timeout=definition.connection_options.timeout_seconds,
                    httpx_client_factory=self._sse_httpx_client_factory(material),
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
                        read_timeout_seconds=_read_timeout_value(self._definition.connection_options.timeout_seconds),
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
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await keeper

    async def close(self) -> None:
        async with self._lock:
            self._session = None
            keeper, self._keeper = self._keeper, None
        if keeper is not None:
            self._stop.set()
            with contextlib.suppress(Exception, asyncio.CancelledError):
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
                    parameters_schema=dict(_read_model_field(tool, "inputSchema", "input_schema") or {}),
                )
            )
        return handles

    async def _list_page(self, method_name: str, cursor: str | None, error_type: Any) -> Any:
        """Request one paginated discovery page across MCP SDK majors."""
        session: Any = await self.connect()
        method = getattr(session, method_name)
        try:
            async with self._observability.span("mcp.discovery", labels={"server": self.name}):
                if _MCP_SDK_MAJOR >= 2:
                    params = mcp_types.PaginatedRequestParams(cursor=cursor) if cursor is not None else None
                    return await asyncio.wait_for(
                        method(params=params),
                        timeout=self._definition.connection_options.timeout_seconds,
                    )
                return await asyncio.wait_for(
                    method(cursor=cursor),
                    timeout=self._definition.connection_options.timeout_seconds,
                )
        except McpError:
            raise
        except Exception as exc:
            raise error_type(self.name, method_name, f"request failed: {exc}", cause=exc) from exc

    async def _request(self, operation: str, request_factory: Any, error_type: Any) -> Any:
        """Run one bounded MCP request and normalize protocol failures."""
        try:
            async with self._observability.span("mcp.request", labels={"server": self.name, "operation": operation}):
                return await asyncio.wait_for(
                    request_factory(),
                    timeout=self._definition.connection_options.timeout_seconds,
                )
        except McpError:
            raise
        except Exception as exc:
            raise error_type(self.name, operation, f"request failed: {exc}", cause=exc) from exc

    def _ensure_response_size(self, operation: str, payload: Any) -> None:
        """Apply the configured response cap to application-controlled payloads."""
        limit = self._definition.connection_options.max_response_bytes
        if limit is None:
            return
        size_bytes = len(json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8"))
        if size_bytes > limit:
            operation_kind = "resource" if "resource" in operation else "prompt" if "prompt" in operation else "tool"
            raise McpResponseTooLargeError(self.name, operation, size_bytes, limit, operation_kind=operation_kind)

    async def list_resources(self) -> list[McpResourceMetadata]:
        """Discover filtered MCP resources for application-controlled use."""
        resources: list[McpResourceMetadata] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        discovered_count = 0
        allowed = set(self._definition.resources_filter) if self._definition.resources_filter else None
        limit = self._definition.connection_options.max_discovery_items
        while True:
            result = await self._list_page("list_resources", cursor, McpResourceError)
            page = _read_model_field(result, "resources") or []
            discovered_count += len(page)
            if discovered_count > limit:
                raise McpResourceError(
                    self.name,
                    "list_resources",
                    f"discovery exceeded the {limit}-item limit",
                )
            for resource in page:
                resource_name = str(_read_model_field(resource, "name") or "")
                uri = str(_read_model_field(resource, "uri") or "")
                if allowed is not None and resource_name not in allowed and uri not in allowed:
                    continue
                resources.append(
                    McpResourceMetadata(
                        uri=uri,
                        name=resource_name,
                        description=str(_read_model_field(resource, "description") or ""),
                        mime_type=_read_model_field(resource, "mimeType", "mime_type"),
                        mcp_name=resource_name,
                    )
                )
            cursor = _read_model_field(result, "nextCursor", "next_cursor")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise McpResourceError(self.name, "list_resources", "server returned a repeated pagination cursor")
            seen_cursors.add(cursor)
        self._ensure_response_size("list_resources", [resource.model_dump(mode="json") for resource in resources])
        return resources

    async def read_resource(self, uri: str) -> list[McpResourceContent]:
        """Read one MCP resource with a bounded, stable OSA payload."""
        session: Any = await self.connect()
        result = await self._request(
            "read_resource",
            lambda: session.read_resource(uri),
            McpResourceError,
        )
        raw_contents = _read_model_field(result, "contents")
        if not isinstance(raw_contents, list):
            raise McpResourceError(self.name, "read_resource", "server returned no resource contents")
        contents: list[McpResourceContent] = []
        try:
            for content in raw_contents:
                text = _read_model_field(content, "text")
                blob = _read_model_field(content, "blob")
                contents.append(
                    McpResourceContent(
                        uri=str(_read_model_field(content, "uri") or uri),
                        mime_type=_read_model_field(content, "mimeType", "mime_type"),
                        text=text if isinstance(text, str) else None,
                        blob=blob if isinstance(blob, str) else None,
                    )
                )
        except (TypeError, ValueError) as exc:
            raise McpResourceError(self.name, "read_resource", f"invalid resource contents: {exc}", cause=exc) from exc
        self._ensure_response_size("read_resource", [content.model_dump(mode="json") for content in contents])
        return contents

    async def list_prompts(self) -> list[McpPromptMetadata]:
        """Discover filtered MCP prompts for application-controlled use."""
        prompts: list[McpPromptMetadata] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        discovered_count = 0
        allowed = set(self._definition.prompts_filter) if self._definition.prompts_filter else None
        limit = self._definition.connection_options.max_discovery_items
        while True:
            result = await self._list_page("list_prompts", cursor, McpPromptError)
            page = _read_model_field(result, "prompts") or []
            discovered_count += len(page)
            if discovered_count > limit:
                raise McpPromptError(
                    self.name,
                    "list_prompts",
                    f"discovery exceeded the {limit}-item limit",
                )
            for prompt in page:
                prompt_name = str(_read_model_field(prompt, "name") or "")
                if allowed is not None and prompt_name not in allowed:
                    continue
                arguments = [_model_dump(argument) for argument in (_read_model_field(prompt, "arguments") or [])]
                prompts.append(
                    McpPromptMetadata(
                        name=prompt_name,
                        description=str(_read_model_field(prompt, "description") or ""),
                        arguments=arguments,
                        mcp_name=prompt_name,
                    )
                )
            cursor = _read_model_field(result, "nextCursor", "next_cursor")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise McpPromptError(self.name, "list_prompts", "server returned a repeated pagination cursor")
            seen_cursors.add(cursor)
        self._ensure_response_size("list_prompts", [prompt.model_dump(mode="json") for prompt in prompts])
        return prompts

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> McpPromptResult:
        """Resolve one MCP prompt with original content fields preserved."""
        allowed = set(self._definition.prompts_filter) if self._definition.prompts_filter else None
        if allowed is not None and name not in allowed:
            raise McpPromptError(self.name, "get_prompt", f"prompt '{name}' is not allowed by the server filter")
        session = await self.connect()
        result = await self._request(
            "get_prompt",
            lambda: session.get_prompt(name, arguments or None),
            McpPromptError,
        )
        raw_messages = _read_model_field(result, "messages")
        if not isinstance(raw_messages, list):
            raise McpPromptError(self.name, "get_prompt", "server returned no prompt messages")
        try:
            messages = [
                McpPromptMessage(
                    role=str(_read_model_field(message, "role") or ""),
                    content=_model_dump(_read_model_field(message, "content")),
                )
                for message in raw_messages
            ]
            prompt = McpPromptResult(
                description=str(_read_model_field(result, "description") or ""),
                messages=messages,
            )
        except (TypeError, ValueError) as exc:
            raise McpPromptError(self.name, "get_prompt", f"invalid prompt result: {exc}", cause=exc) from exc
        self._ensure_response_size("get_prompt", prompt.model_dump(mode="json"))
        return prompt

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
        if _read_model_field(result, "isError", "is_error"):
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
