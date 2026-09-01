"""MCP domain types and catalog."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from osa.generic_agent.config import OutboundCredential, SecretReference, StrictModel


class McpTransport(StrEnum):
    """Supported MCP transport types."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class McpConnectionOptions(StrictModel):
    """Connection options for an MCP server."""

    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_delay_seconds: float = Field(default=1.0, ge=0)
    tls_verify: bool = True
    max_response_bytes: int | None = Field(default=None, gt=0)


class McpDefinition(StrictModel):
    """Definition of an MCP server in the MCP Catalog."""

    name: str
    description: str = ""
    transport: McpTransport = McpTransport.STDIO
    endpoint: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    # Non-secret environment variables for stdio servers; secrets go through
    # credential_ref so values stay external to definitions.
    env: dict[str, str] = Field(default_factory=dict)
    credential: OutboundCredential | None = None
    credential_ref: SecretReference | None = None
    connection_options: McpConnectionOptions = Field(default_factory=McpConnectionOptions)
    tools_filter: list[str] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_credential_fields(self) -> McpDefinition:
        if self.credential is not None and self.credential_ref is not None:
            raise ValueError("credential and credential_ref cannot both be configured")
        return self


class McpToolMetadata(StrictModel):
    """Metadata about a tool exposed by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    mcp_name: str = ""


class McpResourceMetadata(StrictModel):
    """Metadata about a resource exposed by an MCP server."""

    uri: str
    name: str = ""
    description: str = ""
    mime_type: str | None = None
    mcp_name: str = ""


class McpPromptMetadata(StrictModel):
    """Metadata about a prompt exposed by an MCP server."""

    name: str
    description: str = ""
    arguments: list[dict[str, Any]] = Field(default_factory=list)
    mcp_name: str = ""


class McpCatalog:
    """In-memory catalog of MCP server definitions."""

    def __init__(self) -> None:
        self._definitions: dict[str, McpDefinition] = {}

    def register(self, definition: McpDefinition) -> None:
        self._definitions[definition.name] = definition

    def resolve(self, ref: str) -> McpDefinition:
        if ref not in self._definitions:
            raise KeyError(f"MCP server not found: {ref}")
        return self._definitions[ref]

    def delete(self, name: str) -> bool:
        """Remove an MCP server definition. Returns True if it existed."""
        return self._definitions.pop(name, None) is not None

    def list_definitions(self) -> list[McpDefinition]:
        return list(self._definitions.values())

    def __len__(self) -> int:
        return len(self._definitions)

    def __contains__(self, ref: str) -> bool:
        return ref in self._definitions
