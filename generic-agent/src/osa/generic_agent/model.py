"""Model domain types."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from osa.generic_agent.config import SecretReference, StrictModel


class ModelCapabilities(StrictModel):
    """Describes what a model supports."""

    streaming: bool = False
    function_calling: bool = False
    vision: bool = False
    json_mode: bool = False
    max_context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)


class ModelRuntimeSettings(StrictModel):
    """Runtime generation settings for a model."""

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    stop_sequences: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ModelDefinition(StrictModel):
    """Definition of a model in the Model Catalog.

    This is a reusable configuration that agents reference by name.
    """

    name: str
    provider: str
    model_id: str
    description: str = ""
    endpoint: str | None = None
    credential_ref: SecretReference | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    runtime_settings: ModelRuntimeSettings = Field(default_factory=ModelRuntimeSettings)
    is_default: bool = False


class ModelCatalog:
    """In-memory catalog of model definitions.

    Agents reference models by name via `model.ref`.
    The catalog resolves the reference to a ModelDefinition.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelDefinition] = {}

    def register(self, model: ModelDefinition) -> None:
        """Register a model definition."""
        self._models[model.name] = model

    def resolve(self, ref: str) -> ModelDefinition:
        """Resolve a model reference to its definition.

        Args:
            ref: The model reference name.

        Returns:
            The matching ModelDefinition.

        Raises:
            KeyError: If no model with that name is registered.
        """
        if ref not in self._models:
            raise KeyError(f"Model not found: {ref}")
        return self._models[ref]

    def get_default(self) -> ModelDefinition | None:
        """Return the default model, if one is registered."""
        for model in self._models.values():
            if model.is_default:
                return model
        return None

    def list_models(self) -> list[ModelDefinition]:
        """Return all registered models."""
        return list(self._models.values())

    def __len__(self) -> int:
        return len(self._models)

    def __contains__(self, ref: str) -> bool:
        return ref in self._models
