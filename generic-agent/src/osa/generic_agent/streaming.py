"""Framework-neutral streaming events for runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentStreamEvent:
    """One stable OSA streaming event emitted by any runtime backend."""

    type: str
    invocation_id: str
    session_id: str
    text: str = ""
    seq: int = 0

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable event payload used by SSE."""
        return {
            "type": self.type,
            "invocation_id": self.invocation_id,
            "session_id": self.session_id,
            "text": self.text,
            "seq": self.seq,
        }
