"""Agent Runtime HTTP API — endpoints for invoking agents.

This API runs alongside the agent runtime, independent of the Control Plane.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from osa.generic_agent import (
    AgentDefinition,
    AgentRequest,
    FakeModelProvider,
    ModelCatalog,
    ModelDefinition,
)
from osa.runtimes.adk import AdkRuntime, GenericAdkAgent

runtime_app = FastAPI(title="Open Simple Agent Runtime", version="0.1.0")

# Runtime state
_runtime: AdkRuntime | None = None
_agent: GenericAdkAgent | None = None


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


async def initialize_runtime(definition: AgentDefinition) -> None:
    """Initialize the runtime with an agent definition."""
    global _runtime, _agent

    model_catalog = ModelCatalog()
    model_catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))

    _runtime = AdkRuntime(
        model_provider=FakeModelProvider(response="I'm a runtime agent. How can I help?"),
        model_catalog=model_catalog,
    )
    _agent = await _runtime.create(definition)


@runtime_app.post("/v1/invoke", response_model=InvokeResponse)
async def invoke_agent(request: InvokeRequest) -> InvokeResponse:
    """Invoke the agent."""
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    agent_request = AgentRequest(
        input=request.input,
        session_id=request.session_id,
        user_id=request.user_id,
        metadata=request.metadata,
    )

    response = await _agent.invoke(agent_request)

    return InvokeResponse(
        output=response.output,
        invocation_id=str(response.invocation_id),
        session_id=response.session_id,
        error=response.error,
    )


@runtime_app.get("/health/live")
async def health_live() -> dict[str, str]:
    """Liveness check."""
    return {"status": "alive"}


@runtime_app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    """Readiness check."""
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return {"status": "ready"}


@runtime_app.get("/v1/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities() -> CapabilitiesResponse:
    """Get agent capabilities."""
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    return CapabilitiesResponse(
        agent_name=_agent.metadata.name,
        version=_agent.metadata.version,
        streaming=False,
        session_support=True,
        tools=[t.name for t in (_runtime._tool_catalog.list_tools() if _runtime else [])],
        skills=[s.ref for s in _agent.definition.spec.skills],
    )
