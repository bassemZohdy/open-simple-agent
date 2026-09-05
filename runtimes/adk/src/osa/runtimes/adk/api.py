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

import json
import logging
import os
from importlib import metadata
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from osa.generic_agent import (
    AgentDefinition,
    AgentRequest,
    AuditEvent,
    AuditEventSink,
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthMode,
    AuthorizationError,
    AuthorizationPolicy,
    AuthSettings,
    EnvironmentSecretResolver,
    FakeModelProvider,
    InMemoryAuditEventSink,
    JwtAuthenticator,
    ModelCatalog,
    ModelDefinition,
    Observability,
    OsaError,
    PolicyViolationError,
    SecretResolver,
    SessionAccessError,
    SessionError,
    SessionNotFoundError,
    configure_structured_logging,
    error_payload,
    log_context,
    log_event,
    reset_current_principal,
    set_current_principal,
)
from osa.runtimes.adk import AdkRuntime, GenericAdkAgent

logger = logging.getLogger(__name__)

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
    streaming: bool = True
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


async def _record_runtime_audit(
    request: Request,
    *,
    action: str,
    target: str,
    decision: str,
    status_code: int,
    error_code: str | None = None,
) -> None:
    """Emit a boundary event without request or response payloads."""
    sink: AuditEventSink | None = getattr(request.app.state, "audit_sink", None)
    if sink is None:
        return
    principal = getattr(request.state, "osa_principal", None)
    actor = principal.subject if isinstance(principal, AuthenticatedPrincipal) else "anonymous"
    tenant_id = principal.tenant_id if isinstance(principal, AuthenticatedPrincipal) else None
    detail: dict[str, Any] = {"decision": decision, "status_code": status_code, "method": request.method}
    if error_code is not None:
        detail["error_code"] = error_code
    await sink.append(AuditEvent(actor=actor, action=action, target=target, tenant_id=tenant_id, detail=detail))


def maybe_attach_a2a(
    agent: GenericAdkAgent,
    *,
    app: FastAPI | None = None,
    auth_settings: AuthSettings | None = None,
) -> None:
    """Attach A2A routes when the agent definition enables A2A (ADR-005).

    The public URL comes from ``OSA_A2A_URL`` (default localhost). Requires
    the ``a2a`` extra; failures raise before readiness.
    """
    import os

    if not agent.definition.spec.a2a.enabled:
        return
    if not agent.definition.spec.policy.a2a.permits("inbound"):
        raise PolicyViolationError("a2a", "inbound")
    from osa.runtimes.adk.a2a import attach_a2a_routes

    target_app = app or runtime_app
    settings = auth_settings
    if settings is None:
        settings = getattr(target_app.state, "auth_settings", None)
    if settings is None:
        settings = AuthSettings.from_env()
    url = os.environ.get("OSA_A2A_URL", "http://localhost:8080/")
    attach_a2a_routes(target_app, agent, url, auth_settings=settings)


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
    secret_resolver: SecretResolver | None,
) -> None:
    app.state.auth_settings = settings
    if settings.mode is AuthMode.DISABLED:
        return
    token_authenticator = authenticator or JwtAuthenticator(
        settings,
        secret_resolver=secret_resolver or EnvironmentSecretResolver(),
    )
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
        principal_token = set_current_principal(principal)
        try:
            return await call_next(request)
        finally:
            reset_current_principal(principal_token)


CORS_ORIGINS_ENV_VAR = "OSA_RUNTIME_ALLOWED_ORIGINS"


def _allowed_origins_from_env(environ: Mapping[str, str] | None = None) -> list[str]:
    """Parse the opt-in browser origins allowed to call this runtime.

    Unset or empty means no CORS support: browsers cannot call the runtime
    cross-origin (server-to-server callers are unaffected).
    """
    env = os.environ if environ is None else environ
    raw = env.get(CORS_ORIGINS_ENV_VAR, "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def configure_runtime_app(
    app: FastAPI,
    *,
    auth_settings: AuthSettings | None = None,
    authenticator: JwtAuthenticator | None = None,
    audit_sink: AuditEventSink | None = None,
    secret_resolver: SecretResolver | None = None,
    observability: Observability | None = None,
) -> FastAPI:
    """Attach the runtime routes and error mapping to a FastAPI app.

    Used both by the module-level ``runtime_app`` and by the service factory
    (``osa.runtimes.adk.service.create_runtime_app``), which supplies its own
    bundle-driven lifespan.
    """

    settings = auth_settings or AuthSettings.from_env()
    _install_authentication(app, settings, authenticator, secret_resolver)
    allowed_origins = _allowed_origins_from_env()
    if allowed_origins:
        # ADR-008: browser clients (e.g. the Control Panel) invoke the runtime
        # directly at its public endpoint. Added last so CORS runs outermost
        # and preflight OPTIONS requests bypass the bearer boundary.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )
    if audit_sink is None:
        audit_sink = InMemoryAuditEventSink()
    app.state.audit_sink = audit_sink
    app.state.observability = observability or Observability()
    configure_structured_logging()

    @app.middleware("http")
    async def _observe_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        raw_request_id = request.headers.get("x-request-id", "")
        request_id = raw_request_id if _valid_request_id(raw_request_id) else str(uuid4())
        request.state.request_id = request_id
        observation: Observability = app.state.observability
        operation = "a2a" if request.url.path == "/a2a" else "http"
        with log_context({"request_id": request_id, "surface": "runtime"}):
            try:
                async with observation.span(
                    operation,
                    labels={"surface": "runtime", "route": request.url.path},
                    attributes={"http.method": request.method, "http.route": request.url.path},
                ):
                    response = await call_next(request)
            except Exception as exc:
                observation.metrics.increment(
                    "osa_http_requests_total",
                    {"surface": "runtime", "route": request.url.path, "status": 500},
                )
                log_event(
                    logger,
                    logging.ERROR,
                    "runtime request failed",
                    {"error_code": getattr(exc, "code", "internal_error")},
                )
                raise
            observation.metrics.increment(
                "osa_http_requests_total",
                {"surface": "runtime", "route": request.url.path, "status": response.status_code},
            )
            response.headers["X-Request-ID"] = request_id
            principal = getattr(request.state, "osa_principal", None)
            log_event(
                logger,
                logging.INFO,
                "runtime request completed",
                {
                    "status_code": response.status_code,
                    "subject": getattr(principal, "subject", None),
                },
            )
            return response

    @app.middleware("http")
    async def _audit_boundary_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Audit runtime/A2A boundary outcomes without capturing payloads."""
        tracked = request.url.path in {"/v1/invoke", "/a2a"}
        try:
            response = await call_next(request)
        except Exception as exc:
            if tracked:
                await _record_runtime_audit(
                    request,
                    action="a2a.invoke" if request.url.path == "/a2a" else "runtime.invoke",
                    target=request.url.path,
                    decision="failed",
                    status_code=500,
                    error_code=getattr(exc, "code", "internal_error"),
                )
            raise
        if request.url.path == "/a2a":
            decision = (
                "succeeded"
                if response.status_code < 400
                else ("denied" if response.status_code in {401, 403} else "failed")
            )
            await _record_runtime_audit(
                request,
                action="a2a.invoke",
                target="/a2a",
                decision=decision,
                status_code=response.status_code,
            )
        elif request.url.path == "/v1/invoke" and response.status_code in {401, 403}:
            await _record_runtime_audit(
                request,
                action="runtime.request",
                target=request.url.path,
                decision="denied",
                status_code=response.status_code,
                error_code="authentication_failed" if response.status_code == 401 else "authorization_denied",
            )
        return response

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
            await _record_runtime_audit(
                http_request,
                action="runtime.invoke",
                target="uninitialized-agent",
                decision="failed",
                status_code=503,
                error_code="not_initialized",
            )
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

        observation: Observability = http_request.app.state.observability
        async with observation.span(
            "invocation",
            labels={"agent": _agent.metadata.name},
            attributes={
                "osa.agent": _agent.metadata.name,
                "osa.invocation_id": str(agent_request.invocation_id),
                "osa.session_id": agent_request.session_id or "new",
            },
        ):
            response = await _agent.invoke(agent_request)

        log_event(
            logger,
            logging.INFO if response.error is None else logging.ERROR,
            "agent invocation completed",
            {
                "invocation_id": str(agent_request.invocation_id),
                "session_id": response.session_id or agent_request.session_id,
                "agent": _agent.metadata.name,
                "user_id": user_id,
                "caller_id": request_metadata.get("caller_subject"),
                "deployment_id": request_metadata.get("deployment_id"),
                "outcome": "error" if response.error else "success",
            },
        )

        await _record_runtime_audit(
            http_request,
            action="runtime.invoke",
            target=_agent.metadata.name,
            decision="failed" if response.error else "succeeded",
            status_code=502 if response.error else 200,
            error_code="agent_invocation_failed" if response.error else None,
        )

        return InvokeResponse(
            output=response.output,
            invocation_id=str(response.invocation_id),
            session_id=response.session_id,
            error=response.error,
        )

    @app.post("/v1/invoke/stream")
    async def invoke_agent_stream(http_request: Request, request: InvokeRequest) -> Response:
        """Stream the invocation as Server-Sent Events (P2.4).

        Event contract (stable, JSON ``data`` payloads; see
        ``GenericAdkAgent.stream_invoke``):

            event: osa.started / osa.message.delta / osa.message / osa.error
            data: {"type": "...", "invocation_id": "...", "session_id": "...",
                   "text": "...", "seq": N}

        The terminal ``osa.message`` carries the same output ``invoke`` would
        return; ``osa.error`` is terminal and deterministic. Disconnecting
        cancels the underlying run; ``runtime.timeout_seconds`` bounds the
        whole stream. Auth middleware applies identically to non-streaming
        invoke.
        """
        if _agent is None:
            await _record_runtime_audit(
                http_request,
                action="runtime.invoke.stream",
                target="uninitialized-agent",
                decision="failed",
                status_code=503,
                error_code="not_initialized",
            )
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
        observation: Observability = http_request.app.state.observability

        async def event_source() -> AsyncIterator[bytes]:
            async with observation.span(
                "invocation.stream",
                labels={"agent": _agent.metadata.name},
                attributes={
                    "osa.agent": _agent.metadata.name,
                    "osa.invocation_id": str(agent_request.invocation_id),
                },
            ):
                async for event in _agent.stream_invoke(agent_request):
                    payload = json.dumps(event.to_payload())
                    yield f"event: {event.type}\ndata: {payload}\n\n".encode()
            await _record_runtime_audit(
                http_request,
                action="runtime.invoke.stream",
                target=_agent.metadata.name,
                decision="succeeded",
                status_code=200,
            )

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> PlainTextResponse:
        """Expose bounded Prometheus metrics without request payloads."""
        return PlainTextResponse(app.state.observability.metrics.render_prometheus())

    @app.get("/v1/capabilities", response_model=CapabilitiesResponse)
    async def get_capabilities() -> Any:
        """Get agent capabilities."""
        if _agent is None:
            return JSONResponse(status_code=503, content=error_payload("not_initialized", "Agent not initialized"))

        return CapabilitiesResponse(
            agent_name=_agent.metadata.name,
            version=_agent.metadata.version,
            streaming=True,
            session_support=True,
            tools=[t.name for t in (_runtime.tool_catalog.list_tools() if _runtime else [])],
            skills=[s.ref for s in _agent.definition.spec.skills],
        )

    return app


runtime_app = configure_runtime_app(FastAPI(title="Open Simple Agent Runtime", version=_package_version()))


def _valid_request_id(value: str) -> bool:
    """Accept only bounded correlation identifiers supplied by a caller."""
    import re

    return bool(value) and bool(re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", value))
