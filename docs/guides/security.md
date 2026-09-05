# Security Guide

Current security behavior and how to configure it. Enforced behavior here is
tested; anything planned is marked and tracked in `TODO.md`.

## Inbound authentication (Bearer / OIDC)

Both APIs validate Bearer tokens against an issuer you control. Configure
via `OSA_AUTH_*` environment variables:

| Variable | Purpose |
|---|---|
| `OSA_AUTH_MODE` | `disabled` (development default) / `optional` / `required` |
| `OSA_AUTH_ISSUER` | Expected token issuer |
| `OSA_AUTH_AUDIENCE` | Expected audience |
| `OSA_AUTH_JWKS_URL` | Explicit JWKS endpoint for signature keys |
| `OSA_AUTH_DISCOVERY_URL` | OIDC discovery document (resolves `jwks_uri`; explicit JWKS wins) |
| `OSA_AUTH_INTROSPECTION_URL` / `_CLIENT_ID` / `_CLIENT_SECRET_*` | RFC 7662 introspection for opaque tokens |
| `OSA_AUTH_REQUIRED_SCOPES` | Space-separated scopes every token must carry |
| `OSA_AUTH_ENFORCE_PERMISSIONS` | `true` enables route-permission checks against role/scope claims |

`required` mode rejects anonymous calls to every non-public route (health
endpoints stay open). Tokens are validated for signature, issuer, audience,
expiry, and scopes; opaque tokens are introspected. A2A JSON-RPC and the
Agent Card use the same boundary, and protected Agent Cards advertise the
required security scheme.

## Identity lifecycle

OSA keeps no identity store; your IdP is the lifecycle authority (ADR-007,
claim-driven). Practical expectations:

- **Disabled identities**: tokens carrying an `active` claim set to anything
  other than boolean `true` are rejected. Omitting the claim disables the
  check. For immediate revocation of JWTs, keep access tokens short-lived
  (5–15 minutes recommended) or use introspected opaque tokens, whose
  `active: false` introspection response is enforced live.
- **Role/group changes**: re-read from claims on every request; they apply
  when the IdP issues a fresh token. OSA runs no sync jobs.
- **Key rotation**: an unknown signing-key id triggers an immediate JWKS
  refresh; other key changes propagate within
  `OSA_AUTH_JWKS_CACHE_SECONDS` (default 300).
- **Service accounts** are IdP-issued clients whose tokens carry the same
  role/scope/tenant claims as user tokens and are validated identically.

## Roles and permissions

With `OSA_AUTH_ENFORCE_PERMISSIONS=true`, role/permission/scope claims map to
a baseline of administrator, operator, viewer, agent, caller, user, and
service identities with stable route permissions (mutating Control Plane
routes, invocation, read-only views). Tokens without a recognized role fall
back to scope checks. Token claims bind `user_id`/`tenant_id` on invocations
— a caller cannot spoof another identity.

## Tenant and ownership boundaries

- **Sessions** (`osa.generic_agent.session`): owned by
  `(agent_name, user_id, tenant_id)`; unknown caller-supplied IDs are
  rejected, identity changes are access violations.
- **Control Plane records**: managed agents, deployments, and resources are
  tenant-scoped (owner persisted in migrations 0003–0005); reads and writes
  filter by the caller's tenant.
- **Memory**: scope IDs derive from the caller/agent/tenant; entries never
  cross scopes.

## Secrets

- Definitions never contain secret values — only `credential_ref`
  coordinates (`source`, `key`, `env_var`).
- `EnvironmentSecretResolver` resolves at connection/call time; values are
  held by the client, never stored, logged, or returned.
- MCP and outbound A2A calls accept API-key, OAuth2 client-credentials, and
  mTLS adapters via shared credential configuration.
- API responses redact `credential_ref` to its non-secret coordinates.

## Policy (allow/deny independent of prompts)

Definitions can carry allow/deny policy rules for model, tool, MCP, skill,
and inbound-A2A resources. Policies are checked before runtime construction;
denied references fail with a stable `policy_violation` error — the model
cannot talk its way past them.

## Supply chain

- CI runs pip-audit over the exported full lock on every push/PR.
- pip-licenses enforces a permissive-license allow-list; new licenses fail
  the build until reviewed.
- CycloneDX SBOMs: Python-dependency SBOM (security job) and Syft image
  SBOMs for both container images (container job), uploaded as artifacts.

## Audit

Every successful Control Plane management mutation and external-agent
invocation appends a tenant-filtered, redaction-safe audit event
(`GET /audit-events`); runtime and A2A boundary invocations record outcomes
without capturing prompts, credentials, or payloads.

## Browser CORS on the runtime

The runtime API is browser-reachable only when an operator explicitly opts in:
`OSA_RUNTIME_ALLOWED_ORIGINS` (comma-separated origins) enables CORS with a
fixed `GET`/`POST` method allowlist and `Authorization`, `Content-Type`, and
`X-Request-ID` headers; preflight `OPTIONS` requests bypass the bearer
boundary. Unset (the default), no CORS is configured and browser
cross-origin calls fail. When the Control Plane launches runtimes, it forwards
`OSA_DEPLOY_RUNTIME_ALLOWED_ORIGINS` as the runtime's allowlist — this is what
backs the Control Panel's direct browser-to-runtime "Send test message"
feature (ADR-008). Because enabling CORS exposes the runtime's own
`OSA_AUTH_MODE`-gated endpoints to browsers, treat the origin list as an
authorization-surface decision, not a convenience flag: list only origins that
must call the runtime directly, and keep the runtime's authentication
enforcement on (`OSA_AUTH_MODE=required`) whenever any origin is allowed.

## Provider-dependent verification still open

- Live-provider acceptance (requires an explicitly configured CI secret)
- Live identity-provider certification (requires provider credentials)
- Integration tests against a concrete enterprise identity source
