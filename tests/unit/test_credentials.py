"""Tests for outbound MCP/A2A credential adapters."""

from __future__ import annotations

import httpx
import pytest

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentSpec,
    ApiKeyCredential,
    BundleMetadata,
    DeploymentBundle,
    EnvironmentSecretResolver,
    McpDefinition,
    MtlsCredential,
    OAuth2Credential,
    SecretReference,
    collect_secret_references,
    resolve_outbound_credential,
)


def _env_ref(name: str) -> SecretReference:
    return SecretReference(source="env", key=name)


class TestCredentialModels:
    def test_api_key_credential_defaults_to_header(self) -> None:
        credential = ApiKeyCredential(secret_ref=_env_ref("PARTNER_KEY"))
        assert credential.type == "api_key"
        assert credential.header_name == "X-API-Key"

    def test_oauth_requires_absolute_http_token_url(self) -> None:
        with pytest.raises(ValueError, match="absolute HTTP or HTTPS URL"):
            OAuth2Credential(
                token_url="token",
                client_id="osa",
                client_secret_ref=_env_ref("OAUTH_SECRET"),
            )

    def test_mtls_has_no_secret_values_in_model_dump(self) -> None:
        credential = MtlsCredential(
            certificate_ref=_env_ref("CLIENT_CERT_PATH"),
            private_key_ref=_env_ref("CLIENT_KEY_PATH"),
        )
        dumped = credential.model_dump()
        assert "client-certificate" not in str(dumped)
        assert dumped["certificate_ref"]["key"] == "CLIENT_CERT_PATH"

    def test_mcp_rejects_legacy_and_structured_credentials_together(self) -> None:
        with pytest.raises(ValueError, match="cannot both be configured"):
            McpDefinition(
                name="secure",
                credential=ApiKeyCredential(secret_ref=_env_ref("PARTNER_KEY")),
                credential_ref=_env_ref("LEGACY_TOKEN"),
            )

    def test_bundle_collects_structured_credential_references(self) -> None:
        bundle = DeploymentBundle(
            metadata=BundleMetadata(name="bundle"),
            agent=AgentDefinition(metadata=AgentMetadataConfig(name="agent"), spec=AgentSpec()),
            mcps=[
                McpDefinition(
                    name="secure",
                    credential=MtlsCredential(
                        certificate_ref=_env_ref("CLIENT_CERT_PATH"),
                        private_key_ref=_env_ref("CLIENT_KEY_PATH"),
                        ca_bundle_ref=_env_ref("CA_PATH"),
                    ),
                )
            ],
        )
        references = collect_secret_references(bundle)
        assert [reference.key for reference in references] == [
            "CLIENT_CERT_PATH",
            "CLIENT_KEY_PATH",
            "CA_PATH",
        ]


class TestCredentialResolution:
    async def test_api_key_resolves_to_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PARTNER_KEY", "key-value")
        material = await resolve_outbound_credential(
            ApiKeyCredential(secret_ref=_env_ref("PARTNER_KEY")),
            EnvironmentSecretResolver(),
        )
        assert material.headers == {"X-API-Key": "key-value"}
        assert material.environment == {}

    async def test_api_key_can_be_injected_into_stdio_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PARTNER_KEY", "key-value")
        material = await resolve_outbound_credential(
            ApiKeyCredential(
                secret_ref=_env_ref("PARTNER_KEY"),
                environment_variable="PARTNER_TOKEN",
            ),
            EnvironmentSecretResolver(),
        )
        assert material.environment == {"PARTNER_TOKEN": "key-value"}

    async def test_oauth_client_credentials_request_does_not_expose_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OAUTH_SECRET", "super-secret")
        captured: dict[str, object] = {}

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {"access_token": "access-token", "token_type": "Bearer"}

        class Client:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            async def __aenter__(self) -> Client:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def post(self, url: str, *, data: dict[str, str]) -> Response:
                captured["url"] = url
                captured["data"] = data
                return Response()

        monkeypatch.setattr(httpx, "AsyncClient", Client)
        material = await resolve_outbound_credential(
            OAuth2Credential(
                token_url="https://issuer.example.test/oauth/token",
                client_id="osa-client",
                client_secret_ref=_env_ref("OAUTH_SECRET"),
                scopes=["agent.invoke", "agent.read"],
                audience="osa-api",
            ),
            EnvironmentSecretResolver(),
        )

        assert material.headers == {"Authorization": "Bearer access-token"}
        assert captured["url"] == "https://issuer.example.test/oauth/token"
        assert captured["data"] == {
            "grant_type": "client_credentials",
            "client_id": "osa-client",
            "client_secret": "super-secret",
            "scope": "agent.invoke agent.read",
            "audience": "osa-api",
        }

    async def test_mtls_resolves_file_paths_without_logging_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CLIENT_CERT_PATH", "/run/secrets/client.crt")
        monkeypatch.setenv("CLIENT_KEY_PATH", "/run/secrets/client.key")
        monkeypatch.setenv("CA_PATH", "/run/secrets/ca.crt")
        material = await resolve_outbound_credential(
            MtlsCredential(
                certificate_ref=_env_ref("CLIENT_CERT_PATH"),
                private_key_ref=_env_ref("CLIENT_KEY_PATH"),
                ca_bundle_ref=_env_ref("CA_PATH"),
            ),
            EnvironmentSecretResolver(),
        )
        assert material.cert == ("/run/secrets/client.crt", "/run/secrets/client.key")
        assert material.verify == "/run/secrets/ca.crt"

    async def test_missing_resolver_is_deterministic(self) -> None:
        from osa.generic_agent import CredentialResolutionError

        with pytest.raises(CredentialResolutionError, match="secret resolver"):
            await resolve_outbound_credential(
                ApiKeyCredential(secret_ref=_env_ref("PARTNER_KEY")),
                None,
            )

    async def test_header_value_cannot_inject_newline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from osa.generic_agent import CredentialResolutionError

        monkeypatch.setenv("PARTNER_KEY", "safe\nforbidden")
        with pytest.raises(CredentialResolutionError, match="invalid header"):
            await resolve_outbound_credential(
                ApiKeyCredential(secret_ref=_env_ref("PARTNER_KEY")),
                EnvironmentSecretResolver(),
            )
