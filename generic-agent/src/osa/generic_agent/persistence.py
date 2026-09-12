"""Explicit persistence policy and shared-store validation."""

from __future__ import annotations

import os
from enum import StrEnum

from osa.generic_agent.errors import PersistenceConfigurationError

PERSISTENCE_POLICY_ENV_VAR = "OSA_PERSISTENCE_POLICY"


class PersistencePolicy(StrEnum):
    """Deployment posture for stateful OSA subsystems."""

    LOCAL = "local"
    SHARED = "shared"


def get_persistence_policy(environ: dict[str, str] | None = None) -> PersistencePolicy:
    """Read the explicit persistence policy, defaulting to local development."""
    values = os.environ if environ is None else environ
    raw = values.get(PERSISTENCE_POLICY_ENV_VAR)
    if raw is None:
        return PersistencePolicy.LOCAL
    value = raw.strip().lower()
    try:
        return PersistencePolicy(value)
    except ValueError as exc:
        raise PersistenceConfigurationError(f"{PERSISTENCE_POLICY_ENV_VAR} must be 'local' or 'shared'") from exc


def require_shared_database(database_url: str | None, surface: str) -> None:
    """Require a PostgreSQL DSN when the shared policy protects a surface."""
    if get_persistence_policy() != PersistencePolicy.SHARED:
        return
    if database_url is None or not database_url.strip():
        raise PersistenceConfigurationError(
            f"OSA_PERSISTENCE_POLICY=shared requires a PostgreSQL database for {surface}"
        )
    backend = database_url.split(":", 1)[0].split("+", 1)[0].lower()
    if backend != "postgresql":
        raise PersistenceConfigurationError(
            f"OSA_PERSISTENCE_POLICY=shared requires PostgreSQL for {surface}; "
            "SQLite and in-memory stores are local-only"
        )


__all__ = [
    "PERSISTENCE_POLICY_ENV_VAR",
    "PersistencePolicy",
    "get_persistence_policy",
    "require_shared_database",
]
