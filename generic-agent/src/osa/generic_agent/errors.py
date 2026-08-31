"""Stable OSA error types.

These errors cross the runtime boundary: HTTP layers map them to deterministic
responses, so their type (not just message) is contract. Messages must never
contain secret values.
"""

from __future__ import annotations

from typing import Any

# Stable wire format shared by OSA HTTP APIs: {"error": {"code", "message"}}.
_ERROR_FIELD = "error"
_CODE_FIELD = "code"
_MESSAGE_FIELD = "message"


def error_payload(code: str, message: str) -> dict[str, Any]:
    """Build the stable OSA error response body."""
    return {_ERROR_FIELD: {_CODE_FIELD: code, _MESSAGE_FIELD: message}}


class OsaError(Exception):
    """Base class for stable OSA errors."""

    #: Stable wire code used by HTTP layers when mapping this error.
    code = "osa_error"


class ModelConfigurationError(OsaError):
    """A model reference or provider configuration is invalid or incomplete."""

    code = "model_configuration_error"


class MemoryConfigurationError(OsaError):
    """A memory provider configuration is invalid or incomplete."""

    code = "memory_configuration_error"


class ModelInvocationError(OsaError):
    """A model call failed (provider error, unexpected response, cancellation)."""

    code = "model_invocation_failed"

    def __init__(self, model_id: str, message: str, cause: Exception | None = None) -> None:
        self.model_id = model_id
        self.cause = cause
        super().__init__(f"Model '{model_id}' invocation failed: {message}")


class InvocationTimeoutError(OsaError):
    """An invocation exceeded its configured timeout."""

    code = "invocation_timeout"

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Invocation timed out after {timeout_seconds}s")


class IterationLimitExceededError(OsaError):
    """An invocation exceeded its configured tool-iteration limit."""

    code = "iteration_limit_exceeded"

    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations
        super().__init__(f"Tool iteration limit ({max_iterations}) exceeded without a final answer")


class McpError(OsaError):
    """Base error for MCP runtime failures."""


class McpConnectionError(McpError):
    """An MCP server could not be reached, or the connection was lost."""

    code = "mcp_connection_failed"

    def __init__(self, server_name: str, message: str, cause: Exception | None = None) -> None:
        self.server_name = server_name
        self.cause = cause
        super().__init__(f"MCP server '{server_name}': {message}")


class McpTransportNotSupportedError(McpError):
    """The configured MCP transport is not supported by the runtime client."""

    code = "mcp_transport_not_supported"

    def __init__(self, server_name: str, transport: str, supported: str) -> None:
        self.server_name = server_name
        super().__init__(
            f"MCP server '{server_name}' uses transport '{transport}'; the runtime client supports {supported}"
        )


class McpResponseTooLargeError(McpError):
    """An MCP response exceeded the configured size cap."""

    code = "mcp_response_too_large"

    def __init__(self, server_name: str, tool_name: str, size_bytes: int, limit_bytes: int) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"MCP server '{server_name}' tool '{tool_name}' returned {size_bytes} bytes, "
            f"exceeding the {limit_bytes}-byte limit"
        )


class McpToolExecutionError(McpError):
    """An MCP tool call failed after retries."""

    code = "mcp_tool_failed"

    def __init__(self, server_name: str, tool_name: str, message: str, cause: Exception | None = None) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"MCP server '{server_name}' tool '{tool_name}': {message}")
