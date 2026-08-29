"""Agent identity types."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True)
class AgentId:
    """Unique agent identifier."""

    value: UUID

    @classmethod
    def generate(cls) -> AgentId:
        return cls(value=uuid4())

    @classmethod
    def from_string(cls, raw: str) -> AgentId:
        return cls(value=UUID(raw))

    def __str__(self) -> str:
        return str(self.value)
