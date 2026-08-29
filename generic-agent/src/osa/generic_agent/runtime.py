"""Agent runtime and factory contracts.

A Runtime converts an AgentDefinition into a running Agent.
A Factory creates specific agent implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from osa.generic_agent.agent import Agent
    from osa.generic_agent.config import AgentDefinition


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
