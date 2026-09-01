"""ADK Runtime implementation for Open Simple Agent.

This module implements the GenericAdkAgent and AdkRuntime that convert
an AgentDefinition into a running agent using Google ADK.

Invocation flows through the ADK ``Runner``: conversation context comes from
the ADK session service (keyed by the OSA session ID), tools execute through
ADK-native function calling, and ``runtime.timeout_seconds`` /
``max_iterations`` are enforced around the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from google.genai import types

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from osa.generic_agent import (
    AbstractAgent,
    AgentDefinition,
    AgentFactory,
    AgentRequest,
    AgentResponse,
    AgentRuntime,
    McpCatalog,
    MemoryEntry,
    MemoryPolicy,
    MemoryPolicyCatalog,
    MemoryProvider,
    MemoryScope,
    ModelCatalog,
    ModelDefinition,
    ModelProvider,
    Observability,
    PolicyViolationError,
    SecretResolver,
    Session,
    SessionError,
    SessionManager,
    SessionProvider,
    SkillCatalog,
    SkillDefinition,
    Tool,
    ToolCatalog,
    ToolDefinition,
    ToolResult,
    ToolTimeoutError,
)
from osa.generic_agent.errors import (
    InvocationTimeoutError,
    IterationLimitExceededError,
    ModelConfigurationError,
    ModelInvocationError,
    OsaError,
)
from osa.generic_agent.memory import APPLICATION_SCOPE_ID, memory_scope_id
from osa.runtimes.adk.llm_agent import ProviderBackedLlm, build_llm_agent, build_runner
from osa.runtimes.adk.mcp_client import McpConnectionPool
from osa.runtimes.adk.mcp_toolset import OsaMcpToolset
from osa.runtimes.adk.model_adapter import ModelAdapterRegistry, default_registry
from osa.runtimes.adk.session_service import OsaAdkSessionService

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOOL_ITERATIONS = 3

_ANONYMOUS_USER = "anonymous"


@dataclass(frozen=True)
class OsaStreamEvent:
    """One stable OSA streaming event (P2.4)."""

    type: str
    invocation_id: str
    session_id: str
    text: str = ""
    seq: int = 0

    def to_payload(self) -> dict[str, Any]:
        """JSON-serializable form used by the SSE endpoint."""
        return {
            "type": self.type,
            "invocation_id": self.invocation_id,
            "session_id": self.session_id,
            "text": self.text,
            "seq": self.seq,
        }


def _nonnegative_int(value: object) -> int:
    """Convert provider usage metadata to a safe metric value."""
    return value if isinstance(value, int) and value >= 0 else 0


class GenericAdkAgent(AbstractAgent):
    """ADK-based agent implementation.

    Wraps the generic agent contract with ADK-specific runtime behavior.
    Model execution flows through the ADK Runner using either a live model
    adapter (e.g. LiteLLM) or, for deterministic tests, a bridged
    ``ModelProvider``. Sessions enforce ownership via a ``SessionProvider``.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        model_provider: ModelProvider | None = None,
        model_catalog: ModelCatalog | None = None,
        tool_catalog: ToolCatalog | None = None,
        skill_catalog: SkillCatalog | None = None,
        mcp_catalog: McpCatalog | None = None,
        memory_provider: MemoryProvider | None = None,
        memory_policies: MemoryPolicyCatalog | None = None,
        session_provider: SessionProvider | None = None,
        model_adapters: ModelAdapterRegistry | None = None,
        secret_resolver: SecretResolver | None = None,
        mcp_pool: McpConnectionPool | None = None,
        observability: Observability | None = None,
    ) -> None:
        super().__init__(definition)
        self._model_provider = model_provider
        self._model_catalog = model_catalog if model_catalog is not None else ModelCatalog()
        self._tool_catalog = tool_catalog if tool_catalog is not None else ToolCatalog()
        self._skill_catalog = skill_catalog if skill_catalog is not None else SkillCatalog()
        self._mcp_catalog = mcp_catalog if mcp_catalog is not None else McpCatalog()
        self._memory_provider = memory_provider
        self._memory_policies = memory_policies if memory_policies is not None else MemoryPolicyCatalog()
        self._memory_policy = self._resolve_memory_policy()
        self._session_provider = session_provider if session_provider is not None else SessionManager()
        self._observability = observability or Observability()
        self._owns_mcp_pool = mcp_pool is None
        self._mcp_pool = (
            mcp_pool if mcp_pool is not None else McpConnectionPool(secret_resolver, observability=self._observability)
        )
        self._tools: dict[str, Tool] = {}
        self._tool_definitions: dict[str, ToolDefinition] = {}
        self._skills: list[SkillDefinition] = []
        self._mcp_toolsets: list[OsaMcpToolset] = []
        self._resolve_definition_resources()
        self._model_definition = self._resolve_model_definition()
        self._model_id = self._model_definition.model_id if self._model_definition is not None else "fake"
        self._adapters = model_adapters or default_registry(
            fake_provider=model_provider,
            observability=self._observability,
        )
        if self._model_definition is not None:
            self._model = self._adapters.resolve(self._model_definition.provider).build(
                self._model_definition, self._model_parameters()
            )
        else:
            # No model configured anywhere: deterministic mode requires an
            # explicit provider; otherwise refuse to guess.
            if self._model_provider is None:
                raise ModelConfigurationError(
                    f"Agent '{self.metadata.name}' has no model configured, no default model "
                    "exists in the catalog, and no model provider was supplied"
                )
            self._model = ProviderBackedLlm(
                model=self._model_id,
                provider=self._model_provider,
                observability=self._observability,
            )
        # ADK objects for the definition: invocation runs through the Runner.
        self.llm_agent = build_llm_agent(
            self.definition,
            self._model,
            self._tools,
            self._tool_definitions,
            toolsets=self._mcp_toolsets,
            observability=self._observability,
        )
        self._session_service = OsaAdkSessionService(self._session_provider)
        self.runner = build_runner(self.llm_agent, session_service=self._session_service)

    def _resolve_model_definition(self) -> ModelDefinition | None:
        """Resolve the model definition for this agent, failing fast on a
        missing reference — silent fallbacks are not allowed in configured
        deployments. Returns None when no model is configured at all."""
        spec_model = self.definition.spec.model
        if spec_model is not None:
            self._require_policy("model", spec_model.ref, self.definition.spec.policy.models)
            try:
                return self._model_catalog.resolve(spec_model.ref)
            except KeyError:
                raise ValueError(
                    f"Model '{spec_model.ref}' referenced by agent '{self.metadata.name}' "
                    "was not found in the model catalog"
                ) from None
        return self._model_catalog.get_default()

    def _model_parameters(self) -> dict[str, Any]:
        spec_model = self.definition.spec.model
        return dict(spec_model.parameters) if spec_model is not None else {}

    def _resolve_definition_resources(self) -> None:
        """Resolve spec.tools and spec.skills against their catalogs.

        Runs at construction so a definition referencing a resource that does
        not exist fails fast instead of silently doing nothing at invocation.
        """
        agent_name = self.metadata.name
        for tool_ref in self.definition.spec.tools:
            self._require_policy("tool", tool_ref.ref, self.definition.spec.policy.tools)
            try:
                self._tool_definitions[tool_ref.ref] = self._tool_catalog.get_definition(tool_ref.ref)
                self._tools[tool_ref.ref] = self._tool_catalog.get_tool(tool_ref.ref)
            except KeyError as exc:
                raise ValueError(
                    f"Tool '{tool_ref.ref}' referenced by agent '{agent_name}' was not found in the tool catalog"
                ) from exc
        for skill_ref in self.definition.spec.skills:
            self._require_policy("skill", skill_ref.ref, self.definition.spec.policy.skills)
            try:
                self._skills.append(self._skill_catalog.resolve(skill_ref.ref))
            except KeyError as exc:
                raise ValueError(
                    f"Skill '{skill_ref.ref}' referenced by agent '{agent_name}' was not found in the skill catalog"
                ) from exc
        for mcp_ref in self.definition.spec.mcps:
            self._require_policy("mcp", mcp_ref.ref, self.definition.spec.policy.mcps)
            try:
                mcp_definition = self._mcp_catalog.resolve(mcp_ref.ref)
            except KeyError as exc:
                raise ValueError(
                    f"MCP server '{mcp_ref.ref}' referenced by agent '{agent_name}' was not found in the MCP catalog"
                ) from exc
            self._mcp_toolsets.append(
                OsaMcpToolset(
                    mcp_definition,
                    self._mcp_pool.get(mcp_definition),
                    tool_filter=mcp_ref.tools_filter or None,
                )
            )

    @staticmethod
    def _require_policy(resource_type: str, resource_name: str, rule: Any) -> None:
        if not rule.permits(resource_name):
            raise PolicyViolationError(resource_type, resource_name)

    @property
    def tools(self) -> list[str]:
        """Names of the tools resolved for this agent."""
        return list(self._tools)

    @property
    def skills(self) -> list[SkillDefinition]:
        """Skill definitions resolved for this agent."""
        return list(self._skills)

    async def execute_tool(self, tool_name: str, **parameters: Any) -> ToolResult:
        """Execute a resolved tool, enforcing its configured timeout.

        The tool runs in a worker thread; on timeout the await is cancelled and
        ``ToolTimeoutError`` is raised (the worker thread itself cannot be
        killed, so tools must still be written to finish on their own).
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Tool '{tool_name}' is not resolved for agent '{self.metadata.name}'")
        tool_definition = self._tool_definitions.get(tool_name)
        timeout = tool_definition.timeout_seconds if tool_definition else None
        execution = asyncio.to_thread(tool.execute, **parameters)
        if timeout is None:
            return await execution
        try:
            return await asyncio.wait_for(execution, timeout)
        except TimeoutError as exc:
            raise ToolTimeoutError(tool_name, timeout) from exc

    def _resolve_memory_policy(self) -> MemoryPolicy | None:
        """Resolve the referenced memory policy, failing fast when missing.

        A referenced policy is authoritative for scope, limits, and
        retention; without one, ``spec.memory`` fields apply.
        """
        memory_cfg = self.definition.spec.memory
        if not memory_cfg.enabled or memory_cfg.policy is None:
            return None
        try:
            return self._memory_policies.resolve(memory_cfg.policy)
        except KeyError:
            raise ValueError(
                f"Memory policy '{memory_cfg.policy}' referenced by agent '{self.metadata.name}' "
                "was not found in the memory policy catalog"
            ) from None

    def _effective_memory(self) -> tuple[MemoryScope, MemoryPolicy | None]:
        """Effective (scope, policy) for memory operations.

        Returns an inert scope when memory is disabled, including disabled by
        policy (``MemoryPolicy.enabled = false``).
        """
        memory_cfg = self.definition.spec.memory
        policy = self._memory_policy
        if not memory_cfg.enabled or self._memory_provider is None:
            return MemoryScope.USER, None
        if policy is not None and not policy.enabled:
            return MemoryScope.USER, None
        if policy is not None:
            return policy.scope, policy
        return memory_cfg.scope, None

    def _memory_limits(self, policy: MemoryPolicy | None) -> dict[str, int | None]:
        memory_cfg = self.definition.spec.memory
        if policy is not None:
            return {"max_entries": policy.max_entries, "retention_days": policy.retention_days}
        return {"max_entries": memory_cfg.max_entries, "retention_days": None}

    async def _enforce_memory_limits(self, scope: MemoryScope, scope_id: str, policy: MemoryPolicy | None) -> None:
        limits = self._memory_limits(policy)
        provider = self._memory_provider
        if provider is None:
            return
        with contextlib.suppress(NotImplementedError):
            await provider.enforce(scope, scope_id, **limits)

    async def _load_memory_context(self, scope_id: str, query: str) -> str:
        """Load policy-controlled memory relevant to the request.

        Memory context is only injected when ``spec.memory.enabled`` and a
        provider are configured; raw interactions are never auto-persisted.
        """
        scope, policy = self._effective_memory()
        if self._memory_provider is None:
            return ""
        async with self._observability.span(
            "memory.search",
            labels={"agent": self.metadata.name, "scope": scope.value},
            attributes={"osa.agent": self.metadata.name, "osa.memory.scope": scope.value},
        ):
            await self._enforce_memory_limits(scope, scope_id, policy)
            entries = await self._memory_provider.search(query, scope, scope_id=scope_id, limit=5)
        if not entries:
            return ""
        lines = "\n".join(f"- {entry.content}" for entry in entries)
        return f"Memory:\n{lines}"

    async def remember(self, key: str, content: str, *, scope_id: str | None = None) -> None:
        """Store a memory entry explicitly.

        Raw interactions are never persisted automatically; memory writes go
        through this API (policy-driven extraction is reserved and off by
        default). When ``scope_id`` is omitted the entry is stored under the
        application scope ID — callers should pass the scope ID derived via
        :func:`memory_scope_id` for user/tenant scoping.
        """
        if self._memory_provider is None:
            raise RuntimeError(f"No memory provider configured for agent '{self.metadata.name}'")
        policy = self._memory_policy
        if policy is not None and not policy.enabled:
            raise RuntimeError(f"Memory is disabled by policy '{policy.name}' for agent '{self.metadata.name}'")
        scope, policy = self._effective_memory()
        entry = MemoryEntry(key=key, content=content, scope=scope, scope_id=scope_id or APPLICATION_SCOPE_ID)
        async with self._observability.span(
            "memory.store",
            labels={"agent": self.metadata.name, "scope": scope.value},
            attributes={"osa.agent": self.metadata.name, "osa.memory.scope": scope.value},
        ):
            await self._memory_provider.store(entry)
            await self._enforce_memory_limits(scope, entry.scope_id, policy)

    def _resolve_session(self, request: AgentRequest, tenant_id: str | None) -> Session:
        """Resolve or create the OSA session with strict ownership."""
        session_cfg = self.definition.spec.session
        if request.session_id is not None:
            return self._session_provider.resolve(
                request.session_id,
                agent_name=self.metadata.name,
                user_id=request.user_id,
                tenant_id=tenant_id,
            )
        return self._session_provider.create(
            self.metadata.name,
            user_id=request.user_id,
            tenant_id=tenant_id,
            ttl_seconds=session_cfg.ttl_seconds,
            max_history_messages=session_cfg.max_history_messages,
        )

    def _adk_user_id(self, user_id: str | None, tenant_id: str | None) -> str:
        """Identity label for ADK session objects.

        Ownership enforcement happens in the OSA ``SessionProvider`` before
        the run; this label only decorates ADK session metadata.
        """
        if tenant_id:
            return f"{tenant_id}:{user_id or _ANONYMOUS_USER}"
        return user_id or _ANONYMOUS_USER

    async def _ensure_adk_session(self, session_id: str, adk_user_id: str) -> None:
        service = self.runner.session_service
        existing = await service.get_session(app_name=self.runner.app_name, user_id=adk_user_id, session_id=session_id)
        if existing is None:
            await service.create_session(app_name=self.runner.app_name, user_id=adk_user_id, session_id=session_id)

    async def _run_adk(self, session_id: str, adk_user_id: str, user_input: str) -> str:
        """Consume Runner events, enforcing iteration and timeout limits."""
        max_iterations = self.definition.spec.runtime.max_iterations or _DEFAULT_MAX_TOOL_ITERATIONS
        timeout_seconds = self.definition.spec.runtime.timeout_seconds
        message = types.Content(role="user", parts=[types.Part(text=user_input)])

        async def consume() -> str:
            function_call_rounds = 0
            final_text = ""
            async for event in self.runner.run_async(user_id=adk_user_id, session_id=session_id, new_message=message):
                usage = getattr(event, "usage_metadata", None)
                if usage is not None:
                    self._observability.record_token_usage(
                        self._model_id,
                        prompt_tokens=_nonnegative_int(getattr(usage, "prompt_token_count", 0)),
                        completion_tokens=_nonnegative_int(getattr(usage, "candidates_token_count", 0)),
                        total_tokens=_nonnegative_int(getattr(usage, "total_token_count", 0)),
                    )
                if event.get_function_calls():
                    function_call_rounds += 1
                    if function_call_rounds > max_iterations:
                        raise IterationLimitExceededError(max_iterations)
                if event.is_final_response() and event.content and event.content.parts:
                    text = "".join(part.text or "" for part in event.content.parts)
                    if text:
                        final_text = text
            if not final_text:
                raise ModelInvocationError(self._model_id, "the model produced no final response")
            return final_text

        async with self._observability.span(
            "model.run",
            labels={"agent": self.metadata.name, "model": self._model_id},
            attributes={"osa.agent": self.metadata.name, "osa.model": self._model_id},
        ):
            if timeout_seconds is None:
                return await consume()
            try:
                return await asyncio.wait_for(consume(), timeout_seconds)
            except TimeoutError as exc:
                raise InvocationTimeoutError(float(timeout_seconds)) from exc

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        """Invoke the agent with a request.

        The session is resolved under strict ownership (unknown caller-supplied
        IDs and identity changes raise :class:`SessionError` subclasses).
        Conversation context comes from the ADK session bound to the OSA
        session ID; tools execute through ADK-native function calling.
        Model/tool/timeout failures are captured in ``AgentResponse.error``.
        """
        async with self._observability.span(
            "agent.invoke",
            labels={"agent": self.metadata.name},
            attributes={
                "osa.agent": self.metadata.name,
                "osa.invocation_id": str(request.invocation_id),
                "osa.session_id": request.session_id or "new",
                "osa.user_id": request.user_id or "anonymous",
            },
        ):
            return await self._invoke(request)

    async def _invoke(self, request: AgentRequest) -> AgentResponse:
        tenant_id = request.metadata.get("tenant_id")
        session_observation = self._observability
        async with session_observation.span(
            "session.resolve",
            labels={"agent": self.metadata.name},
            attributes={"osa.agent": self.metadata.name, "osa.session_id": request.session_id or "new"},
        ):
            session = self._resolve_session(request, tenant_id)
        adk_user_id = self._adk_user_id(request.user_id, tenant_id)
        await self._ensure_adk_session(str(session.session_id), adk_user_id)

        # Policy-controlled memory context is injected into the instruction
        # for this invocation only.
        instruction = self.definition.spec.instruction or ""
        memory_scope, _ = self._effective_memory()
        memory_context = await self._load_memory_context(
            memory_scope_id(memory_scope, user_id=request.user_id, agent_name=self.metadata.name, tenant_id=tenant_id),
            request.input,
        )
        if memory_context:
            self.llm_agent.instruction = f"{instruction}\n\n{memory_context}"

        try:
            # Pre-flight MCP connections: ADK resolves toolsets fail-open (a
            # broken server silently loses its tools), which is neither
            # predictable nor observable; OSA fails deterministically.
            for mcp_toolset in self._mcp_toolsets:
                await mcp_toolset.connection.connect()
            output = await self._run_adk(str(session.session_id), adk_user_id, request.input)
        except SessionError:
            raise
        except OsaError as exc:
            logger.error("Agent invocation failed: %s", exc)
            return AgentResponse(
                output="",
                invocation_id=request.invocation_id,
                session_id=str(session.session_id),
                error=str(exc),
            )
        except Exception as exc:
            failure = ModelInvocationError(self._model_id, str(exc), cause=exc)
            logger.error("Agent invocation failed: %s", failure)
            return AgentResponse(
                output="",
                invocation_id=request.invocation_id,
                session_id=str(session.session_id),
                error=str(failure),
            )

        # Mirror the exchange into the bounded OSA conversation history.
        session.add_message("user", request.input)
        session.add_message("assistant", output)
        self._session_provider.save(session)

        return AgentResponse(
            output=output,
            invocation_id=request.invocation_id,
            session_id=str(session.session_id),
        )

    async def stream_invoke(self, request: AgentRequest) -> AsyncGenerator[OsaStreamEvent, None]:
        """Stream an invocation as stable OSA events (P2.4).

        Event contract (JSON-serializable, ``seq`` monotonic per invocation):
            - ``osa.started``: invocation accepted (invocation/session ids).
            - ``osa.message.delta``: incremental model text from a runner
              event (token-level deltas require a streaming model; events are
              per runner round otherwise).
            - ``osa.message``: the final complete output — the same text
              :meth:`invoke` would return.
            - ``osa.error``: terminal deterministic error text.

        Session resolution/ownership, memory context, MCP pre-flight, and
        ``runtime.timeout_seconds`` / ``max_iterations`` behave exactly like
        :meth:`invoke`; the timeout bounds the whole stream lifetime (a slow
        consumer counts against it). Yields directly to the consumer with no
        intermediate buffering, so a slow consumer applies natural
        backpressure. Closing the iterator (client disconnect) cancels the
        underlying ADK run.
        """
        tenant_id = request.metadata.get("tenant_id")
        session = self._resolve_session(request, tenant_id)
        adk_user_id = self._adk_user_id(request.user_id, tenant_id)
        await self._ensure_adk_session(str(session.session_id), adk_user_id)

        instruction = self.definition.spec.instruction or ""
        memory_scope, _ = self._effective_memory()
        memory_context = await self._load_memory_context(
            memory_scope_id(memory_scope, user_id=request.user_id, agent_name=self.metadata.name, tenant_id=tenant_id),
            request.input,
        )
        if memory_context:
            self.llm_agent.instruction = f"{instruction}\n\n{memory_context}"

        session_id = str(session.session_id)
        invocation_id = str(request.invocation_id)
        seq = 0

        async def emit(event_type: str, event_text: str = "") -> OsaStreamEvent:
            nonlocal seq
            event = OsaStreamEvent(
                type=event_type,
                invocation_id=invocation_id,
                session_id=session_id,
                text=event_text,
                seq=seq,
            )
            seq += 1
            return event

        yield await emit("osa.started")

        final_text = ""
        try:
            timeout_seconds = self.definition.spec.runtime.timeout_seconds
            max_iterations = self.definition.spec.runtime.max_iterations or _DEFAULT_MAX_TOOL_ITERATIONS
            # Pre-flight MCP connections (same deterministic failure policy
            # as invoke).
            for mcp_toolset in self._mcp_toolsets:
                await mcp_toolset.connection.connect()

            async with self._observability.span(
                "model.stream",
                labels={"agent": self.metadata.name, "model": self._model_id},
                attributes={"osa.agent": self.metadata.name, "osa.model": self._model_id},
            ):
                function_call_rounds = 0
                timed_out = False
                async with asyncio.timeout(timeout_seconds):
                    async for event in self.runner.run_async(
                        user_id=adk_user_id,
                        session_id=session_id,
                        new_message=types.Content(role="user", parts=[types.Part(text=request.input)]),
                    ):
                        usage = getattr(event, "usage_metadata", None)
                        if usage is not None:
                            self._observability.record_token_usage(
                                self._model_id,
                                prompt_tokens=_nonnegative_int(getattr(usage, "prompt_token_count", 0)),
                                completion_tokens=_nonnegative_int(getattr(usage, "candidates_token_count", 0)),
                                total_tokens=_nonnegative_int(getattr(usage, "total_token_count", 0)),
                            )
                        if event.get_function_calls():
                            function_call_rounds += 1
                            if function_call_rounds > max_iterations:
                                raise IterationLimitExceededError(max_iterations)
                        if event.content and event.content.parts:
                            text = "".join(part.text or "" for part in event.content.parts)
                            if not text:
                                continue
                            if event.is_final_response():
                                final_text = text
                            else:
                                yield await emit("osa.message.delta", text)
                if timed_out:  # pragma: no cover - asyncio.timeout raises instead
                    pass

            if not final_text:
                raise ModelInvocationError(self._model_id, "the model produced no final response")
        except TimeoutError:
            timeout = self.definition.spec.runtime.timeout_seconds
            timeout_failure = InvocationTimeoutError(float(timeout)) if timeout else InvocationTimeoutError(-1)
            logger.error("Streaming invocation failed: %s", timeout_failure)
            yield await emit("osa.error", str(timeout_failure))
            return
        except SessionError:
            raise
        except OsaError as exc:
            logger.error("Streaming invocation failed: %s", exc)
            yield await emit("osa.error", str(exc))
            return
        except Exception as exc:
            failure = ModelInvocationError(self._model_id, str(exc), cause=exc)
            logger.error("Streaming invocation failed: %s", failure)
            yield await emit("osa.error", str(failure))
            return

        session.add_message("user", request.input)
        session.add_message("assistant", final_text)
        self._session_provider.save(session)
        yield await emit("osa.message", final_text)

    async def shutdown(self) -> None:
        if self._owns_mcp_pool:
            await self._mcp_pool.close()


class AdkRuntime(AgentRuntime):
    """ADK runtime that creates GenericAdkAgent instances from definitions."""

    def __init__(
        self,
        model_provider: ModelProvider | None = None,
        model_catalog: ModelCatalog | None = None,
        tool_catalog: ToolCatalog | None = None,
        skill_catalog: SkillCatalog | None = None,
        mcp_catalog: McpCatalog | None = None,
        memory_provider: MemoryProvider | None = None,
        memory_policies: MemoryPolicyCatalog | None = None,
        session_provider: SessionProvider | None = None,
        model_adapters: ModelAdapterRegistry | None = None,
        secret_resolver: SecretResolver | None = None,
        observability: Observability | None = None,
    ) -> None:
        self._model_provider = model_provider
        self._model_catalog = model_catalog if model_catalog is not None else ModelCatalog()
        self._tool_catalog = tool_catalog if tool_catalog is not None else ToolCatalog()
        self._skill_catalog = skill_catalog if skill_catalog is not None else SkillCatalog()
        self._mcp_catalog = mcp_catalog if mcp_catalog is not None else McpCatalog()
        self._memory_provider = memory_provider
        self._memory_policies = memory_policies
        self._session_provider = session_provider if session_provider is not None else SessionManager()
        self._model_adapters = model_adapters
        self._observability = observability or Observability()
        self._mcp_pool = McpConnectionPool(secret_resolver, observability=self._observability)
        self._agents: list[GenericAdkAgent] = []

    async def create(self, definition: AgentDefinition) -> GenericAdkAgent:
        """Create a GenericAdkAgent from an AgentDefinition."""
        agent = GenericAdkAgent(
            definition=definition,
            model_provider=self._model_provider,
            model_catalog=self._model_catalog,
            tool_catalog=self._tool_catalog,
            skill_catalog=self._skill_catalog,
            mcp_catalog=self._mcp_catalog,
            memory_provider=self._memory_provider,
            memory_policies=self._memory_policies,
            session_provider=self._session_provider,
            model_adapters=self._model_adapters,
            mcp_pool=self._mcp_pool,
            observability=self._observability,
        )
        self._agents.append(agent)
        logger.info("Created agent '%s'", definition.metadata.name)
        return agent

    @property
    def session_provider(self) -> SessionProvider:
        """The session provider shared by all agents of this runtime."""
        return self._session_provider

    @property
    def tool_catalog(self) -> ToolCatalog:
        """The tool catalog shared by all agents of this runtime."""
        return self._tool_catalog

    @property
    def memory_provider(self) -> MemoryProvider:
        """The memory provider shared by all agents of this runtime."""
        assert self._memory_provider is not None
        return self._memory_provider

    async def shutdown(self) -> None:
        """Shut down all agents created by this runtime, then MCP connections."""
        for agent in self._agents:
            await agent.shutdown()
        self._agents.clear()
        await self._mcp_pool.close()
        logger.info("ADK runtime shut down")


class AdkAgentFactory(AgentFactory):
    """Factory that creates GenericAdkAgent instances synchronously."""

    def __init__(
        self,
        model_provider: ModelProvider | None = None,
        model_catalog: ModelCatalog | None = None,
        tool_catalog: ToolCatalog | None = None,
        skill_catalog: SkillCatalog | None = None,
        mcp_catalog: McpCatalog | None = None,
        memory_provider: MemoryProvider | None = None,
        memory_policies: MemoryPolicyCatalog | None = None,
        session_provider: SessionProvider | None = None,
        model_adapters: ModelAdapterRegistry | None = None,
        secret_resolver: SecretResolver | None = None,
        observability: Observability | None = None,
    ) -> None:
        self._model_provider = model_provider
        self._model_catalog = model_catalog if model_catalog is not None else ModelCatalog()
        self._tool_catalog = tool_catalog if tool_catalog is not None else ToolCatalog()
        self._skill_catalog = skill_catalog if skill_catalog is not None else SkillCatalog()
        self._mcp_catalog = mcp_catalog if mcp_catalog is not None else McpCatalog()
        self._memory_provider = memory_provider
        self._memory_policies = memory_policies
        self._session_provider = session_provider if session_provider is not None else SessionManager()
        self._model_adapters = model_adapters
        self._secret_resolver = secret_resolver
        self._observability = observability or Observability()

    def create(self, definition: AgentDefinition) -> GenericAdkAgent:
        """Create a GenericAdkAgent from an AgentDefinition."""
        return GenericAdkAgent(
            definition=definition,
            model_provider=self._model_provider,
            model_catalog=self._model_catalog,
            tool_catalog=self._tool_catalog,
            skill_catalog=self._skill_catalog,
            mcp_catalog=self._mcp_catalog,
            memory_provider=self._memory_provider,
            memory_policies=self._memory_policies,
            session_provider=self._session_provider,
            model_adapters=self._model_adapters,
            secret_resolver=self._secret_resolver,
            observability=self._observability,
        )
