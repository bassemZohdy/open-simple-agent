# HTTP API Reference

This document describes routes implemented on `main`. The Control Plane uses
in-memory repositories by default and can use PostgreSQL; the runtime keeps
session state in its configured provider and memory can use PostgreSQL. Neither
application currently provides rate limiting. Both use the stable OSA error
envelope `{"error": {"code", "message"}}` and share the optional JWT Bearer
authentication boundary described below.

## Control Plane API

Application: `osa.control_plane.backend.api:app`

Development command:

```bash
uv run uvicorn osa.control_plane.backend.api:app --reload
```

Interactive OpenAPI documentation is available at `/docs` while the service is
running.

### Routes

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/agents` | Create a draft agent record from a built-in template, an inline definition, or neither (explicit draft placeholder) |
| `GET` | `/agents` | List records; filters (`q`, `status`, `skill`, `runtime`) combine with AND; results are sorted and paginated |
| `GET` | `/agents/{agent_id}` | Get one record |
| `GET` | `/agents/{agent_id}/versions` | List immutable version metadata without returning definitions |
| `GET` | `/agents/{agent_id}/versions/{version_id}` | Return one immutable version with secret-like values redacted |
| `PATCH` | `/agents/{agent_id}` | Replace selected record fields; optional `expected_version` for optimistic concurrency |
| `POST` | `/agents/{agent_id}/versions` | Snapshot the current definition as a new immutable version |
| `POST` | `/agents/{agent_id}/activate` | Transition to `active` after validating the definition and its resource references |
| `POST` | `/agents/{agent_id}/disable` | Transition to `disabled` |
| `POST` | `/agents/{agent_id}/archive` | Transition to `archived` (terminal) |
| `DELETE` | `/agents/{agent_id}` | Delete a record |
| `GET` | `/health/live` | Process liveness response |
| `GET` | `/health/ready` | Always reports ready in the current implementation |

### Create request

```json
{
  "name": "support-agent",
  "description": "Support agent",
  "template": "support",
  "labels": {
    "team": "service"
  }
}
```

`template` and `definition` are mutually exclusive: providing both returns
422. With neither, the API creates an explicit draft placeholder without a
definition. A definition whose `metadata.name` differs from the request name
is rejected with 422.

The built-in templates are `generic`, `support`, and `research`. Template skill
names are not automatically registered in the Skill Catalog.

### Listing, pagination, and sorting

`GET /agents` query parameters:

| Parameter | Behavior |
|---|---|
| `status` | One of `draft`, `active`, `disabled`, `archived`; unknown values return 400 |
| `skill` / `runtime` / `q` | Combine with `status` (AND) |
| `sort_by` | `name` (default), `created_at`, or `updated_at`; unknown values return 422 |
| `order` | `asc` (default) or `desc` |
| `limit` | Page size, 1..200, default 50 |
| `offset` | Page offset, >= 0 |

The response reports `total` (count after filters, before pagination) plus
`limit` and `offset`.

### Lifecycle transitions

Allowed transitions: `draft -> active`, `draft -> archived`,
`active -> disabled`, `active -> archived`, `disabled -> active`,
`disabled -> archived`. `archived` is terminal. Invalid transitions return
400 with code `invalid_transition`. Activation requires a definition whose
model, tool, skill, MCP, and memory-policy references resolve in the resource
catalogs (422 otherwise).

### Versions

`GET /agents/{agent_id}/versions` returns the agent's immutable version history
in creation order. Each entry includes its identifier, version label, creation
metadata, change summary, and whether a definition snapshot exists. Definition
contents are never returned by this endpoint because they may contain
credentials or other deployment-only configuration.

`GET /agents/{agent_id}/versions/{version_id}` returns the selected immutable
snapshot for authorized inspection. It includes the same metadata plus a
`definition` object and `redacted_fields`. Secret-like keys in arbitrary model
parameters are replaced with `"<redacted>"`; safe secret-reference metadata
such as `credential_ref` and `secret_ref` remains available. The endpoint never
resolves secrets and does not return resolved credentials.

`POST /agents/{agent_id}/versions` takes a JSON body:

```json
{
  "version": "2.0.0",
  "change_summary": "Major update"
}
```

Each version is an immutable snapshot of the agent's definition at creation
time; later record updates never mutate it. Duplicate version identifiers
return 409.

### Optimistic concurrency

`PATCH /agents/{agent_id}` accepts `expected_version`; when it does not match
the record's current version the request fails with 409 (code `conflict`).

### Error envelope

Every error response uses:

```json
{
  "error": {
    "code": "conflict",
    "message": "Agent with name 'x' already exists"
  }
}
```

Documented codes: `not_found` (404), `conflict` (409), `bad_request` (400),
`invalid_transition` (400), `validation_error` (422),
`authentication_failed` (401), and `authorization_denied` (403).

### Authentication

Both applications accept `auth_settings`/`authenticator` when constructed and
otherwise read `OSA_AUTH_*` environment variables. `disabled` is the default;
`optional` permits anonymous requests but validates a supplied Bearer token;
`required` rejects anonymous non-public requests. `/health/live`,
`/health/ready`, `/docs`, `/redoc`, and `/openapi.json` remain public. Invalid
or missing credentials return 401 with `WWW-Authenticate: Bearer`; a valid
token lacking configured scopes or violating an identity check returns 403.

The current validator accepts signed RS256/384/512 and ES256/384/512 JWTs,
checks `exp`, `iss`, `sub`, issuer, audience, and configured scopes against an
explicit JWKS URL or the issuer's standard OIDC discovery `jwks_uri`. An
explicit JWKS URL takes precedence; discovery metadata must advertise the
configured issuer and an absolute HTTP JWKS URI. On `/v1/invoke`, an omitted `user_id` uses the token `sub`;
an explicitly different `user_id` is denied. RFC 7662 introspection can
validate opaque bearer tokens, applying the same issuer, audience, scope, role,
permission, and tenant checks. This is an authentication
foundation. Set `OSA_AUTH_ENFORCE_PERMISSIONS=true` to enforce stable route
permissions. Roles are read from `roles`/`role` or Keycloak
`realm_access.roles`; explicit permissions are read from `permissions` or
`permission`, and scopes also count as permissions. Built-in roles are
`administrator`/`admin` (wildcard), `operator` (all), `viewer` (read),
`agent`/`caller`/`user` (`agent:invoke`), and `service`
(`agent:invoke` plus `resource:read`). Runtime invocations also bind
`tenant_id`/`tid` to request metadata, injecting an omitted value and rejecting
spoofed or mismatched values. Control Plane managed agents are assigned the
authenticated `tenant_id`/`tid` on creation; list and lifecycle/read routes
return only the same tenant's records. Resource definitions use the same
tenant scope, allow equal names in different tenants, and are resolved from
tenant-scoped catalogs; PostgreSQL migration 0005 stores the owner. Inbound
A2A JSON-RPC uses the same bearer boundary and propagates the validated
subject/tenant into the OSA request. Protected Agent Cards advertise the
required `osa_oidc` scheme. Outbound API-key/OAuth2/mTLS adapters are available
for external-agent calls.

### Audit events

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/audit-events?limit=` | Recent append-only management/invocation audit events (tenant-filtered, redaction-safe) |
| `GET` | `/metrics` | Prometheus counters and duration summaries |

`GET /audit-events?limit=` returns recent append-only management and external
agent invocation events within the caller's tenant scope. The `audit:read`
permission is granted to administrators and operators when permission
enforcement is enabled. Event details contain only safe identifiers, action
names, statuses, versions, and changed-field names; request payloads,
definitions, prompts, credentials, and remote outputs are never recorded.
The in-memory repository is the default; PostgreSQL persistence uses migration
0006. Runtime and A2A boundary invocations plus authentication/authorization
denials are emitted through the optional runtime audit sink. Internal
capability-level events and distributed persistence remain open in P2.2.

### Definition resource policy

`spec.policy` provides exact `allow`/`deny` rules for `models`, `tools`,
`mcps`, `skills`, and A2A `inbound` exposure. Policies are enforced before
runtime resource construction; denied references produce the stable
`policy_violation` error without exposing prompts or credentials.

### Resource and template APIs (P1.2)

Tenant-scoped catalog resources are managed under `/resources/{kind}` with `kind` one of
`Model`, `Tool`, `Skill`, `Mcp`, or `MemoryPolicy` (unknown kinds return
404):

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/resources/{kind}` | Create from an envelope (`{apiVersion, kind, spec}`); duplicate names return 409 within the caller's tenant |
| `GET` | `/resources/{kind}?q=` | List envelopes (optionally filtered by name substring) |
| `GET` | `/resources/{kind}/{name}` | Get one envelope |
| `PUT` | `/resources/{kind}/{name}` | Replace (must exist; `spec.name` must match the path) |
| `DELETE` | `/resources/{kind}/{name}` | Delete; 409 with the referencing agent names if any agent definition uses the resource |
| `POST` | `/resources/import` | Import a list of envelopes (bundle resource-file format); existing resources are replaced |
| `GET` | `/resources/export` | Export all resources as envelopes |
| `GET` | `/templates` | List built-in agent templates (read-only) |

All reads and writes are restricted to the caller's tenant (or the shared scope
when authentication is disabled). Equal names in different tenants are
independent resources. Writes are validated against the domain schema (422 on
violation) and persisted write-through to the `ResourceDefinitionRepository`,
so resources survive restarts and are shared across replicas. Secret values never appear:
`credential_ref` exposes only non-secret coordinates (`source`, `key`,
`env_var`) and is redacted defensively in every response.

### Deployment APIs (P1.5)

Deployments launch the agent's **current definition** (must be active) as a
local runtime process: the Control Plane exports the definition plus its
referenced resources to a bundle directory and launches it through a
server-owned command template (`OSA_DEPLOY_COMMAND_TEMPLATE`, default
`osa-runtime --config {bundle_path} --port {port}`). **Requests never carry
process commands** — unknown fields are rejected — and readiness is probed
against the launched runtime before the deployment reports `running`.
Deployment responses also carry `invoke_url`: an optional public runtime
endpoint synthesized from `OSA_DEPLOY_INVOKE_URL_TEMPLATE` (placeholders
`{deployment_id}`, `{agent_id}`, `{version}`, `{port}`; ADR-008), or `null`
when the template is unset.

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/agents/{agent_id}/deploy` | Deploy the current definition (agent must be active) |
| `GET` | `/agents/{agent_id}/deployments` | Deployment history for an agent |
| `GET` | `/deployments/{deployment_id}` | Observed status (persisted through the deployment record repository) |
| `POST` | `/deployments/{deployment_id}/stop` | Stop the deployment |
| `POST` | `/deployments/{deployment_id}/restart` | Restart (same identity, fresh process) |
| `GET` | `/deployments/{deployment_id}/logs?tail=` | Bounded captured logs (newest last) |
| `POST` | `/deployments/{deployment_id}/rollback?version=` | Relaunch from an earlier immutable version snapshot |

Every transition persists intent and observed state through the
`DeploymentRecordRepository` (in-memory by default, PostgreSQL when the
Control Plane is configured with a database). The Kubernetes provider and
multi-host scheduling remain open (see `TODO.md`).

### A2A and external agents (P2.1)

When an agent definition sets `spec.a2a.enabled: true` (and the runtime runs
with the `osa-adk-runtime[a2a]` extra), the runtime API serves:

- `GET /.well-known/agent-card.json` — the A2A Agent Card generated from the
  validated definition and resolved skills (name, version, description,
  skills, `text/plain` modes; `protocol_binding: JSONRPC`).
- `POST /a2a` — A2A JSON-RPC `message/send`: one A2A task per invocation,
  submitted → working → completed (agent output as a task artifact) or
  failed (deterministic error text). The A2A context id maps to an OSA
  session created on first contact, so multi-turn conversations keep one
  session per conversation.

External agents are A2A servers outside OSA, tracked as records distinct
from managed agents (they are never deployed). Registration may include a
redacted credential reference, for example:

```json
{
  "name": "partner",
  "url": "https://partner.example.test",
  "credential": {
    "type": "oauth2",
    "token_url": "https://identity.example.test/oauth/token",
    "client_id": "osa-control-plane",
    "client_secret_ref": {"source": "env", "key": "PARTNER_CLIENT_SECRET"},
    "scopes": ["agent.invoke"]
  }
}
```

Credential values are resolved from the configured secret resolver and are
never included in external-agent responses, audit events, or errors.

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/external-agents` | Register by URL and optional outbound `credential`; the Agent Card is fetched and validated (422 if unreachable/invalid); duplicate names 409 |
| `GET` | `/external-agents?status=` | List records with health status |
| `GET` | `/external-agents/{external_id}` | Get one record |
| `POST` | `/external-agents/{external_id}/refresh` | Re-fetch the card and update health |
| `POST` | `/external-agents/{external_id}/invoke?message=` | Invoke the external agent over A2A (502 on remote failure) |
| `DELETE` | `/external-agents/{external_id}` | Delete the record |

Attempts to deploy an external record are rejected with 422 — external
agents are never deployed by OSA. A2A JSON-RPC uses the same shared
bearer/OIDC enforcement as the runtime invoke route, and protected Agent
Cards advertise the required `osa_oidc` scheme. Outbound remote-agent calls can
attach the configured API-key, OAuth2, or mTLS credential.

### Streaming (P2.4)

`POST /v1/invoke/stream` streams one invocation as Server-Sent Events. The
shared bearer/OIDC auth middleware applies identically to non-streaming
invoke. Events carry JSON `data` payloads with stable fields (`type`,
`invocation_id`, `session_id`, `text`, monotonic `seq`):

| SSE `event:` | Meaning |
|---|---|
| `osa.started` | Invocation accepted; carries the server-issued session id |
| `osa.message.delta` | Incremental model text (per runner round; token-level deltas require a streaming model) |
| `osa.message` | Terminal success; `text` is exactly what `POST /v1/invoke` would return |
| `osa.error` | Terminal deterministic failure (`invocation_timeout`, `iteration_limit_exceeded`, `model_invocation_failed`, ...) |

`runtime.timeout_seconds` bounds the whole stream lifetime (a slow consumer
counts against it); `max_iterations` is enforced mid-stream. Disconnecting
the client cancels the underlying ADK run. Yields go directly to the
consumer with no server-side buffering, so a slow consumer applies natural
backpressure.

**Replicas:** sessions and conversation context live in the
`SessionProvider`. Replicas configured with the same shared provider
(persistent provider; tracked in the backlog) share session state, keep
ownership enforced identically, and stream without leaking another caller's
events — verified by cross-replica tests over a shared provider.

## Runtime API

Application: `osa.runtimes.adk.api:runtime_app`

Two ways to serve it:

- Service (production path): the `osa-runtime` CLI or
  `osa.runtimes.adk.service.create_runtime_app(bundle_path)` bootstraps from a
  deployment bundle during startup:

  ```bash
  OSA_BUNDLE=/app/config uv run osa-runtime --config ./my-bundle --port 8080
  ```

  Startup validates the bundle and resolves every secret before the process
  reports ready; an invalid bundle aborts startup. SIGTERM triggers uvicorn's
  graceful shutdown, which closes the runtime.

- Programmatic: call `initialize_runtime(definition)` and serve
  `runtime_app` directly (tests and embedding).

### Routes

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/v1/invoke` | Invoke the single initialized agent |
| `GET` | `/v1/capabilities` | Return agent name, version, session flag, tools, and skills |
| `GET` | `/health/live` | Process liveness response |
| `GET` | `/health/ready` | Ready only after successful bundle initialization |

### Invoke request

```json
{
  "input": "Hello",
  "session_id": null,
  "user_id": "user-123",
  "metadata": {
    "channel": "web",
    "tenant_id": "tenant-1"
  }
}
```

### Invoke response

```json
{
  "output": "Model response",
  "invocation_id": "generated-id",
  "session_id": "generated-or-existing-id",
  "error": null
}
```

### Sessions and errors

Session semantics (enforced by the OSA `SessionProvider`):

- Omitting `session_id` creates a new session; the response returns its
  server-issued stable ID.
- A caller-supplied unknown ID returns **404** (`session_not_found`) — unknown
  IDs are never silently created.
- A session is only accessible to its own `(agent_name, user_id, tenant_id)`
  owner; foreign access returns **403** (`session_access_denied`).
- Sessions carry a TTL and a bounded history when configured.

Model/timeout/limit failures are returned as HTTP 200 with `error` populated
(domain results); `invocation_timeout` and `iteration_limit_exceeded` carry
deterministic messages. Genuine upstream model failures map to **502** with
the `model_invocation_failed` code when raised to the HTTP layer.

### Current runtime behavior

- Invocation flows through the ADK `Runner`; tools execute through ADK-native
  function calling with declarations from `ToolDefinition.capabilities`.
- One agent is stored in module-level state, matching the bundle model.
- Sessions are in memory and not replica-safe; memory can use the PostgreSQL
  provider when `OSA_MEMORY_DATABASE_URL` is set.
- The `fake` model provider requires explicit opt-in via
  `OSA_ALLOW_FAKE_PROVIDER=1` in service bootstraps.
- A2A Agent Card and JSON-RPC routes are available when `spec.a2a.enabled` and
  the optional A2A extra are installed.
- With required authentication, the Agent Card advertises `osa_oidc` and the
  JSON-RPC route requires the same validated bearer token as `/v1/invoke`.
- `/metrics` exposes bounded Prometheus counters/duration summaries. Set
  `OSA_LOG_FORMAT=json` for redaction-safe structured logs; OpenTelemetry API
  spans are emitted when an SDK provider/exporter is configured.
- Streaming is not implemented.
