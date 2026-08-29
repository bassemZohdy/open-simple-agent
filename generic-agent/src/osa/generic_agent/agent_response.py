"""Agent invocation response."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


@dataclass
class AgentResponse:
    """Output from an agent invocation."""

    output: str
    invocation_id: UUID
    session_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    error: str | None = None
