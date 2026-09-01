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


class AuthSettings(StrictModel):
    """Externalized OIDC/OAuth bearer-token validation settings.

    OSA validates signed JWT access tokens locally using the issuer's JWKS.
    ``disabled`` is the development default; ``required`` is the production
    mode and requires all issuer, audience, and JWKS settings.
    """

    mode: AuthMode = AuthMode.DISABLED
    issuer: str | None = None
    audience: str | None = None
    jwks_url: str | None = None
    required_scopes: tuple[str, ...] = ()
    clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    jwks_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    jwks_cache_seconds: int = Field(default=300, gt=0, le=86400)

    @field_validator("issuer", "jwks_url")
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
            return self
        missing = [name for name in ("issuer", "audience", "jwks_url") if getattr(self, name) in (None, "")]
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
            required_scopes=scopes,
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


JwksLoader = Callable[[], Awaitable[Mapping[str, Any]]]


class JwksClient:
    """Fetch and cache JSON Web Key Sets without retaining token material."""

    def __init__(
        self,
        settings: AuthSettings,
        *,
        loader: JwksLoader | None = None,
    ) -> None:
        if settings.jwks_url is None and loader is None:
            raise ValueError("jwks_url is required when no JWKS loader is provided")
        self._settings = settings
        self._loader = loader
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
        assert self._settings.jwks_url is not None
        try:
            async with httpx.AsyncClient(timeout=self._settings.jwks_timeout_seconds) as client:
                response = await client.get(str(self._settings.jwks_url))
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
        return AuthenticatedPrincipal(subject=subject, issuer=issuer, audience=audience, scopes=frozenset(scopes))


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
