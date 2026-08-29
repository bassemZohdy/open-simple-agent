"""Agent invocation request."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass
class AgentRequest:
    """Input to an agent invocation."""

    input: str
    invocation_id: UUID = field(default_factory=uuid4)
    session_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
