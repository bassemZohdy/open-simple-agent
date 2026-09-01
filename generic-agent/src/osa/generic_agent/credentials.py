"""Outbound credential adapters for MCP and A2A clients.

Credential models contain references only.  Adapters resolve values just
before an outbound connection is created and return short-lived request
material to that connection; resolved values are never stored on OSA domain
models or included in errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from osa.generic_agent.secret import SecretError

if TYPE_CHECKING:
    from osa.generic_agent.config import OutboundCredential, SecretReference
    from osa.generic_agent.secret import SecretResolver


class CredentialResolutionError(SecretError):
    """An outbound credential could not be resolved or used safely."""

    def __init__(self, credential_type: str, detail: str) -> None:
        self.credential_type = credential_type
        super().__init__(f"Outbound {credential_type} credential could not be resolved: {detail}")


@dataclass(frozen=True)
class ResolvedOutboundCredential:
    """Resolved request material consumed by one outbound HTTP/stdio call.

    This object is intentionally created at connection time and is not held
    by a catalog or definition.  ``headers`` and ``environment`` may contain
    secret values and must not be logged or persisted by callers.
    """

    headers: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    verify: str | bool | None = None
    cert: tuple[str, str] | None = None


async def resolve_outbound_credential(
    credential: OutboundCredential,
    secret_resolver: SecretResolver | None,
) -> ResolvedOutboundCredential:
    """Resolve one configured credential into transport-specific material.

    API keys and OAuth tokens are emitted as headers, or as an explicitly
    configured environment variable for stdio MCP servers.  mTLS references
    resolve to certificate/key file paths and an optional CA bundle path,
    which are passed directly to ``httpx``.
    """
    from osa.generic_agent.config import ApiKeyCredential, MtlsCredential, OAuth2Credential

    if secret_resolver is None:
        raise CredentialResolutionError(credential.type, "a secret resolver is required")

    if isinstance(credential, ApiKeyCredential):
        value = _resolve_secret(secret_resolver, credential.secret_ref, credential.type)
        header_value = _with_prefix(value, credential.value_prefix, credential.type)
        headers = {credential.header_name: header_value}
        environment = _environment_value(credential.environment_variable, value)
        return ResolvedOutboundCredential(headers=headers, environment=environment)

    if isinstance(credential, OAuth2Credential):
        client_secret = _resolve_secret(secret_resolver, credential.client_secret_ref, credential.type)
        access_token, token_type = await _request_client_credentials_token(credential, client_secret)
        header_value = _with_prefix(access_token, token_type, credential.type)
        headers = {credential.header_name: header_value}
        environment = _environment_value(credential.environment_variable, access_token)
        return ResolvedOutboundCredential(headers=headers, environment=environment)

    if isinstance(credential, MtlsCredential):
        certificate_path = _resolve_secret(secret_resolver, credential.certificate_ref, credential.type)
        key_path = _resolve_secret(secret_resolver, credential.private_key_ref, credential.type)
        ca_path = (
            _resolve_secret(secret_resolver, credential.ca_bundle_ref, credential.type)
            if credential.ca_bundle_ref is not None
            else None
        )
        return ResolvedOutboundCredential(
            verify=ca_path,
            cert=(certificate_path, key_path),
        )

    # The discriminated Pydantic union makes this unreachable for validated
    # configuration, but retaining a safe failure protects programmatic use.
    raise CredentialResolutionError(type(credential).__name__, "unsupported credential type")


def credential_secret_references(credential: OutboundCredential) -> tuple[SecretReference, ...]:
    """Return the secret references required by a credential model."""
    from osa.generic_agent.config import ApiKeyCredential, MtlsCredential, OAuth2Credential

    if isinstance(credential, ApiKeyCredential):
        return (credential.secret_ref,)
    if isinstance(credential, OAuth2Credential):
        return (credential.client_secret_ref,)
    if isinstance(credential, MtlsCredential):
        references: list[SecretReference] = [credential.certificate_ref, credential.private_key_ref]
        if credential.ca_bundle_ref is not None:
            references.append(credential.ca_bundle_ref)
        return tuple(references)
    return ()


def _resolve_secret(secret_resolver: SecretResolver, reference: object, credential_type: str) -> str:
    try:
        value = secret_resolver.resolve(reference)  # type: ignore[arg-type]
    except Exception as exc:
        # SecretError messages are reference-safe.  Other resolver failures
        # are normalized so an implementation cannot accidentally expose a
        # provider response containing secret material.
        from osa.generic_agent.secret import SecretError

        if isinstance(exc, SecretError):
            raise
        raise CredentialResolutionError(credential_type, "secret provider failed") from exc
    if not value:
        raise CredentialResolutionError(credential_type, "secret resolved to an empty value")
    return value


def _with_prefix(value: str, prefix: str | None, credential_type: str) -> str:
    if any(character in value for character in "\r\n"):
        raise CredentialResolutionError(credential_type, "resolved value contains invalid header characters")
    if prefix is None:
        return value
    return f"{prefix} {value}"


def _environment_value(environment_variable: str | None, value: str) -> dict[str, str]:
    return {environment_variable: value} if environment_variable is not None else {}


async def _request_client_credentials_token(
    credential: object,
    client_secret: str,
) -> tuple[str, str]:
    from osa.generic_agent.config import OAuth2Credential

    if not isinstance(credential, OAuth2Credential):
        raise CredentialResolutionError(type(credential).__name__, "invalid OAuth configuration")

    import httpx

    form: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": credential.client_id,
        "client_secret": client_secret,
    }
    if credential.scopes:
        form["scope"] = " ".join(credential.scopes)
    if credential.audience is not None:
        form["audience"] = credential.audience

    try:
        async with httpx.AsyncClient(timeout=credential.timeout_seconds) as client:
            response = await client.post(credential.token_url, data=form)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise CredentialResolutionError(credential.type, "token endpoint request failed") from exc

    if not isinstance(payload, dict):
        raise CredentialResolutionError(credential.type, "token endpoint returned an invalid response")
    access_token = payload.get("access_token")
    token_type = payload.get("token_type", "Bearer")
    if not isinstance(access_token, str) or not access_token:
        raise CredentialResolutionError(credential.type, "token endpoint did not return an access token")
    if not isinstance(token_type, str) or not token_type or any(character in token_type for character in "\r\n"):
        raise CredentialResolutionError(credential.type, "token endpoint returned an invalid token type")
    return access_token, token_type


__all__ = [
    "CredentialResolutionError",
    "ResolvedOutboundCredential",
    "credential_secret_references",
    "resolve_outbound_credential",
]
