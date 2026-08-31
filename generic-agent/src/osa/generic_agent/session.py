"""Session domain types, ownership rules, and providers.

Sessions carry isolated conversation state per ``(agent, user, tenant)``
combination. The :class:`SessionProvider` contract is storage-agnostic; the
in-memory :class:`SessionManager` is intended for tests and single-replica
deployments.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

DEFAULT_MAX_HISTORY_MESSAGES = 20


class SessionError(Exception):
    """Base error for session resolution failures."""

    #: Stable wire code used by HTTP layers when mapping this error.
    code = "session_error"


class SessionNotFoundError(SessionError):
    """A caller-supplied session ID does not exist (or has expired)."""

    code = "session_not_found"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session '{session_id}' not found")


class SessionAccessError(SessionError):
    """The caller's identity does not match the session's owner.

    The message deliberately does not reveal whether the session exists under
    a different owner.
    """

    code = "session_access_denied"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session '{session_id}' is not accessible to this caller")


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
    tenant_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_active_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int | None = None
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    metadata: dict[str, Any] = field(default_factory=dict)
    conversation_history: list[dict[str, str]] = field(default_factory=list)

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the bounded conversation history.

        When the history exceeds ``max_history_messages`` the oldest messages
        are dropped so per-session memory stays bounded.
        """
        self.conversation_history.append({"role": role, "content": content})
        if len(self.conversation_history) > self.max_history_messages:
            del self.conversation_history[: len(self.conversation_history) - self.max_history_messages]
        self.last_active_at = datetime.now(UTC)

    def history_window(self, limit: int) -> list[dict[str, str]]:
        """Return the most recent ``limit`` messages, oldest first."""
        return self.conversation_history[-limit:] if limit > 0 else []

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.ttl_seconds is None:
            return False
        current = now or datetime.now(UTC)
        return (current - self.last_active_at).total_seconds() >= self.ttl_seconds

    def matches_owner(self, *, agent_name: str, user_id: str | None, tenant_id: str | None) -> bool:
        """Whether the given identity exactly matches the session owner."""
        return self.agent_name == agent_name and self.user_id == user_id and self.tenant_id == tenant_id

    @property
    def message_count(self) -> int:
        return len(self.conversation_history)


class SessionProvider(ABC):
    """Storage contract for sessions.

    Ownership is enforced by every method that takes a session identity: a
    session is only ever returned to its own ``(agent_name, user_id,
    tenant_id)`` owner, caller-supplied unknown IDs are rejected, and identity
    changes are access violations. The runtime-internal accessors
    (:meth:`get_runtime_view`, :meth:`save`) exist for framework adapters that
    run strictly after the caller has been authorized.
    """

    @abstractmethod
    def create(
        self,
        agent_name: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    ) -> Session:
        """Create a new session with a server-issued stable ID."""
        ...

    @abstractmethod
    def resolve(
        self,
        session_id: str,
        *,
        agent_name: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> Session:
        """Resolve a caller-supplied session ID under strict ownership.

        Raises:
            SessionNotFoundError: The ID is unknown or its TTL has expired.
            SessionAccessError: The session belongs to another agent, user,
                or tenant.
        """
        ...

    @abstractmethod
    def get_runtime_view(self, session_id: str) -> Session | None:
        """Fetch a session after authorization (framework adapter path).

        No identity is supplied because the caller has already been verified
        via :meth:`resolve`; returns None for unknown or expired sessions.
        """
        ...

    @abstractmethod
    def save(self, session: Session) -> None:
        """Persist an updated session (bounded history and metadata)."""
        ...

    @abstractmethod
    def delete(
        self, session_id: str, *, agent_name: str, user_id: str | None = None, tenant_id: str | None = None
    ) -> bool:
        """Delete a session owned by the given identity. Returns True if deleted."""
        ...

    @abstractmethod
    def purge_expired(self) -> int:
        """Remove expired sessions; returns the number purged."""
        ...


class SessionManager(SessionProvider):
    """In-memory session provider.

    Suitable for tests and single-process deployments; multi-replica
    deployments require a shared persistent provider (see TODO.md, P1).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        agent_name: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    ) -> Session:
        session_id = SessionId.generate()
        session = Session(
            session_id=session_id,
            agent_name=agent_name,
            user_id=user_id,
            tenant_id=tenant_id,
            ttl_seconds=ttl_seconds,
            max_history_messages=max_history_messages,
        )
        self._sessions[session_id.value] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session by ID without ownership checks (administrative use)."""
        return self._sessions.get(session_id)

    def resolve(
        self,
        session_id: str,
        *,
        agent_name: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.is_expired():
            del self._sessions[session_id]
            raise SessionNotFoundError(session_id)
        if not session.matches_owner(agent_name=agent_name, user_id=user_id, tenant_id=tenant_id):
            raise SessionAccessError(session_id)
        return session

    def get_runtime_view(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is not None and session.is_expired():
            del self._sessions[session_id]
            return None
        return session

    def save(self, session: Session) -> None:
        session.last_active_at = datetime.now(UTC)
        self._sessions[str(session.session_id)] = session

    def delete(
        self, session_id: str, *, agent_name: str, user_id: str | None = None, tenant_id: str | None = None
    ) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        if not session.matches_owner(agent_name=agent_name, user_id=user_id, tenant_id=tenant_id):
            raise SessionAccessError(session_id)
        return self._sessions.pop(session_id, None) is not None

    def purge_expired(self) -> int:
        expired = [sid for sid, session in self._sessions.items() if session.is_expired()]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def list_sessions(self) -> list[Session]:
        """List all active sessions (administrative use)."""
        return list(self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)
