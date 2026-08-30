"""ADK Runtime implementation for Open Simple Agent.

This module implements the GenericAdkAgent and AdkRuntime that convert
an AgentDefinition into a running agent using Google ADK.
"""

from __future__ import annotations

import logging

from osa.generic_agent import (
    AbstractAgent,
    AgentDefinition,
    AgentFactory,
    AgentRequest,
    AgentResponse,
    AgentRuntime,
    FakeModelProvider,
    ModelCatalog,
    ModelProvider,
    SessionManager,
    ToolCatalog,
)

logger = logging.getLogger(__name__)


class GenericAdkAgent(AbstractAgent):
    """ADK-based agent implementation.

    Wraps the generic agent contract with ADK-specific runtime behavior.
    Uses a ModelProvider for model calls and a ToolCatalog for tool resolution.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        model_provider: ModelProvider,
        model_catalog: ModelCatalog,
        tool_catalog: ToolCatalog | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        super().__init__(definition)
        self._model_provider = model_provider
        self._model_catalog = model_catalog
        self._tool_catalog = tool_catalog or ToolCatalog()
        self._session_manager = session_manager or SessionManager()
        self._running = False

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        """Invoke the agent with a request.

        Resolves the model, builds the prompt from the definition's instruction
        and the user's input, calls the model, and returns the response.
        """
        if not self._running:
            self._running = True

        # Resolve model
        model_id = "fake"
        if self.definition.spec.model:
            try:
                model_def = self._model_catalog.resolve(self.definition.spec.model.ref)
                model_id = model_def.model_id
            except KeyError:
                logger.warning("Model '%s' not found, using fallback", self.definition.spec.model.ref)

        # Build prompt from instruction + user input
        instruction = self.definition.spec.instruction or ""
        prompt = f"{instruction}\n\nUser: {request.input}" if instruction else request.input

        # Get or create session
        session = self._session_manager.get_or_create(
            request.session_id, agent_name=self.metadata.name, user_id=request.user_id
        )
        session.add_message("user", request.input)

        try:
            # Call model
            model_response = await self._model_provider.generate(prompt=prompt, model_id=model_id)

            # Store response in session
            session.add_message("assistant", model_response.text)

            return AgentResponse(
                output=model_response.text,
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
    ) -> None:
        self._model_provider = model_provider or FakeModelProvider()
        self._model_catalog = model_catalog or ModelCatalog()
        self._tool_catalog = tool_catalog or ToolCatalog()
        self._session_manager = SessionManager()
        self._agents: list[GenericAdkAgent] = []

    async def create(self, definition: AgentDefinition) -> GenericAdkAgent:
        """Create a GenericAdkAgent from an AgentDefinition."""
        agent = GenericAdkAgent(
            definition=definition,
            model_provider=self._model_provider,
            model_catalog=self._model_catalog,
            tool_catalog=self._tool_catalog,
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
    ) -> None:
        self._model_provider = model_provider or FakeModelProvider()
        self._model_catalog = model_catalog or ModelCatalog()
        self._tool_catalog = tool_catalog or ToolCatalog()
        self._session_manager = SessionManager()

    def create(self, definition: AgentDefinition) -> GenericAdkAgent:
        """Create a GenericAdkAgent from an AgentDefinition."""
        return GenericAdkAgent(
            definition=definition,
            model_provider=self._model_provider,
            model_catalog=self._model_catalog,
            tool_catalog=self._tool_catalog,
            session_manager=self._session_manager,
        )
