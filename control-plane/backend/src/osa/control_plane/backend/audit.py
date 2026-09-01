"""Safe audit-event emission for Control Plane mutations and invocations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from osa.control_plane.backend.repositories import AuditEvent, AuditEventRepository
from osa.generic_agent import AuthenticatedPrincipal

if TYPE_CHECKING:
    from fastapi import Request


async def record_audit_event(
    request: Request,
    *,
    action: str,
    target: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a redaction-safe event using the request's authenticated identity."""
    repository: AuditEventRepository | None = getattr(request.app.state, "audit_repository", None)
    if repository is None:
        return
    principal = getattr(request.state, "osa_principal", None)
    actor = principal.subject if isinstance(principal, AuthenticatedPrincipal) else "anonymous"
    tenant_id = principal.tenant_id if isinstance(principal, AuthenticatedPrincipal) else None
    await repository.append(
        AuditEvent(
            event_id=str(uuid4()),
            actor=actor,
            action=action,
            target=target,
            detail=dict(detail or {}),
            tenant_id=tenant_id,
        )
    )


__all__ = ["record_audit_event"]
