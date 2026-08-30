"""Open Simple Agent domain model and runtime contracts."""

from osa.generic_agent.agent import AbstractAgent, Agent
from osa.generic_agent.agent_capabilities import AgentCapabilities
from osa.generic_agent.agent_id import AgentId
from osa.generic_agent.agent_metadata import AgentMetadata
from osa.generic_agent.agent_request import AgentRequest
from osa.generic_agent.agent_response import AgentResponse
from osa.generic_agent.agent_status import AgentStatus
from osa.generic_agent.config import (
    A2AConfig,
    AgentDefinition,
    AgentMetadataConfig,
    AgentSpec,
    ConfigurationError,
    McpRef,
    MemoryConfig,
    MemoryScope,
    ModelRef,
    RuntimeConfig,
    SecretReference,
    SessionConfig,
    SkillRef,
    StrictModel,
    ToolRef,
    load_agent_definition,
)
from osa.generic_agent.example_tools import CalculatorTool
from osa.generic_agent.mcp import (
    McpCatalog,
    McpConnectionOptions,
    McpDefinition,
    McpPromptMetadata,
    McpResourceMetadata,
    McpToolMetadata,
    McpTransport,
)
from osa.generic_agent.memory import (
    InMemoryProvider,
    MemoryEntry,
    MemoryPolicy,
    MemoryProvider,
)
from osa.generic_agent.model import ModelCapabilities, ModelCatalog, ModelDefinition, ModelRuntimeSettings
from osa.generic_agent.model_provider import FakeModelProvider, ModelProvider, ModelResponse, TokenUsage
from osa.generic_agent.runtime import AgentFactory, AgentRuntime
from osa.generic_agent.session import Session, SessionId, SessionManager
from osa.generic_agent.skill import SkillCatalog, SkillDefinition
from osa.generic_agent.tool import (
    Tool,
    ToolCatalog,
    ToolCategory,
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolTimeoutError,
)

__all__ = [
    "A2AConfig",
    "AbstractAgent",
    "Agent",
    "AgentCapabilities",
    "AgentDefinition",
    "AgentFactory",
    "AgentId",
    "AgentMetadata",
    "AgentMetadataConfig",
    "AgentRequest",
    "AgentResponse",
    "AgentRuntime",
    "AgentSpec",
    "AgentStatus",
    "CalculatorTool",
    "ConfigurationError",
    "FakeModelProvider",
    "InMemoryProvider",
    "McpCatalog",
    "McpConnectionOptions",
    "McpDefinition",
    "McpPromptMetadata",
    "McpRef",
    "McpResourceMetadata",
    "McpToolMetadata",
    "McpTransport",
    "MemoryConfig",
    "MemoryEntry",
    "MemoryPolicy",
    "MemoryProvider",
    "MemoryScope",
    "ModelCapabilities",
    "ModelCatalog",
    "ModelDefinition",
    "ModelProvider",
    "ModelRef",
    "ModelResponse",
    "ModelRuntimeSettings",
    "RuntimeConfig",
    "SecretReference",
    "Session",
    "SessionConfig",
    "SessionId",
    "SessionManager",
    "SkillCatalog",
    "SkillDefinition",
    "SkillRef",
    "StrictModel",
    "TokenUsage",
    "Tool",
    "ToolCatalog",
    "ToolCategory",
    "ToolDefinition",
    "ToolError",
    "ToolRef",
    "ToolResult",
    "ToolTimeoutError",
    "load_agent_definition",
]
