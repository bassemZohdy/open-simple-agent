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

## Not implemented yet

- Image signing and registry provenance
- Live identity-provider certification (requires provider credentials)
- Enterprise identity lifecycle beyond the built-in baseline
