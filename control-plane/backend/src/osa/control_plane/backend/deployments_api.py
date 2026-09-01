"""Control Plane deployment APIs (P1.5).

Routes for deploying versioned agents through a ``DeploymentProvider``:

- ``POST /agents/{agent_id}/deploy`` — export the agent's definition +
  referenced resources to a bundle and launch a runtime via the server-owned
  command template. Requests never carry process commands.
- ``GET /deployments/{deployment_id}`` — observed status (persisted).
- ``GET /agents/{agent_id}/deployments`` — deployment history for an agent.
- ``POST /deployments/{deployment_id}/stop`` / ``/restart`` — lifecycle.
- ``GET /deployments/{deployment_id}/logs?tail=n`` — bounded captured logs.
- ``POST /deployments/{deployment_id}/rollback?version=`` — relaunch from an
  earlier immutable version snapshot.

Every transition persists through the ``DeploymentRecordRepository``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from osa.control_plane.backend.audit import record_audit_event
from osa.control_plane.backend.deployment_service import DeploymentError, DeploymentService
from osa.generic_agent import AuthenticatedPrincipal, log_event

logger = logging.getLogger(__name__)


class DeployRequest(BaseModel):
    """Deployment intent.

    Deliberately carries no command, image, or host fields: launch commands
    are synthesized server-side from a trusted template.
    """

    model_config = ConfigDict(extra="forbid")


class DeploymentResponse(BaseModel):
    """Persisted deployment intent and observed state."""

    deployment_id: str
    agent_id: str
    agent_name: str
    tenant_id: str | None
    version: str
    status: str
    detail: str


class DeploymentLogsResponse(BaseModel):
    """Bounded captured logs for a deployment."""

    deployment_id: str
    lines: list[str]


def _response(record: Any) -> DeploymentResponse:
    return DeploymentResponse(
        deployment_id=record.deployment_id,
        agent_id=record.agent_id,
        agent_name=record.agent_name,
        tenant_id=record.tenant_id,
        version=record.version,
        status=record.status,
        detail=record.detail,
    )


def configure_deployment_routes(app: FastAPI) -> FastAPI:
    """Attach deployment routes (requires app.state.deployment_service)."""

    def tenant_id(request: Request) -> str | None:
        principal = getattr(request.state, "osa_principal", None)
        return principal.tenant_id if isinstance(principal, AuthenticatedPrincipal) else None

    async def require_agent(request: Request, agent_id: str) -> None:
        agent = await app.state.agent_repository.get(agent_id)
        if agent is None or agent.tenant_id != tenant_id(request):
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    async def require_deployment(request: Request, deployment_id: str) -> None:
        service: DeploymentService = app.state.deployment_service
        record = await service.get_record(deployment_id)
        if record is None or record.tenant_id != tenant_id(request):
            raise HTTPException(status_code=404, detail=f"Deployment not found: {deployment_id}")

    @app.post("/agents/{agent_id}/deploy", response_model=DeploymentResponse, status_code=201)
    async def deploy_agent(http_request: Request, agent_id: str, request: DeployRequest) -> DeploymentResponse:
        """Deploy the agent's current definition (must be active)."""
        service: DeploymentService = app.state.deployment_service
        await require_agent(http_request, agent_id)
        try:
            record = await service.deploy(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        except DeploymentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_audit_event(
            http_request,
            action="deployment.deploy",
            target=record.deployment_id,
            detail={"agent_id": record.agent_id, "version": record.version},
        )
        log_event(
            logger,
            logging.INFO,
            "deployment started",
            {
                "deployment_id": record.deployment_id,
                "agent_id": record.agent_id,
                "version": record.version,
                "status": record.status,
            },
        )
        return _response(record)

    @app.get("/agents/{agent_id}/deployments", response_model=list[DeploymentResponse])
    async def list_agent_deployments(http_request: Request, agent_id: str) -> list[DeploymentResponse]:
        """Deployment history for an agent."""
        service: DeploymentService = app.state.deployment_service
        await require_agent(http_request, agent_id)
        records = await service.list_for_agent(agent_id)
        return [_response(r) for r in records]

    @app.get("/deployments/{deployment_id}", response_model=DeploymentResponse)
    async def get_deployment(http_request: Request, deployment_id: str) -> DeploymentResponse:
        """Observed status of a deployment."""
        service: DeploymentService = app.state.deployment_service
        await require_deployment(http_request, deployment_id)
        try:
            record = await service.status(deployment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        await record_audit_event(http_request, action="deployment.stop", target=deployment_id)
        log_event(
            logger, logging.INFO, "deployment status checked", {"deployment_id": deployment_id, "status": record.status}
        )
        return _response(record)

    @app.post("/deployments/{deployment_id}/stop", response_model=DeploymentResponse)
    async def stop_deployment(http_request: Request, deployment_id: str) -> DeploymentResponse:
        """Stop a deployment."""
        service: DeploymentService = app.state.deployment_service
        await require_deployment(http_request, deployment_id)
        try:
            record = await service.stop(deployment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        await record_audit_event(http_request, action="deployment.restart", target=deployment_id)
        log_event(logger, logging.INFO, "deployment stopped", {"deployment_id": deployment_id, "status": record.status})
        return _response(record)

    @app.post("/deployments/{deployment_id}/restart", response_model=DeploymentResponse)
    async def restart_deployment(http_request: Request, deployment_id: str) -> DeploymentResponse:
        """Restart a deployment (same identity, fresh process)."""
        service: DeploymentService = app.state.deployment_service
        await require_deployment(http_request, deployment_id)
        try:
            record = await service.restart(deployment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        log_event(
            logger, logging.INFO, "deployment restarted", {"deployment_id": deployment_id, "status": record.status}
        )
        return _response(record)

    @app.get("/deployments/{deployment_id}/logs", response_model=DeploymentLogsResponse)
    async def deployment_logs(
        http_request: Request,
        deployment_id: str,
        tail: int = Query(default=200, ge=1, le=1000),
    ) -> DeploymentLogsResponse:
        """Captured logs for a deployment (bounded, newest last)."""
        service: DeploymentService = app.state.deployment_service
        await require_deployment(http_request, deployment_id)
        try:
            lines = await service.logs(deployment_id, tail)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        log_event(
            logger,
            logging.INFO,
            "deployment logs requested",
            {"deployment_id": deployment_id, "line_count": len(lines)},
        )
        return DeploymentLogsResponse(deployment_id=deployment_id, lines=lines)

    @app.post("/deployments/{deployment_id}/rollback", response_model=DeploymentResponse)
    async def rollback_deployment(
        http_request: Request,
        deployment_id: str,
        version: str | None = Query(default=None, description="Target version (default: previous)"),
    ) -> DeploymentResponse:
        """Relaunch a deployment from an earlier immutable version snapshot."""
        service: DeploymentService = app.state.deployment_service
        await require_deployment(http_request, deployment_id)
        try:
            record = await service.rollback(deployment_id, version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        except DeploymentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_audit_event(
            http_request,
            action="deployment.rollback",
            target=deployment_id,
            detail={"version": record.version},
        )
        log_event(
            logger,
            logging.INFO,
            "deployment rolled back",
            {"deployment_id": deployment_id, "version": record.version, "status": record.status},
        )
        return _response(record)

    return app


__all__ = ["configure_deployment_routes"]
