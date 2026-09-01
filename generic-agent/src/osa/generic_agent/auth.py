"""JWT bearer authentication shared by OSA HTTP services.

The generic package owns token validation so the Control Plane and runtime API
apply the same issuer, audience, signing-key, scope, and error rules. The
FastAPI applications install the HTTP middleware around this validator; this
module deliberately has no web-framework dependency.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import jwt
from pydantic import AnyHttpUrl, Field, TypeAdapter, ValidationError, field_validator, model_validator

from osa.generic_agent.config import ConfigurationError, StrictModel
from osa.generic_agent.errors import OsaError


class AuthMode(StrEnum):
    """HTTP authentication enforcement mode."""

    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


class AuthPermission(StrEnum):
    """Stable permissions used by the OSA HTTP surfaces."""

    AGENT_INVOKE = "agent:invoke"
    AGENT_READ = "agent:read"
    AGENT_WRITE = "agent:write"
    RESOURCE_READ = "resource:read"
    RESOURCE_WRITE = "resource:write"
    DEPLOYMENT_READ = "deployment:read"
    DEPLOYMENT_WRITE = "deployment:write"
    EXTERNAL_AGENT_READ = "external-agent:read"
    EXTERNAL_AGENT_WRITE = "external-agent:write"


_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "administrator": frozenset({"*"}),
    "admin": frozenset({"*"}),
    "operator": frozenset(
        {
            AuthPermission.AGENT_INVOKE,
            AuthPermission.AGENT_READ,
            AuthPermission.AGENT_WRITE,
            AuthPermission.RESOURCE_READ,
            AuthPermission.RESOURCE_WRITE,
            AuthPermission.DEPLOYMENT_READ,
            AuthPermission.DEPLOYMENT_WRITE,
            AuthPermission.EXTERNAL_AGENT_READ,
            AuthPermission.EXTERNAL_AGENT_WRITE,
        }
    ),
    "viewer": frozenset(
        {
            AuthPermission.AGENT_READ,
            AuthPermission.RESOURCE_READ,
            AuthPermission.DEPLOYMENT_READ,
            AuthPermission.EXTERNAL_AGENT_READ,
        }
    ),
    "agent": frozenset({AuthPermission.AGENT_INVOKE}),
    "caller": frozenset({AuthPermission.AGENT_INVOKE}),
    "user": frozenset({AuthPermission.AGENT_INVOKE}),
    "service": frozenset({AuthPermission.AGENT_INVOKE, AuthPermission.RESOURCE_READ}),
}


class AuthSettings(StrictModel):
    """Externalized OIDC/OAuth bearer-token validation settings.

    OSA validates signed JWT access tokens locally using an explicit JWKS URL
    or the issuer's standard OIDC discovery document. ``disabled`` is the
    development default; enabled modes require an issuer and audience.
    """

    mode: AuthMode = AuthMode.DISABLED
    issuer: str | None = None
    audience: str | None = None
    jwks_url: str | None = None
    discovery_url: str | None = None
    required_scopes: tuple[str, ...] = ()
    enforce_permissions: bool = False
    clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    jwks_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    jwks_cache_seconds: int = Field(default=300, gt=0, le=86400)

    @field_validator("issuer", "jwks_url", "discovery_url")
    @classmethod
    def _validate_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(TypeAdapter(AnyHttpUrl).validate_python(value))
        except ValidationError as exc:
            raise ValueError("must be a valid HTTP URL") from exc

    @model_validator(mode="after")
    def _validate_enabled_settings(self) -> AuthSettings:
        if self.mode is AuthMode.DISABLED:
            if self.enforce_permissions:
                raise ValueError("Permission enforcement requires optional or required authentication mode")
            return self
        missing = [name for name in ("issuer", "audience") if getattr(self, name) in (None, "")]
        if missing:
            raise ValueError(f"Authentication mode '{self.mode}' requires: {', '.join(missing)}")
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthSettings:
        """Load authentication settings from ``OSA_AUTH_*`` variables."""
        values = os.environ if environ is None else environ
        raw_mode = values.get("OSA_AUTH_MODE", AuthMode.DISABLED.value).strip().lower()
        try:
            mode = AuthMode(raw_mode)
        except ValueError as exc:
            accepted = ", ".join(item.value for item in AuthMode)
            raise ConfigurationError(f"Invalid OSA_AUTH_MODE '{raw_mode}'; expected one of: {accepted}") from exc

        scopes = tuple(scope for scope in values.get("OSA_AUTH_REQUIRED_SCOPES", "").split() if scope)
        return cls(
            mode=mode,
            issuer=values.get("OSA_AUTH_ISSUER"),
            audience=values.get("OSA_AUTH_AUDIENCE"),
            jwks_url=values.get("OSA_AUTH_JWKS_URL"),
            discovery_url=values.get("OSA_AUTH_DISCOVERY_URL"),
            required_scopes=scopes,
            enforce_permissions=_parse_bool(values, "OSA_AUTH_ENFORCE_PERMISSIONS", False),
            clock_skew_seconds=_parse_int(values, "OSA_AUTH_CLOCK_SKEW_SECONDS", 30),
            jwks_timeout_seconds=_parse_float(values, "OSA_AUTH_JWKS_TIMEOUT_SECONDS", 2.0),
            jwks_cache_seconds=_parse_int(values, "OSA_AUTH_JWKS_CACHE_SECONDS", 300),
        )


class AuthenticationError(OsaError):
    """The bearer token is missing or cannot be authenticated."""

    code = "authentication_failed"


class AuthorizationError(OsaError):
    """The authenticated principal is not permitted to perform an action."""

    code = "authorization_denied"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Minimal identity carried from a validated access token."""

    subject: str
    issuer: str
    audience: tuple[str, ...]
    scopes: frozenset[str]
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    tenant_id: str | None = None


class AuthorizationPolicy:
    """Resolve stable HTTP permissions from token roles and claims."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled

    @staticmethod
    def permission_for_request(path: str, method: str) -> str | None:
        """Return the permission required by a known OSA route."""
        normalized_method = method.upper()
        if path == "/v1/invoke" and normalized_method == "POST":
            return AuthPermission.AGENT_INVOKE
        if path == "/v1/capabilities" and normalized_method == "GET":
            return AuthPermission.AGENT_READ
        if path.startswith("/a2a") and normalized_method == "POST":
            return AuthPermission.AGENT_INVOKE
        if path == "/templates" and normalized_method == "GET":
            return AuthPermission.RESOURCE_READ
        if path.startswith("/resources/"):
            return AuthPermission.RESOURCE_READ if normalized_method == "GET" else AuthPermission.RESOURCE_WRITE
        if path.startswith("/deployments/") or path.endswith("/deploy"):
            return AuthPermission.DEPLOYMENT_READ if normalized_method == "GET" else AuthPermission.DEPLOYMENT_WRITE
        if path.startswith("/external-agents"):
            if path.endswith("/invoke") and normalized_method == "POST":
                return AuthPermission.AGENT_INVOKE
            return (
                AuthPermission.EXTERNAL_AGENT_READ
                if normalized_method == "GET"
                else AuthPermission.EXTERNAL_AGENT_WRITE
            )
        if path == "/agents" or path.startswith("/agents/"):
            return AuthPermission.AGENT_READ if normalized_method == "GET" else AuthPermission.AGENT_WRITE
        return None

    def require(self, principal: AuthenticatedPrincipal, permission: str) -> None:
        """Raise when a principal lacks a required permission."""
        granted = set(principal.permissions) | set(principal.scopes)
        for role in principal.roles:
            granted.update(_ROLE_PERMISSIONS.get(role.lower(), ()))
        if "*" not in granted and permission not in granted:
            raise AuthorizationError(f"Permission '{permission}' is required")


JwksLoader = Callable[[], Awaitable[Mapping[str, Any]]]
OidcDiscoveryLoader = Callable[[], Awaitable[Mapping[str, Any]]]


class OidcDiscoveryClient:
    """Resolve a provider's JWKS endpoint from standard OIDC metadata."""

    def __init__(
        self,
        settings: AuthSettings,
        *,
        loader: OidcDiscoveryLoader | None = None,
    ) -> None:
        self._settings = settings
        self._loader = loader

    async def jwks_url(self) -> str:
        """Return the validated JWKS URI advertised by the issuer."""
        try:
            metadata = await self._load()
        except AuthenticationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise AuthenticationError("The OIDC discovery document could not be retrieved") from exc

        advertised_issuer = metadata.get("issuer")
        if advertised_issuer != self._settings.issuer:
            raise AuthenticationError("The OIDC discovery issuer does not match the configured issuer")
        raw_jwks_url = metadata.get("jwks_uri")
        if not isinstance(raw_jwks_url, str) or not raw_jwks_url:
            raise AuthenticationError("The OIDC discovery document has no JWKS URI")
        try:
            return str(TypeAdapter(AnyHttpUrl).validate_python(raw_jwks_url))
        except ValidationError as exc:
            raise AuthenticationError("The OIDC discovery JWKS URI is invalid") from exc

    async def _load(self) -> Mapping[str, Any]:
        if self._loader is not None:
            return await self._loader()
        issuer = self._settings.issuer
        assert issuer is not None
        discovery_url = self._settings.discovery_url or f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=self._settings.jwks_timeout_seconds) as client:
                response = await client.get(discovery_url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthenticationError("The OIDC discovery document could not be retrieved") from exc
        if not isinstance(payload, dict):
            raise AuthenticationError("The OIDC discovery document is invalid")
        return payload


class JwksClient:
    """Fetch and cache JSON Web Key Sets without retaining token material."""

    def __init__(
        self,
        settings: AuthSettings,
        *,
        loader: JwksLoader | None = None,
        discovery_client: OidcDiscoveryClient | None = None,
    ) -> None:
        self._settings = settings
        self._loader = loader
        self._discovery = discovery_client or OidcDiscoveryClient(settings)
        self._keys: dict[str, dict[str, Any]] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_key(self, key_id: str) -> Mapping[str, Any]:
        """Return a signing key, refreshing once when the key is unknown."""
        now = time.monotonic()
        key = self._keys.get(key_id)
        if key is not None and now < self._expires_at:
            return key

        async with self._lock:
            now = time.monotonic()
            key = self._keys.get(key_id)
            if key is not None and now < self._expires_at:
                return key
            await self._refresh()
            key = self._keys.get(key_id)
            if key is None:
                raise AuthenticationError("The token signing key is not available")
            return key

    async def _refresh(self) -> None:
        try:
            payload = await self._load()
        except AuthenticationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise AuthenticationError("The token signing keys could not be retrieved") from exc

        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list):
            raise AuthenticationError("The token signing-key response is invalid")
        parsed: dict[str, dict[str, Any]] = {}
        for raw_key in raw_keys:
            if isinstance(raw_key, dict) and isinstance(raw_key.get("kid"), str):
                parsed[raw_key["kid"]] = raw_key
        if not parsed:
            raise AuthenticationError("The token signing-key response has no usable keys")
        self._keys = parsed
        self._expires_at = time.monotonic() + self._settings.jwks_cache_seconds

    async def _load(self) -> Mapping[str, Any]:
        if self._loader is not None:
            return await self._loader()
        jwks_url = self._settings.jwks_url
        if jwks_url is None:
            jwks_url = await self._discovery.jwks_url()
        try:
            async with httpx.AsyncClient(timeout=self._settings.jwks_timeout_seconds) as client:
                response = await client.get(jwks_url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthenticationError("The token signing keys could not be retrieved") from exc
        if not isinstance(payload, dict):
            raise AuthenticationError("The token signing-key response is invalid")
        return payload


class JwtAuthenticator:
    """Authenticate an HTTP Bearer token against configured OIDC settings."""

    _ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})

    def __init__(self, settings: AuthSettings, *, jwks_client: JwksClient | None = None) -> None:
        if settings.mode is AuthMode.DISABLED:
            raise ValueError("JwtAuthenticator requires optional or required authentication mode")
        self._settings = settings
        self._jwks = jwks_client or JwksClient(settings)

    async def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal:
        """Validate a bearer token and return its non-sensitive identity."""
        token = _bearer_token(authorization)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("The bearer token header is invalid") from exc

        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in self._ALLOWED_ALGORITHMS or not isinstance(key_id, str) or not key_id:
            raise AuthenticationError("The bearer token signing algorithm or key id is invalid")

        jwk = await self._jwks.get_key(key_id)
        try:
            signing_key = jwt.PyJWK.from_dict(dict(jwk))
            if signing_key.algorithm_name != algorithm:
                raise AuthenticationError("The bearer token signing algorithm is not accepted")
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                audience=self._settings.audience,
                issuer=str(self._settings.issuer),
                leeway=self._settings.clock_skew_seconds,
                options={"require": ["exp", "iss", "sub"]},
            )
        except AuthenticationError:
            raise
        except jwt.PyJWTError as exc:
            raise AuthenticationError("The bearer token is invalid or expired") from exc

        subject = claims.get("sub")
        issuer = claims.get("iss")
        if not isinstance(subject, str) or not subject or not isinstance(issuer, str) or not issuer:
            raise AuthenticationError("The bearer token identity claims are invalid")
        audience = _audience_values(claims.get("aud"))
        scopes = _scope_values(claims)
        missing_scopes = set(self._settings.required_scopes) - scopes
        if missing_scopes:
            raise AuthorizationError("The bearer token does not grant the required scope")
        roles = _role_values(claims)
        permissions = _permission_values(claims)
        tenant_id = _tenant_value(claims)
        return AuthenticatedPrincipal(
            subject=subject,
            issuer=issuer,
            audience=audience,
            scopes=frozenset(scopes),
            roles=frozenset(roles),
            permissions=frozenset(permissions),
            tenant_id=tenant_id,
        )


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise AuthenticationError("A bearer token is required")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise AuthenticationError("A bearer token is required")
    return token.strip()


def _audience_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    raise AuthenticationError("The bearer token audience claim is invalid")


def _scope_values(claims: Mapping[str, Any]) -> set[str]:
    raw_scope = claims.get("scope", claims.get("scp", ""))
    if isinstance(raw_scope, str):
        return {scope for scope in raw_scope.split() if scope}
    if isinstance(raw_scope, list) and all(isinstance(item, str) for item in raw_scope):
        return {item for item in raw_scope if item}
    raise AuthenticationError("The bearer token scope claim is invalid")


def _role_values(claims: Mapping[str, Any]) -> set[str]:
    """Read common OIDC role claim shapes, including Keycloak realms."""
    values = _claim_values(claims.get("roles", claims.get("role", "")), "roles")
    realm_access = claims.get("realm_access")
    if realm_access is not None:
        if not isinstance(realm_access, dict):
            raise AuthenticationError("The bearer token role claim is invalid")
        values.update(_claim_values(realm_access.get("roles", []), "roles"))
    return values


def _permission_values(claims: Mapping[str, Any]) -> set[str]:
    """Read explicit permission claims; scopes are evaluated separately."""
    return _claim_values(claims.get("permissions", claims.get("permission", [])), "permissions")


def _claim_values(value: Any, claim_name: str) -> set[str]:
    if isinstance(value, str):
        return {item for item in value.split() if item}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return {item for item in value if item}
    if value in (None, ""):
        return set()
    raise AuthenticationError(f"The bearer token {claim_name} claim is invalid")


def _tenant_value(claims: Mapping[str, Any]) -> str | None:
    value = claims.get("tenant_id", claims.get("tid"))
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AuthenticationError("The bearer token tenant claim is invalid")
    return value


def _parse_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw_value = values.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid integer value for {name}") from exc


def _parse_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw_value = values.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid numeric value for {name}") from exc


def _parse_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = values.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean value for {name}")
