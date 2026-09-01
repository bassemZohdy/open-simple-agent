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
checks `exp`, `iss`, `sub`, issuer, audience, and configured scopes against the
external JWKS URL. On `/v1/invoke`, an omitted `user_id` uses the token `sub`;
an explicitly different `user_id` is denied. This is an authentication
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
tenant-scoped catalogs; PostgreSQL migration 0005 stores the owner. A2A
security schemes, outbound API-key/OAuth/mTLS adapters, and audit events remain
open in P2.2.

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
from managed agents (they are never deployed):

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/external-agents` | Register by URL: the Agent Card is fetched and validated (422 if unreachable/invalid); duplicate names 409 |
| `GET` | `/external-agents?status=` | List records with health status |
| `GET` | `/external-agents/{id}` | Get one record |
| `POST` | `/external-agents/{id}/refresh` | Re-fetch the card and update health |
| `POST` | `/external-agents/{id}/invoke?message=` | Invoke the external agent over A2A (502 on remote failure) |
| `DELETE` | `/external-agents/{id}` | Delete the record |

Attempts to deploy an external record are rejected with 422 — external
agents are never deployed by OSA. A2A security-scheme enforcement remains
open in P2.2; the generic HTTP JWT middleware does not yet enforce an A2A
security scheme or attach credentials to outbound remote-agent calls.

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
- Streaming is not implemented; A2A security-scheme enforcement is pending.
