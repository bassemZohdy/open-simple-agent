"""ADK Runtime implementation for Open Simple Agent.

This module implements the GenericAdkAgent and AdkRuntime that convert
an AgentDefinition into a running agent using Google ADK.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from osa.generic_agent import (
    AbstractAgent,
    AgentDefinition,
    AgentFactory,
    AgentRequest,
    AgentResponse,
    AgentRuntime,
    FakeModelProvider,
    MemoryEntry,
    MemoryProvider,
    ModelCatalog,
    ModelProvider,
    SessionManager,
    SkillCatalog,
    SkillDefinition,
    Tool,
    ToolCatalog,
    ToolDefinition,
    ToolResult,
    ToolTimeoutError,
)
from osa.runtimes.adk.llm_agent import build_llm_agent, build_runner

logger = logging.getLogger(__name__)

_TOOL_CALL_PREFIX = "TOOL_CALL"
_DEFAULT_MAX_TOOL_ITERATIONS = 3


def _parse_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Parse a tool-call directive: ``TOOL_CALL <name> {<json arguments>}``.

    This is the transitional invocation protocol used until ADK `LlmAgent`
    function-calling is wired up (Milestone 8 leftover). Responses that do not
    start with the prefix are treated as final answers.
    """
    stripped = text.strip()
    if not stripped.startswith(_TOOL_CALL_PREFIX):
        return None
    parts = stripped.split(" ", 2)
    if len(parts) < 2 or not parts[1]:
        return None
    raw_arguments = parts[2] if len(parts) == 3 else "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        arguments = None
    return (parts[1], arguments if isinstance(arguments, dict) else {})


class GenericAdkAgent(AbstractAgent):
    """ADK-based agent implementation.

    Wraps the generic agent contract with ADK-specific runtime behavior.
    Uses a ModelProvider for model calls, a ToolCatalog for tool resolution,
    and a SkillCatalog for skill metadata.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        model_provider: ModelProvider,
        model_catalog: ModelCatalog,
        tool_catalog: ToolCatalog | None = None,
        skill_catalog: SkillCatalog | None = None,
        memory_provider: MemoryProvider | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        super().__init__(definition)
        self._model_provider = model_provider
        self._model_catalog = model_catalog
        self._tool_catalog = tool_catalog or ToolCatalog()
        self._skill_catalog = skill_catalog or SkillCatalog()
        self._memory_provider = memory_provider
        self._session_manager = session_manager or SessionManager()
        self._running = False
        self._tools: dict[str, Tool] = {}
        self._tool_definitions: dict[str, ToolDefinition] = {}
        self._skills: list[SkillDefinition] = []
        self._resolve_definition_resources()
        self._model_id = self._resolve_model_id()
        # ADK objects for the definition: a real model can be routed through
        # them when configured; deterministic invocation keeps using the
        # injected ModelProvider until then.
        self.llm_agent = build_llm_agent(self.definition, self._model_id, self._tools)
        self.runner = build_runner(self.llm_agent)

    def _resolve_model_id(self) -> str:
        if not self.definition.spec.model:
            return "fake"
        try:
            return self._model_catalog.resolve(self.definition.spec.model.ref).model_id
        except KeyError:
            logger.warning("Model '%s' not found, using fallback", self.definition.spec.model.ref)
            return "fake"

    def _resolve_definition_resources(self) -> None:
        """Resolve spec.tools and spec.skills against their catalogs.

        Runs at construction so a definition referencing a resource that does
        not exist fails fast instead of silently doing nothing at invocation.
        """
        agent_name = self.metadata.name
        for tool_ref in self.definition.spec.tools:
            try:
                self._tool_definitions[tool_ref.ref] = self._tool_catalog.get_definition(tool_ref.ref)
                self._tools[tool_ref.ref] = self._tool_catalog.get_tool(tool_ref.ref)
            except KeyError as exc:
                raise ValueError(
                    f"Tool '{tool_ref.ref}' referenced by agent '{agent_name}' was not found in the tool catalog"
                ) from exc
        for skill_ref in self.definition.spec.skills:
            try:
                self._skills.append(self._skill_catalog.resolve(skill_ref.ref))
            except KeyError as exc:
                raise ValueError(
                    f"Skill '{skill_ref.ref}' referenced by agent '{agent_name}' was not found in the skill catalog"
                ) from exc

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

    async def _load_memory_context(self, scope_id: str, query: str) -> str:
        """Load policy-controlled memory relevant to the request.

        Memory context is only injected when ``spec.memory.enabled`` and a
        provider are configured; raw interactions are never auto-persisted.
        """
        memory_cfg = self.definition.spec.memory
        if not memory_cfg.enabled or self._memory_provider is None:
            return ""
        entries = await self._memory_provider.search(query, memory_cfg.scope, scope_id=scope_id, limit=5)
        if not entries:
            return ""
        lines = "\n".join(f"- {entry.content}" for entry in entries)
        return f"Memory:\n{lines}"

    async def remember(self, key: str, content: str, *, scope_id: str = "") -> None:
        """Store a memory entry explicitly.

        Raw interactions are never persisted automatically; memory writes go
        through this API (or future policy-driven extraction).
        """
        if self._memory_provider is None:
            raise RuntimeError(f"No memory provider configured for agent '{self.metadata.name}'")
        memory_cfg = self.definition.spec.memory
        await self._memory_provider.store(
            MemoryEntry(key=key, content=content, scope=memory_cfg.scope, scope_id=scope_id)
        )

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        """Invoke the agent with a request.

        Resolves the model, builds the prompt from the definition's
        instruction, policy-loaded memory, and the user's input, and calls the
        model. If the model answers with a ``TOOL_CALL`` directive (see
        ``_parse_tool_call``), the referenced tool is executed — subject to its
        configured timeout — and the result is fed back to the model until it
        produces a final answer.
        """
        if not self._running:
            self._running = True

        # Build prompt from instruction + memory context + user input
        instruction = self.definition.spec.instruction or ""
        memory_context = await self._load_memory_context(request.user_id or "", request.input)
        base = f"{instruction}\n\n{memory_context}" if memory_context else instruction
        prompt = f"{base}\n\nUser: {request.input}" if base else request.input

        # Get or create session
        session = self._session_manager.get_or_create(
            request.session_id, agent_name=self.metadata.name, user_id=request.user_id
        )
        session.add_message("user", request.input)

        max_iterations = self.definition.spec.runtime.max_iterations or _DEFAULT_MAX_TOOL_ITERATIONS
        try:
            output: str | None = None
            for _ in range(max_iterations + 1):
                model_response = await self._model_provider.generate(prompt=prompt, model_id=self._model_id)
                tool_call = _parse_tool_call(model_response.text)
                if tool_call is None:
                    output = model_response.text
                    break
                tool_name, arguments = tool_call
                logger.info("Agent '%s' requested tool '%s'", self.metadata.name, tool_name)
                result = await self.execute_tool(tool_name, **arguments)
                prompt = f"{prompt}\n\nTool '{tool_name}' result (success={result.success}): {result.output}"

            if output is None:
                raise RuntimeError(f"Tool iteration limit ({max_iterations}) exceeded without a final answer")

            # Store response in session
            session.add_message("assistant", output)

            return AgentResponse(
                output=output,
                invocation_id=request.invocation_id,
                session_id=str(session.session_id),
            )
        except Exception as e:
            logger.error("Agent invocation failed: %s", e)
            return AgentResponse(
                output="",
                invocation_id=request.invocation_id,
                session_id=str(session.session_id),
                error=str(e),
            )

    async def shutdown(self) -> None:
        self._running = False


class AdkRuntime(AgentRuntime):
    """ADK runtime that creates GenericAdkAgent instances from definitions."""

    def __init__(
        self,
        model_provider: ModelProvider | None = None,
        model_catalog: ModelCatalog | None = None,
        tool_catalog: ToolCatalog | None = None,
        skill_catalog: SkillCatalog | None = None,
        memory_provider: MemoryProvider | None = None,
    ) -> None:
        self._model_provider = model_provider or FakeModelProvider()
        self._model_catalog = model_catalog or ModelCatalog()
        self._tool_catalog = tool_catalog or ToolCatalog()
        self._skill_catalog = skill_catalog or SkillCatalog()
        self._memory_provider = memory_provider
        self._session_manager = SessionManager()
        self._agents: list[GenericAdkAgent] = []

    async def create(self, definition: AgentDefinition) -> GenericAdkAgent:
        """Create a GenericAdkAgent from an AgentDefinition."""
        agent = GenericAdkAgent(
            definition=definition,
            model_provider=self._model_provider,
            model_catalog=self._model_catalog,
            tool_catalog=self._tool_catalog,
            skill_catalog=self._skill_catalog,
            memory_provider=self._memory_provider,
            session_manager=self._session_manager,
        )
        self._agents.append(agent)
        logger.info("Created agent '%s'", definition.metadata.name)
        return agent

    async def shutdown(self) -> None:
        """Shut down all agents created by this runtime."""
        for agent in self._agents:
            await agent.shutdown()
        self._agents.clear()
        logger.info("ADK runtime shut down")


class AdkAgentFactory(AgentFactory):
    """Factory that creates GenericAdkAgent instances synchronously."""

    def __init__(
        self,
        model_provider: ModelProvider | None = None,
        model_catalog: ModelCatalog | None = None,
        tool_catalog: ToolCatalog | None = None,
        skill_catalog: SkillCatalog | None = None,
        memory_provider: MemoryProvider | None = None,
    ) -> None:
        self._model_provider = model_provider or FakeModelProvider()
        self._model_catalog = model_catalog or ModelCatalog()
        self._tool_catalog = tool_catalog or ToolCatalog()
        self._skill_catalog = skill_catalog or SkillCatalog()
        self._memory_provider = memory_provider
        self._session_manager = SessionManager()

    def create(self, definition: AgentDefinition) -> GenericAdkAgent:
        """Create a GenericAdkAgent from an AgentDefinition."""
        return GenericAdkAgent(
            definition=definition,
            model_provider=self._model_provider,
            model_catalog=self._model_catalog,
            tool_catalog=self._tool_catalog,
            skill_catalog=self._skill_catalog,
            memory_provider=self._memory_provider,
            session_manager=self._session_manager,
        )
