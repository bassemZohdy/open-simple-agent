"""Agent runtime contracts.

These define the interface for running agents.
They must not contain ADK-specific types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from osa.generic_agent.agent_metadata import AgentMetadata

if TYPE_CHECKING:
    from osa.generic_agent.agent_request import AgentRequest
    from osa.generic_agent.agent_response import AgentResponse
    from osa.generic_agent.config import AgentDefinition


@runtime_checkable
class Agent(Protocol):
    """Public agent contract — the minimal interface for invoking an agent."""

    @property
    def metadata(self) -> AgentMetadata: ...

    async def invoke(self, request: AgentRequest) -> AgentResponse: ...

    async def shutdown(self) -> None: ...


class AbstractAgent(ABC):
    """Base implementation providing common agent behavior.

    Runtime-specific agents extend this class.
    """

    def __init__(self, definition: AgentDefinition) -> None:
        self._definition = definition
        self._metadata = AgentMetadata(
            name=definition.metadata.name,
            version=definition.metadata.version,
            description=definition.metadata.description,
            labels=dict(definition.metadata.labels),
        )

    @property
    def definition(self) -> AgentDefinition:
        return self._definition

    @property
    def metadata(self) -> AgentMetadata:
        return self._metadata

    @abstractmethod
    async def invoke(self, request: AgentRequest) -> AgentResponse: ...

    @abstractmethod
    async def shutdown(self) -> None: ...
