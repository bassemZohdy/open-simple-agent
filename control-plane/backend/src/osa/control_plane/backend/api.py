"""Control Plane API — FastAPI endpoints for agent management."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from osa.control_plane.backend import (
    AgentCatalog,
    AgentRecord,
    AgentRecordStatus,
    AgentVersion,
    ResourceCatalogs,
    create_default_template_catalog,
)
from osa.generic_agent import AgentDefinition

app = FastAPI(title="Open Simple Agent Control Plane", version="0.1.0")

# Shared state (in-memory for now)
agent_catalog = AgentCatalog()
resource_catalogs = ResourceCatalogs()
template_catalog = create_default_template_catalog()


# --- Request/Response Models ---


class CreateAgentRequest(BaseModel):
    """Request to create a new agent."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    template: str | None = None
    definition: dict[str, Any] | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class UpdateAgentRequest(BaseModel):
    """Request to update an agent."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    definition: dict[str, Any] | None = None
    labels: dict[str, str] | None = None


class AgentResponse(BaseModel):
    """Response containing agent information."""

    agent_id: str
    name: str
    description: str
    status: str
    current_version: str
    runtime: str
    skills: list[str]
    labels: dict[str, str]


class AgentListResponse(BaseModel):
    """Response containing a list of agents."""

    agents: list[AgentResponse]
    total: int


# --- Agent Management API ---


@app.post("/agents", response_model=AgentResponse, status_code=201)
async def create_agent(request: CreateAgentRequest) -> AgentResponse:
    """Create a new agent."""
    definition = None

    if request.template:
        template = template_catalog.get(request.template)
        definition = template.create_definition(
            name=request.name,
            extra_labels=request.labels,
        )
    elif request.definition:
        definition = AgentDefinition.model_validate(request.definition)

    record = AgentRecord(
        name=request.name,
        description=request.description,
        definition=definition,
        labels=request.labels,
    )
    agent_catalog.create(record)

    return _record_to_response(record)


@app.get("/agents", response_model=AgentListResponse)
async def list_agents(
    status: str | None = None,
    skill: str | None = None,
    runtime: str | None = None,
    q: str | None = None,
) -> AgentListResponse:
    """List agents with optional filtering."""
    if q:
        records = agent_catalog.search(q)
    elif status:
        records = agent_catalog.filter_by_status(AgentRecordStatus(status))
    elif skill:
        records = agent_catalog.filter_by_skill(skill)
    elif runtime:
        records = agent_catalog.filter_by_runtime(runtime)
    else:
        records = agent_catalog.list_all()

    return AgentListResponse(
        agents=[_record_to_response(r) for r in records],
        total=len(records),
    )


@app.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str) -> AgentResponse:
    """Get an agent by ID."""
    record = agent_catalog.get(agent_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return _record_to_response(record)


@app.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, request: UpdateAgentRequest) -> AgentResponse:
    """Update an agent."""
    updates: dict[str, Any] = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    if request.labels is not None:
        updates["labels"] = request.labels
    if request.definition is not None:
        updates["definition"] = AgentDefinition.model_validate(request.definition)

    try:
        record = agent_catalog.update(agent_id, **updates)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}") from None

    return _record_to_response(record)


@app.post("/agents/{agent_id}/versions", response_model=AgentResponse)
async def create_agent_version(agent_id: str, version: str, change_summary: str = "") -> AgentResponse:
    """Create a new version of an agent."""
    record = agent_catalog.get(agent_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    agent_version = AgentVersion(
        version=version,
        definition=record.definition,
        change_summary=change_summary,
    )
    agent_catalog.add_version(agent_id, agent_version)

    return _record_to_response(record)


@app.post("/agents/{agent_id}/disable", response_model=AgentResponse)
async def disable_agent(agent_id: str) -> AgentResponse:
    """Disable an agent."""
    try:
        record = agent_catalog.disable(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}") from None
    return _record_to_response(record)


@app.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    """Delete an agent."""
    if not agent_catalog.delete(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")


# --- Health ---


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    return {"status": "ready"}


# --- Helpers ---


def _record_to_response(record: AgentRecord) -> AgentResponse:
    return AgentResponse(
        agent_id=record.agent_id,
        name=record.name,
        description=record.description,
        status=record.status.value,
        current_version=record.current_version,
        runtime=record.runtime,
        skills=record.skills,
        labels=record.labels,
    )
