"""Model adapters: build ADK model instances from ModelDefinitions.

Adapters are the seam between the OSA Model Catalog and ADK execution. The
``litellm`` adapter is the first production path (any provider LiteLLM
supports); the ``fake`` adapter wraps a deterministic :class:`ModelProvider`
for offline tests.

Parameter precedence for generation settings (explicit):
    ``ModelDefinition.runtime_settings`` (catalog defaults) are overridden by
    ``ModelRef.parameters`` (per-agent overrides).
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, Protocol

from osa.generic_agent.errors import ModelConfigurationError

if TYPE_CHECKING:
    from google.adk.models.base_llm import BaseLlm

    from osa.generic_agent import ModelDefinition, ModelProvider, ModelRuntimeSettings
    from osa.generic_agent.secret import SecretResolver

FAKE_PROVIDER = "fake"
LITELLM_PROVIDER = "litellm"


class ModelAdapter(Protocol):
    """Builds an ADK model for a resolved model definition."""

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseLlm:
        """Build the ADK model instance.

        Args:
            definition: The resolved model definition.
            parameters: Per-agent overrides from ``ModelRef.parameters``.
        """
        ...


def _runtime_setting_kwargs(settings: ModelRuntimeSettings) -> dict[str, Any]:
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


class LiteLlmAdapter:
    """Production adapter: any LiteLLM-supported provider via ADK's ``LiteLlm``.

    When the definition carries a ``credential_ref``, the secret is resolved
    with the configured :class:`SecretResolver` and passed to LiteLLM as the
    API key. Resolved values are held only by the model client — never in OSA
    models, responses, or logs.

    Requires the optional ``litellm`` dependency (``osa-adk-runtime[litellm]``).
    """

    def __init__(self, secret_resolver: SecretResolver | None = None) -> None:
        self._secret_resolver = secret_resolver

    @staticmethod
    def _require_litellm() -> None:
        if find_spec("litellm") is None:
            raise ModelConfigurationError(
                f"The '{LITELLM_PROVIDER}' model provider requires the optional 'litellm' "
                "dependency; install the 'osa-adk-runtime[litellm]' extra"
            )

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseLlm:
        self._require_litellm()
        from google.adk.models.lite_llm import LiteLlm

        kwargs = _runtime_setting_kwargs(definition.runtime_settings)
        kwargs.update(parameters)
        if definition.credential_ref is not None:
            if self._secret_resolver is None:
                raise ModelConfigurationError(
                    f"Model '{definition.name}' requires a secret resolver to resolve "
                    f"credential '{definition.credential_ref.key}'"
                )
            kwargs["api_key"] = self._secret_resolver.resolve(definition.credential_ref)
        return LiteLlm(model=definition.model_id, **kwargs)


class FakeProviderAdapter:
    """Deterministic adapter for tests: bridges a ``ModelProvider`` into ADK.

    The provider receives a single flattened prompt string (system instruction
    plus conversation text), keeping offline tests deterministic.
    """

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def build(self, definition: ModelDefinition, parameters: dict[str, Any]) -> BaseLlm:
        from osa.runtimes.adk.llm_agent import ProviderBackedLlm

        return ProviderBackedLlm(model=definition.model_id, provider=self._provider)


class ModelAdapterRegistry:
    """Maps model provider names to adapters (fail-fast on unknown providers)."""

    def __init__(self) -> None:
        self._adapters: dict[str, ModelAdapter] = {}

    def register(self, provider: str, adapter: ModelAdapter) -> None:
        self._adapters[provider] = adapter

    def resolve(self, provider: str) -> ModelAdapter:
        adapter = self._adapters.get(provider)
        if adapter is None:
            known = ", ".join(sorted(self._adapters)) or "none registered"
            raise ModelConfigurationError(
                f"No model adapter registered for provider '{provider}'. Known providers: {known}"
            )
        return adapter


def default_registry(
    fake_provider: ModelProvider | None = None,
    secret_resolver: SecretResolver | None = None,
) -> ModelAdapterRegistry:
    """Registry with the built-in ``fake`` and ``litellm`` adapters.

    The ``fake`` adapter is only registered when a deterministic provider is
    supplied, so a live deployment cannot silently fall back to a fake model.
    """
    registry = ModelAdapterRegistry()
    if fake_provider is not None:
        registry.register(FAKE_PROVIDER, FakeProviderAdapter(fake_provider))
    registry.register(LITELLM_PROVIDER, LiteLlmAdapter(secret_resolver))
    return registry
