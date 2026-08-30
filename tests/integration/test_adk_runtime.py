"""Tests for the ADK runtime vertical slice.

Verifies the end-to-end flow:
    AgentDefinition -> ADK Runtime -> Fake Model -> Response
"""

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    FakeModelProvider,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
    ModelResponse,
)
from osa.runtimes.adk import AdkAgentFactory, AdkRuntime, GenericAdkAgent


def _make_definition(name: str = "test-agent", instruction: str = "Help users.") -> AgentDefinition:
    return AgentDefinition(
        metadata=AgentMetadataConfig(name=name),
        spec=AgentSpec(instruction=instruction, model=ModelRef(ref="default")),
    )


def _make_catalog_with_default() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model"))
    return catalog


class TestGenericAdkAgent:
    async def test_invoke_returns_response(self) -> None:
        provider = FakeModelProvider(response="Hello! How can I help?")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        request = AgentRequest(input="Hi there")
        response = await agent.invoke(request)

        assert response.output == "Hello! How can I help?"
        assert response.error is None
        assert response.invocation_id == request.invocation_id

    async def test_invoke_includes_instruction_in_prompt(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition(instruction="You are a support agent.")

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        await agent.invoke(AgentRequest(input="help me"))
        prompt = provider.calls[0]["prompt"]
        assert isinstance(prompt, str)
        assert "You are a support agent." in prompt
        assert "help me" in prompt

    async def test_invoke_creates_session(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        response = await agent.invoke(AgentRequest(input="hello"))
        assert response.session_id is not None

    async def test_invoke_tracks_conversation(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        r1 = await agent.invoke(AgentRequest(input="first"))
        await agent.invoke(AgentRequest(input="second", session_id=r1.session_id))
        assert len(provider.calls) == 2

    async def test_invoke_handles_model_error(self) -> None:
        class ErrorProvider(FakeModelProvider):
            async def generate(self, prompt: str, model_id: str, **kwargs: object) -> ModelResponse:
                raise RuntimeError("model failed")

        provider = ErrorProvider()
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        response = await agent.invoke(AgentRequest(input="hello"))
        assert response.error is not None
        assert "model failed" in response.error

    async def test_invoke_uses_resolved_model(self) -> None:
        provider = FakeModelProvider(response="ok")
        catalog = _make_catalog_with_default()
        definition = _make_definition()

        agent = GenericAdkAgent(
            definition=definition,
            model_provider=provider,
            model_catalog=catalog,
        )

        await agent.invoke(AgentRequest(input="hello"))
        assert provider.calls[0]["model_id"] == "fake-model"

    async def test_shutdown(self) -> None:
        provider = FakeModelProvider()
        catalog = _make_catalog_with_default()
        agent = GenericAdkAgent(
            definition=_make_definition(),
            model_provider=provider,
            model_catalog=catalog,
        )
        await agent.invoke(AgentRequest(input="hi"))
        await agent.shutdown()


class TestAdkRuntime:
    async def test_create_agent(self) -> None:
        runtime = AdkRuntime(
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_make_catalog_with_default(),
        )
        definition = _make_definition()
        agent = await runtime.create(definition)

        assert isinstance(agent, GenericAdkAgent)
        assert agent.metadata.name == "test-agent"

    async def test_invoke_through_runtime(self) -> None:
        provider = FakeModelProvider(response="I can help with that!")
        runtime = AdkRuntime(
            model_provider=provider,
            model_catalog=_make_catalog_with_default(),
        )
        definition = _make_definition(instruction="Be helpful.")
        agent = await runtime.create(definition)

        request = AgentRequest(input="I need help")
        response = await agent.invoke(request)

        assert response.output == "I can help with that!"
        assert response.error is None

    async def test_shutdown_all_agents(self) -> None:
        runtime = AdkRuntime(
            model_provider=FakeModelProvider(),
            model_catalog=_make_catalog_with_default(),
        )
        await runtime.create(_make_definition("a"))
        await runtime.create(_make_definition("b"))
        await runtime.shutdown()


class TestAdkAgentFactory:
    def test_create_agent(self) -> None:
        factory = AdkAgentFactory(
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_make_catalog_with_default(),
        )
        definition = _make_definition()
        agent = factory.create(definition)

        assert isinstance(agent, GenericAdkAgent)
        assert agent.metadata.name == "test-agent"
