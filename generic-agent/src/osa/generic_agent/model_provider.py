"""Model provider interface and implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class ModelResponse:
    """Response from a model provider."""

    text: str
    model_id: str
    usage: TokenUsage = field(default_factory=lambda: TokenUsage())
    response_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass
class TokenUsage:
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelProvider(ABC):
    """Interface for model providers.

    Implementations handle the actual model API calls.
    """

    @abstractmethod
    async def generate(self, prompt: str, model_id: str, **kwargs: object) -> ModelResponse:
        """Generate a response from the model.

        Args:
            prompt: The input prompt.
            model_id: The model identifier.
            **kwargs: Provider-specific parameters.

        Returns:
            The model response.
        """
        ...


class FakeModelProvider(ModelProvider):
    """Deterministic fake model provider for testing.

    Returns predictable responses without making API calls.
    """

    def __init__(self, response: str = "fake response") -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def generate(self, prompt: str, model_id: str, **kwargs: object) -> ModelResponse:
        self.calls.append({"prompt": prompt, "model_id": model_id, **kwargs})
        return ModelResponse(
            text=self._response,
            model_id=model_id,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
