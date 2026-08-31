"""Secret resolution contract.

A secret reference names an external secret; the resolved value is only ever
returned by a resolver and must never be stored on models, included in API
responses, or written to logs or exception messages.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from osa.generic_agent.config import SecretReference


class SecretError(Exception):
    """Base error for secret resolution failures."""


class SecretResolutionError(SecretError):
    """A secret could not be resolved.

    Only the reference coordinates (source/key) are reported; the missing or
    resolved value itself is never included in the message.
    """

    def __init__(self, reference: SecretReference, env_var: str) -> None:
        self.reference = reference
        self.env_var = env_var
        super().__init__(
            f"Secret '{reference.key}' (source: {reference.source}) could not be resolved "
            f"from environment variable '{env_var}'"
        )


class SecretSourceError(SecretError):
    """The secret source is not supported by the resolver."""

    def __init__(self, reference: SecretReference, resolver: str) -> None:
        self.reference = reference
        super().__init__(
            f"Secret source '{reference.source}' is not supported by the {resolver}; "
            f"cannot resolve secret '{reference.key}'"
        )


class SecretResolver(ABC):
    """Resolves `SecretReference` objects to secret values.

    Implementations return the value only to the caller. Values must never be
    persisted, logged, or embedded in errors.
    """

    @abstractmethod
    def resolve(self, reference: SecretReference) -> str:
        """Resolve a secret reference to its value.

        Raises:
            SecretError: If the secret cannot be resolved. The error message
                identifies the reference, never the value.
        """
        ...


class EnvironmentSecretResolver(SecretResolver):
    """Resolves secrets from environment variables.

    Reference semantics:
        - ``source`` must be ``env``.
        - The environment variable is ``reference.env_var`` when set,
          otherwise ``reference.key`` is used directly as the variable name.
    """

    ENV_SOURCE = "env"

    def resolve(self, reference: SecretReference) -> str:
        if reference.source != self.ENV_SOURCE:
            raise SecretSourceError(reference, self.__class__.__name__)
        env_var = reference.env_var or reference.key
        value = os.environ.get(env_var)
        if value is None:
            raise SecretResolutionError(reference, env_var)
        return value
