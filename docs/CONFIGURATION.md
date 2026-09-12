# Configuration Reference

This document reflects `osa.generic_agent.config` and the deployment-bundle
loader (`osa.generic_agent.bundle`) on `main`.

## Loading and validation

`load_agent_definition()` accepts a YAML string or a `pathlib.Path`. It applies
supported environment overrides and validates the result as an immutable
Pydantic model. Unknown properties are rejected at every schema level.

## Selecting a runtime backend

The agent definition does not contain a framework selector. Deployment code
chooses the backend while preserving the same OSA definition and catalogs:

- `osa.runtimes.adk.AdkRuntime` is the packaged ADK 2.x runtime and owns the
  current FastAPI/A2A service surface.
- `osa.runtimes.langgraph.LangGraphRuntime` is the programmatic
  LangChain/LangGraph backend. It uses the shared `RuntimeDependencies` layer
  and currently supports native tools, sessions, memory, timeouts, and OSA
  streaming. Install its `providers` extra for direct LangChain integrations,
  or its `litellm` extra to reuse `provider: litellm` model entries; MCP, A2A,
  and the shared HTTP service adapter remain pending.

This keeps framework-native objects and provider packages out of the generic
configuration contract.

```python
from pathlib import Path

from osa.generic_agent import load_agent_definition

definition = load_agent_definition(Path("agent.yaml"))
```

## Complete schema

```yaml
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: example
  version: 0.1.0
  description: Example agent
  labels:
    team: platform
spec:
  description: Runtime-facing description
  instruction: Assist the user.
  model:
    ref: default
    parameters:
      temperature: 0.2
  mcps:
    - ref: knowledge
      tools_filter:
        - search
  tools:
    - ref: calculator
  skills:
    - ref: arithmetic
  memory:
    enabled: false
    policy: null
    scope: user
    max_entries: null
  session:
    persistence: false
    ttl_seconds: null
  a2a:
    enabled: false
  runtime:
    timeout_seconds: null
    max_iterations: null
```

## Fields

### Root and metadata

| Path | Type | Default | Current behavior |
|---|---|---:|---|
| `apiVersion` | string | `osa/v1alpha1` | Must equal `osa/v1alpha1`; other values are rejected |
| `kind` | string | `Agent` | Must equal `Agent`; other values are rejected |
| `metadata.name` | string | required | Used for runtime metadata and framework-specific agent naming |
| `metadata.version` | string | `0.1.0` | No semantic-version validation |
| `metadata.description` | string | empty | Used in generic metadata |
| `metadata.labels` | map of string | empty | Stored as metadata |

`spec.description` is separate from `metadata.description`; the selected
runtime backend uses `spec.description` when its framework supports an agent
description.

### Runtime references

| Path | Type | Default | Current behavior |
|---|---|---:|---|
| `spec.instruction` | string | empty | Used as the system/instruction message by the selected runtime backend |
| `spec.model` | model reference or null | null | Resolved from the catalog; an unknown reference fails fast, an absent reference uses the catalog default (deterministic mode only when no default exists) |
| `spec.model.parameters` | map | empty | Per-agent generation overrides; override `ModelDefinition.runtime_settings` |
| `spec.mcps` | MCP references | empty | Resolved and invoked by the ADK runtime (ADR-002); the LangGraph backend currently fails fast until its MCP adapter is added |
| `spec.mcps[].tools_filter` | string list | empty | Agent-level allowlist of server tool names (intersects the server definition's filter) |
| `spec.tools` | tool references | empty | Definitions and implementations resolve at construction |
| `spec.skills` | skill references | empty | Definitions resolve at construction as metadata |

Model, MCP, tool, and skill references accept either a bare string or an object:

```yaml
tools:
  - calculator
  - ref: lookup
```

### Memory, session, A2A, and runtime

#### Resource policy

`spec.policy` applies exact allow/deny rules before runtime resources are
constructed. An empty rule allows all names; a non-empty `allow` list becomes
an allow-list, and `deny` always wins. A2A uses `inbound` to control exposure.

```yaml
spec:
  policy:
    models: {allow: [default]}
    tools: {allow: [calculator], deny: [shell]}
    mcps: {deny: [untrusted-server]}
    skills: {allow: [support]}
    a2a: {deny: [inbound]}
```

Allow/deny overlap is invalid. The policy is independent of the prompt;
enterprise policy evaluation remains open. Inbound A2A security is enforced by
the shared OIDC/OAuth boundary. Kubernetes provider selection is available via
operator configuration; real Kind validation passes in CI. The provider
factory rejects `OSA_DEPLOY_PROVIDER=openshift` until a dedicated OpenShift
provider is implemented, so OpenShift-specific behavior is not mixed into the
generic Kubernetes path.

| Path | Type | Default | Current behavior |
|---|---|---:|---|
| `spec.memory.enabled` | boolean | false | Enables search-based context only when a provider is configured |
| `spec.memory.policy` | string or null | null | Must resolve in the bundle when memory is enabled; an attached policy is authoritative for scope, limits, and retention, and `enabled: false` on the policy disables memory |
| `spec.memory.scope` | enum | `user` | `user`, `agent`, `tenant`, or `application` (used when no policy is attached) |
| `spec.memory.max_entries` | integer or null | null | Per-scope cap; oldest entries are evicted beyond it (used when no policy is attached) |
| `spec.session.persistence` | boolean | false | Selects the durable PostgreSQL or explicit local SQLite session provider; startup fails closed when its DSN or migrated schema is absent |
| `spec.session.ttl_seconds` | integer or null | null | Must be > 0 when set; expired sessions are deleted on access |
| `spec.session.max_history_messages` | integer | 20 | Bounds the per-session conversation history |
| `spec.a2a.enabled` | boolean | false | Enables ADK runtime Agent Card and JSON-RPC A2A routes when the optional A2A extra is installed; not exposed by the current LangGraph slice |
| `spec.runtime.timeout_seconds` | integer or null | null | Must be > 0 when set; the invocation is cancelled with `invocation_timeout` when exceeded |
| `spec.runtime.max_iterations` | integer or null | null | Must be >= 1 when set; caps model/tool rounds (default 3) in both runtime backends, failing with `iteration_limit_exceeded` |

## Memory runtime behavior

Scope IDs are derived from the invocation context: `user` -> caller ID,
`agent` -> agent name, `tenant` -> `tenant_id` request metadata, and
`application` -> the deployment constant. Entries are never visible across
scope IDs or scopes.

When `spec.memory.policy` references a policy, that policy is authoritative:
its `scope`, `max_entries`, and `retention_days` replace the spec-level
fields, and `enabled: false` disables memory for the agent (writes raise,
reads return nothing). Limits are enforced after every write: the oldest
entries beyond `max_entries` are evicted per `(scope, scope_id)`, and entries
not updated within `retention_days` are purged.

Extraction is explicit — `remember()` only; raw interactions are never
persisted automatically. `MemoryPolicy.auto_extract` is reserved for a future
opt-in extraction pipeline (ADR-003).

## Persistence provider selection and fallback policy

Persistence is externalized independently for each subsystem. The service-level
database environment variable is the authoritative provider selector for that
subsystem; a bundle may opt into durable sessions with
`spec.session.persistence`, but it never contains database credentials.

The current contract is:

| Configuration state | Behavior |
|---|---|
| PostgreSQL DSN is explicitly configured | Use the PostgreSQL provider, apply the required operator-owned migration, and validate connectivity before readiness. |
| No DSN and the subsystem permits ephemeral operation | Use the documented in-memory/process-local provider. State is lost on restart and is not shared across replicas. |
| DSN is configured but unreachable, invalid, or unmigrated | Fail startup/readiness; never silently downgrade to SQLite or memory. |
| File-backed SQLite DSN for a subsystem with an explicit SQLite provider | Use that subsystem's local-only provider and its own migration command. The provider enables a five-second busy timeout, WAL, and foreign keys; on POSIX hosts migrated files are restricted to mode `0600`. `:memory:` and shared/network-replica use are rejected or unsupported. |

There is no implicit “try PostgreSQL, then SQLite, then memory” chain. This
prevents a database outage from turning durable state into silently divergent
or lost state. Production deployments must explicitly select durable providers
for any state that must survive restarts or be shared across replicas; memory
is appropriate for tests and single-process development only. SQLite-backed
development must be an explicit, subsystem-specific choice; it is not a
general fallback or a shared-replica provider. Set
`OSA_PERSISTENCE_POLICY=shared` to make this production posture fail closed:
enabled stateful surfaces require PostgreSQL, and the Control Plane also
requires the Kubernetes deployment provider.

### Current provider matrix

The current implementation intentionally has different provider contracts by
subsystem:

| Subsystem | Explicit durable selector | No DSN | SQLite status | Restart/replica behavior |
|---|---|---|---|---|
| Control Plane | `OSA_CONTROL_PLANE_DATABASE_URL` | In-memory repositories | Explicit file-backed `sqlite+aiosqlite:///...` with `osa-cp-migrate`; `:memory:` rejected | PostgreSQL survives restart and is shared across replicas; SQLite survives restart but is single-process; in-memory is process-local |
| Runtime memory | `OSA_MEMORY_DATABASE_URL` | In-memory provider | Explicit file-backed `sqlite+aiosqlite:///...` with `osa-memory-migrate`; `:memory:` rejected | PostgreSQL survives restart and is shareable after migration; SQLite survives restart but is single-process; in-memory is ephemeral |
| Runtime sessions | `spec.session.persistence: true` plus `OSA_SESSION_DATABASE_URL` | `SessionManager` when persistence is false; missing DSN fails when true | Explicit file-backed `sqlite:///...` with `osa-session-migrate`; `:memory:` rejected | PostgreSQL preserves ownership/history across restart and replicas; SQLite preserves it in one process; in-memory is process-local |
| A2A task records | `OSA_A2A_TASK_DATABASE_URL` | SDK in-memory task store | Explicit SQLite is supported for local testing where the SDK supports it; not a shared-production provider | PostgreSQL task records and OSA ownership leases survive restart and coordinate replicas; SDK task saves are fenced, but the active-task registry remains process-local |
| HTTP rate limits | `OSA_RATE_LIMIT_DATABASE_URL` | In-memory limiter | Explicit SQLite is exercised for local tests through SQLAlchemy; PostgreSQL is required for cross-replica production limits | Shared PostgreSQL windows coordinate replicas; in-memory/SQLite are local-only |
| Capability telemetry | `OSA_CAPABILITY_TELEMETRY_DATABASE_URL` or `OSA_CAPABILITY_TELEMETRY_PATH` | Disabled | SQLite is unsupported; JSONL is file-backed and local-only | PostgreSQL events are shared, deduplicated, ordered by database ingestion time plus ID, and retained/deleted under operator policy |

For every row, an explicitly selected durable provider is authoritative. Invalid,
unreachable, or unmigrated configured databases fail startup/readiness; the
service does not move to another row or silently discard state. The matrix is
covered by unit tests plus the optional PostgreSQL integration suite in CI.

### Control Plane deployment-operation ownership

When `OSA_CONTROL_PLANE_DATABASE_URL` selects PostgreSQL, the Control Plane
uses the migration-owned `osa_deployment_operation_owners` table to coordinate
mutating deployment operations across replicas. One tenant/resource key is
owned at a time; acquisition advances a fencing epoch and creates a stable
operation ID. The owner renews its lease while the provider call is in flight,
and PostgreSQL deployment-record writes are accepted only while that exact
owner and fence remain current.

| Variable | Purpose | Default |
|---|---|---:|
| `OSA_DEPLOYMENT_OPERATION_LEASE_SECONDS` | Lease duration for deploy, stop, restart, and rollback ownership; takeover is possible after expiry | `30` seconds (minimum `5`) |

Run `osa-cp-migrate` through migration `0010` before starting a PostgreSQL
Control Plane. A missing or unmigrated ownership table is a startup failure;
the service never creates it or falls back to local coordination. In-memory
and explicit SQLite Control Planes use process-local ownership and must not be
used to coordinate replicas. Read-only status, logs, and reconciliation remain
observation paths and do not wait on the mutating-operation lease.

## Memory persistence

By default memory is in-memory (single process, lost on restart). Setting
`OSA_MEMORY_DATABASE_URL` to `postgresql+asyncpg://user:pass@host/db` selects
the PostgreSQL provider; setting it to
`sqlite+aiosqlite:///./osa-memory.db` selects the explicit local SQLite
provider (`osa-adk-runtime[sqlite]`). PostgreSQL entries survive restarts and
are shared across replicas; SQLite entries survive restarts only for a local
single-process deployment. The independent memory schema is owned by the
versioned `osa-memory-migrate` command; runtime startup validates connectivity
and refuses to serve an unmigrated database:

```bash
OSA_MEMORY_DATABASE_URL=postgresql+asyncpg://... uv run osa-memory-migrate
```

For local SQLite:

```bash
OSA_MEMORY_DATABASE_URL=sqlite+aiosqlite:///./osa-memory.db uv run osa-memory-migrate
```

Back up the memory database before applying a new migration. Migrations are
forward-only in this release; rollback means restoring the last database
backup, then starting the runtime with the previous package version. The
Control Plane Alembic history does not manage this independent database.

Persistent sessions are opt-in per agent. Set `spec.session.persistence: true`
and run the separate session migration before starting the runtime. PostgreSQL
is used for shared deployments:

```bash
OSA_SESSION_DATABASE_URL=postgresql://... uv run osa-session-migrate
```

For local SQLite:

```bash
OSA_SESSION_DATABASE_URL=sqlite:///./osa-sessions.db uv run osa-session-migrate
```

The runtime then uses `OSA_SESSION_DATABASE_URL` for ownership-checked,
bounded-history sessions with optimistic concurrent-update protection. If the
flag is false, sessions remain process-local even when a DSN is present; under
`OSA_PERSISTENCE_POLICY=shared`, that process-local mode is rejected.

SQLite operations are local-disk operations: keep the database file and its
directory private, back it up with the service quiesced (or SQLite's online
backup API), and restore/verify the backup before rollout. Do not place these
files on a shared filesystem or use them as a replica coordination mechanism.

## A2A task persistence

Inbound A2A task state is process-local unless `OSA_A2A_TASK_DATABASE_URL` is
set. With that variable, the runtime uses the A2A SDK's SQLAlchemy
`DatabaseTaskStore` and validates the migrated schema before readiness. The
default table is `osa_a2a_tasks`; operators may set `OSA_A2A_TASK_TABLE` to a
simple SQL identifier when multiple runtime databases share a schema. The DSN
must use an async SQLAlchemy driver, for example:

```bash
OSA_A2A_TASK_DATABASE_URL=postgresql+asyncpg://... \
  OSA_A2A_TASK_TABLE=osa_a2a_tasks \
  uv run osa-runtime --config ./agent-bundle
```

Durable records are scoped by the validated OSA tenant and subject when the
shared authentication boundary is active; unauthenticated embedded callers
use the A2A protocol user scope. Run the explicit schema step before startup:

```bash
OSA_A2A_TASK_DATABASE_URL=postgresql+asyncpg://... \
  OSA_A2A_TASK_TABLE=osa_a2a_tasks \
  uv run osa-a2a-migrate
```

The migration provisions the SDK task table, a version row, and the OSA
ownership table `<task_table>_ownership`. Startup validates all three without
creating or altering them. The ownership row uses the same bounded tenant /
caller scope as task lookup, a worker lease, heartbeats, a fencing token on
takeover, and a durable cancellation request. A retry can replay a durable
terminal task or reclaim an expired lease. Remote cancellation waits for the
durable owner or safely finalizes cancellation after the owner lease expires;
terminal snapshots replay through read-only SDK events without a duplicate
write. The SDK active-task registry is still process-local. Durable SDK task
saves carry the ownership fence and hold the ownership-row lock through the
write, so an expired or superseded worker cannot persist a late task mutation.
Terminal events drain through the SDK consumer before the owner releases its
lease. Process-boundary PostgreSQL acceptance covers shared task creation,
lookup, cancellation, and crash recovery. Expired-owner retries finalize
non-terminal tasks as failed with a stable owner-loss message and do not replay
unknown model/tool side effects. Multi-process streaming/late-event acceptance
remains open. Deployment owners should
provision a dedicated database/schema and back it up according to their
operational policy.

Timeouts, TTLs, limits, and iterations carry positive/range validation
(`timeout_seconds > 0`, `ttl_seconds > 0`, `max_iterations >= 1`,
`max_entries >= 1`, model `temperature` in 0..2, `top_p` in 0..1, token
limits > 0, MCP connection options likewise).

## Agent-definition environment overrides

These environment variables override fields in an agent definition:

| Variable | Target field | Parsing |
|---|---|---|
| `OSA_AGENT_NAME` | `metadata.name` | string |
| `OSA_AGENT_VERSION` | `metadata.version` | string |
| `OSA_AGENT_DESCRIPTION` | `metadata.description` | string |
| `OSA_MODEL_REF` | `spec.model.ref` | string |
| `OSA_MEMORY_ENABLED` | `spec.memory.enabled` | boolean |
| `OSA_SESSION_PERSISTENCE` | `spec.session.persistence` | boolean |

The runtime also accepts these service-level controls:

| Variable | Purpose | Default |
|---|---|---:|
| `OSA_PERSISTENCE_POLICY` | `local` permits documented process-local/SQLite choices; `shared` requires PostgreSQL for enabled stateful surfaces and a shared deployment provider | `local` |
| `OSA_MEMORY_DATABASE_URL` | PostgreSQL DSN for shared memory or file-backed `sqlite+aiosqlite:///...` DSN for local memory | unset (in-memory) |
| `OSA_SESSION_DATABASE_URL` | PostgreSQL DSN for shared sessions or file-backed `sqlite:///...` DSN for local sessions when persistence is enabled | required only for persistent agents |
| `OSA_A2A_TASK_DATABASE_URL` | Async SQLAlchemy DSN for durable A2A task records | unset (in-memory) |
| `OSA_A2A_TASK_TABLE` | SQL identifier used by the A2A SDK task store | `osa_a2a_tasks` |
| `OSA_A2A_TASK_LEASE_SECONDS` | Ownership lease duration before takeover; must be at least 5 seconds | `30` |
| `OSA_A2A_TASK_CANCEL_WAIT_SECONDS` | Maximum time a remote cancellation waits for the owner before returning a retryable cancellation error | `30` |
| `OSA_RATE_LIMIT_REQUESTS` | Per-route, per-caller fixed-window request budget; `0` disables | `0` |
| `OSA_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window length | `60` |
| `OSA_RATE_LIMIT_BURST` | Optional per-window burst capacity | request budget |
| `OSA_RATE_LIMIT_DATABASE_URL` | Async SQLAlchemy DSN for atomic shared rate-limit windows | unset (in-memory) |
| `OSA_RATE_LIMIT_TABLE` | SQL identifier used by the shared rate-limit store | `osa_rate_limit_windows` |
| `OSA_CAPABILITY_TELEMETRY_PATH` | Optional bounded JSONL file for capability outcomes | unset |
| `OSA_CAPABILITY_TELEMETRY_MAX_BYTES` | Maximum JSONL sink size before newest-event compaction | `10000000` |
| `OSA_CAPABILITY_TELEMETRY_DATABASE_URL` | PostgreSQL DSN for the shared, durable capability telemetry sink; mutually exclusive with the JSONL path | unset |
| `OSA_CAPABILITY_TELEMETRY_TABLE` | SQL identifier for the shared capability telemetry table | `osa_capability_telemetry` |
| `OSA_CAPABILITY_TELEMETRY_RETENTION_DAYS` | Operator-owned retention window for shared capability events | `30` |

When enabled, the HTTP services expose rate-limit headers (`X-RateLimit-Limit`,
`X-RateLimit-Remaining`, and `X-RateLimit-Reset`) and return `429` with
`Retry-After` when the route/caller window is exhausted. The built-in store is
bounded and process-local. Set `OSA_RATE_LIMIT_DATABASE_URL` to use the
atomic PostgreSQL-compatible shared store across replicas; run
`uv run osa-rate-limit-migrate --database-url postgresql+asyncpg://...` before
starting a production service. The key contains only a method, bounded route,
and hashed caller identity; request payloads are never stored. Long-running
operation ownership and gateway-level global quotas remain deployment policy.

Capability telemetry is emitted for model, native-tool, and MCP spans as
bounded Prometheus counters. Set `OSA_CAPABILITY_TELEMETRY_PATH` to enable the
bounded, sanitized JSONL sink (or provide an `Observability` capability sink
programmatically). It receives only bounded capability kind/name, success or
failure, stable error code, duration, event ID, operation/tenant correlation,
producer time, and optional sequence metadata; prompts, inputs, outputs,
credentials, and tool arguments are never included. The file sink is
process-local. For replica-wide durable events, set
`OSA_CAPABILITY_TELEMETRY_DATABASE_URL` to PostgreSQL and run
`uv run osa-capability-telemetry-migrate --database-url postgresql+asyncpg://...`
before startup. The shared sink deduplicates by stable `event_id`, stores
tenant/operation correlation and producer sequence metadata, orders reads by
database `ingested_at` plus `event_id`, and applies the configured retention
window. A database sink and JSONL sink cannot be enabled together.

Boolean values are case-insensitive. Accepted true values are `1`, `true`,
`yes`, and `on`; false values are `0`, `false`, `no`, and `off`.

`OSA_MODEL_REF` applies to bare-string model references by replacing them
outright: `model: default` plus `OSA_MODEL_REF=other` resolves to
`{ref: other}`. Environment overrides always win over the file value.

If an intermediate YAML value is some other non-mapping, the override is
skipped and schema validation reports the underlying problem.

## HTTP authentication

Control Plane and runtime HTTP applications share
`osa.generic_agent.auth`. Authentication is disabled by default for local
development. Set `OSA_AUTH_MODE=required` in a deployed service to require a
signed JWT Bearer token on every endpoint except liveness, readiness, and
OpenAPI discovery. `optional` validates a token when one is supplied but
permits anonymous requests. Enabled modes require an issuer and audience.
`OSA_AUTH_JWKS_URL` is optional: when it is unset, OSA fetches the issuer's
standard `.well-known/openid-configuration` document and uses its validated
`jwks_uri`. Set `OSA_AUTH_DISCOVERY_URL` when the provider uses a non-default
discovery endpoint. An explicitly configured JWKS URL takes precedence and
avoids discovery. When opaque OAuth access tokens are used, configure RFC 7662
introspection below; the introspection client secret is resolved only at
request time.

OSA validates the JWT locally against the configured or OIDC-discovered JWKS document. The
supported signing algorithms are RS256/384/512 and ES256/384/512. The token
must contain `exp`, `iss`, and `sub`, and its issuer/audience must match the
configured values. `OSA_AUTH_REQUIRED_SCOPES` is a space-separated list; a
missing scope returns 403. JWKS is cached and refreshed when an unknown key ID
is encountered.

| Variable | Meaning | Default |
|---|---|---:|
| `OSA_AUTH_MODE` | `disabled`, `optional`, or `required` | `disabled` |
| `OSA_AUTH_ISSUER` | Expected JWT `iss` and OIDC issuer URL | unset |
| `OSA_AUTH_AUDIENCE` | Expected JWT `aud` value | unset |
| `OSA_AUTH_JWKS_URL` | Optional explicit HTTP JSON Web Key Set URL; takes precedence over discovery | unset |
| `OSA_AUTH_DISCOVERY_URL` | Optional OIDC discovery document URL; defaults to `{issuer}/.well-known/openid-configuration` | unset |
| `OSA_AUTH_INTROSPECTION_URL` | Optional RFC 7662 token introspection endpoint | unset |
| `OSA_AUTH_INTROSPECTION_CLIENT_ID` | Confidential client ID for introspection | unset |
| `OSA_AUTH_INTROSPECTION_CLIENT_SECRET_KEY` | Secret-reference key for the introspection client secret | unset |
| `OSA_AUTH_INTROSPECTION_CLIENT_SECRET_SOURCE` | Secret source for the introspection client secret | `env` |
| `OSA_AUTH_INTROSPECTION_CLIENT_SECRET_ENV_VAR` | Optional environment variable override for that secret | unset |
| `OSA_AUTH_INTROSPECTION_TIMEOUT_SECONDS` | Introspection request timeout, >0 and <=30 seconds | `5.0` |
| `OSA_AUTH_REQUIRED_SCOPES` | Required scopes separated by spaces | empty |
| `OSA_AUTH_ENFORCE_PERMISSIONS` | Enforce mapped HTTP permissions from roles, permissions, and scopes | `false` |
| `OSA_AUTH_CLOCK_SKEW_SECONDS` | JWT clock leeway, 0..300 seconds | `30` |
| `OSA_AUTH_JWKS_TIMEOUT_SECONDS` | JWKS request timeout, >0 and <=30 seconds | `2.0` |
| `OSA_AUTH_JWKS_CACHE_SECONDS` | JWKS cache lifetime, >0 and <=86400 seconds | `300` |

Permission enforcement requires `OSA_AUTH_MODE=optional` or `required`; the
configuration is rejected when authentication is `disabled`.

### Browser CORS (runtime)

Browser clients such as the Control Panel cannot call the runtime cross-origin
unless the runtime explicitly allows it. Set `OSA_RUNTIME_ALLOWED_ORIGINS` to a
comma-separated list of allowed origins to enable CORS on the runtime
application. When set, the runtime adds `CORSMiddleware` allowing `GET` and
`POST` from the listed origins, with `Authorization`, `Content-Type`, and
`X-Request-ID` headers permitted and `X-Request-ID` exposed. Preflight `OPTIONS`
requests bypass the bearer boundary.

When a deployment is launched through the Control Plane,
`OSA_DEPLOY_RUNTIME_ALLOWED_ORIGINS` is forwarded as
`OSA_RUNTIME_ALLOWED_ORIGINS` to the runtime process.

| Variable | Meaning | Default |
|---|---|---:|
| `OSA_RUNTIME_ALLOWED_ORIGINS` | Comma-separated browser origins allowed to call the runtime | unset (no CORS) |
| `OSA_DEPLOY_RUNTIME_ALLOWED_ORIGINS` | Forwarded by the deployment service as `OSA_RUNTIME_ALLOWED_ORIGINS` | unset |

When enabled, `OSA_AUTH_ENFORCE_PERMISSIONS` maps known routes to stable
permissions: `agent:invoke`, `agent:read`, `agent:write`, `resource:read`,
`resource:write`, `deployment:read`, `deployment:write`,
`external-agent:read`, `external-agent:write`, and `audit:read`. The validator accepts
roles from `roles`/`role` and Keycloak `realm_access.roles`, explicit
permissions from `permissions`/`permission`, and configured scopes. Built-in
role mappings are: `administrator`/`admin` wildcard; `operator` all
permissions; `viewer` read permissions; `agent`/`caller`/`user`
`agent:invoke`; and `service` `agent:invoke` plus `resource:read`.

The validated token subject is the runtime caller identity when `user_id` is
omitted, and a different supplied `user_id` is rejected. A `tenant_id` or
`tid` claim is bound to runtime invocation metadata; omitted metadata is
injected, while a mismatch or an unscoped tenant claim is rejected. Control
Plane managed agents are assigned the authenticated tenant on creation and are
filtered and protected by that tenant on subsequent agent routes. Deployment
records inherit agent tenant ownership and are protected by the same boundary.
Resource definitions use the same tenant boundary, with equal names allowed in
different tenants and tenant-scoped catalog resolution during activation and
deployment bundle export. PostgreSQL migration 0005 stores resource ownership.
Inbound A2A JSON-RPC and runtime invocation routes use the same bearer
boundary. With `OSA_AUTH_MODE=required` (or permission enforcement enabled),
the generated Agent Card advertises a required `osa_oidc` scheme; a validated
subject and tenant are propagated into the OSA session and invocation
metadata. Enterprise policy evaluation remains open. Outbound API-key,
OAuth2, and mTLS adapters are available for MCP and external A2A calls.
Token material is never logged or retained after validation.

## Secret references

Reusable resource definitions may contain:

```yaml
credential_ref:
  source: env
  key: provider-api-key
  env_var: PROVIDER_API_KEY
```

`SecretReference` stores metadata only; resolved values are never stored on
models, returned in responses, logged, or embedded in error messages.

`EnvironmentSecretResolver` (`osa.generic_agent.secret`) resolves secrets from
environment variables: `source` must be `env`, and the variable is `env_var`
when set, otherwise `key` itself. Unresolvable secrets raise
`SecretResolutionError`, which identifies the reference - never the value.
Service bootstraps resolve every bundle secret before reporting ready.

## Outbound credentials

MCP definitions and external A2A agent records may use a `credential` object.
It contains references only; the configured `SecretResolver` resolves values
when the connection is created. Resolved values are never stored in a
definition, returned by an API, logged, or included in an error. Outbound
destinations use the shared URL/DNS/private-network and redirect policy;
deployment-level egress restrictions remain required defense in depth.

API keys are sent in a named HTTP header. An optional
`environment_variable` is useful for stdio MCP servers:

```yaml
credential:
  type: api_key
  secret_ref:
    source: env
    key: PARTNER_API_KEY
  header_name: X-API-Key
```

OAuth 2.0 uses the client-credentials grant and sends the resulting bearer
token to the remote endpoint. `client_secret_ref` is resolved only for the
token request; `scopes` and `audience` are optional:

```yaml
credential:
  type: oauth2
  token_url: https://identity.example.test/oauth/token
  client_id: osa-runtime
  client_secret_ref:
    source: env
    key: OSA_OAUTH_CLIENT_SECRET
  scopes: [agent.invoke]
```

mTLS resolves certificate and private-key references as file paths, with an
optional CA bundle path:

```yaml
credential:
  type: mtls
  certificate_ref: {source: env, key: OSA_CLIENT_CERT_PATH}
  private_key_ref: {source: env, key: OSA_CLIENT_KEY_PATH}
  ca_bundle_ref: {source: env, key: OSA_CA_BUNDLE_PATH}
```

The legacy `credential_ref` field remains supported: MCP HTTP and A2A calls
treat it as a bearer token, while stdio MCP injects it under `env_var` or
`key`. A definition must not configure both `credential` and
`credential_ref`.

## Deployment bundles

A deployment bundle is one agent plus the catalog resources it references
(`osa.generic_agent.bundle`). Two layouts are supported.

A single `AgentBundle` document:

```yaml
apiVersion: osa/v1alpha1
kind: AgentBundle
metadata:
  name: my-bundle
agent:
  apiVersion: osa/v1alpha1
  kind: Agent
  metadata:
    name: greeter
  spec:
    instruction: Say hello.
    model:
      ref: default
models:
  - name: default
    provider: litellm
    model_id: openai/gpt-4o-mini
```

A directory layout (see `examples/smoke-bundle`):

```text
my-bundle/
├── agent.yaml          # standard AgentDefinition document (required)
├── bundle.yaml         # optional bundle metadata (name, version, labels)
├── models/*.yaml       # resource envelopes
├── tools/*.yaml
├── skills/*.yaml
├── mcps/*.yaml
└── memory-policies/*.yaml
```

Each resource file is an envelope with `apiVersion: osa/v1alpha1`, a `kind`
(`Model`, `Tool`, `Skill`, `Mcp`, or `MemoryPolicy`), and the domain
definition under `spec`:

```yaml
apiVersion: osa/v1alpha1
kind: Model
spec:
  name: default
  provider: litellm
  model_id: openai/gpt-4o-mini
  credential_ref:
    source: env
    key: OPENAI_API_KEY
```

Loading (`load_bundle`) is fail-fast: unknown resource kinds, unsupported
apiVersions, duplicate resource names, and agent references to missing
resources all raise deterministic `BundleError` subclasses before the bundle
is usable. Native tool implementations ship with the runtime (see
`osa.runtimes.adk.service.BUILTIN_TOOLS`); an agent referencing a tool
definition without an available implementation fails at construction.

## MCP runtime behavior

MCP servers referenced by an agent connect lazily at invocation time
(`osa.runtimes.adk.mcp_client`, ADR-002) using the official `mcp` SDK.

The runtime supports the official SDK 1.x and 2.x compatibility lines
(`mcp>=1.24,<3`). OSA normalizes the SDK-major differences in timeout values,
wire-model field names, and the Streamable HTTP client. The checked-in lock is
authoritative for normal installs; CI also runs the MCP protocol and ADK Runner
suites against representative versions of both supported majors.

- **Transports:** `stdio` (uses `command`/`args`/`env`) and
  `streamable_http` (uses `endpoint`). Legacy `sse` is not supported at
  runtime and fails with `mcp_transport_not_supported`.
- **Credentials:** `credential` resolves through the shared outbound adapter
  at connect time; API-key and OAuth2 credentials are sent as configured HTTP
  headers, and mTLS supplies the client certificate/key and optional CA bundle.
  For stdio, API-key/OAuth2 credentials can be injected into an explicitly
  configured environment variable. The legacy `credential_ref` shorthand
  retains its bearer/stdio behavior. Values are never stored or logged.
- **Options:** `timeout_seconds` bounds connection and call attempts;
  `max_retries`/`retry_delay_seconds` bound transient failures;
  `tls_verify` disables certificate verification for HTTP servers;
  `max_response_bytes` caps tool results (excess raises
  `mcp_response_too_large`).
- **Filtering and namespacing:** the server definition's `tools_filter` and
  the agent reference's `tools_filter` intersect; tools are exposed as
  `<server>_<tool>` with origin metadata preserved.
- **Failures are deterministic:** a server that cannot connect, authorize,
  answer in time, or stay within limits produces stable OSA errors
  (`mcp_connection_failed`, `mcp_tool_failed`, `mcp_response_too_large`) —
  the invocation fails with a clear message rather than silently losing the
  tools.

## Precedence

The implemented precedence for agent fields is:

```text
Pydantic defaults < YAML < supported OSA_* environment variables
```

Generation settings use explicit precedence:

```text
ModelDefinition.runtime_settings (catalog defaults) < ModelRef.parameters (per-agent overrides)
```

Bundle secrets are resolved before service readiness; a bundle whose secrets
cannot be resolved never starts.
