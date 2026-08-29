"""Agent metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from osa.generic_agent.agent_id import AgentId
from osa.generic_agent.agent_status import AgentStatus


@dataclass
class AgentMetadata:
    """Persistent metadata associated with an agent definition."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    agent_id: AgentId = field(default_factory=AgentId.generate)
    status: AgentStatus = AgentStatus.DRAFT
    labels: dict[str, str] = field(default_factory=dict)
