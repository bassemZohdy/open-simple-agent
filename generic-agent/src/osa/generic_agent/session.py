"""Session domain types and manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass
class SessionId:
    """Unique session identifier."""

    value: str

    @classmethod
    def generate(cls) -> SessionId:
        return cls(value=str(uuid4()))

    def __str__(self) -> str:
        return self.value


@dataclass
class Session:
    """Active runtime session state."""

    session_id: SessionId
    agent_name: str
    user_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_active_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)
    conversation_history: list[dict[str, str]] = field(default_factory=list)

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation history."""
        self.conversation_history.append({"role": role, "content": content})
        self.last_active_at = datetime.now(UTC)

    @property
    def message_count(self) -> int:
        return len(self.conversation_history)


class SessionManager:
    """Manages session lifecycle.

    Provides in-memory session storage. Implementations can extend
    this for persistent storage.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, agent_name: str, user_id: str | None = None) -> Session:
        """Create a new session."""
        session_id = SessionId.generate()
        session = Session(session_id=session_id, agent_name=agent_name, user_id=user_id)
        self._sessions[session_id.value] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None, agent_name: str, user_id: str | None = None) -> Session:
        """Get an existing session or create a new one."""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create(agent_name=agent_name, user_id=user_id)

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if the session existed."""
        return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[Session]:
        """List all active sessions."""
        return list(self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)
