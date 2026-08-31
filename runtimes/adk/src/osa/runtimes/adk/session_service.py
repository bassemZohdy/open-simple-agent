"""ADK session service backed by the OSA ``SessionProvider``.

The OSA session store is the single source of truth: ownership, TTL, and
history bounds live there. ADK events are serialized into the OSA session
(bounded by ``Session.max_history_messages``), so conversation context fed to
the model through the ADK Runner stays bounded and survives process-internal
restarts of the runner objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.adk.events.event import Event as AdkEvent
from google.adk.sessions.base_session_service import (
    BaseSessionService,
    GetSessionConfig,
    ListSessionsResponse,
)
from google.adk.sessions.session import Session as AdkSession

if TYPE_CHECKING:
    from osa.generic_agent import SessionProvider

# Events kept in the store per session: function-call rounds consume four
# events (user message, call, response, final answer), so the bound tracks the
# message bound generously while still preventing unbounded growth.
_MIN_EVENTS = 8


def _event_bound(max_history_messages: int) -> int:
    return max(_MIN_EVENTS, max_history_messages * 2)


class OsaAdkSessionService(BaseSessionService):
    """Maps ADK session operations onto an OSA ``SessionProvider``.

    Used strictly after the invoking caller has been authorized by
    ``GenericAdkAgent`` (which resolves ownership via ``SessionProvider.resolve``).
    """

    def __init__(self, provider: SessionProvider) -> None:
        self._provider = provider

    def _load_adk_session(self, session_id: str, app_name: str, user_id: str) -> AdkSession | None:
        session = self._provider.get_runtime_view(session_id)
        if session is None:
            return None
        raw_events = session.metadata.get("adk_events", [])
        events: list[AdkEvent] = []
        if isinstance(raw_events, list):
            for raw in raw_events:
                if isinstance(raw, dict):
                    events.append(AdkEvent.model_validate(raw))
        return AdkSession(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            events=events,
        )

    def _persist_events(self, session_id: str, adk_session: AdkSession) -> None:
        session = self._provider.get_runtime_view(session_id)
        if session is None:
            return
        bound = _event_bound(session.max_history_messages)
        serialized = [event.model_dump(mode="json") for event in adk_session.events][-bound:]
        session.metadata["adk_events"] = serialized
        self._provider.save(session)

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> AdkSession:
        if session_id is None:
            raise ValueError("OsaAdkSessionService requires the OSA session id to be provided")
        session = self._provider.get_runtime_view(session_id)
        if session is None:
            raise ValueError(f"OSA session '{session_id}' does not exist")
        return self._load_adk_session(session_id, app_name, user_id)  # type: ignore[return-value]

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> AdkSession | None:
        return self._load_adk_session(session_id, app_name, user_id)

    async def list_sessions(self, *, app_name: str, user_id: str | None = None) -> ListSessionsResponse:
        # The OSA provider contract does not expose agent-wide listing to the
        # runtime path; the Runner core does not require it.
        return ListSessionsResponse(sessions=[])

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        session = self._provider.get_runtime_view(session_id)
        if session is None:
            return
        self._provider.delete(
            session_id,
            agent_name=session.agent_name,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
        )

    async def append_event(self, session: AdkSession, event: AdkEvent) -> AdkEvent:
        event = await super().append_event(session, event)
        self._persist_events(session.id, session)
        return event
