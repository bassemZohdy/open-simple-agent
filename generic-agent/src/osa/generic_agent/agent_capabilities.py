"""Agent capabilities metadata."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentCapabilities:
    """Describes what an agent supports."""

    streaming: bool = False
    a2a: bool = False
    memory: bool = False
    session_persistence: bool = False
    custom: dict[str, bool] = field(default_factory=dict)
