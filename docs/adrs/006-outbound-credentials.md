# ADR-006: Shared outbound credentials for MCP and A2A

## Status

Accepted

## Date

2026-09-01

## Context

MCP servers and external A2A agents need explicit, configuration-driven
authentication. The earlier `credential_ref` field only represented a bearer
secret and could not model API-key placement, OAuth token acquisition, or
mTLS. Credential values must remain outside bundles, catalogs, API responses,
logs, and audit events.

## Decision

Use one discriminated `OutboundCredential` contract in
`osa.generic_agent.config`, resolved by adapters in
`osa.generic_agent.credentials`:

- `api_key` resolves one secret and sends it in a configured HTTP header. An
  optional environment variable supports stdio MCP servers.
- `oauth2` uses the OAuth 2.0 client-credentials grant, resolving the client
  secret only for the token request and sending the returned token as a
  bearer-style header. Scope and audience form parameters are optional.
- `mtls` resolves certificate and private-key file paths plus an optional CA
  bundle path and supplies them to `httpx`.

MCP `credential` and external A2A `credential` records store models only.
Adapters resolve credentials at connection/call time and do not cache the
resolved material. A legacy `credential_ref` remains supported for existing
bundles and maps to the previous bearer-header or stdio-environment behavior.
Inbound A2A security-scheme enforcement is provided by the shared OIDC/OAuth
HTTP boundary described in ADR-005; this ADR covers outbound credentials only.

## Consequences

- MCP and A2A share the same secret-resolution and redaction behavior.
- OAuth token failures are normalized without copying token endpoint bodies
  into errors.
- mTLS requires certificate material to be available as file paths to the
  resolver; a secret manager integration can provide those paths without
  changing the domain contract.
- OAuth token caching and dynamic token exchange are not included in this
  slice; inbound A2A enforcement is covered by the shared HTTP boundary.

## Validation

Unit tests cover model validation, API-key headers/environment injection,
OAuth client-credentials form construction, mTLS material, missing resolvers,
header-injection rejection, and secret-safe failures. Protocol tests verify
API-key authentication for Streamable HTTP MCP and A2A calls.
