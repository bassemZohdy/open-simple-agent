"""Agent runtime and factory contracts.

A Runtime converts an AgentDefinition into a running Agent.
A Factory creates specific agent implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from osa.generic_agent.observability import Observability

if TYPE_CHECKING:
    from osa.generic_agent.agent import Agent
    from osa.generic_agent.config import AgentDefinition
    from osa.generic_agent.mcp import McpCatalog
    from osa.generic_agent.memory import MemoryPolicyCatalog, MemoryProvider
    from osa.generic_agent.model import ModelCatalog
    from osa.generic_agent.model_provider import ModelProvider
    from osa.generic_agent.secret import SecretResolver
    from osa.generic_agent.session import SessionProvider
    from osa.generic_agent.skill import SkillCatalog
    from osa.generic_agent.tool import ToolCatalog


@dataclass(frozen=True)
class RuntimeDependencies:
    """Framework-neutral dependencies shared by runtime implementations.

    Runtime backends receive the same OSA catalogs, providers, and policy
    services. Keeping this composition object in the generic package prevents
    ADK, LangChain, or LangGraph types from leaking into the domain layer and
    makes backend parity testable.
    """

    model_provider: ModelProvider | None = None
    model_catalog: ModelCatalog | None = None
    tool_catalog: ToolCatalog | None = None
    skill_catalog: SkillCatalog | None = None
    mcp_catalog: McpCatalog | None = None
    memory_provider: MemoryProvider | None = None
    memory_policies: MemoryPolicyCatalog | None = None
    session_provider: SessionProvider | None = None
    secret_resolver: SecretResolver | None = None
    observability: Observability | None = None

    @classmethod
    def with_defaults(
        cls,
        *,
        model_provider: ModelProvider | None = None,
        model_catalog: ModelCatalog | None = None,
        tool_catalog: ToolCatalog | None = None,
        skill_catalog: SkillCatalog | None = None,
        mcp_catalog: McpCatalog | None = None,
        memory_provider: MemoryProvider | None = None,
        memory_policies: MemoryPolicyCatalog | None = None,
        session_provider: SessionProvider | None = None,
        secret_resolver: SecretResolver | None = None,
        observability: Observability | None = None,
    ) -> RuntimeDependencies:
        """Build dependencies with safe OSA defaults.

        ``None`` is used deliberately instead of truthiness because catalog
        and provider objects define ``__len__`` and an empty object is valid.
        """
        from osa.generic_agent.mcp import McpCatalog
        from osa.generic_agent.memory import MemoryPolicyCatalog
        from osa.generic_agent.model import ModelCatalog
        from osa.generic_agent.session import SessionManager
        from osa.generic_agent.skill import SkillCatalog
        from osa.generic_agent.tool import ToolCatalog

        return cls(
            model_provider=model_provider,
            model_catalog=model_catalog if model_catalog is not None else ModelCatalog(),
            tool_catalog=tool_catalog if tool_catalog is not None else ToolCatalog(),
            skill_catalog=skill_catalog if skill_catalog is not None else SkillCatalog(),
            mcp_catalog=mcp_catalog if mcp_catalog is not None else McpCatalog(),
            memory_provider=memory_provider,
            memory_policies=memory_policies if memory_policies is not None else MemoryPolicyCatalog(),
            session_provider=session_provider if session_provider is not None else SessionManager(),
            secret_resolver=secret_resolver,
            observability=observability if observability is not None else Observability(),
        )


class AgentRuntime(ABC):
    """Runtime interface — creates agents from definitions.

    Implementations handle framework-specific agent construction.
    """

    @abstractmethod
    async def create(self, definition: AgentDefinition) -> Agent:
        """Create and start an agent from its definition."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Shut down the runtime and all agents it created."""
        ...


class AgentFactory(ABC):
    """Factory interface — produces agent instances from definitions.

    This is a synchronous alternative to AgentRuntime for cases
    where agent construction doesn't require async initialization.
    """

    @abstractmethod
    def create(self, definition: AgentDefinition) -> Agent:
        """Create an agent instance from its definition."""
        ...
