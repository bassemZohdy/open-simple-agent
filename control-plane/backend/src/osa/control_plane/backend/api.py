"""Control Plane API — FastAPI endpoints for agent management.

Contract invariants:

- Requests must supply at most one of ``template`` or ``definition``; a
  request with neither creates an explicit draft placeholder.
- Request names and definition metadata names must agree.
- List filters are cumulative (AND); lists are paginated and sorted.
- Lifecycle transitions follow draft -> active -> disabled/archived; archived
  is terminal. Activation requires a definition with resolvable resources.
- Versions are immutable snapshots; duplicate names/versions are conflicts.
- Errors use the stable OSA schema ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from osa.control_plane.backend import (
    AgentCatalog,
    AgentCatalogError,
    AgentRecord,
    AgentRecordStatus,
    AgentVersion,
    DuplicateAgentError,
    DuplicateVersionError,
    InvalidTransitionError,
    ResourceCatalogs,
    create_default_template_catalog,
)
from osa.generic_agent import AgentDefinition, error_payload


def _package_version_safe() -> str:
    try:
        return metadata.version("osa-control-plane")
    except metadata.PackageNotFoundError:
        return "0"


app = FastAPI(title="Open Simple Agent Control Plane", version=_package_version_safe())

# Shared state (in-memory for now)
agent_catalog = AgentCatalog()
resource_catalogs = ResourceCatalogs()
template_catalog = create_default_template_catalog()

_SORT_FIELDS = {
    "name": lambda r: r.name,
    "created_at": lambda r: r.created_at,
    "updated_at": lambda r: r.updated_at,
}


# --- Request/Response Models ---


class CreateAgentRequest(BaseModel):
    """Request to create a new agent.

    Exactly one of ``template`` or ``definition`` configures the agent; with
    neither, an explicit draft placeholder is created.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    template: str | None = None
    definition: dict[str, Any] | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class UpdateAgentRequest(BaseModel):
    """Request to update an agent.

    ``expected_version`` enables optimistic concurrency: the request fails
    with a conflict if the record's current version differs.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    definition: dict[str, Any] | None = None
    labels: dict[str, str] | None = None
    expected_version: str | None = None


class CreateVersionRequest(BaseModel):
    """Request to snapshot the current definition as a new version."""

    model_config = ConfigDict(extra="forbid")

    version: str
    change_summary: str = ""


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
    """Paginated response containing agents."""

    agents: list[AgentResponse]
    total: int
    limit: int
    offset: int


# --- Error mapping (stable OSA schema) ---


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = {
        404: "not_found",
        409: "conflict",
        400: "bad_request",
        422: "validation_error",
    }.get(exc.status_code, "error")
    detail = exc.detail if isinstance(exc.detail, str) else "request failed"
    return JSONResponse(status_code=exc.status_code, content=error_payload(code, detail))


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    summary = "; ".join(
        f"{'.'.join(str(part) for part in error.get('loc', []) if part != 'body')}: {error.get('msg', 'invalid')}"
        for error in exc.errors()
    )
    return JSONResponse(status_code=422, content=error_payload("validation_error", summary))


@app.exception_handler(AgentCatalogError)
async def _catalog_error_handler(request: Request, exc: AgentCatalogError) -> JSONResponse:
    # Domain errors must never leak as 500s; map the typed hierarchy.
    if isinstance(exc, DuplicateAgentError | DuplicateVersionError):
        return JSONResponse(status_code=409, content=error_payload("conflict", str(exc)))
    if isinstance(exc, InvalidTransitionError):
        return JSONResponse(status_code=400, content=error_payload("invalid_transition", str(exc)))
    return JSONResponse(status_code=400, content=error_payload("bad_request", str(exc)))


# --- Helpers ---


def _parse_definition(raw: dict[str, Any]) -> AgentDefinition:
    try:
        return AgentDefinition.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid agent definition: {exc.error_count()} schema error(s)"
        ) from exc


def _definition_problems(definition: AgentDefinition, name: str) -> list[str]:
    """Validation problems for a definition relative to the request."""
    problems: list[str] = []
    if definition.metadata.name != name:
        problems.append(f"definition metadata.name '{definition.metadata.name}' does not match request name '{name}'")
    return problems


def _missing_resource_refs(definition: AgentDefinition) -> list[str]:
    """Resource references that do not resolve in the resource catalogs."""
    missing: list[str] = []
    spec = definition.spec

    def check(kind: str, ref: str, lookup) -> None:  # type: ignore[no-untyped-def]
        try:
            lookup(ref)
        except KeyError:
            missing.append(f"{kind} '{ref}' not found")

    if spec.model is not None:
        check("model", spec.model.ref, resource_catalogs.get_model)
    for tool_ref in spec.tools:
        check("tool", tool_ref.ref, resource_catalogs.get_tool)
    for skill_ref in spec.skills:
        check("skill", skill_ref.ref, resource_catalogs.get_skill)
    for mcp_ref in spec.mcps:
        check("mcp", mcp_ref.ref, resource_catalogs.get_mcp)
    if spec.memory.enabled and spec.memory.policy is not None:
        check("memory policy", spec.memory.policy, resource_catalogs.get_memory_policy)
    return missing


def _require_record(agent_id: str) -> AgentRecord:
    record = agent_catalog.get(agent_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return record


def _check_expected_version(record: AgentRecord, expected_version: str | None) -> None:
    if expected_version is not None and expected_version != record.current_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version conflict: expected current version '{expected_version}' but record is at "
                f"'{record.current_version}'"
            ),
        )


def _sync_derived_fields(record: AgentRecord) -> AgentRecord:
    if record.definition is not None:
        record.skills = [ref.ref for ref in record.definition.spec.skills]
    return record


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


# --- Agent Management API ---


@app.post("/agents", response_model=AgentResponse, status_code=201)
async def create_agent(request: CreateAgentRequest) -> AgentResponse:
    """Create a new agent.

    With both ``template`` and ``definition`` the request is rejected; with
    neither, an explicit draft placeholder (no definition) is created.
    """
    if request.template and request.definition:
        raise HTTPException(status_code=422, detail="Provide either 'template' or 'definition', not both")

    definition: AgentDefinition | None = None
    if request.template:
        try:
            template = template_catalog.get(request.template)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Template not found: {request.template}") from None
        definition = template.create_definition(name=request.name, extra_labels=request.labels)
    elif request.definition:
        definition = _parse_definition(request.definition)
        for problem in _definition_problems(definition, request.name):
            raise HTTPException(status_code=422, detail=problem)

    record = AgentRecord(
        name=request.name,
        description=request.description,
        definition=definition,
        labels=request.labels,
    )
    if definition is not None:
        _sync_derived_fields(record)
    agent_catalog.create(record)

    return _record_to_response(record)


@app.get("/agents", response_model=AgentListResponse)
async def list_agents(
    status: str | None = None,
    skill: str | None = None,
    runtime: str | None = None,
    q: str | None = None,
    sort_by: str = "name",
    order: str = "asc",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AgentListResponse:
    """List agents; all filters combine (AND), results are sorted and paginated."""
    if status is not None and status not in {s.value for s in AgentRecordStatus}:
        raise HTTPException(status_code=400, detail=f"Unknown status filter: '{status}'")
    if sort_by not in _SORT_FIELDS:
        raise HTTPException(status_code=422, detail=f"Unknown sort field: '{sort_by}'")
    if order not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail=f"Unknown sort order: '{order}'")

    records = agent_catalog.list_all()
    if status is not None:
        records = [r for r in records if r.status.value == status]
    if skill is not None:
        records = [r for r in records if skill in r.skills]
    if runtime is not None:
        records = [r for r in records if r.runtime == runtime]
    if q is not None:
        query = q.lower()
        records = [r for r in records if query in r.name.lower() or query in r.description.lower()]

    key = _SORT_FIELDS[sort_by]
    records = sorted(records, key=key, reverse=(order == "desc"))
    total = len(records)
    page = records[offset : offset + limit]

    return AgentListResponse(
        agents=[_record_to_response(r) for r in page],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str) -> AgentResponse:
    """Get an agent by ID."""
    return _record_to_response(_require_record(agent_id))


@app.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, request: UpdateAgentRequest) -> AgentResponse:
    """Update an agent (optionally guarded by optimistic concurrency)."""
    record = _require_record(agent_id)
    _check_expected_version(record, request.expected_version)

    updates: dict[str, Any] = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    if request.labels is not None:
        updates["labels"] = request.labels
    if request.definition is not None:
        definition = _parse_definition(request.definition)
        target_name = updates.get("name", record.name)
        for problem in _definition_problems(definition, target_name):
            raise HTTPException(status_code=422, detail=problem)
        updates["definition"] = definition

    try:
        record = agent_catalog.update(agent_id, **updates)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}") from None
    _sync_derived_fields(record)
    return _record_to_response(record)


@app.post("/agents/{agent_id}/versions", response_model=AgentResponse, status_code=201)
async def create_agent_version(agent_id: str, request: CreateVersionRequest) -> AgentResponse:
    """Snapshot the current definition as a new immutable version."""
    record = _require_record(agent_id)
    if record.definition is None:
        raise HTTPException(status_code=422, detail="Agent has no definition to snapshot")
    version = AgentVersion(
        version=request.version,
        change_summary=request.change_summary,
    )
    try:
        agent_catalog.add_version(agent_id, version)
    except DuplicateVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _record_to_response(record)


@app.post("/agents/{agent_id}/activate", response_model=AgentResponse)
async def activate_agent(agent_id: str) -> AgentResponse:
    """Transition an agent to active, after validating its configuration."""
    record = _require_record(agent_id)
    if record.definition is None:
        raise HTTPException(status_code=422, detail="Agent cannot be activated without a definition")
    missing = _missing_resource_refs(record.definition)
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Cannot activate agent; missing resources: " + ", ".join(missing),
        )
    record = agent_catalog.transition(agent_id, AgentRecordStatus.ACTIVE)
    return _record_to_response(record)


@app.post("/agents/{agent_id}/disable", response_model=AgentResponse)
async def disable_agent(agent_id: str) -> AgentResponse:
    """Transition an agent to disabled."""
    record = agent_catalog.transition(agent_id, AgentRecordStatus.DISABLED)
    return _record_to_response(record)


@app.post("/agents/{agent_id}/archive", response_model=AgentResponse)
async def archive_agent(agent_id: str) -> AgentResponse:
    """Transition an agent to archived (terminal)."""
    record = agent_catalog.transition(agent_id, AgentRecordStatus.ARCHIVED)
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
