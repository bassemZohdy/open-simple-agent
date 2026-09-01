"""Control Plane resource and template APIs (P1.2).

Routes for catalog resources (models, tools, skills, MCPs, memory policies)
with write-through to the ``ResourceDefinitionRepository``: creation,
lookup, listing/search, replacement, and deletion with reference-usage
checks (a resource referenced by any agent record cannot be deleted).
``POST /resources/import`` and ``GET /resources/export`` move resource
bundles in and out using the same envelope format as deployment bundles
(``{apiVersion, kind, spec}``). Templates are exposed read-only
(code-defined built-ins).

Secret values never exist in resource definitions (``credential_ref`` holds
only source/key names), so responses cannot leak them; the redaction tests
pin that contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from osa.control_plane.backend.audit import record_audit_event
from osa.generic_agent import (
    AuthenticatedPrincipal,
    InvalidBundleError,
    McpDefinition,
    MemoryPolicy,
    ModelDefinition,
    SkillDefinition,
    StrictModel,
    ToolDefinition,
    parse_resource_document,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from osa.control_plane.backend.repositories import (
        AgentRepository,
        ResourceDefinitionRepository,
    )
    from osa.control_plane.backend.resource_catalogs import ResourceCatalogs

API_VERSION = "osa/v1alpha1"


@dataclass(frozen=True)
class _KindBinding:
    kind: str
    model_cls: type[StrictModel]
    has: Callable[[ResourceCatalogs, str], bool]
    get: Callable[[ResourceCatalogs, str], Any]
    list: Callable[[ResourceCatalogs], list[Any]]
    register: Callable[[ResourceCatalogs, Any], None]
    delete: Callable[[ResourceCatalogs, str], bool]
    get_name: Callable[[Any], str]


_KIND_BINDINGS: dict[str, _KindBinding] = {
    binding.kind: binding
    for binding in (
        _KindBinding(
            kind="Model",
            model_cls=ModelDefinition,
            has=lambda c, n: c.has_model(n),
            get=lambda c, n: c.get_model(n),
            list=lambda c: c.list_models(),
            register=lambda c, d: c.register_model(d),
            delete=lambda c, n: c.delete_model(n),
            get_name=lambda d: d.name,
        ),
        _KindBinding(
            kind="Tool",
            model_cls=ToolDefinition,
            has=lambda c, n: c.has_tool(n),
            get=lambda c, n: c.get_tool(n),
            list=lambda c: c.list_tools(),
            register=lambda c, d: c.register_tool(d),
            delete=lambda c, n: c.delete_tool(n),
            get_name=lambda d: d.name,
        ),
        _KindBinding(
            kind="Skill",
            model_cls=SkillDefinition,
            has=lambda c, n: c.has_skill(n),
            get=lambda c, n: c.get_skill(n),
            list=lambda c: c.list_skills(),
            register=lambda c, d: c.register_skill(d),
            delete=lambda c, n: c.delete_skill(n),
            get_name=lambda d: d.name,
        ),
        _KindBinding(
            kind="Mcp",
            model_cls=McpDefinition,
            has=lambda c, n: c.has_mcp(n),
            get=lambda c, n: c.get_mcp(n),
            list=lambda c: c.list_mcps(),
            register=lambda c, d: c.register_mcp(d),
            delete=lambda c, n: c.delete_mcp(n),
            get_name=lambda d: d.name,
        ),
        _KindBinding(
            kind="MemoryPolicy",
            model_cls=MemoryPolicy,
            has=lambda c, n: c.has_memory_policy(n),
            get=lambda c, n: c.get_memory_policy(n),
            list=lambda c: c.list_memory_policies(),
            register=lambda c, d: c.register_memory_policy(d),
            delete=lambda c, n: c.delete_memory_policy(n),
            get_name=lambda d: d.name,
        ),
    )
}


class ResourceEnvelope(BaseModel):
    """A resource envelope: ``{apiVersion, kind, spec}``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: str = Field(default=API_VERSION, alias="apiVersion")
    kind: str
    spec: dict[str, Any]


class ResourceImportRequest(BaseModel):
    """A list of resource envelopes to import."""

    model_config = ConfigDict(extra="forbid")

    resources: list[dict[str, Any]] = Field(default_factory=list)


class ResourceBundleResponse(BaseModel):
    """Bundle-compatible envelopes (export result / import report)."""

    resources: list[dict[str, Any]] = Field(default_factory=list)
    imported: dict[str, list[str]] = Field(default_factory=dict)


class ResourceListResponse(BaseModel):
    """Envelope list for one resource kind."""

    kind: str
    total: int
    resources: list[dict[str, Any]]


class TemplateResponse(BaseModel):
    """A built-in agent template."""

    name: str
    description: str
    skills: list[str]
    memory_enabled: bool
    memory_policy: str | None


# --- helpers ---


def _binding(kind: str) -> _KindBinding:
    binding = _KIND_BINDINGS.get(kind)
    if binding is None:
        supported = ", ".join(sorted(_KIND_BINDINGS))
        raise HTTPException(status_code=404, detail=f"Unknown resource kind '{kind}'. Supported kinds: {supported}")
    return binding


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip credential material from serialized resources.

    Definitions never carry secret values; this guarantees only the
    non-secret reference coordinates (source/key/env_var) are exposed.
    """
    reference = payload.get("credential_ref")
    if isinstance(reference, dict):
        allowed = {"source", "key", "env_var"}
        for field_name in list(reference):
            if field_name not in allowed:
                del reference[field_name]
    return payload


def _serialize(definition: Any) -> dict[str, Any]:
    return _redact(definition.model_dump(mode="json", by_alias=True))


def _spec_name(kind: str, spec: dict[str, Any]) -> str:
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=422, detail=f"{kind} resource requires a non-empty 'name'")
    return name


def _validate_spec(binding: _KindBinding, spec: dict[str, Any], origin: str) -> StrictModel:
    try:
        return binding.model_cls.model_validate(spec)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid {binding.kind} resource '{origin}': {exc}") from exc


def _envelope(kind: str, definition: Any) -> dict[str, Any]:
    return {"apiVersion": API_VERSION, "kind": kind, "spec": _serialize(definition)}


async def _referencing_agents(
    agent_repository: AgentRepository,
    binding: _KindBinding,
    name: str,
    tenant_id: str | None,
) -> list[str]:
    """Names of agent records whose definitions reference this resource."""
    referencing: list[str] = []
    for record in await agent_repository.list_all():
        if record.tenant_id != tenant_id:
            continue
        definition = record.definition
        if definition is None:
            continue
        spec = definition.spec
        used = False
        if binding.kind == "Model":
            used = spec.model is not None and spec.model.ref == name
        elif binding.kind == "Tool":
            used = any(ref.ref == name for ref in spec.tools)
        elif binding.kind == "Skill":
            used = any(ref.ref == name for ref in spec.skills)
        elif binding.kind == "Mcp":
            used = any(ref.ref == name for ref in spec.mcps)
        elif binding.kind == "MemoryPolicy":
            used = spec.memory.enabled and spec.memory.policy == name
        if used:
            referencing.append(record.name)
    return referencing


def configure_resource_routes(
    app: FastAPI,
    *,
    agent_repository: AgentRepository,
    resource_catalogs: ResourceCatalogs,
    resource_repository: ResourceDefinitionRepository,
) -> FastAPI:
    """Attach resource and template routes to a Control Plane app."""

    def request_tenant(request: Request) -> str | None:
        principal = getattr(request.state, "osa_principal", None)
        return principal.tenant_id if isinstance(principal, AuthenticatedPrincipal) else None

    def scoped_catalogs(request: Request) -> ResourceCatalogs:
        return resource_catalogs.for_tenant(request_tenant(request))

    @app.post("/resources/import", response_model=ResourceBundleResponse)
    async def import_resources(http_request: Request, request: ResourceImportRequest) -> ResourceBundleResponse:
        """Import resource envelopes (same format as bundle resource files).

        Each resource is validated and written through to the repository and
        catalogs; already-existing resources are replaced.
        """
        imported: dict[str, list[str]] = {}
        envelopes: list[dict[str, Any]] = []
        tenant_id = request_tenant(http_request)
        catalogs = scoped_catalogs(http_request)
        for index, document in enumerate(request.resources):
            try:
                kind, definition = parse_resource_document(document, origin=f"resources[{index}]")
            except InvalidBundleError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            binding = _binding(kind)
            binding.register(catalogs, definition)
            serialized = _serialize(definition)
            name = binding.get_name(definition)
            await resource_repository.upsert(kind, name, serialized, tenant_id=tenant_id)
            imported.setdefault(kind, []).append(name)
            envelopes.append({"apiVersion": API_VERSION, "kind": kind, "spec": serialized})
        await record_audit_event(
            http_request,
            action="resource.import",
            target="resources",
            detail={"counts": {kind: len(names) for kind, names in imported.items()}},
        )
        return ResourceBundleResponse(resources=envelopes, imported=imported)

    @app.get("/resources/export", response_model=ResourceBundleResponse)
    async def export_resources(http_request: Request) -> ResourceBundleResponse:
        """Export every resource as bundle-compatible envelopes."""
        envelopes: list[dict[str, Any]] = []
        catalogs = scoped_catalogs(http_request)
        for binding in _KIND_BINDINGS.values():
            for definition in sorted(binding.list(catalogs), key=lambda d: d.name):
                envelopes.append(_envelope(binding.kind, definition))
        return ResourceBundleResponse(resources=envelopes)

    @app.get("/resources/{kind}", response_model=ResourceListResponse)
    async def list_resources(
        http_request: Request,
        kind: str,
        q: str | None = Query(default=None, description="Substring filter on resource name"),
    ) -> ResourceListResponse:
        """List resources of one kind (optionally filtered by name substring)."""
        binding = _binding(kind)
        definitions = binding.list(scoped_catalogs(http_request))
        if q is not None:
            needle = q.lower()
            definitions = [d for d in definitions if needle in d.name.lower()]
        definitions.sort(key=lambda d: d.name)
        return ResourceListResponse(
            kind=kind,
            total=len(definitions),
            resources=[_envelope(kind, d) for d in definitions],
        )

    @app.post("/resources/{kind}", response_model=dict[str, Any], status_code=201)
    async def create_resource(http_request: Request, kind: str, request: ResourceEnvelope) -> dict[str, Any]:
        """Create a resource; a name that already exists is a conflict."""
        binding = _binding(kind)
        name = _spec_name(binding.kind, request.spec)
        catalogs = scoped_catalogs(http_request)
        tenant_id = request_tenant(http_request)
        if binding.has(catalogs, name):
            raise HTTPException(status_code=409, detail=f"{binding.kind} '{name}' already exists")
        definition = _validate_spec(binding, request.spec, name)
        binding.register(catalogs, definition)
        serialized = _serialize(definition)
        await resource_repository.upsert(kind, name, serialized, tenant_id=tenant_id)
        await record_audit_event(http_request, action="resource.create", target=f"{binding.kind}/{name}")
        return {"apiVersion": API_VERSION, "kind": kind, "spec": serialized}

    @app.get("/resources/{kind}/{name}", response_model=dict[str, Any])
    async def get_resource(http_request: Request, kind: str, name: str) -> dict[str, Any]:
        """Get one resource."""
        binding = _binding(kind)
        catalogs = scoped_catalogs(http_request)
        if not binding.has(catalogs, name):
            raise HTTPException(status_code=404, detail=f"{binding.kind} not found: {name}")
        return _envelope(kind, binding.get(catalogs, name))

    @app.put("/resources/{kind}/{name}", response_model=dict[str, Any])
    async def replace_resource(
        http_request: Request, kind: str, name: str, request: ResourceEnvelope
    ) -> dict[str, Any]:
        """Replace a resource (must already exist; spec.name must match the path)."""
        binding = _binding(kind)
        catalogs = scoped_catalogs(http_request)
        tenant_id = request_tenant(http_request)
        if not binding.has(catalogs, name):
            raise HTTPException(status_code=404, detail=f"{binding.kind} not found: {name}")
        spec_name = _spec_name(binding.kind, request.spec)
        if spec_name != name:
            raise HTTPException(
                status_code=422,
                detail=f"spec.name '{spec_name}' does not match resource name '{name}'",
            )
        definition = _validate_spec(binding, request.spec, name)
        binding.register(catalogs, definition)
        serialized = _serialize(definition)
        await resource_repository.upsert(kind, name, serialized, tenant_id=tenant_id)
        await record_audit_event(http_request, action="resource.replace", target=f"{binding.kind}/{name}")
        return {"apiVersion": API_VERSION, "kind": kind, "spec": serialized}

    @app.delete("/resources/{kind}/{name}", status_code=204)
    async def delete_resource(http_request: Request, kind: str, name: str) -> None:
        """Delete a resource; referenced resources cannot be deleted."""
        binding = _binding(kind)
        catalogs = scoped_catalogs(http_request)
        tenant_id = request_tenant(http_request)
        if not binding.has(catalogs, name):
            raise HTTPException(status_code=404, detail=f"{binding.kind} not found: {name}")
        referencing = await _referencing_agents(agent_repository, binding, name, tenant_id)
        if referencing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{binding.kind} '{name}' is referenced by agents: "
                    + ", ".join(sorted(referencing))
                    + "; update or delete those agents first"
                ),
            )
        if not binding.delete(catalogs, name):
            raise HTTPException(status_code=404, detail=f"{binding.kind} not found: {name}")
        await resource_repository.delete(kind, name, tenant_id=tenant_id)
        await record_audit_event(http_request, action="resource.delete", target=f"{binding.kind}/{name}")

    @app.get("/templates", response_model=list[TemplateResponse])
    async def list_templates() -> list[TemplateResponse]:
        """List built-in agent templates (read-only release artifacts)."""
        templates = app.state.template_catalog.list_templates()
        return [
            TemplateResponse(
                name=template.name,
                description=template.description,
                skills=list(template.default_skills),
                memory_enabled=template.default_memory_enabled,
                memory_policy=template.default_memory_policy,
            )
            for template in templates
        ]

    return app


__all__ = ["configure_resource_routes"]
