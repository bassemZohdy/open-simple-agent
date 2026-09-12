"""Opt-in acceptance coverage for a selected enterprise OIDC/OAuth provider.

The test deliberately consumes only operator-provided environment values. It
does not mint, print, persist, or include access tokens in assertion messages.
It supports both signed JWTs (JWKS/discovery) and opaque tokens (RFC 7662
introspection), matching the shared production authentication boundary.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import configure_control_plane_app
from osa.control_plane.backend.repositories import InMemoryAgentRepository
from osa.control_plane.backend.resource_catalogs import ResourceCatalogs
from osa.control_plane.backend.templates import create_default_template_catalog
from osa.generic_agent import (
    AuthenticationError,
    AuthMode,
    AuthSettings,
    EnvironmentSecretResolver,
    JwtAuthenticator,
)

_REQUIRED_ENVIRONMENT = (
    "OSA_ENTERPRISE_IDENTITY_ACCESS_TOKEN",
    "OSA_ENTERPRISE_IDENTITY_EXPECTED_SUBJECT",
    "OSA_ENTERPRISE_IDENTITY_EXPECTED_TENANT",
    "OSA_AUTH_ISSUER",
    "OSA_AUTH_AUDIENCE",
)

pytestmark = pytest.mark.skipif(
    not all(os.environ.get(name, "").strip() for name in _REQUIRED_ENVIRONMENT),
    reason=(
        "enterprise identity acceptance is opt-in; configure the selected "
        "provider, test token, subject, and tenant variables"
    ),
)


def _settings() -> AuthSettings:
    """Load the production auth settings while forcing a protected boundary."""
    values = dict(os.environ)
    values["OSA_AUTH_MODE"] = AuthMode.REQUIRED.value
    for name in ("OSA_AUTH_JWKS_URL", "OSA_AUTH_DISCOVERY_URL"):
        if not values.get(name, "").strip():
            values.pop(name, None)
    if not values.get("OSA_AUTH_INTROSPECTION_URL", "").strip():
        for name in (
            "OSA_AUTH_INTROSPECTION_URL",
            "OSA_AUTH_INTROSPECTION_CLIENT_ID",
            "OSA_AUTH_INTROSPECTION_CLIENT_SECRET_KEY",
        ):
            values.pop(name, None)
    return AuthSettings.from_env(values)


@pytest.mark.asyncio
async def test_selected_enterprise_identity_authenticates_and_binds_tenant() -> None:
    """Validate a real provider token at auth and protected-route boundaries."""
    settings = _settings()
    authenticator = JwtAuthenticator(settings, secret_resolver=EnvironmentSecretResolver())
    access_token = os.environ["OSA_ENTERPRISE_IDENTITY_ACCESS_TOKEN"]

    principal = await authenticator.authenticate(f"Bearer {access_token}")

    assert principal.subject == os.environ["OSA_ENTERPRISE_IDENTITY_EXPECTED_SUBJECT"]
    assert principal.tenant_id == os.environ["OSA_ENTERPRISE_IDENTITY_EXPECTED_TENANT"]
    expected_scopes = set(os.environ.get("OSA_ENTERPRISE_IDENTITY_EXPECTED_SCOPES", "").split())
    assert expected_scopes <= principal.scopes

    app = configure_control_plane_app(
        FastAPI(title="OSA enterprise identity acceptance"),
        agent_repository=InMemoryAgentRepository(),
        resource_catalogs=ResourceCatalogs(),
        template_catalog=create_default_template_catalog(),
        auth_settings=settings,
        authenticator=authenticator,
        secret_resolver=EnvironmentSecretResolver(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://identity-acceptance.test") as client:
        response = await client.get("/agents", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_selected_enterprise_identity_rejects_optional_inactive_token() -> None:
    """Exercise disabled/revoked identity behavior when the provider supplies a fixture token."""
    inactive_token = os.environ.get("OSA_ENTERPRISE_IDENTITY_INACTIVE_TOKEN", "").strip()
    if not inactive_token:
        pytest.skip("set OSA_ENTERPRISE_IDENTITY_INACTIVE_TOKEN to test provider-side disablement")

    authenticator = JwtAuthenticator(_settings(), secret_resolver=EnvironmentSecretResolver())
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate(f"Bearer {inactive_token}")
