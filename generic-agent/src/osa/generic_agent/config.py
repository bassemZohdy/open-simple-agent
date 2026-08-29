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
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SecretReference(StrictModel):
    """Reference to an external secret — never contains the value directly."""

    source: str
    key: str
    env_var: str | None = None


class ModelRef(StrictModel):
    """Reference to a model in the Model Catalog."""

    ref: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class McpRef(StrictModel):
    """Reference to an MCP server in the MCP Catalog."""

    ref: str
    tools_filter: list[str] = Field(default_factory=list)


class ToolRef(StrictModel):
    """Reference to a native tool."""

    ref: str


class SkillRef(StrictModel):
    """Reference to a skill in the Skill Catalog."""

    ref: str


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


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """Apply OSA_* environment variables to the definition data."""
    for env_var, (path_keys, is_boolean) in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is None:
            continue

        current = data
        for key in path_keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        final_key = path_keys[-1]
        if is_boolean:
            current[final_key] = value.lower() == "true"
        else:
            current[final_key] = value
