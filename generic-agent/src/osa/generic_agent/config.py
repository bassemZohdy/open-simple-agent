"""Configuration models for agent definitions.

All models use strict validation — unknown properties are rejected, and
timeouts, TTLs, limits, and iterations must be positive/in-range where set.

Configuration precedence:
    Built-in Defaults -> Configuration File -> Environment Variables
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_API_VERSION = "osa/v1alpha1"
SUPPORTED_KIND = "Agent"


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


class ConfigurationError(ValueError):
    """Raised when configuration cannot be interpreted.

    Distinct from pydantic.ValidationError (schema violations): this covers
    values that cannot be parsed at all, such as an unrecognized OSA_*
    boolean environment override.
    """


def _coerce_bare_string_ref(data: Any) -> Any:
    """Accept a bare string wherever a catalog reference mapping is expected.

    ``tools: [calculator]`` and ``tools: [{ref: calculator}]`` are equivalent.
    """
    return {"ref": data} if isinstance(data, str) else data


class SecretReference(StrictModel):
    """Reference to an external secret — never contains the value directly."""

    source: str
    key: str
    env_var: str | None = None


_HTTP_HEADER_PATTERN = r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$"
_ENVIRONMENT_VARIABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class ApiKeyCredential(StrictModel):
    """API-key credential for outbound MCP/A2A HTTP calls."""

    type: Literal["api_key"] = "api_key"
    secret_ref: SecretReference
    header_name: str = Field(default="X-API-Key", pattern=_HTTP_HEADER_PATTERN)
    value_prefix: str | None = Field(default=None, min_length=1, pattern=r"^[^\r\n]+$")
    environment_variable: str | None = Field(default=None, pattern=_ENVIRONMENT_VARIABLE_PATTERN)


class OAuth2Credential(StrictModel):
    """OAuth 2.0 client-credentials configuration for outbound calls."""

    type: Literal["oauth2"] = "oauth2"
    token_url: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret_ref: SecretReference
    scopes: list[str] = Field(default_factory=list)
    audience: str | None = None
    header_name: str = Field(default="Authorization", pattern=_HTTP_HEADER_PATTERN)
    environment_variable: str | None = Field(default=None, pattern=_ENVIRONMENT_VARIABLE_PATTERN)
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @model_validator(mode="after")
    def _validate_token_url(self) -> OAuth2Credential:
        parsed = urlparse(self.token_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("token_url must be an absolute HTTP or HTTPS URL")
        return self


class MtlsCredential(StrictModel):
    """mTLS credential using resolver values as certificate file paths."""

    type: Literal["mtls"] = "mtls"
    certificate_ref: SecretReference
    private_key_ref: SecretReference
    ca_bundle_ref: SecretReference | None = None


OutboundCredential = Annotated[ApiKeyCredential | OAuth2Credential | MtlsCredential, Field(discriminator="type")]


class ModelRef(StrictModel):
    """Reference to a model in the Model Catalog."""

    ref: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_string(cls, data: Any) -> Any:
        return _coerce_bare_string_ref(data)


class McpRef(StrictModel):
    """Reference to an MCP server in the MCP Catalog."""

    ref: str
    tools_filter: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_string(cls, data: Any) -> Any:
        return _coerce_bare_string_ref(data)


class ToolRef(StrictModel):
    """Reference to a native tool."""

    ref: str

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_string(cls, data: Any) -> Any:
        return _coerce_bare_string_ref(data)


class SkillRef(StrictModel):
    """Reference to a skill in the Skill Catalog."""

    ref: str

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_string(cls, data: Any) -> Any:
        return _coerce_bare_string_ref(data)


class MemoryScope(StrEnum):
    """Memory scope options."""

    USER = "user"
    AGENT = "agent"
    TENANT = "tenant"
    APPLICATION = "application"


class MemoryConfig(StrictModel):
    """Memory configuration for an agent."""

    enabled: bool = False
    policy: str | None = None
    scope: MemoryScope = MemoryScope.USER
    max_entries: int | None = Field(default=None, ge=1)


class SessionConfig(StrictModel):
    """Session configuration for an agent."""

    persistence: bool = False
    ttl_seconds: int | None = Field(default=None, gt=0)
    max_history_messages: int = Field(default=20, ge=1)


class A2AConfig(StrictModel):
    """A2A exposure configuration."""

    enabled: bool = False


class AccessRule(StrictModel):
    """Allow/deny rule for a named runtime capability."""

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_non_overlapping(self) -> AccessRule:
        overlap = sorted(set(self.allow) & set(self.deny))
        if overlap:
            raise ValueError(f"Policy name(s) cannot be both allowed and denied: {', '.join(overlap)}")
        return self

    def permits(self, name: str) -> bool:
        """Return whether this rule permits an exact resource name."""
        if name in self.deny:
            return False
        return not self.allow or name in self.allow


class ResourcePolicy(StrictModel):
    """Definition-owned policy for runtime resources and A2A exposure."""

    models: AccessRule = Field(default_factory=AccessRule)
    tools: AccessRule = Field(default_factory=AccessRule)
    mcps: AccessRule = Field(default_factory=AccessRule)
    skills: AccessRule = Field(default_factory=AccessRule)
    a2a: AccessRule = Field(default_factory=AccessRule)


class RuntimeConfig(StrictModel):
    """Runtime-specific configuration."""

    timeout_seconds: int | None = Field(default=None, gt=0)
    max_iterations: int | None = Field(default=None, ge=1)


class AgentSpec(StrictModel):
    """The spec section of an agent definition."""

    description: str = ""
    instruction: str = ""
    model: ModelRef | None = None
    mcps: list[McpRef] = Field(default_factory=list)
    tools: list[ToolRef] = Field(default_factory=list)
    skills: list[SkillRef] = Field(default_factory=list)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    a2a: A2AConfig = Field(default_factory=A2AConfig)
    policy: ResourcePolicy = Field(default_factory=ResourcePolicy)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)


class AgentMetadataConfig(StrictModel):
    """Metadata section of an agent definition."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class AgentDefinition(StrictModel):
    """Complete agent definition loaded from YAML configuration.

    This is the persistent configuration that describes an agent.
    It is NOT a running runtime object.
    """

    api_version: str = Field(default=SUPPORTED_API_VERSION, alias="apiVersion")
    kind: str = SUPPORTED_KIND
    metadata: AgentMetadataConfig
    spec: AgentSpec

    @model_validator(mode="after")
    def _validate_supported_document(self) -> AgentDefinition:
        if self.api_version != SUPPORTED_API_VERSION:
            raise ValueError(f"Unsupported apiVersion '{self.api_version}'; expected '{SUPPORTED_API_VERSION}'")
        if self.kind != SUPPORTED_KIND:
            raise ValueError(f"Unsupported kind '{self.kind}'; expected '{SUPPORTED_KIND}'")
        return self


def load_agent_definition(source: str | Path) -> AgentDefinition:
    """Load an AgentDefinition from a YAML string or file path.

    Configuration precedence:
        1. Built-in defaults (from model definitions)
        2. YAML configuration
        3. Environment variables (OSA_* prefix)

    Raises:
        FileNotFoundError: If source is a Path and the file doesn't exist.
        pydantic.ValidationError: If the definition is invalid.
        ConfigurationError: If an OSA_* boolean override has an unrecognized value.
    """
    if isinstance(source, Path):
        if not source.is_file():
            raise FileNotFoundError(f"Agent definition not found: {source}")
        raw = source.read_text()
    else:
        raw = source

    data = yaml.safe_load(raw)
    _apply_env_overrides(data)
    return AgentDefinition.model_validate(data)


_OSA_ENV_PREFIX = "OSA_"

# Maps env var -> (path in data dict, is_boolean)
_ENV_MAP: dict[str, tuple[list[str], bool]] = {
    "OSA_AGENT_NAME": (["metadata", "name"], False),
    "OSA_AGENT_VERSION": (["metadata", "version"], False),
    "OSA_AGENT_DESCRIPTION": (["metadata", "description"], False),
    "OSA_MODEL_REF": (["spec", "model", "ref"], False),
    "OSA_MEMORY_ENABLED": (["spec", "memory", "enabled"], True),
    "OSA_SESSION_PERSISTENCE": (["spec", "session", "persistence"], True),
}


_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off"}


def _parse_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False
    accepted = ", ".join(sorted(_TRUTHY_VALUES | _FALSY_VALUES))
    raise ConfigurationError(
        f"Invalid boolean value {value!r} for an OSA_* environment override. Accepted values: {accepted}."
    )


def _apply_env_overrides(data: object) -> None:
    """Apply OSA_* environment variables to the raw definition data.

    Non-mapping documents (empty input, lists, scalars) are left untouched so
    AgentDefinition validation reports the real problem. Overrides onto a null
    intermediate node create the missing mapping; overrides are skipped when
    an intermediate node is some other non-mapping value.

    ``OSA_MODEL_REF`` targets a bare-string model reference by replacing the
    string entirely — ``model: default`` plus ``OSA_MODEL_REF=other`` resolves
    to ``{ref: other}`` (environment precedence), never a merge with the file
    value.
    """
    if not isinstance(data, dict):
        return

    for env_var, (path_keys, is_boolean) in _ENV_MAP.items():
        raw_value = os.environ.get(env_var)
        if raw_value is None:
            continue

        current: dict[str, Any] = data
        reachable = True
        for key in path_keys[:-1]:
            nested = current.get(key)
            if isinstance(nested, str) and key == "model":
                # Bare-string model reference: coerce so the override applies.
                nested = {"ref": nested}
                current[key] = nested
            if nested is None:
                nested = {}
                current[key] = nested
            if not isinstance(nested, dict):
                reachable = False
                break
            current = nested
        if not reachable:
            continue

        current[path_keys[-1]] = _parse_boolean(raw_value) if is_boolean else raw_value
