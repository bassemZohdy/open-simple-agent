"""External A2A agent records (P2.1, ADR-005).

External agents are configuration records pointing at A2A servers outside
OSA's control. They are distinct from managed agents: they are never
deployed, and their Agent Card is fetched and cached at registration and on
refresh, with reachability tracked as health.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from osa.control_plane.backend.audit import record_audit_event
from osa.generic_agent import (
    AuthenticatedPrincipal,
    EnvironmentSecretResolver,
    OutboundCredential,
    SecretResolver,
)
from osa.generic_agent.a2a_client import RemoteA2aError, resolve_agent_card

AGENT_TYPE_EXTERNAL = "external"


def _request_tenant(request: Request) -> str | None:
    """Tenant of the authenticated caller (kept local to avoid an api.py cycle)."""
    principal = getattr(request.state, "osa_principal", None)
    return principal.tenant_id if isinstance(principal, AuthenticatedPrincipal) else None


@dataclass
class ExternalAgentRecord:
    """A registered external A2A agent."""

    external_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    url: str = ""
    card: dict[str, Any] = field(default_factory=dict)
    status: str = "unknown"
    detail: str = ""
    last_checked_at: datetime | None = None
    credential: OutboundCredential | None = None
    agent_type: str = AGENT_TYPE_EXTERNAL
    tenant_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class DuplicateExternalAgentError(ValueError):
    """An external-agent name already exists in the tenant scope."""


class ExternalAgentRepository(ABC):
    """Persistence contract for external A2A agent registrations."""

    @abstractmethod
    async def create(self, record: ExternalAgentRecord) -> ExternalAgentRecord: ...

    @abstractmethod
    async def get(self, external_id: str, *, tenant_id: str | None = None) -> ExternalAgentRecord | None: ...

    @abstractmethod
    async def list(self, *, tenant_id: str | None = None) -> list[ExternalAgentRecord]: ...

    @abstractmethod
    async def save(self, record: ExternalAgentRecord) -> ExternalAgentRecord: ...

    @abstractmethod
    async def delete(self, external_id: str, *, tenant_id: str | None = None) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class InMemoryExternalAgentRepository(ExternalAgentRepository):
    """Repository adapter for the process-local external-agent catalog."""

    def __init__(self, catalog: ExternalAgentCatalog) -> None:
        self._catalog = catalog

    async def create(self, record: ExternalAgentRecord) -> ExternalAgentRecord:
        try:
            return self._catalog.for_tenant(record.tenant_id).register(record)
        except ValueError as exc:
            raise DuplicateExternalAgentError(str(exc)) from exc

    async def get(self, external_id: str, *, tenant_id: str | None = None) -> ExternalAgentRecord | None:
        record = self._catalog.for_tenant(tenant_id).get(external_id)
        return record

    async def list(self, *, tenant_id: str | None = None) -> list[ExternalAgentRecord]:
        return self._catalog.for_tenant(tenant_id).list_all()

    async def save(self, record: ExternalAgentRecord) -> ExternalAgentRecord:
        catalog = self._catalog.for_tenant(record.tenant_id)
        existing = catalog.get(record.external_id)
        if existing is None:
            raise KeyError(f"External agent not found: {record.external_id}")
        catalog._records[record.external_id] = record
        return record

    async def delete(self, external_id: str, *, tenant_id: str | None = None) -> bool:
        record = await self.get(external_id, tenant_id=tenant_id)
        return record is not None and self._catalog.for_tenant(tenant_id).delete(external_id)

    async def close(self) -> None:
        return None


class PostgresExternalAgentRepository(ExternalAgentRepository):
    """Durable tenant-scoped external-agent registry."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @staticmethod
    def _scope(tenant_id: str | None) -> str:
        return tenant_id or ""

    @staticmethod
    def _from_row(row: Any) -> ExternalAgentRecord:
        from pydantic import TypeAdapter

        credential: OutboundCredential | None = None
        if row.credential is not None:
            credential = TypeAdapter(OutboundCredential).validate_python(row.credential)
        return ExternalAgentRecord(
            external_id=row.external_id,
            name=row.name,
            url=row.url,
            card=dict(row.card or {}),
            status=row.status,
            detail=row.detail,
            last_checked_at=row.last_checked_at,
            credential=credential,
            tenant_id=row.tenant_id or None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, record: ExternalAgentRecord) -> ExternalAgentRecord:
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError

        from osa.control_plane.backend.tables import external_agents_table

        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    insert(external_agents_table).values(
                        external_id=record.external_id,
                        tenant_id=self._scope(record.tenant_id),
                        name=record.name,
                        url=record.url,
                        card=record.card,
                        status=record.status,
                        detail=record.detail,
                        last_checked_at=record.last_checked_at,
                        credential=record.credential.model_dump(mode="json") if record.credential else None,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                    )
                )
        except IntegrityError as exc:
            raise DuplicateExternalAgentError(f"External agent with name '{record.name}' already exists") from exc
        return record

    async def get(self, external_id: str, *, tenant_id: str | None = None) -> ExternalAgentRecord | None:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import external_agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(external_agents_table).where(
                    external_agents_table.c.external_id == external_id,
                    external_agents_table.c.tenant_id == self._scope(tenant_id),
                )
            )
            row = result.first()
        return self._from_row(row) if row is not None else None

    async def list(self, *, tenant_id: str | None = None) -> list[ExternalAgentRecord]:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import external_agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(external_agents_table)
                .where(external_agents_table.c.tenant_id == self._scope(tenant_id))
                .order_by(external_agents_table.c.name.asc())
            )
            rows = result.fetchall()
        return [self._from_row(row) for row in rows]

    async def save(self, record: ExternalAgentRecord) -> ExternalAgentRecord:
        from sqlalchemy import update

        from osa.control_plane.backend.tables import external_agents_table

        record.updated_at = datetime.now(UTC)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(external_agents_table)
                .where(
                    external_agents_table.c.external_id == record.external_id,
                    external_agents_table.c.tenant_id == self._scope(record.tenant_id),
                )
                .values(
                    name=record.name,
                    url=record.url,
                    card=record.card,
                    status=record.status,
                    detail=record.detail,
                    last_checked_at=record.last_checked_at,
                    credential=record.credential.model_dump(mode="json") if record.credential else None,
                    updated_at=record.updated_at,
                )
            )
        if not result.rowcount:
            raise KeyError(f"External agent not found: {record.external_id}")
        return record

    async def delete(self, external_id: str, *, tenant_id: str | None = None) -> bool:
        from sqlalchemy import delete

        from osa.control_plane.backend.tables import external_agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(external_agents_table).where(
                    external_agents_table.c.external_id == external_id,
                    external_agents_table.c.tenant_id == self._scope(tenant_id),
                )
            )
        return bool(result.rowcount)

    async def close(self) -> None:
        return None


class SqliteExternalAgentRepository(PostgresExternalAgentRepository):
    """SQLite external-agent repository for explicit local use."""


class ExternalAgentCatalog:
    """Tenant-scoped in-memory catalog of external agent records.

    ``None`` is the shared scope used by local development and by
    authentication-disabled applications. Each authenticated tenant resolves
    its own namespace (like ``ResourceCatalogs.for_tenant``), so records —
    and the outbound credentials they carry — never cross tenants.
    """

    def __init__(self) -> None:
        self._records: dict[str, ExternalAgentRecord] = {}
        self._tenant_scopes: dict[str, ExternalAgentCatalog] = {}

    def for_tenant(self, tenant_id: str | None) -> ExternalAgentCatalog:
        """Return the catalog namespace owned by ``tenant_id``."""
        if tenant_id is None:
            return self
        scoped = self._tenant_scopes.get(tenant_id)
        if scoped is None:
            scoped = ExternalAgentCatalog()
            self._tenant_scopes[tenant_id] = scoped
        return scoped

    def register(self, record: ExternalAgentRecord) -> ExternalAgentRecord:
        if any(r.name == record.name for r in self._records.values()):
            raise ValueError(f"External agent with name '{record.name}' already exists")
        self._records[record.external_id] = record
        return record

    def get(self, external_id: str) -> ExternalAgentRecord | None:
        return self._records.get(external_id)

    def list_all(self) -> list[ExternalAgentRecord]:
        return sorted(self._records.values(), key=lambda r: r.name)

    def delete(self, external_id: str) -> bool:
        return self._records.pop(external_id, None) is not None

    def __len__(self) -> int:
        return len(self._records) + sum(len(scope) for scope in self._tenant_scopes.values())


# --- API models ---


class RegisterExternalAgentRequest(BaseModel):
    """Register an external A2A agent by URL.

    The Agent Card is fetched and validated at registration; an unreachable
    or invalid agent is rejected with 422.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    credential: OutboundCredential | None = None


class ExternalAgentResponse(BaseModel):
    """An external agent record."""

    external_id: str
    name: str
    url: str
    card_name: str = ""
    card_version: str = ""
    skills: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    detail: str
    agent_type: str = AGENT_TYPE_EXTERNAL


def _response(record: ExternalAgentRecord) -> ExternalAgentResponse:
    return ExternalAgentResponse(
        external_id=record.external_id,
        name=record.name,
        url=record.url,
        card_name=str(record.card.get("name", "")),
        card_version=str(record.card.get("version", "")),
        skills=list(record.card.get("skills", [])),
        status=record.status,
        detail=record.detail,
        agent_type=record.agent_type,
    )


def configure_external_agent_routes(
    app: FastAPI,
    *,
    secret_resolver: SecretResolver | None = None,
) -> FastAPI:
    """Attach external-agent routes (requires app.state.external_agent_catalog)."""

    resolver = secret_resolver if secret_resolver is not None else EnvironmentSecretResolver()

    def repository(request: Request) -> ExternalAgentRepository:
        configured: ExternalAgentRepository | None = getattr(app.state, "external_agent_repository", None)
        if configured is not None:
            return configured
        return InMemoryExternalAgentRepository(app.state.external_agent_catalog)

    @app.post("/external-agents", response_model=ExternalAgentResponse, status_code=201)
    async def register_external_agent(
        http_request: Request,
        request: RegisterExternalAgentRequest,
        timeout_seconds: float = Query(default=10.0, gt=0, le=60),
    ) -> ExternalAgentResponse:
        """Register an external A2A agent by fetching and validating its card."""
        tenant_id = _request_tenant(http_request)
        records = repository(http_request)
        import asyncio

        try:
            card = await asyncio.wait_for(
                resolve_agent_card(
                    request.url,
                    credential=request.credential,
                    secret_resolver=resolver,
                ),
                timeout=timeout_seconds,
            )
        except RemoteA2aError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = ExternalAgentRecord(
            name=request.name,
            url=request.url.rstrip("/"),
            card=card,
            status="healthy",
            last_checked_at=datetime.now(UTC),
            credential=request.credential,
            tenant_id=tenant_id,
        )
        try:
            await records.create(record)
        except DuplicateExternalAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await record_audit_event(
            http_request,
            action="external_agent.register",
            target=record.external_id,
            detail={"name": record.name},
        )
        return _response(record)

    @app.get("/external-agents", response_model=list[ExternalAgentResponse])
    async def list_external_agents(
        request: Request,
        status: str | None = Query(default=None, description="Filter by health status"),
    ) -> list[ExternalAgentResponse]:
        """List external agent records (scoped to the caller's tenant)."""
        records = await repository(request).list(tenant_id=_request_tenant(request))
        if status is not None:
            records = [r for r in records if r.status == status]
        return [_response(r) for r in records]

    @app.get("/external-agents/{external_id}", response_model=ExternalAgentResponse)
    async def get_external_agent(request: Request, external_id: str) -> ExternalAgentResponse:
        """Get one external agent record."""
        record = await repository(request).get(external_id, tenant_id=_request_tenant(request))
        if record is None:
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")
        return _response(record)

    @app.post("/external-agents/{external_id}/refresh", response_model=ExternalAgentResponse)
    async def refresh_external_agent(
        http_request: Request,
        external_id: str,
        timeout_seconds: float = Query(default=10.0, gt=0, le=60),
    ) -> ExternalAgentResponse:
        """Re-fetch the Agent Card and update health."""
        records = repository(http_request)
        record = await records.get(external_id, tenant_id=_request_tenant(http_request))
        if record is None:
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")
        import asyncio

        try:
            card = await asyncio.wait_for(
                resolve_agent_card(
                    record.url,
                    credential=record.credential,
                    secret_resolver=resolver,
                ),
                timeout=timeout_seconds,
            )
        except RemoteA2aError as exc:
            record.status = "unreachable"
            record.detail = str(exc)
            record.last_checked_at = datetime.now(UTC)
            await records.save(record)
            await record_audit_event(
                http_request,
                action="external_agent.refresh",
                target=record.external_id,
                detail={"status": record.status},
            )
            return _response(record)
        record.card = card
        record.status = "healthy"
        record.detail = ""
        record.last_checked_at = datetime.now(UTC)
        await records.save(record)
        await record_audit_event(
            http_request,
            action="external_agent.refresh",
            target=record.external_id,
            detail={"status": record.status},
        )
        return _response(record)

    @app.delete("/external-agents/{external_id}", status_code=204)
    async def delete_external_agent(http_request: Request, external_id: str) -> None:
        """Delete an external agent record."""
        if not await repository(http_request).delete(external_id, tenant_id=_request_tenant(http_request)):
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")
        await record_audit_event(http_request, action="external_agent.delete", target=external_id)

    @app.post("/external-agents/{external_id}/invoke", response_model=dict[str, str])
    async def invoke_external_agent(
        http_request: Request,
        external_id: str,
        message: str = Query(description="Text message to send"),
        timeout_seconds: float = Query(default=30.0, gt=0, le=300),
    ) -> dict[str, str]:
        """Invoke the external agent through the A2A protocol."""
        from osa.generic_agent.a2a_client import invoke_remote_agent

        record = await repository(http_request).get(external_id, tenant_id=_request_tenant(http_request))
        if record is None:
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")
        import asyncio

        try:
            output = await asyncio.wait_for(
                invoke_remote_agent(
                    record.url,
                    message,
                    credential=record.credential,
                    secret_resolver=resolver,
                ),
                timeout=timeout_seconds,
            )
        except RemoteA2aError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        await record_audit_event(http_request, action="external_agent.invoke", target=external_id)
        return {"output": output}

    return app


__all__ = [
    "AGENT_TYPE_EXTERNAL",
    "DuplicateExternalAgentError",
    "ExternalAgentCatalog",
    "ExternalAgentRecord",
    "ExternalAgentRepository",
    "InMemoryExternalAgentRepository",
    "PostgresExternalAgentRepository",
    "SqliteExternalAgentRepository",
    "configure_external_agent_routes",
]
