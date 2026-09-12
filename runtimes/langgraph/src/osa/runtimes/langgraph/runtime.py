"""LangChain/LangGraph runtime implementation for Open Simple Agent.

The adapter owns only framework construction and execution. OSA still owns
resource resolution, authorization policy, sessions, memory, timeouts, stable
errors, and the response/streaming contracts. The graph is intentionally
small: a model node routes to a LangChain ``ToolNode`` until the model emits
its final answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from osa.generic_agent import (
    AbstractAgent,
    AgentDefinition,
    AgentFactory,
    AgentRequest,
    AgentResponse,
    AgentRuntime,
    AgentStreamEvent,
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
    RuntimeDependencies,
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
from osa.runtimes.langgraph.model_adapter import LangChainModelAdapterRegistry, default_registry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOOL_ITERATIONS = 3


async def _close_owned_resource(resource: Any) -> None:
    """Close either async or synchronous resources without double-closing."""
    closer = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if closer is None:
        return
    result = closer()
    if inspect.isawaitable(result):
        await result


class OsaGraphState(TypedDict):
    """State carried through the model/tool graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    model_calls: int


def _message_text(message: AnyMessage) -> str:
    """Extract text from a LangChain message without exposing tool metadata."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks: list[str] = []
        for block in content:
            if isinstance(block, str):
                blocks.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                blocks.append(block["text"])
        return "".join(blocks)
    return str(content)


def _history_messages(session: Session) -> list[AnyMessage]:
    """Translate bounded OSA history into LangChain messages."""
    messages: list[AnyMessage] = []
    for item in session.history_window(session.max_history_messages):
        role = item.get("role")
        content = item.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "system":
            messages.append(SystemMessage(content=content))
    return messages


class OsaLangGraphAgent(AbstractAgent):
    """OSA agent backed by a LangChain chat model and LangGraph graph."""

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
        model_adapters: LangChainModelAdapterRegistry | None = None,
        secret_resolver: SecretResolver | None = None,
        observability: Observability | None = None,
        dependencies: RuntimeDependencies | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        super().__init__(definition)
        configured = dependencies or RuntimeDependencies.with_defaults(
            model_provider=model_provider,
            model_catalog=model_catalog,
            tool_catalog=tool_catalog,
            skill_catalog=skill_catalog,
            mcp_catalog=mcp_catalog,
            memory_provider=memory_provider,
            memory_policies=memory_policies,
            session_provider=session_provider,
            secret_resolver=secret_resolver,
            observability=observability,
        )
        self._model_provider = configured.model_provider
        self._model_catalog = configured.model_catalog if configured.model_catalog is not None else ModelCatalog()
        self._tool_catalog = configured.tool_catalog if configured.tool_catalog is not None else ToolCatalog()
        self._skill_catalog = configured.skill_catalog if configured.skill_catalog is not None else SkillCatalog()
        self._memory_provider = configured.memory_provider
        self._memory_policies = (
            configured.memory_policies if configured.memory_policies is not None else MemoryPolicyCatalog()
        )
        self._session_provider = (
            configured.session_provider if configured.session_provider is not None else SessionManager()
        )
        self._observability = configured.observability if configured.observability is not None else Observability()
        self._secret_resolver = configured.secret_resolver
        self._checkpointer = checkpointer
        self._tools: dict[str, Tool] = {}
        self._tool_definitions: dict[str, ToolDefinition] = {}
        self._skills: list[SkillDefinition] = []
        self._memory_policy = self._resolve_memory_policy()
        self._resolve_definition_resources()
        self._model_definition = self._resolve_model_definition()
        self._model_id = self._model_definition.model_id if self._model_definition is not None else "fake"
        adapters = model_adapters or default_registry(
            fake_provider=self._model_provider,
            secret_resolver=self._secret_resolver,
            observability=self._observability,
        )
        model_definition = self._model_definition
        if model_definition is None:
            if self._model_provider is None:
                raise ModelConfigurationError(
                    f"Agent '{self.metadata.name}' has no model configured, no default model "
                    "exists in the catalog, and no model provider was supplied"
                )
            model_definition = ModelDefinition(name="runtime-default", provider="fake", model_id=self._model_id)
        self._model = adapters.resolve(model_definition.provider).build(
            model_definition,
            self._model_parameters(),
        )
        self._langchain_tools = self._build_langchain_tools()
        self._model_with_tools = self._model.bind_tools(self._langchain_tools) if self._langchain_tools else self._model
        self.graph = self._build_graph()

    def _resolve_model_definition(self) -> ModelDefinition | None:
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
        """Resolve all catalog references before constructing framework state."""
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
        if self.definition.spec.mcps:
            # The MCP client currently lives in the ADK runtime. Refusing this
            # configuration is safer than silently dropping declared tools.
            for mcp_ref in self.definition.spec.mcps:
                self._require_policy("mcp", mcp_ref.ref, self.definition.spec.policy.mcps)
            raise ModelConfigurationError(
                "The LangGraph runtime does not yet support MCP references; use the ADK runtime "
                "or provide a LangChain MCP adapter"
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
        """Execute a resolved OSA tool with its configured timeout."""
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

    def _build_langchain_tools(self) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for tool_name, tool in self._tools.items():
            definition = self._tool_definitions.get(tool_name)

            async def invoke_tool(_tool_name: str = tool_name, **parameters: Any) -> str:
                result = await self.execute_tool(_tool_name, **parameters)
                if result.success:
                    return result.output
                return result.error or f"Tool '{_tool_name}' failed"

            schema = (
                definition.capabilities[0].parameters_schema
                if definition is not None and definition.capabilities
                else None
            )
            tools.append(
                StructuredTool.from_function(
                    coroutine=invoke_tool,
                    name=tool.name,
                    description=tool.description or (definition.description if definition else ""),
                    args_schema=schema,
                    infer_schema=schema is None,
                )
            )
        return tools

    def _resolve_memory_policy(self) -> MemoryPolicy | None:
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
        provider = self._memory_provider
        if provider is None:
            return
        with contextlib.suppress(NotImplementedError):
            await provider.enforce(scope, scope_id, **self._memory_limits(policy))

    async def _load_memory_context(self, scope_id: str, query: str) -> str:
        scope, policy = self._effective_memory()
        if (
            self._memory_provider is None
            or not self.definition.spec.memory.enabled
            or (self._memory_policy is not None and not self._memory_policy.enabled)
        ):
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
        return "Memory:\n" + "\n".join(f"- {entry.content}" for entry in entries)

    async def remember(self, key: str, content: str, *, scope_id: str | None = None) -> None:
        """Store an explicitly requested, policy-controlled memory entry."""
        if self._memory_provider is None:
            raise RuntimeError(f"No memory provider configured for agent '{self.metadata.name}'")
        if not self.definition.spec.memory.enabled:
            raise RuntimeError(f"Memory is disabled for agent '{self.metadata.name}'")
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

    def _build_graph(self) -> Any:
        builder = StateGraph(OsaGraphState)

        async def model_node(state: OsaGraphState) -> dict[str, Any]:
            async with self._observability.span(
                "model.run",
                labels={"agent": self.metadata.name, "model": self._model_id},
                attributes={"osa.agent": self.metadata.name, "osa.model": self._model_id},
            ):
                response = await self._model_with_tools.ainvoke(state["messages"])
            if not isinstance(response, AIMessage):
                response = AIMessage(content=_message_text(response))
            return {"messages": [response], "model_calls": state.get("model_calls", 0) + 1}

        def route_after_model(state: OsaGraphState) -> str:
            latest = state["messages"][-1]
            if not isinstance(latest, AIMessage) or not latest.tool_calls:
                return END
            max_iterations = self.definition.spec.runtime.max_iterations or _DEFAULT_MAX_TOOL_ITERATIONS
            if state.get("model_calls", 0) >= max_iterations:
                raise IterationLimitExceededError(max_iterations)
            return "tools"

        builder.add_node("model", model_node)
        builder.add_node("tools", ToolNode(self._langchain_tools, handle_tool_errors=True))
        builder.add_edge(START, "model")
        builder.add_conditional_edges("model", route_after_model, {"tools": "tools", END: END})
        builder.add_edge("tools", "model")
        return builder.compile(checkpointer=self._checkpointer)

    def _initial_state(self, session: Session, request: AgentRequest, memory_context: str) -> OsaGraphState:
        instruction = self.definition.spec.instruction or ""
        if memory_context:
            instruction = f"{instruction}\n\n{memory_context}" if instruction else memory_context
        messages: list[AnyMessage] = []
        if instruction:
            messages.append(SystemMessage(content=instruction))
        if not (self._checkpointer is not None and request.session_id is not None):
            messages.extend(_history_messages(session))
        messages.append(HumanMessage(content=request.input))
        return {"messages": messages, "model_calls": 0}

    def _graph_config(self, session_id: str) -> dict[str, Any] | None:
        if self._checkpointer is None:
            return None
        return {"configurable": {"thread_id": session_id}}

    async def _run_graph(self, state: OsaGraphState, session_id: str) -> str:
        config = self._graph_config(session_id)
        timeout = self.definition.spec.runtime.timeout_seconds
        execution = self.graph.ainvoke(state) if config is None else self.graph.ainvoke(state, config=config)
        try:
            result = await asyncio.wait_for(execution, timeout) if timeout is not None else await execution
        except TimeoutError as exc:
            assert timeout is not None
            raise InvocationTimeoutError(float(timeout)) from exc
        messages = result.get("messages", [])
        final_message = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage) and not message.tool_calls),
            None,
        )
        final_text = _message_text(final_message) if final_message is not None else ""
        if not final_text:
            raise ModelInvocationError(self._model_id, "the model produced no final response")
        return final_text

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        """Invoke the LangGraph pipeline through the stable OSA contract."""
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
        session = self._resolve_session(request, tenant_id)
        scope, _ = self._effective_memory()
        memory_context = await self._load_memory_context(
            memory_scope_id(scope, user_id=request.user_id, agent_name=self.metadata.name, tenant_id=tenant_id),
            request.input,
        )
        try:
            output = await self._run_graph(
                self._initial_state(session, request, memory_context), str(session.session_id)
            )
        except SessionError:
            raise
        except OsaError as exc:
            logger.error("LangGraph agent invocation failed: %s", exc)
            return AgentResponse(
                output="",
                invocation_id=request.invocation_id,
                session_id=str(session.session_id),
                error=str(exc),
            )
        except Exception as exc:
            failure = ModelInvocationError(self._model_id, str(exc), cause=exc)
            logger.error("LangGraph agent invocation failed: %s", failure)
            return AgentResponse(
                output="",
                invocation_id=request.invocation_id,
                session_id=str(session.session_id),
                error=str(failure),
            )
        session.add_message("user", request.input)
        session.add_message("assistant", output)
        self._session_provider.save(session)
        return AgentResponse(output=output, invocation_id=request.invocation_id, session_id=str(session.session_id))

    async def stream_invoke(self, request: AgentRequest) -> AsyncGenerator[AgentStreamEvent, None]:
        """Stream LangGraph model updates as stable OSA events."""
        tenant_id = request.metadata.get("tenant_id")
        session = self._resolve_session(request, tenant_id)
        scope, _ = self._effective_memory()
        memory_context = await self._load_memory_context(
            memory_scope_id(scope, user_id=request.user_id, agent_name=self.metadata.name, tenant_id=tenant_id),
            request.input,
        )
        session_id = str(session.session_id)
        invocation_id = str(request.invocation_id)
        state = self._initial_state(session, request, memory_context)
        config = self._graph_config(session_id)
        sequence = 0

        def emit(event_type: str, text: str = "") -> AgentStreamEvent:
            nonlocal sequence
            event = AgentStreamEvent(
                type=event_type,
                invocation_id=invocation_id,
                session_id=session_id,
                text=text,
                seq=sequence,
            )
            sequence += 1
            return event

        yield emit("osa.started")
        final_text = ""
        try:
            if config is None:
                updates = self.graph.astream(state, stream_mode="updates")
            else:
                updates = self.graph.astream(state, config=config, stream_mode="updates")
            timeout = self.definition.spec.runtime.timeout_seconds
            async with self._observability.span(
                "model.stream",
                labels={"agent": self.metadata.name, "model": self._model_id},
                attributes={"osa.agent": self.metadata.name, "osa.model": self._model_id},
            ):
                if timeout is None:
                    async for update in updates:
                        final_text, emitted = _stream_update(update, final_text, emit)
                        for event in emitted:
                            yield event
                else:
                    async with asyncio.timeout(timeout):
                        async for update in updates:
                            final_text, emitted = _stream_update(update, final_text, emit)
                            for event in emitted:
                                yield event
            if not final_text:
                raise ModelInvocationError(self._model_id, "the model produced no final response")
        except TimeoutError:
            timeout = self.definition.spec.runtime.timeout_seconds
            timeout_failure = InvocationTimeoutError(float(timeout)) if timeout else InvocationTimeoutError(-1)
            logger.error("LangGraph streaming invocation failed: %s", timeout_failure)
            yield emit("osa.error", str(timeout_failure))
            return
        except SessionError:
            raise
        except OsaError as exc:
            logger.error("LangGraph streaming invocation failed: %s", exc)
            yield emit("osa.error", str(exc))
            return
        except Exception as exc:
            failure = ModelInvocationError(self._model_id, str(exc), cause=exc)
            logger.error("LangGraph streaming invocation failed: %s", failure)
            yield emit("osa.error", str(failure))
            return
        session.add_message("user", request.input)
        session.add_message("assistant", final_text)
        self._session_provider.save(session)
        yield emit("osa.message", final_text)

    async def shutdown(self) -> None:
        """Release backend resources owned by this agent."""
        # Model instances, adapter registries, and shared OSA dependencies are
        # caller-owned. The runtime owns only the checkpointer, if supplied.
        return None


def _stream_update(
    update: Any,
    current_text: str,
    emit: Any,
) -> tuple[str, list[AgentStreamEvent]]:
    """Emit text from LangGraph update chunks and return the latest final text."""
    if not isinstance(update, dict):
        return current_text, []
    latest_text = current_text
    emitted: list[AgentStreamEvent] = []
    for node_update in update.values():
        if not isinstance(node_update, dict):
            continue
        for message in node_update.get("messages", []):
            if not isinstance(message, AIMessage):
                continue
            text = _message_text(message)
            if not text:
                continue
            if not message.tool_calls:
                latest_text = text
            # A model may emit text before a tool call. It is still a useful
            # delta; the terminal osa.message remains the authoritative output.
            emitted.append(emit("osa.message.delta", text))
    return latest_text, emitted


class LangGraphRuntime(AgentRuntime):
    """Runtime that creates OSA agents backed by LangGraph."""

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
        model_adapters: LangChainModelAdapterRegistry | None = None,
        secret_resolver: SecretResolver | None = None,
        observability: Observability | None = None,
        dependencies: RuntimeDependencies | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._dependencies = dependencies or RuntimeDependencies.with_defaults(
            model_provider=model_provider,
            model_catalog=model_catalog,
            tool_catalog=tool_catalog,
            skill_catalog=skill_catalog,
            mcp_catalog=mcp_catalog,
            memory_provider=memory_provider,
            memory_policies=memory_policies,
            session_provider=session_provider,
            secret_resolver=secret_resolver,
            observability=observability,
        )
        self._model_adapters = model_adapters
        self._checkpointer = checkpointer
        self._checkpointer_closed = False
        self._agents: list[OsaLangGraphAgent] = []

    async def create(self, definition: AgentDefinition) -> OsaLangGraphAgent:
        """Create an OSA LangGraph agent from a definition."""
        agent = OsaLangGraphAgent(
            definition=definition,
            dependencies=self._dependencies,
            model_adapters=self._model_adapters,
            checkpointer=self._checkpointer,
        )
        self._agents.append(agent)
        logger.info("Created LangGraph agent '%s'", definition.metadata.name)
        return agent

    @property
    def session_provider(self) -> SessionProvider:
        """The session provider shared by all agents in this runtime."""
        provider = self._dependencies.session_provider
        assert provider is not None
        return provider

    @property
    def tool_catalog(self) -> ToolCatalog:
        """The tool catalog shared by all agents in this runtime."""
        catalog = self._dependencies.tool_catalog
        assert catalog is not None
        return catalog

    @property
    def memory_provider(self) -> MemoryProvider:
        """The memory provider shared by all agents in this runtime."""
        provider = self._dependencies.memory_provider
        assert provider is not None
        return provider

    async def shutdown(self) -> None:
        """Shut down all agents created by this runtime."""
        for agent in self._agents:
            await agent.shutdown()
        self._agents.clear()
        if self._checkpointer is not None and not self._checkpointer_closed:
            await _close_owned_resource(self._checkpointer)
            self._checkpointer_closed = True
        logger.info("LangGraph runtime shut down")


class LangGraphAgentFactory(AgentFactory):
    """Synchronous factory for OSA LangGraph agents."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def create(self, definition: AgentDefinition) -> OsaLangGraphAgent:
        """Create an OSA LangGraph agent synchronously."""
        return OsaLangGraphAgent(definition=definition, **self._kwargs)
