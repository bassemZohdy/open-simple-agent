"""Agent Runtime HTTP API — endpoints for invoking agents.

This API runs alongside the agent runtime, independent of the Control Plane.

Two ways to serve it:

- Programmatic: call :func:`initialize_runtime` with an ``AgentDefinition``
  and serve :data:`runtime_app` (used by tests and embedders).
- Service: :func:`osa.runtimes.adk.service.create_runtime_app` bootstraps
  from a deployment bundle during startup, or run the ``osa-runtime`` CLI.

Error responses use the stable OSA schema ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from osa.generic_agent import (
    AgentDefinition,
    AgentRequest,
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthMode,
    AuthorizationError,
    AuthorizationPolicy,
    AuthSettings,
    FakeModelProvider,
    JwtAuthenticator,
    ModelCatalog,
    ModelDefinition,
    OsaError,
    SecretResolver,
    SessionAccessError,
    SessionError,
    SessionNotFoundError,
    error_payload,
)
from osa.runtimes.adk import AdkRuntime, GenericAdkAgent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.responses import Response


def _package_version() -> str:
    try:
        return metadata.version("osa-adk-runtime")
    except metadata.PackageNotFoundError:
        return "0"


# Runtime state (single agent per process, matching the deployment-bundle model)
_runtime: AdkRuntime | None = None
_agent: GenericAdkAgent | None = None
_start_error: str | None = None


class InvokeRequest(BaseModel):
    """Request to invoke an agent."""

    model_config = ConfigDict(extra="forbid")

    input: str
    session_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class InvokeResponse(BaseModel):
    """Response from an agent invocation."""

    output: str
    invocation_id: str
    session_id: str | None = None
    error: str | None = None


class CapabilitiesResponse(BaseModel):
    """Agent capabilities."""

    agent_name: str
    version: str
    streaming: bool = False
    session_support: bool = True
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


async def initialize_runtime(
    definition: AgentDefinition,
    *,
    secret_resolver: SecretResolver | None = None,
) -> GenericAdkAgent:
    """Initialize the runtime with an agent definition.

    Deterministic bootstrap used by tests and programmatic embedding: a fake
    default model plus the injected provider. Production deployments bootstrap
    from a bundle via the service factory instead.
    """
    model_catalog = ModelCatalog()
    model_catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))

    runtime = AdkRuntime(
        model_provider=FakeModelProvider(response="I'm a runtime agent. How can I help?"),
        model_catalog=model_catalog,
    )
    agent = await runtime.create(definition)
    maybe_attach_a2a(agent)
    set_runtime(runtime, agent)
    return agent


def maybe_attach_a2a(agent: GenericAdkAgent) -> None:
    """Attach A2A routes when the agent definition enables A2A (ADR-005).

    The public URL comes from ``OSA_A2A_URL`` (default localhost). Requires
    the ``a2a`` extra; failures raise before readiness.
    """
    import os

    if not agent.definition.spec.a2a.enabled:
        return
    from osa.runtimes.adk.a2a import attach_a2a_routes

    url = os.environ.get("OSA_A2A_URL", "http://localhost:8080/")
    attach_a2a_routes(runtime_app, agent, url)


def reset_runtime() -> None:
    """Clear runtime state (used by tests and lifespan shutdown)."""
    global _runtime, _agent, _start_error
    _runtime = None
    _agent = None
    _start_error = None


def set_start_error(message: str) -> None:
    """Record a startup failure so readiness reports the real cause."""
    global _start_error
    _start_error = message


def get_agent() -> GenericAdkAgent | None:
    """The initialized agent, if any."""
    return _agent


def set_runtime(runtime: AdkRuntime, agent: GenericAdkAgent) -> None:
    """Install runtime state created by an external bootstrap."""
    global _runtime, _agent, _start_error
    _runtime = runtime
    _agent = agent
    _start_error = None


def _install_authentication(
    app: FastAPI,
    settings: AuthSettings,
    authenticator: JwtAuthenticator | None,
) -> None:
    app.state.auth_settings = settings
    if settings.mode is AuthMode.DISABLED:
        return
    token_authenticator = authenticator or JwtAuthenticator(settings)
    authorization_policy = AuthorizationPolicy(enabled=settings.enforce_permissions)
    app.state.authenticator = token_authenticator
    app.state.authorization_policy = authorization_policy
    public_paths = {"/health/live", "/health/ready", "/docs", "/redoc", "/openapi.json"}

    @app.middleware("http")
    async def _authenticate_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in public_paths:
            return await call_next(request)
        required_permission = (
            authorization_policy.permission_for_request(request.url.path, request.method)
            if settings.enforce_permissions
            else None
        )
        if (
            settings.mode is AuthMode.OPTIONAL
            and "authorization" not in request.headers
            and required_permission is None
        ):
            return await call_next(request)
        try:
            principal = await token_authenticator.authenticate(request.headers.get("authorization"))
            if required_permission is not None:
                authorization_policy.require(principal, required_permission)
        except AuthenticationError as exc:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content=error_payload(exc.code, str(exc)),
            )
        except AuthorizationError as exc:
            return JSONResponse(status_code=403, content=error_payload(exc.code, str(exc)))
        request.state.osa_principal = principal
        return await call_next(request)


def configure_runtime_app(
    app: FastAPI,
    *,
    auth_settings: AuthSettings | None = None,
    authenticator: JwtAuthenticator | None = None,
) -> FastAPI:
    """Attach the runtime routes and error mapping to a FastAPI app.

    Used both by the module-level ``runtime_app`` and by the service factory
    (``osa.runtimes.adk.service.create_runtime_app``), which supplies its own
    bundle-driven lifespan.
    """

    settings = auth_settings or AuthSettings.from_env()
    _install_authentication(app, settings, authenticator)

    @app.exception_handler(AuthorizationError)
    async def _authorization_error_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(status_code=403, content=error_payload(exc.code, str(exc)))

    @app.exception_handler(SessionNotFoundError)
    async def _session_not_found_handler(request: Request, exc: SessionNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=error_payload(exc.code, str(exc)))

    @app.exception_handler(SessionAccessError)
    async def _session_access_handler(request: Request, exc: SessionAccessError) -> JSONResponse:
        return JSONResponse(status_code=403, content=error_payload(exc.code, str(exc)))

    @app.exception_handler(SessionError)
    async def _session_error_handler(request: Request, exc: SessionError) -> JSONResponse:
        return JSONResponse(status_code=400, content=error_payload(exc.code, str(exc)))

    @app.exception_handler(OsaError)
    async def _osa_error_handler(request: Request, exc: OsaError) -> JSONResponse:
        return JSONResponse(status_code=502, content=error_payload(exc.code, str(exc)))

    @app.post("/v1/invoke", response_model=InvokeResponse)
    async def invoke_agent(http_request: Request, request: InvokeRequest) -> Any:
        """Invoke the agent."""
        if _agent is None:
            return JSONResponse(status_code=503, content=error_payload("not_initialized", "Agent not initialized"))

        principal = getattr(http_request.state, "osa_principal", None)
        user_id = request.user_id
        request_metadata = dict(request.metadata)
        if isinstance(principal, AuthenticatedPrincipal):
            if user_id is None:
                user_id = principal.subject
            elif user_id != principal.subject:
                raise AuthorizationError("Request user_id must match the authenticated subject")
            request_metadata.setdefault("caller_subject", principal.subject)
            requested_tenant = request_metadata.get("tenant_id")
            if principal.tenant_id is None and requested_tenant is not None:
                raise AuthorizationError("Request tenant_id requires an authenticated tenant claim")
            if principal.tenant_id is not None:
                if requested_tenant is None:
                    request_metadata["tenant_id"] = principal.tenant_id
                elif requested_tenant != principal.tenant_id:
                    raise AuthorizationError("Request tenant_id must match the authenticated tenant")

        agent_request = AgentRequest(
            input=request.input,
            session_id=request.session_id,
            user_id=user_id,
            metadata=request_metadata,
        )

        response = await _agent.invoke(agent_request)

        return InvokeResponse(
            output=response.output,
            invocation_id=str(response.invocation_id),
            session_id=response.session_id,
            error=response.error,
        )

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        """Liveness check."""
        return {"status": "alive"}

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        """Readiness reflects successful agent initialization."""
        if _agent is None:
            message = _start_error or "Agent not initialized"
            return JSONResponse(status_code=503, content=error_payload("not_initialized", message))
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.get("/v1/capabilities", response_model=CapabilitiesResponse)
    async def get_capabilities() -> Any:
        """Get agent capabilities."""
        if _agent is None:
            return JSONResponse(status_code=503, content=error_payload("not_initialized", "Agent not initialized"))

        return CapabilitiesResponse(
            agent_name=_agent.metadata.name,
            version=_agent.metadata.version,
            streaming=False,
            session_support=True,
            tools=[t.name for t in (_runtime.tool_catalog.list_tools() if _runtime else [])],
            skills=[s.ref for s in _agent.definition.spec.skills],
        )

    return app


runtime_app = configure_runtime_app(FastAPI(title="Open Simple Agent Runtime", version=_package_version()))
