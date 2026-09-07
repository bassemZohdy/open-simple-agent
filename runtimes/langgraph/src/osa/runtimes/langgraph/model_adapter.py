"""LangChain chat-model adapters for OSA model catalog definitions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from osa.generic_agent.errors import ModelConfigurationError
from osa.generic_agent.model_provider import ModelProvider  # noqa: TC001 - pydantic field type
from osa.generic_agent.observability import Observability  # noqa: TC001 - pydantic field type

if TYPE_CHECKING:
    from osa.generic_agent import ModelDefinition, ModelRuntimeSettings
    from osa.generic_agent.secret import SecretResolver


def _runtime_setting_kwargs(settings: ModelRuntimeSettings) -> dict[str, Any]:
    """Translate OSA generation settings to LangChain model arguments."""
    kwargs: dict[str, Any] = {}
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    if settings.top_p is not None:
        kwargs["top_p"] = settings.top_p
    if settings.max_tokens is not None:
        kwargs["max_tokens"] = settings.max_tokens
    if settings.stop_sequences:
        kwargs["stop"] = settings.stop_sequences
    kwargs.update(settings.extra)
    return kwargs


def _message_text(messages: list[BaseMessage]) -> str:
    """Flatten LangChain messages for the generic deterministic provider."""
    lines: list[str] = []
    for message in messages:
        content = message.content
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            blocks: list[str] = []
            for block in content:
                if isinstance(block, str):
                    blocks.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    blocks.append(block["text"])
            text = "".join(blocks)
        else:
            text = str(content)
        if text:
            lines.append(f"{message.type}: {text}")
    return "\n\n".join(lines)


class ProviderBackedChatModel(BaseChatModel):
    """LangChain chat model bridged to OSA's deterministic provider contract."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str
    provider: ModelProvider
    generation_parameters: dict[str, Any] = Field(default_factory=dict)
    observability: Observability | None = None

    @property
    def _llm_type(self) -> str:
        return "osa-model-provider"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        parameters = dict(self.generation_parameters)
        if stop:
            parameters["stop"] = stop
        parameters.update(kwargs)
        response = await self.provider.generate(
            prompt=_message_text(messages),
            model_id=self.model_name,
            **parameters,
        )
        if self.observability is not None:
            self.observability.record_token_usage(
                self.model_name,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=response.text))],
            llm_output={
                "model_id": response.model_id,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
            },
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Support synchronous LangChain callers outside an event loop."""
        return asyncio.run(self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs))

    def bind_tools(self, tools: Any, **kwargs: Any) -> ProviderBackedChatModel:
        """Keep deterministic providers constructible with OSA tool catalogs.

        ``ModelProvider`` is intentionally a text-only test contract, so it
        cannot produce structured tool calls. The graph still exposes the
        tools to real LangChain models; the deterministic bridge simply keeps
        them as metadata-free model inputs.
        """
        del tools, kwargs
        return self


class LangChainModelAdapter(Protocol):
    """Build a LangChain chat model from a resolved OSA model definition."""

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseChatModel:
        """Build the framework-native chat model."""
        ...


class FakeProviderAdapter:
    """Offline adapter used only when an explicit OSA model provider is given."""

    def __init__(self, provider: ModelProvider, observability: Observability | None = None) -> None:
        self._provider = provider
        self._observability = observability

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseChatModel:
        return ProviderBackedChatModel(
            model_name=definition.model_id,
            provider=self._provider,
            generation_parameters=dict(parameters),
            observability=self._observability,
        )


class InitChatModelAdapter:
    """Adapter for LangChain provider integrations via ``init_chat_model``."""

    def __init__(self, secret_resolver: SecretResolver | None = None) -> None:
        self._secret_resolver = secret_resolver

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseChatModel:
        kwargs = _runtime_setting_kwargs(definition.runtime_settings)
        kwargs.update(parameters)
        if definition.endpoint is not None:
            kwargs.setdefault("base_url", definition.endpoint)
        if definition.credential_ref is not None:
            if self._secret_resolver is None:
                raise ModelConfigurationError(
                    f"Model '{definition.name}' requires a secret resolver to resolve "
                    f"credential '{definition.credential_ref.key}'"
                )
            kwargs["api_key"] = self._secret_resolver.resolve(definition.credential_ref)

        # ``langchain`` and ``langgraph`` are OSA aliases for a provider-prefixed
        # model ID (for example ``openai:gpt-4.1-mini``). For concrete provider
        # names, pass the provider through to LangChain's resolver.
        provider = None if definition.provider in {"langchain", "langgraph"} else definition.provider
        try:
            return cast("BaseChatModel", init_chat_model(definition.model_id, model_provider=provider, **kwargs))
        except Exception as exc:
            raise ModelConfigurationError(
                f"Unable to initialize LangChain model '{definition.model_id}' "
                f"for provider '{definition.provider}'; verify its integration and configuration"
            ) from exc


class LiteLlmAdapter:
    """Optional compatibility adapter for OSA's existing LiteLLM models."""

    def __init__(self, secret_resolver: SecretResolver | None = None) -> None:
        self._secret_resolver = secret_resolver

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseChatModel:
        try:
            from langchain_litellm import ChatLiteLLM
        except ImportError as exc:
            raise ModelConfigurationError(
                "The 'litellm' model provider requires the optional 'osa-langgraph-runtime[litellm]' dependency"
            ) from exc

        kwargs = _runtime_setting_kwargs(definition.runtime_settings)
        kwargs.update(parameters)
        if definition.endpoint is not None:
            kwargs.setdefault("api_base", definition.endpoint)
        if definition.credential_ref is not None:
            if self._secret_resolver is None:
                raise ModelConfigurationError(
                    f"Model '{definition.name}' requires a secret resolver to resolve "
                    f"credential '{definition.credential_ref.key}'"
                )
            kwargs["api_key"] = self._secret_resolver.resolve(definition.credential_ref)
        try:
            return cast("BaseChatModel", ChatLiteLLM(model=definition.model_id, **kwargs))
        except Exception as exc:
            raise ModelConfigurationError(
                f"Unable to initialize LiteLLM model '{definition.model_id}'; verify its configuration"
            ) from exc


class LangChainModelAdapterRegistry:
    """Maps OSA model provider names to LangChain model adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, LangChainModelAdapter] = {}

    def register(self, provider: str, adapter: LangChainModelAdapter) -> None:
        """Register or replace an adapter for a provider name."""
        self._adapters[provider] = adapter

    def resolve(self, provider: str) -> LangChainModelAdapter:
        """Resolve a provider or fail before the agent becomes ready."""
        adapter = self._adapters.get(provider)
        if adapter is None:
            known = ", ".join(sorted(self._adapters)) or "none registered"
            raise ModelConfigurationError(
                f"No LangChain model adapter registered for provider '{provider}'. Known providers: {known}"
            )
        return adapter


def default_registry(
    fake_provider: ModelProvider | None = None,
    secret_resolver: SecretResolver | None = None,
    observability: Observability | None = None,
) -> LangChainModelAdapterRegistry:
    """Build the default LangChain adapter registry.

    The explicit provider names are deliberately narrow. Adding a provider is
    a controlled runtime packaging decision, not a way for an agent bundle to
    import arbitrary code.
    """
    registry = LangChainModelAdapterRegistry()
    if fake_provider is not None:
        registry.register("fake", FakeProviderAdapter(fake_provider, observability))
    init_adapter = InitChatModelAdapter(secret_resolver)
    for provider in (
        "anthropic",
        "azure_openai",
        "bedrock_converse",
        "google_genai",
        "google_vertexai",
        "groq",
        "langchain",
        "langgraph",
        "mistralai",
        "ollama",
        "openai",
        "openrouter",
        "together",
    ):
        registry.register(provider.strip(), init_adapter)
    registry.register("litellm", LiteLlmAdapter(secret_resolver))
    return registry
