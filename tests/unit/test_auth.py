"""Tests for OSA's shared JWT/OIDC authentication boundary."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import configure_control_plane_app
from osa.control_plane.backend.repositories import InMemoryAgentRepository
from osa.control_plane.backend.resource_catalogs import ResourceCatalogs
from osa.control_plane.backend.templates import create_default_template_catalog
from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentSpec,
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthMode,
    AuthorizationError,
    AuthorizationPolicy,
    AuthPermission,
    AuthSettings,
    JwksClient,
    JwtAuthenticator,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _base64url(number: int) -> str:
    length = max(1, (number.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(number.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


@pytest.fixture
def signing_material() -> tuple[rsa.RSAPrivateKey, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "use": "sig",
        "alg": "RS256",
        "n": _base64url(public_numbers.n),
        "e": _base64url(public_numbers.e),
    }
    return private_key, jwk


def _settings(*, scopes: tuple[str, ...] = (), enforce_permissions: bool = False) -> AuthSettings:
    return AuthSettings(
        mode=AuthMode.REQUIRED,
        issuer="https://issuer.example.test/",
        audience="osa-api",
        jwks_url="https://issuer.example.test/.well-known/jwks.json",
        required_scopes=scopes,
        enforce_permissions=enforce_permissions,
    )


def _token(private_key: rsa.RSAPrivateKey, **claims: Any) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": "https://issuer.example.test/",
        "sub": "user-123",
        "aud": "osa-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "scope": "invoke read",
    }
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key"})


def _authenticator(
    settings: AuthSettings,
    jwk: Mapping[str, str],
) -> JwtAuthenticator:
    async def loader() -> Mapping[str, Any]:
        return {"keys": [dict(jwk)]}

    return JwtAuthenticator(settings, jwks_client=JwksClient(settings, loader=loader))


def test_auth_settings_load_from_environment() -> None:
    settings = AuthSettings.from_env(
        {
            "OSA_AUTH_MODE": "required",
            "OSA_AUTH_ISSUER": "https://issuer.example.test/",
            "OSA_AUTH_AUDIENCE": "osa-api",
            "OSA_AUTH_JWKS_URL": "https://issuer.example.test/keys",
            "OSA_AUTH_REQUIRED_SCOPES": "invoke admin",
            "OSA_AUTH_ENFORCE_PERMISSIONS": "true",
            "OSA_AUTH_CLOCK_SKEW_SECONDS": "15",
            "OSA_AUTH_JWKS_TIMEOUT_SECONDS": "3.5",
            "OSA_AUTH_JWKS_CACHE_SECONDS": "60",
        }
    )

    assert settings.mode is AuthMode.REQUIRED
    assert settings.required_scopes == ("invoke", "admin")
    assert settings.clock_skew_seconds == 15
    assert settings.jwks_timeout_seconds == 3.5
    assert settings.jwks_cache_seconds == 60
    assert settings.enforce_permissions is True


def test_auth_settings_require_provider_metadata_when_enabled() -> None:
    with pytest.raises(ValueError, match="requires: issuer, audience, jwks_url"):
        AuthSettings(mode=AuthMode.REQUIRED)


@pytest.mark.asyncio
async def test_valid_jwt_returns_minimal_principal(
    signing_material: tuple[rsa.RSAPrivateKey, dict[str, str]],
) -> None:
    private_key, jwk = signing_material
    principal = await _authenticator(_settings(), jwk).authenticate(f"Bearer {_token(private_key)}")

    assert principal.subject == "user-123"
    assert principal.issuer == "https://issuer.example.test/"
    assert principal.audience == ("osa-api",)
    assert principal.scopes == frozenset({"invoke", "read"})
    assert principal.roles == frozenset()
    assert principal.permissions == frozenset()
    assert principal.tenant_id is None


@pytest.mark.asyncio
async def test_jwt_extracts_common_roles_permissions_and_tenant_claims(
    signing_material: tuple[rsa.RSAPrivateKey, dict[str, str]],
) -> None:
    private_key, jwk = signing_material
    principal = await _authenticator(_settings(), jwk).authenticate(
        f"Bearer {_token(private_key, roles=['viewer'], permissions=['resource:read'], tid='tenant-1')}"
    )

    assert principal.roles == frozenset({"viewer"})
    assert principal.permissions == frozenset({"resource:read"})
    assert principal.tenant_id == "tenant-1"


def test_authorization_policy_expands_roles_and_explicit_permissions() -> None:
    policy = AuthorizationPolicy(enabled=True)
    viewer = AuthenticatedPrincipal(
        subject="viewer",
        issuer="issuer",
        audience=("osa-api",),
        scopes=frozenset(),
        roles=frozenset({"viewer"}),
    )
    custom = AuthenticatedPrincipal(
        subject="custom",
        issuer="issuer",
        audience=("osa-api",),
        scopes=frozenset(),
        permissions=frozenset({AuthPermission.AGENT_WRITE}),
    )

    policy.require(viewer, AuthPermission.AGENT_READ)
    with pytest.raises(AuthorizationError):
        policy.require(viewer, AuthPermission.AGENT_WRITE)
    policy.require(custom, AuthPermission.AGENT_WRITE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [None, "Basic abc", "Bearer", "Bearer not-a-jwt"],
)
async def test_invalid_bearer_header_is_rejected(authorization: str | None) -> None:
    authenticator = JwtAuthenticator(_settings(), jwks_client=JwksClient(_settings(), loader=_empty_loader))
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate(authorization)


async def _empty_loader() -> Mapping[str, Any]:
    return {"keys": []}


@pytest.mark.asyncio
async def test_expired_or_wrong_audience_jwt_is_rejected(
    signing_material: tuple[rsa.RSAPrivateKey, dict[str, str]],
) -> None:
    private_key, jwk = signing_material
    authenticator = _authenticator(_settings(), jwk)

    expired = _token(private_key, exp=datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate(f"Bearer {expired}")

    wrong_audience = _token(private_key, aud="other-api")
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate(f"Bearer {wrong_audience}")


@pytest.mark.asyncio
async def test_required_scope_is_enforced(
    signing_material: tuple[rsa.RSAPrivateKey, dict[str, str]],
) -> None:
    private_key, jwk = signing_material
    authenticator = _authenticator(_settings(scopes=("admin",)), jwk)

    with pytest.raises(AuthorizationError):
        await authenticator.authenticate(f"Bearer {_token(private_key)}")


@pytest.mark.asyncio
async def test_runtime_middleware_requires_auth_and_uses_subject_for_session_user(
    signing_material: tuple[rsa.RSAPrivateKey, dict[str, str]],
) -> None:
    private_key, jwk = signing_material
    from osa.runtimes.adk.api import configure_runtime_app, initialize_runtime, reset_runtime

    definition = AgentDefinition(metadata=AgentMetadataConfig(name="auth-agent"), spec=AgentSpec())
    await initialize_runtime(definition)
    app = configure_runtime_app(
        FastAPI(),
        auth_settings=_settings(enforce_permissions=True),
        authenticator=_authenticator(_settings(enforce_permissions=True), jwk),
    )
    token = _token(private_key, roles=["caller"], tid="tenant-1")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            live = await client.get("/health/live")
            assert live.status_code == 200

            unauthorized = await client.post("/v1/invoke", json={"input": "hello"})
            assert unauthorized.status_code == 401
            assert unauthorized.json()["error"]["code"] == "authentication_failed"
            assert unauthorized.headers["www-authenticate"] == "Bearer"

            authenticated = await client.post(
                "/v1/invoke",
                headers={"Authorization": f"Bearer {token}"},
                json={"input": "hello"},
            )
            assert authenticated.status_code == 200
            session_id = authenticated.json()["session_id"]
            assert session_id

            continued = await client.post(
                "/v1/invoke",
                headers={"Authorization": f"Bearer {token}"},
                json={"input": "again", "session_id": session_id},
            )
            assert continued.status_code == 200

            spoofed = await client.post(
                "/v1/invoke",
                headers={"Authorization": f"Bearer {token}"},
                json={"input": "hello", "user_id": "another-user"},
            )
            assert spoofed.status_code == 403
            assert spoofed.json()["error"]["code"] == "authorization_denied"

            wrong_tenant = await client.post(
                "/v1/invoke",
                headers={"Authorization": f"Bearer {token}"},
                json={"input": "hello", "metadata": {"tenant_id": "tenant-2"}},
            )
            assert wrong_tenant.status_code == 403
            assert wrong_tenant.json()["error"]["code"] == "authorization_denied"

            unscoped_tenant = await client.post(
                "/v1/invoke",
                headers={"Authorization": f"Bearer {_token(private_key, roles=['caller'])}"},
                json={"input": "hello", "metadata": {"tenant_id": "tenant-1"}},
            )
            assert unscoped_tenant.status_code == 403
            assert unscoped_tenant.json()["error"]["code"] == "authorization_denied"
    finally:
        reset_runtime()


@pytest.mark.asyncio
async def test_control_plane_middleware_requires_auth(
    signing_material: tuple[rsa.RSAPrivateKey, dict[str, str]],
) -> None:
    private_key, jwk = signing_material
    settings = _settings(enforce_permissions=True)
    app = configure_control_plane_app(
        FastAPI(),
        agent_repository=InMemoryAgentRepository(),
        resource_catalogs=ResourceCatalogs(),
        template_catalog=create_default_template_catalog(),
        auth_settings=settings,
        authenticator=_authenticator(settings, jwk),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/agents")
        assert unauthorized.status_code == 401

        authorized = await client.get(
            "/agents",
            headers={"Authorization": f"Bearer {_token(private_key, roles=['viewer'])}"},
        )
        assert authorized.status_code == 200

        created = await client.post(
            "/agents",
            headers={"Authorization": f"Bearer {_token(private_key, roles=['operator'], tid='tenant-1')}"},
            json={"name": "tenant-one-agent"},
        )
        assert created.status_code == 201
        created_agent_id = created.json()["agent_id"]
        assert created.json()["tenant_id"] == "tenant-1"

        other_tenant = {"Authorization": f"Bearer {_token(private_key, roles=['viewer'], tid='tenant-2')}"}
        isolated_list = await client.get("/agents", headers=other_tenant)
        assert isolated_list.status_code == 200
        assert isolated_list.json()["total"] == 0
        isolated_get = await client.get(f"/agents/{created_agent_id}", headers=other_tenant)
        assert isolated_get.status_code == 404

        denied_write = await client.post(
            "/agents",
            headers={"Authorization": f"Bearer {_token(private_key, roles=['viewer'])}"},
            json={"name": "viewer-cannot-create"},
        )
        assert denied_write.status_code == 403
        assert denied_write.json()["error"]["code"] == "authorization_denied"
