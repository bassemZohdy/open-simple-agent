"""Bounded polling relay for migration-owned A2A task events.

The A2A SDK's queue manager remains process-local. This module provides the
small cross-process primitive needed by a future request-handler adapter:
read durable events after a caller cursor, preserve tenant scope, and stop at
one durable terminal event. It deliberately does not attach a route or enable
streaming in the Agent Card.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from osa.runtimes.adk.a2a_event_store import A2aTaskEvent, A2aTaskEventStore
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore

A2A_EVENT_RELAY_POLL_INTERVAL_SECONDS = 0.25
A2A_EVENT_RELAY_TIMEOUT_SECONDS = 30.0
A2A_EVENT_RELAY_PAGE_SIZE = 128


class A2aEventStreamTimeoutError(TimeoutError):
    """Raised when a durable event stream has no new event before its deadline."""


class A2aEventDecodeError(ValueError):
    """Raised when a stored event is not a supported A2A protocol message."""


class A2aTaskEventRelay:
    """Read a bounded, tenant-scoped A2A event stream from durable storage."""

    def __init__(
        self,
        event_store: A2aTaskEventStore,
        ownership_store: A2aTaskOwnershipStore,
        *,
        poll_interval_seconds: float = A2A_EVENT_RELAY_POLL_INTERVAL_SECONDS,
        timeout_seconds: float = A2A_EVENT_RELAY_TIMEOUT_SECONDS,
        page_size: int = A2A_EVENT_RELAY_PAGE_SIZE,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("A2A event relay poll interval must be positive")
        if timeout_seconds <= 0:
            raise ValueError("A2A event relay timeout must be positive")
        if page_size < 1:
            raise ValueError("A2A event relay page size must be positive")
        self._event_store = event_store
        self._ownership_store = ownership_store
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._page_size = page_size

    async def stream(
        self,
        *,
        scope_key: str,
        task_id: str,
        after_sequence: int = 0,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[Any]:
        """Yield events after ``after_sequence`` until terminal or timeout.

        Only one bounded page is held at a time. Reconnects pass the last
        acknowledged sequence, so replay is durable and resumable. A terminal
        event ends the stream; an absent or released ownership row ends an
        otherwise empty stream without inventing a protocol event.
        """
        if not scope_key or not task_id:
            raise ValueError("A2A event relay requires scope and task identifiers")
        if after_sequence < 0:
            raise ValueError("A2A event relay cursor must not be negative")
        resolved_timeout = self._timeout_seconds if timeout_seconds is None else timeout_seconds
        if resolved_timeout <= 0:
            raise ValueError("A2A event relay timeout must be positive")

        cursor = after_sequence
        deadline = asyncio.get_running_loop().time() + resolved_timeout
        while True:
            stored_events = await self._event_store.read_after_scope(
                scope_key=scope_key,
                task_id=task_id,
                after_sequence=cursor,
                limit=self._page_size,
            )
            if stored_events:
                for stored_event in stored_events:
                    event = decode_task_event(stored_event)
                    cursor = stored_event.sequence
                    yield event
                    if _is_terminal_event(event):
                        return
                continue

            if await self._stream_is_finished(task_id, scope_key):
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise A2aEventStreamTimeoutError(
                    f"A2A task {task_id} produced no event after cursor {cursor} before the relay deadline"
                )
            await asyncio.sleep(min(self._poll_interval_seconds, remaining))

    async def _stream_is_finished(self, task_id: str, scope_key: str) -> bool:
        ownership = await self._ownership_store.get(task_id, scope_key)
        return ownership is None or ownership.state in {
            "released",
            "completed",
            "failed",
            "canceled",
        }


def decode_task_event(stored_event: A2aTaskEvent) -> Any:
    """Decode one stored protobuf event using the supported task-event types."""
    from a2a.types import Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent

    event_types: dict[str, Any] = {
        "Task": Task,
        "TaskArtifactUpdateEvent": TaskArtifactUpdateEvent,
        "TaskStatusUpdateEvent": TaskStatusUpdateEvent,
    }
    event_type = event_types.get(stored_event.event_type)
    if event_type is None:
        raise A2aEventDecodeError(f"Unsupported stored A2A event type: {stored_event.event_type}")
    try:
        return event_type.FromString(stored_event.payload)
    except Exception as exc:  # noqa: BLE001 - normalize malformed durable data
        raise A2aEventDecodeError(f"Unable to decode stored A2A event: {stored_event.event_type}") from exc


def _is_terminal_event(event: Any) -> bool:
    from a2a.types import TaskState, TaskStatusUpdateEvent

    return isinstance(event, TaskStatusUpdateEvent) and event.status.state in {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }


__all__ = [
    "A2A_EVENT_RELAY_PAGE_SIZE",
    "A2A_EVENT_RELAY_POLL_INTERVAL_SECONDS",
    "A2A_EVENT_RELAY_TIMEOUT_SECONDS",
    "A2aEventDecodeError",
    "A2aEventStreamTimeoutError",
    "A2aTaskEventRelay",
    "decode_task_event",
]
