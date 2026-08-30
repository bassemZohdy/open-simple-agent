"""Tests for the model domain types and catalog."""

import pytest

from osa.generic_agent import (
    FakeModelProvider,
    ModelCapabilities,
    ModelCatalog,
    ModelDefinition,
    ModelResponse,
    ModelRuntimeSettings,
    TokenUsage,
)


class TestModelCapabilities:
    def test_defaults(self) -> None:
        c = ModelCapabilities()
        assert c.streaming is False
        assert c.function_calling is False
        assert c.vision is False
        assert c.json_mode is False
        assert c.max_context_tokens is None

    def test_custom(self) -> None:
        c = ModelCapabilities(streaming=True, max_context_tokens=128000)
        assert c.streaming is True
        assert c.max_context_tokens == 128000


class TestModelRuntimeSettings:
    def test_defaults(self) -> None:
        s = ModelRuntimeSettings()
        assert s.temperature is None
        assert s.max_tokens is None
        assert s.stop_sequences == []

    def test_custom(self) -> None:
        s = ModelRuntimeSettings(temperature=0.7, max_tokens=1024)
        assert s.temperature == 0.7
        assert s.max_tokens == 1024


class TestModelDefinition:
    def test_create(self) -> None:
        m = ModelDefinition(
            name="default",
            provider="openai",
            model_id="gpt-4",
            description="GPT-4 model",
        )
        assert m.name == "default"
        assert m.provider == "openai"
        assert m.model_id == "gpt-4"
        assert m.is_default is False

    def test_with_capabilities(self) -> None:
        m = ModelDefinition(
            name="fast",
            provider="openai",
            model_id="gpt-4o-mini",
            capabilities=ModelCapabilities(streaming=True, function_calling=True),
        )
        assert m.capabilities.streaming is True
        assert m.capabilities.function_calling is True


class TestModelCatalog:
    def test_register_and_resolve(self) -> None:
        catalog = ModelCatalog()
        model = ModelDefinition(name="default", provider="openai", model_id="gpt-4")
        catalog.register(model)
        resolved = catalog.resolve("default")
        assert resolved.name == "default"
        assert resolved.model_id == "gpt-4"

    def test_resolve_missing_raises(self) -> None:
        catalog = ModelCatalog()
        with pytest.raises(KeyError, match="Model not found"):
            catalog.resolve("nonexistent")

    def test_get_default(self) -> None:
        catalog = ModelCatalog()
        catalog.register(ModelDefinition(name="fast", provider="openai", model_id="gpt-4o-mini"))
        catalog.register(ModelDefinition(name="default", provider="openai", model_id="gpt-4", is_default=True))
        default = catalog.get_default()
        assert default is not None
        assert default.name == "default"

    def test_get_default_returns_none(self) -> None:
        catalog = ModelCatalog()
        catalog.register(ModelDefinition(name="fast", provider="openai", model_id="gpt-4o-mini"))
        assert catalog.get_default() is None

    def test_list_models(self) -> None:
        catalog = ModelCatalog()
        catalog.register(ModelDefinition(name="a", provider="p", model_id="m1"))
        catalog.register(ModelDefinition(name="b", provider="p", model_id="m2"))
        assert len(catalog.list_models()) == 2

    def test_contains(self) -> None:
        catalog = ModelCatalog()
        catalog.register(ModelDefinition(name="x", provider="p", model_id="m"))
        assert "x" in catalog
        assert "y" not in catalog

    def test_len(self) -> None:
        catalog = ModelCatalog()
        assert len(catalog) == 0
        catalog.register(ModelDefinition(name="x", provider="p", model_id="m"))
        assert len(catalog) == 1


class TestFakeModelProvider:
    async def test_generate(self) -> None:
        provider = FakeModelProvider(response="hello world")
        response = await provider.generate(prompt="hi", model_id="fake")
        assert response.text == "hello world"
        assert response.model_id == "fake"
        assert isinstance(response, ModelResponse)

    async def test_tracks_calls(self) -> None:
        provider = FakeModelProvider()
        await provider.generate(prompt="a", model_id="m1")
        await provider.generate(prompt="b", model_id="m2")
        assert len(provider.calls) == 2
        assert provider.calls[0]["prompt"] == "a"
        assert provider.calls[1]["model_id"] == "m2"

    async def test_default_usage(self) -> None:
        provider = FakeModelProvider()
        response = await provider.generate(prompt="x", model_id="m")
        assert response.usage.total_tokens == 15


class TestModelIntegration:
    def test_resolve_model_from_agent_definition(self) -> None:
        from osa.generic_agent import AgentDefinition, AgentMetadataConfig, AgentSpec, ModelRef

        catalog = ModelCatalog()
        catalog.register(ModelDefinition(name="default", provider="openai", model_id="gpt-4"))
        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="test"),
            spec=AgentSpec(model=ModelRef(ref="default")),
        )
        assert definition.spec.model is not None
        resolved = catalog.resolve(definition.spec.model.ref)
        assert resolved.model_id == "gpt-4"

    def test_model_with_credential_ref(self) -> None:
        from osa.generic_agent import SecretReference

        m = ModelDefinition(
            name="prod",
            provider="openai",
            model_id="gpt-4",
            endpoint="https://api.openai.com/v1",
            credential_ref=SecretReference(source="vault", key="openai-key"),
        )
        assert m.endpoint == "https://api.openai.com/v1"
        assert m.credential_ref is not None
        assert m.credential_ref.source == "vault"


class TestTokenUsage:
    def test_defaults(self) -> None:
        u = TokenUsage()
        assert u.prompt_tokens == 0
        assert u.total_tokens == 0
