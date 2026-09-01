"""External A2A agent records (P2.1, ADR-005).

External agents are configuration records pointing at A2A servers outside
OSA's control. They are distinct from managed agents: they are never
deployed, and their Agent Card is fetched and cached at registration and on
refresh, with reachability tracked as health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from osa.generic_agent.a2a_client import RemoteA2aError, resolve_agent_card

AGENT_TYPE_EXTERNAL = "external"


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
    agent_type: str = AGENT_TYPE_EXTERNAL


class ExternalAgentCatalog:
    """In-memory catalog of external agent records."""

    def __init__(self) -> None:
        self._records: dict[str, ExternalAgentRecord] = {}

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
        return len(self._records)


# --- API models ---


class RegisterExternalAgentRequest(BaseModel):
    """Register an external A2A agent by URL.

    The Agent Card is fetched and validated at registration; an unreachable
    or invalid agent is rejected with 422.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str


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


def configure_external_agent_routes(app: FastAPI) -> FastAPI:
    """Attach external-agent routes (requires app.state.external_agent_catalog)."""

    @app.post("/external-agents", response_model=ExternalAgentResponse, status_code=201)
    async def register_external_agent(
        request: RegisterExternalAgentRequest,
        timeout_seconds: float = Query(default=10.0, gt=0, le=60),
    ) -> ExternalAgentResponse:
        """Register an external A2A agent by fetching and validating its card."""
        catalog: ExternalAgentCatalog = app.state.external_agent_catalog
        import asyncio

        try:
            card = await asyncio.wait_for(resolve_agent_card(request.url), timeout=timeout_seconds)
        except RemoteA2aError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = ExternalAgentRecord(
            name=request.name,
            url=request.url.rstrip("/"),
            card=card,
            status="healthy",
            last_checked_at=datetime.now(UTC),
        )
        try:
            catalog.register(record)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _response(record)

    @app.get("/external-agents", response_model=list[ExternalAgentResponse])
    async def list_external_agents(
        status: str | None = Query(default=None, description="Filter by health status"),
    ) -> list[ExternalAgentResponse]:
        """List external agent records."""
        catalog: ExternalAgentCatalog = app.state.external_agent_catalog
        records = catalog.list_all()
        if status is not None:
            records = [r for r in records if r.status == status]
        return [_response(r) for r in records]

    @app.get("/external-agents/{external_id}", response_model=ExternalAgentResponse)
    async def get_external_agent(external_id: str) -> ExternalAgentResponse:
        """Get one external agent record."""
        catalog: ExternalAgentCatalog = app.state.external_agent_catalog
        record = catalog.get(external_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")
        return _response(record)

    @app.post("/external-agents/{external_id}/refresh", response_model=ExternalAgentResponse)
    async def refresh_external_agent(
        external_id: str,
        timeout_seconds: float = Query(default=10.0, gt=0, le=60),
    ) -> ExternalAgentResponse:
        """Re-fetch the Agent Card and update health."""
        catalog: ExternalAgentCatalog = app.state.external_agent_catalog
        record = catalog.get(external_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")
        import asyncio

        try:
            card = await asyncio.wait_for(resolve_agent_card(record.url), timeout=timeout_seconds)
        except RemoteA2aError as exc:
            record.status = "unreachable"
            record.detail = str(exc)
            record.last_checked_at = datetime.now(UTC)
            return _response(record)
        record.card = card
        record.status = "healthy"
        record.detail = ""
        record.last_checked_at = datetime.now(UTC)
        return _response(record)

    @app.delete("/external-agents/{external_id}", status_code=204)
    async def delete_external_agent(external_id: str) -> None:
        """Delete an external agent record."""
        catalog: ExternalAgentCatalog = app.state.external_agent_catalog
        if not catalog.delete(external_id):
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")

    @app.post("/external-agents/{external_id}/invoke", response_model=dict[str, str])
    async def invoke_external_agent(
        external_id: str,
        message: str = Query(description="Text message to send"),
        timeout_seconds: float = Query(default=30.0, gt=0, le=300),
    ) -> dict[str, str]:
        """Invoke the external agent through the A2A protocol."""
        from osa.generic_agent.a2a_client import invoke_remote_agent

        catalog: ExternalAgentCatalog = app.state.external_agent_catalog
        record = catalog.get(external_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"External agent not found: {external_id}")
        import asyncio

        try:
            output = await asyncio.wait_for(invoke_remote_agent(record.url, message), timeout=timeout_seconds)
        except RemoteA2aError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        return {"output": output}

    return app


__all__ = [
    "AGENT_TYPE_EXTERNAL",
    "ExternalAgentCatalog",
    "ExternalAgentRecord",
    "configure_external_agent_routes",
]
