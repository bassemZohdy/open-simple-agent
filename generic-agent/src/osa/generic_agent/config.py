"""Configuration models for agent definitions.

All models use strict validation — unknown properties are rejected.

Configuration precedence:
    Built-in Defaults -> Configuration File -> Environment Variables
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    max_entries: int | None = None


class SessionConfig(StrictModel):
    """Session configuration for an agent."""

    persistence: bool = False
    ttl_seconds: int | None = None


class A2AConfig(StrictModel):
    """A2A exposure configuration."""

    enabled: bool = False


class RuntimeConfig(StrictModel):
    """Runtime-specific configuration."""

    timeout_seconds: int | None = None
    max_iterations: int | None = None


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

    api_version: str = Field(default="osa/v1alpha1", alias="apiVersion")
    kind: str = "Agent"
    metadata: AgentMetadataConfig
    spec: AgentSpec


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
