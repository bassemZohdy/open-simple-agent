"""Session provider contract regressions."""

from __future__ import annotations

import pytest

from osa.generic_agent import SessionConcurrencyError, SessionManager


def test_in_memory_save_advances_revision() -> None:
    provider = SessionManager()
    session = provider.create("assistant", user_id="ada", tenant_id="acme")
    assert session.revision == 0
    session.add_message("user", "hello")
    provider.save(session)
    assert session.revision == 1


def test_concurrency_error_is_stable_and_non_sensitive() -> None:
    error = SessionConcurrencyError("session-1")
    assert error.code == "session_concurrent_update"
    assert "session-1" in str(error)
    assert "prompt" not in str(error)


def test_persistence_is_explicitly_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # This test documents the contract without requiring a database service.
    from osa.generic_agent import SessionConfigurationError
    from osa.runtimes.adk.service import create_session_provider

    monkeypatch.delenv("OSA_SESSION_DATABASE_URL", raising=False)
    with pytest.raises(SessionConfigurationError, match="OSA_SESSION_DATABASE_URL"):
        create_session_provider(persistence=True)
