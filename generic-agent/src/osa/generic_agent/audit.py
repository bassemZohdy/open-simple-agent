"""Engine-independent audit-event contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Minimal, redaction-safe record emitted by a runtime boundary."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    actor: str = "anonymous"
    action: str = ""
    target: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class AuditEventSink(Protocol):
    """Async destination for audit events."""

    async def append(self, event: AuditEvent) -> None: ...


class InMemoryAuditEventSink:
    """Bounded in-memory sink for local runs and tests."""

    def __init__(self, *, max_events: int = 1000) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._max_events = max_events
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)
        del self.events[: -self._max_events]


__all__ = ["AuditEvent", "AuditEventSink", "InMemoryAuditEventSink"]
