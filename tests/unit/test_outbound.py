import socket

import pytest

from osa.generic_agent import OutboundUrlError, outbound_trust_env, validate_outbound_url


def _info(address: str) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


def test_rejects_private_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSA_OUTBOUND_ALLOWED_HOSTS", raising=False)
    with pytest.raises(OutboundUrlError, match="private"):
        validate_outbound_url("http://127.0.0.1:8080", purpose="partner URL")


def test_allows_explicit_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_OUTBOUND_ALLOWED_HOSTS", "localhost")
    assert validate_outbound_url("http://localhost:8080") == "http://localhost:8080"


def test_allows_private_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_OUTBOUND_ALLOW_PRIVATE_NETWORKS", "true")
    assert validate_outbound_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"


def test_checks_resolved_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("osa.generic_agent.outbound.socket.getaddrinfo", lambda *args, **kwargs: _info("93.184.216.34"))
    assert validate_outbound_url("https://example.test") == "https://example.test"


def test_rejects_private_dns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("osa.generic_agent.outbound.socket.getaddrinfo", lambda *args, **kwargs: _info("10.0.0.2"))
    with pytest.raises(OutboundUrlError, match="private"):
        validate_outbound_url("https://example.test")


def test_rejects_unresolvable_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise socket.gaierror("no host")

    monkeypatch.setattr("osa.generic_agent.outbound.socket.getaddrinfo", fail)
    with pytest.raises(OutboundUrlError, match="resolve"):
        validate_outbound_url("https://missing.test")


def test_rejects_non_http_urls() -> None:
    with pytest.raises(OutboundUrlError, match="http"):
        validate_outbound_url("file:///etc/passwd")


def test_proxy_trust_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSA_OUTBOUND_TRUST_ENV", raising=False)
    assert outbound_trust_env() is False
    monkeypatch.setenv("OSA_OUTBOUND_TRUST_ENV", "1")
    assert outbound_trust_env() is True
