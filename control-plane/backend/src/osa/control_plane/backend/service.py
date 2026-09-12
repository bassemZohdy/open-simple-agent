"""Control Plane service assembly (ADR-004).

``create_control_plane_app`` selects repositories from external
configuration: ``OSA_CONTROL_PLANE_DATABASE_URL`` (or an explicit DSN)
selects the PostgreSQL or local SQLite repositories; without it the app runs
in-memory.
Persisted resource definitions are materialized into the in-memory resource
catalogs at startup so route-level validation keeps working unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from importlib import metadata
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from osa.control_plane.backend.api import configure_control_plane_app
from osa.control_plane.backend.db import create_db_engine, database_backend, database_url_from_env
from osa.control_plane.backend.repositories import (
    AgentRepository,
    InMemoryAgentRepository,
    InMemoryAuditEventRepository,
    InMemoryResourceDefinitionRepository,
    PostgresAgentRepository,
    PostgresAuditEventRepository,
    PostgresDeploymentRecordRepository,
    PostgresResourceDefinitionRepository,
    ResourceDefinitionRepository,
    SqliteAgentRepository,
    SqliteAuditEventRepository,
    SqliteDeploymentRecordRepository,
    SqliteResourceDefinitionRepository,
)
from osa.control_plane.backend.resource_catalogs import ResourceCatalogs
from osa.control_plane.backend.templates import create_default_template_catalog
from osa.generic_agent import (
    McpDefinition,
    MemoryPolicy,
    ModelDefinition,
    Observability,
    PersistenceConfigurationError,
    PersistencePolicy,
    SecretResolver,
    SkillDefinition,
    ToolDefinition,
    close_rate_limit_limiter,
    get_persistence_policy,
    initialize_rate_limit_limiter,
    require_shared_database,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)
DEPLOYMENT_RECONCILE_INTERVAL_SECONDS = 15.0

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
    secret_resolver: SecretResolver | None = None,
    observability: Observability | None = None,
) -> FastAPI:
    """Build the Control Plane API app.

    With a DSN (explicit or ``OSA_CONTROL_PLANE_DATABASE_URL``) the app uses
    PostgreSQL or local SQLite repositories; otherwise it runs in-memory. Persisted
    resource definitions are materialized at startup; agent records are read
    per request.
    """
    dsn = database_url if database_url is not None else database_url_from_env()
    persistence_policy = get_persistence_policy()
    if persistence_policy == PersistencePolicy.SHARED and (dsn is None or not dsn.strip()):
        require_shared_database(None, "Control Plane state")
    engine: Any = None
    backend: str | None = None
    deployment_records: Any
    audit_repository: Any
    agents: AgentRepository
    resources: ResourceDefinitionRepository
    if dsn is not None:
        backend = database_backend(dsn)
        require_shared_database(dsn, "Control Plane state")
        if (
            persistence_policy == PersistencePolicy.SHARED
            and os.environ.get("OSA_DEPLOY_PROVIDER", "").strip().lower() != "kubernetes"
        ):
            raise PersistenceConfigurationError(
                "OSA_PERSISTENCE_POLICY=shared requires the Kubernetes deployment provider "
                "(OSA_DEPLOY_PROVIDER=kubernetes) for the Control Plane"
            )
        if backend == "sqlite" and os.environ.get("OSA_DEPLOY_PROVIDER", "").strip().lower() == "kubernetes":
            from osa.control_plane.backend.agent_catalog import AgentCatalogError

            raise AgentCatalogError(
                "SQLite persistence is local-only and cannot be combined with "
                "OSA_DEPLOY_PROVIDER=kubernetes or a shared deployment"
            )
        engine = create_db_engine(dsn)
        if backend == "postgresql":
            agents = PostgresAgentRepository(engine)
            resources = PostgresResourceDefinitionRepository(engine)
            audit_repository = PostgresAuditEventRepository(engine)
            # Deployment intent must be as durable as agent records (ADR-004):
            # without this, multi-replica Control Planes cannot see each other's
            # deployments and history is lost on restart.
            deployment_records = PostgresDeploymentRecordRepository(engine)
        else:
            agents = SqliteAgentRepository(engine)
            resources = SqliteResourceDefinitionRepository(engine)
            audit_repository = SqliteAuditEventRepository(engine)
            deployment_records = SqliteDeploymentRecordRepository(engine)
    else:
        agents = agent_repository if agent_repository is not None else InMemoryAgentRepository()
        resources = resource_repository if resource_repository is not None else InMemoryResourceDefinitionRepository()
        audit_repository = InMemoryAuditEventRepository()
        deployment_records = None

    resource_catalogs = ResourceCatalogs()

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        if backend == "sqlite":
            from osa.control_plane.backend.sqlite_migrations import ensure_sqlite_schema

            await ensure_sqlite_schema(engine)
        await _materialize_resources(resource_catalogs, resources)
        await initialize_rate_limit_limiter(fastapi_app)
        deployment_service = getattr(fastapi_app.state, "deployment_service", None)
        reconcile: Callable[[], Awaitable[object]] | None = getattr(
            deployment_service, "reconcile_provider_state", None
        )
        if reconcile is not None:
            with contextlib.suppress(Exception):
                await reconcile()

        async def watch_deployments(callback: Callable[[], Awaitable[object]]) -> None:
            while True:
                await asyncio.sleep(DEPLOYMENT_RECONCILE_INTERVAL_SECONDS)
                try:
                    await callback()
                except Exception:  # noqa: BLE001 - the watcher must survive provider outages
                    logger.warning("deployment provider reconciliation failed", exc_info=True)

        reconcile_task: asyncio.Task[None] | None = None
        if reconcile is not None:
            reconcile_task = asyncio.create_task(watch_deployments(reconcile))
        try:
            yield
        finally:
            if reconcile_task is not None:
                reconcile_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reconcile_task
            provider = getattr(deployment_service, "_provider", None)
            shutdown = getattr(provider, "shutdown", None)
            if shutdown is not None:
                await shutdown()
            await agents.close()
            await resources.close()
            await close_rate_limit_limiter(fastapi_app)
            if engine is not None:
                await engine.dispose()

    app = FastAPI(title="Open Simple Agent Control Plane", version=_app_version(), lifespan=lifespan)
    configured = configure_control_plane_app(
        app,
        agent_repository=agents,
        resource_catalogs=resource_catalogs,
        template_catalog=create_default_template_catalog(),
        resource_repository=resources,
        secret_resolver=secret_resolver,
        observability=observability,
        audit_repository=audit_repository,
    )
    if dsn is not None:
        if backend == "sqlite":
            from osa.control_plane.backend.external_agents import SqliteExternalAgentRepository

            configured.state.external_agent_repository = SqliteExternalAgentRepository(engine)
        else:
            from osa.control_plane.backend.external_agents import PostgresExternalAgentRepository

            configured.state.external_agent_repository = PostgresExternalAgentRepository(engine)
    if deployment_records is not None:
        from osa.control_plane.backend.deployment_service import DeploymentService, create_deployment_provider

        configured.state.deployment_service = DeploymentService(
            provider=create_deployment_provider(require_shared=backend == "postgresql"),
            record_repository=deployment_records,
            agent_repository=agents,
            resource_catalogs=resource_catalogs,
            resource_repository=resources,
        )
    return configured
