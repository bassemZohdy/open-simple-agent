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

Storage is backend-agnostic: routes go through an ``AgentRepository``
(ADR-004). Use :func:`osa.control_plane.backend.service.create_control_plane_app`
for PostgreSQL-backed persistence; the module-level app below is in-memory.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from osa.control_plane.backend.agent_catalog import AgentCatalogError, AgentRecord, AgentRecordStatus
from osa.control_plane.backend.repositories import (
    AgentRepository,
    ConcurrentUpdateError,
    DuplicateAgentError,
    DuplicateVersionError,
    InMemoryAgentRepository,
    InMemoryResourceDefinitionRepository,
    InvalidTransitionError,
    ResourceDefinitionRepository,
)
from osa.control_plane.backend.resource_catalogs import ResourceCatalogs
from osa.control_plane.backend.templates import TemplateCatalog, create_default_template_catalog
from osa.generic_agent import AgentDefinition, error_payload


def _package_version_safe() -> str:
    try:
        return metadata.version("osa-control-plane")
    except metadata.PackageNotFoundError:
        return "0"


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


_SORT_FIELDS = {
    "name": lambda r: r.name,
    "created_at": lambda r: r.created_at,
    "updated_at": lambda r: r.updated_at,
}


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


def _missing_resource_refs(resource_catalogs: ResourceCatalogs, definition: AgentDefinition) -> list[str]:
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


# --- App configuration ---


def configure_control_plane_app(
    app: FastAPI,
    *,
    agent_repository: AgentRepository,
    resource_catalogs: ResourceCatalogs,
    template_catalog: TemplateCatalog,
    resource_repository: ResourceDefinitionRepository | None = None,
    deployment_provider: Any = None,
) -> FastAPI:
    """Attach routes and error mapping to a Control Plane app.

    Routes go through the repository contract, so in-memory and PostgreSQL
    backends behave identically.
    """
    from osa.control_plane.backend.deployment import LocalDeploymentProvider
    from osa.control_plane.backend.deployment_service import DeploymentService
    from osa.control_plane.backend.deployments_api import configure_deployment_routes
    from osa.control_plane.backend.repositories import InMemoryDeploymentRecordRepository
    from osa.control_plane.backend.resources_api import configure_resource_routes

    if resource_repository is None:
        resource_repository = InMemoryResourceDefinitionRepository()
    app.state.agent_repository = agent_repository
    app.state.resource_catalogs = resource_catalogs
    app.state.template_catalog = template_catalog
    app.state.resource_repository = resource_repository
    app.state.deployment_service = DeploymentService(
        provider=deployment_provider if deployment_provider is not None else LocalDeploymentProvider(),
        record_repository=InMemoryDeploymentRecordRepository(),
        agent_repository=agent_repository,
        resource_catalogs=resource_catalogs,
    )

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

    @app.exception_handler(ConcurrentUpdateError)
    async def _concurrent_update_handler(request: Request, exc: ConcurrentUpdateError) -> JSONResponse:
        return JSONResponse(status_code=409, content=error_payload("conflict", str(exc)))

    @app.exception_handler(KeyError)
    async def _key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        message = exc.args[0] if exc.args else "not found"
        return JSONResponse(status_code=404, content=error_payload("not_found", str(message)))

    @app.post("/agents", response_model=AgentResponse, status_code=201)
    async def create_agent(request: CreateAgentRequest) -> AgentResponse:
        """Create a new agent.

        With both ``template`` and ``definition`` the request is rejected;
        with neither, an explicit draft placeholder (no definition) is
        created.
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
        await agent_repository.create(record)

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

        records = await agent_repository.list_all()
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
        record = await agent_repository.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        return _record_to_response(record)

    @app.patch("/agents/{agent_id}", response_model=AgentResponse)
    async def update_agent(agent_id: str, request: UpdateAgentRequest) -> AgentResponse:
        """Update an agent (optionally guarded by optimistic concurrency)."""
        record = await agent_repository.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        if request.expected_version is not None and request.expected_version != record.current_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Version conflict: expected current version '{request.expected_version}' but record is at "
                    f"'{record.current_version}'"
                ),
            )

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

        updated = await agent_repository.update(agent_id, expected_version=request.expected_version, **updates)
        _sync_derived_fields(updated)
        return _record_to_response(updated)

    @app.post("/agents/{agent_id}/versions", response_model=AgentResponse, status_code=201)
    async def create_agent_version(agent_id: str, request: CreateVersionRequest) -> AgentResponse:
        """Snapshot the current definition as a new immutable version."""
        from osa.control_plane.backend.agent_catalog import AgentVersion

        record = await agent_repository.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        if record.definition is None:
            raise HTTPException(status_code=422, detail="Agent has no definition to snapshot")
        await agent_repository.add_version(
            agent_id,
            AgentVersion(version=request.version, change_summary=request.change_summary),
        )
        refreshed = await agent_repository.get(agent_id)
        assert refreshed is not None
        return _record_to_response(refreshed)

    @app.post("/agents/{agent_id}/activate", response_model=AgentResponse)
    async def activate_agent(agent_id: str) -> AgentResponse:
        """Transition an agent to active, after validating its configuration."""
        record = await agent_repository.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        if record.definition is None:
            raise HTTPException(status_code=422, detail="Agent cannot be activated without a definition")
        missing = _missing_resource_refs(resource_catalogs, record.definition)
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Cannot activate agent; missing resources: " + ", ".join(missing),
            )
        updated = await agent_repository.transition(agent_id, AgentRecordStatus.ACTIVE)
        return _record_to_response(updated)

    @app.post("/agents/{agent_id}/disable", response_model=AgentResponse)
    async def disable_agent(agent_id: str) -> AgentResponse:
        """Transition an agent to disabled."""
        updated = await agent_repository.transition(agent_id, AgentRecordStatus.DISABLED)
        return _record_to_response(updated)

    @app.post("/agents/{agent_id}/archive", response_model=AgentResponse)
    async def archive_agent(agent_id: str) -> AgentResponse:
        """Transition an agent to archived (terminal)."""
        updated = await agent_repository.transition(agent_id, AgentRecordStatus.ARCHIVED)
        return _record_to_response(updated)

    @app.delete("/agents/{agent_id}", status_code=204)
    async def delete_agent(agent_id: str) -> None:
        """Delete an agent."""
        if not await agent_repository.delete(agent_id):
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    configure_resource_routes(
        app,
        agent_repository=agent_repository,
        resource_catalogs=resource_catalogs,
        resource_repository=resource_repository,
    )
    configure_deployment_routes(app)

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def health_ready() -> dict[str, str]:
        return {"status": "ready"}

    return app


# Module-level default app: in-memory repositories (tests and development).
# For PostgreSQL persistence use
# osa.control_plane.backend.service.create_control_plane_app().
agent_repository = InMemoryAgentRepository()
resource_catalogs = ResourceCatalogs()
template_catalog = create_default_template_catalog()

app = configure_control_plane_app(
    FastAPI(title="Open Simple Agent Control Plane", version=_package_version_safe()),
    agent_repository=agent_repository,
    resource_catalogs=resource_catalogs,
    template_catalog=template_catalog,
)

#: Backward-compatible alias for tests (the in-memory catalog behind ``app``).
agent_catalog = agent_repository.catalog
