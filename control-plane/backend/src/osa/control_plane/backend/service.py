"""Control Plane service assembly (ADR-004).

``create_control_plane_app`` selects repositories from external
configuration: ``OSA_CONTROL_PLANE_DATABASE_URL`` (or an explicit DSN)
selects the PostgreSQL repositories; without it the app runs in-memory.
Persisted resource definitions are materialized into the in-memory resource
catalogs at startup so route-level validation keeps working unchanged.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib import metadata
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from osa.control_plane.backend.api import configure_control_plane_app
from osa.control_plane.backend.db import create_db_engine, database_url_from_env
from osa.control_plane.backend.repositories import (
    AgentRepository,
    InMemoryAgentRepository,
    InMemoryAuditEventRepository,
    InMemoryResourceDefinitionRepository,
    PostgresAgentRepository,
    PostgresAuditEventRepository,
    PostgresResourceDefinitionRepository,
    ResourceDefinitionRepository,
)
from osa.control_plane.backend.resource_catalogs import ResourceCatalogs
from osa.control_plane.backend.templates import create_default_template_catalog
from osa.generic_agent import McpDefinition, MemoryPolicy, ModelDefinition, SkillDefinition, ToolDefinition

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Resource kinds persisted via the resource definition repository, mapped to
# their domain model and catalog registration.
_RESOURCE_KINDS: dict[str, tuple[Any, Any]] = {
    "Model": (ModelDefinition, lambda catalogs, d: catalogs.models.register(d)),
    "Tool": (ToolDefinition, lambda catalogs, d: catalogs.tools.register_definition(d)),
    "Skill": (SkillDefinition, lambda catalogs, d: catalogs.skills.register(d)),
    "Mcp": (McpDefinition, lambda catalogs, d: catalogs.mcps.register(d)),
    "MemoryPolicy": (MemoryPolicy, lambda catalogs, d: catalogs.register_memory_policy(d)),
}


def _app_version() -> str:
    try:
        return metadata.version("osa-control-plane")
    except metadata.PackageNotFoundError:
        return "0"


async def _materialize_resources(resource_catalogs: ResourceCatalogs, repository: ResourceDefinitionRepository) -> None:
    """Load persisted resource definitions into tenant-scoped catalogs."""
    for kind, (model_cls, register) in _RESOURCE_KINDS.items():
        records = await repository.list_all(kind)
        for tenant_id, spec in records:
            register(resource_catalogs.for_tenant(tenant_id), model_cls.model_validate(spec))


def create_control_plane_app(
    *,
    database_url: str | None = None,
    agent_repository: AgentRepository | None = None,
    resource_repository: ResourceDefinitionRepository | None = None,
) -> FastAPI:
    """Build the Control Plane API app.

    With a DSN (explicit or ``OSA_CONTROL_PLANE_DATABASE_URL``) the app uses
    the PostgreSQL repositories; otherwise it runs in-memory. Persisted
    resource definitions are materialized at startup; agent records are read
    per request.
    """
    dsn = database_url if database_url is not None else database_url_from_env()
    engine: Any = None
    deployment_records: Any = None
    audit_repository: Any
    if dsn:
        engine = create_db_engine(dsn)
        agents: AgentRepository = PostgresAgentRepository(engine)
        resources: ResourceDefinitionRepository = PostgresResourceDefinitionRepository(engine)
        audit_repository = PostgresAuditEventRepository(engine)
    else:
        agents = agent_repository if agent_repository is not None else InMemoryAgentRepository()
        resources = resource_repository if resource_repository is not None else InMemoryResourceDefinitionRepository()
        audit_repository = InMemoryAuditEventRepository()

    resource_catalogs = ResourceCatalogs()

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        await _materialize_resources(resource_catalogs, resources)
        try:
            yield
        finally:
            await agents.close()
            await resources.close()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(title="Open Simple Agent Control Plane", version=_app_version(), lifespan=lifespan)
    configured = configure_control_plane_app(
        app,
        agent_repository=agents,
        resource_catalogs=resource_catalogs,
        template_catalog=create_default_template_catalog(),
        resource_repository=resources,
        audit_repository=audit_repository,
    )
    if deployment_records is not None:
        from osa.control_plane.backend.deployment import LocalDeploymentProvider
        from osa.control_plane.backend.deployment_service import DeploymentService

        configured.state.deployment_service = DeploymentService(
            provider=LocalDeploymentProvider(),
            record_repository=deployment_records,
            agent_repository=agents,
            resource_catalogs=resource_catalogs,
        )
    return configured
